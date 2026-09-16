"""
ExcelValidationService (spec §13, §14, §15).

Parses an uploaded Excel into normalized rows, validates every row, and returns
a structured preview WITHOUT touching the database. The importer only runs after
an admin confirms.

Key rules:
  * File format / extension / size validated before parsing (caller + here).
  * An employee is identified by phone OR JSHSHIR — whichever the row has.
    Neither column is required to exist in the file; a real payroll export
    often only carries one of the two (many only have JSHSHIR/ПИНФЛ, not a
    phone number at all — the employee's phone lives in the HR registry,
    not in payroll). employee_code, if present, is stored as a label only
    and never used to look anyone up.
  * Phone/JSHSHIR match an employee GLOBALLY, regardless of which branch
    they were last seen under — which branch someone "belongs to" is not
    part of their identity, only a record of who most recently reported
    them (see SalaryImportService, which moves them to this unit on
    confirm).
  * Employees are pre-registered (HR registry / Employee CRUD) — a payroll
    row is never allowed to create one. A phone that matches no existing
    Employee is a validation error, not a new hire.
  * Column ORDER never matters — every column is resolved by its header
    text, wherever it sits. Real payroll exports (e.g. a 1C-style HR
    report) also aren't reliably one clean header row: a second row can
    carry sub-codes with no phone/name of their own, and the same header
    text can appear twice for unrelated columns. Both are handled below.
  * Only the 7 canonical fields are ever read — everything else in a wide
    export (national ID, department, individual bonus/deduction line
    items, ...) is simply never looked at, let alone sent to the employee.
  * Salary values numeric and non-negative, no duplicate employee within the
    file (existing or newly-created).
  * Formula cells are rejected (openpyxl data_only=False lets us detect them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from apps.common.jshshir import normalize_jshshir
from apps.common.phone import is_valid_uz_phone, normalize_phone
from apps.employees.models import Employee

# Canonical fields the importer understands.
CANONICAL_FIELDS = [
    "employee_code",
    "phone",
    "jshshir",
    "full_name",
    "gross_salary",
    "advance",
    "deductions",
    "net_salary",
]

# Recognized header text per canonical field, tried in order, first match
# wins. Covers both a simple hand-made template and a real Uzbek-railway/1C
# payroll export (Cyrillic) — a unit's own ColumnMapping (see ColumnMapping
# model) always takes priority over these when set.
DEFAULT_HEADER_CANDIDATES: dict[str, list[str]] = {
    "employee_code": ["employee_code", "табельный номер", "таб. номер"],
    "phone": ["phone", "phone_number", "телефон"],
    "jshshir": ["jshshir", "jshshir_raqami", "пинфл"],
    "full_name": ["full_name", "сотрудник", "ф.и.ш", "физическое лицо"],
    "gross_salary": ["gross_salary", "всего начислено"],
    "advance": ["advance", "аванс"],
    "deductions": ["deductions", "всего удержано"],
    "net_salary": ["net_salary", "сальдо на конец", "к выдаче"],
}
# Back-compat: some callers (build_template, error report headers) still
# want a single canonical->label default, e.g. for the downloadable template.
DEFAULT_HEADERS = {field: candidates[0] for field, candidates in DEFAULT_HEADER_CANDIDATES.items()}

# Only the salary figure is truly required in every file. Phone and JSHSHIR
# are the two possible employee identifiers, but neither column has to exist
# on its own — a row just needs at least one of them filled in (checked per
# row in _validate_row, not here, since it's an "either/or" not an "all").
REQUIRED_ALL = ["net_salary"]

MONEY_FIELDS = ["gross_salary", "advance", "deductions", "net_salary"]

# Header text (case-insensitive) that must NEVER reach the employee even
# though it isn't one of the 7 canonical fields: row numbering, duplicate
# name column, national ID, and internal HR classification codes. Every
# OTHER column with a value on a given row is sent to the employee as-is
# (Salary.components) — the payroll breakdown (bonuses, allowances,
# deductions, ...) genuinely belongs to them, so it isn't hand-picked here.
EXCLUDED_COMPONENT_HEADERS = {
    "табельный номер", "phone_number", "номер п/п", "физическое лицо",
    "пинфл", "тарифная группа", "разряд",
}


@dataclass
class RowResult:
    row_number: int
    raw: dict
    normalized: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    employee_id: int | None = None
    telegram_linked: bool = False
    # Every other non-empty, non-excluded column on this row, as it will be
    # shown to the employee: [{"label": <header text>, "value": <cell value>}, ...]
    components: list = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass
class ValidationReport:
    total_rows: int = 0
    valid_rows: int = 0
    error_rows: int = 0
    telegram_linked: int = 0
    telegram_unlinked: int = 0
    rows: list[RowResult] = field(default_factory=list)
    fatal_error: str | None = None  # e.g. missing columns / unreadable file

    def to_payload(self) -> dict:
        """JSON-serializable summary stored on the SalaryImport."""
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "error_rows": self.error_rows,
            "telegram_linked": self.telegram_linked,
            "telegram_unlinked": self.telegram_unlinked,
            "fatal_error": self.fatal_error,
            "rows": [
                {
                    "row_number": r.row_number,
                    "normalized": {k: str(v) for k, v in r.normalized.items()},
                    "raw": {k: (str(v) if v is not None else "") for k, v in r.raw.items()},
                    "errors": r.errors,
                    "employee_id": r.employee_id,
                    "telegram_linked": r.telegram_linked,
                    "components": r.components,
                }
                for r in self.rows
            ],
        }


class ExcelValidationService:
    def __init__(self, *, organization_unit, header_mapping: dict | None = None):
        self.unit = organization_unit
        # Explicit per-unit override only (canonical_field -> excel header
        # text). Anything not overridden here falls back to trying every
        # entry in DEFAULT_HEADER_CANDIDATES for that field.
        self.header_mapping = header_mapping or {}

    # --------------------------------------------------------------------- #
    def validate(self, file_obj) -> ValidationReport:
        report = ValidationReport()
        try:
            wb = load_workbook(file_obj, read_only=True, data_only=False)
        except Exception:
            report.fatal_error = "Faylni o'qib bo'lmadi. Excel (.xlsx) fayl ekanligini tekshiring."
            return report

        ws = wb.active
        rows_iter = ws.iter_rows(values_only=False)

        try:
            header_cells = next(rows_iter)
        except StopIteration:
            report.fatal_error = "Fayl bo'sh."
            return report

        headers = [
            (str(c.value).strip() if c.value is not None else "") for c in header_cells
        ]
        # Build excel-header -> column index. Some real exports repeat a
        # header (e.g. two columns both literally called "Всего начислено")
        # for unrelated subtotals — first occurrence wins, since that's
        # consistently the primary summary column in the files we've seen.
        header_index: dict[str, int] = {}
        for i, h in enumerate(headers):
            if h:
                header_index.setdefault(h.lower(), i)

        # Resolve canonical field -> column index. A unit's own explicit
        # mapping is used as-is; otherwise try each recognized header text
        # for that field, in order, until one is found in this file.
        col_for: dict[str, int] = {}
        for canonical in CANONICAL_FIELDS:
            candidates = (
                [self.header_mapping[canonical]] if canonical in self.header_mapping
                else DEFAULT_HEADER_CANDIDATES.get(canonical, [canonical])
            )
            for candidate in candidates:
                idx = header_index.get(str(candidate).strip().lower())
                if idx is not None:
                    col_for[canonical] = idx
                    break

        # Check required columns.
        missing = [req for req in REQUIRED_ALL if req not in col_for]
        if missing:
            report.fatal_error = "Majburiy ustunlar topilmadi: " + ", ".join(missing)
            return report

        # Every column NOT already used for a canonical field and not on the
        # exclusion list becomes a generic component — whatever it's called.
        component_cols = [
            (i, h) for i, h in enumerate(headers)
            if h and i not in col_for.values() and h.lower() not in EXCLUDED_COMPONENT_HEADERS
        ]

        # Preload ALL active employees for fast phone/JSHSHIR lookup —
        # matching is global, not scoped to this unit.
        active_employees = list(Employee.objects.filter(is_active=True))
        by_phone = {e.normalized_phone: e for e in active_employees}
        by_jshshir = {e.jshshir: e for e in active_employees if e.jshshir}

        seen_employee_ids: set[int] = set()
        seen_new_phones: set[str] = set()
        seen_new_jshshirs: set[str] = set()
        row_number = 1  # header was row 1

        for cells in rows_iter:
            row_number += 1
            values = [c.value for c in cells]
            # Skip completely empty rows.
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
                continue

            raw = {
                field_name: (values[idx] if idx < len(values) else None)
                for field_name, idx in col_for.items()
            }
            # Not an employee row — no phone, no JSHSHIR, AND no name (e.g. a
            # payroll export's secondary sub-code row under the real header,
            # or a subtotal/footer row). Skip silently rather than reporting
            # a bogus "identifier required" error; a genuine employee row
            # missing all identification but WITH a name still gets flagged
            # below (a name alone can't identify anyone).
            if (not self._clean_str(raw.get("phone"))
                    and not self._clean_str(raw.get("jshshir"))
                    and not self._clean_str(raw.get("full_name"))):
                continue

            rr = RowResult(row_number=row_number, raw=raw)

            # Every other non-empty column on this row -> a component the
            # employee will see, whatever it's labeled.
            for idx, header_text in component_cols:
                if idx >= len(values):
                    continue
                v = values[idx]
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                rr.components.append({"label": header_text, "value": self._json_safe(v)})

            # Reject formula cells (spec §13).
            for field_name, idx in col_for.items():
                cell = cells[idx] if idx < len(cells) else None
                if cell is not None and isinstance(cell.value, str) and cell.value.startswith("="):
                    rr.errors.append(f"'{field_name}' katagida formula bor")

            self._validate_row(rr, by_phone, by_jshshir, seen_employee_ids,
                               seen_new_phones, seen_new_jshshirs)
            report.rows.append(rr)

        wb.close()

        # Aggregate.
        report.total_rows = len(report.rows)
        for r in report.rows:
            if r.is_valid:
                report.valid_rows += 1
                if r.telegram_linked:
                    report.telegram_linked += 1
                else:
                    report.telegram_unlinked += 1
            else:
                report.error_rows += 1
        return report

    # --------------------------------------------------------------------- #
    def _validate_row(self, rr, by_phone, by_jshshir, seen_ids,
                      seen_new_phones, seen_new_jshshirs) -> None:
        raw = rr.raw
        code = self._clean_str(raw.get("employee_code"))
        phone_raw = self._clean_str(raw.get("phone"))
        norm_phone = normalize_phone(phone_raw) if phone_raw else ""
        full_name = self._clean_str(raw.get("full_name"))
        # Used both to identify the employee (when phone is absent/unmatched)
        # and, later, as a second identity check against the matched
        # Employee's own on-file JSHSHIR at notification time.
        jshshir = normalize_jshshir(raw.get("jshshir"))

        rr.normalized["employee_code"] = code
        rr.normalized["phone"] = norm_phone
        rr.normalized["full_name"] = full_name
        rr.normalized["jshshir"] = jshshir

        # Resolve the employee — by phone first if it's present and looks
        # valid, falling back to JSHSHIR. Globally, regardless of branch.
        employee = None
        phone_invalid = False
        if norm_phone:
            if is_valid_uz_phone(norm_phone):
                employee = by_phone.get(norm_phone)
            else:
                phone_invalid = True
        if employee is None and jshshir:
            employee = by_jshshir.get(jshshir)

        if employee is None:
            # Employees are pre-registered (HR registry / Employee CRUD) —
            # a payroll row never creates one. Failing to identify anyone is
            # an error, not a new hire.
            if not phone_raw and not jshshir:
                rr.errors.append("Xodimni aniqlash uchun telefon yoki JSHSHIR ko'rsatilishi kerak")
            elif phone_invalid and not jshshir:
                rr.errors.append("Telefon raqami noto'g'ri formatda")
            elif (norm_phone and norm_phone in seen_new_phones) or (
                jshshir and jshshir in seen_new_jshshirs
            ):
                rr.errors.append("Faylda bu xodim (telefon/JSHSHIR) takrorlangan")
            else:
                if norm_phone:
                    seen_new_phones.add(norm_phone)
                if jshshir:
                    seen_new_jshshirs.add(jshshir)
                rr.errors.append(
                    "Bu xodim ro'yxatda topilmadi — avval uni Xodimlar bo'limida ro'yxatdan o'tkazing"
                )
        else:
            if employee.id in seen_ids:
                rr.errors.append("Faylda bu xodim takrorlangan")
            else:
                seen_ids.add(employee.id)
            rr.employee_id = employee.id
            rr.telegram_linked = employee.is_telegram_linked

        # Money fields.
        for f in MONEY_FIELDS:
            if f not in raw:
                rr.normalized[f] = Decimal("0")
                continue
            value = self._to_decimal(raw.get(f))
            if value is None:
                if f == "net_salary" or raw.get(f) not in (None, ""):
                    rr.errors.append(f"'{f}' qiymati son emas")
                rr.normalized[f] = Decimal("0")
            elif value < 0:
                rr.errors.append(f"'{f}' manfiy bo'lishi mumkin emas")
                rr.normalized[f] = value
            else:
                rr.normalized[f] = value

    @staticmethod
    def _clean_str(v) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @staticmethod
    def _json_safe(v):
        """validation_payload is a JSONField — keep only JSON-native types,
        stringify anything else (e.g. a date-formatted cell -> datetime)."""
        if v is None or isinstance(v, (int, float, str, bool)):
            return v
        return str(v)

    @staticmethod
    def _to_decimal(v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            if isinstance(v, str):
                v = v.replace(" ", "").replace("\u00a0", "").replace(",", ".")
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError):
            return None
