"""
SalaryImportService (spec §10 revisions, §31 workflow).

Runs the two-phase import:
  1. validate_and_stage(): parse + validate, store the report on the import,
     set status to VALID / HAS_ERRORS. No salary rows written.
  2. confirm_and_commit(): admin has confirmed — write valid rows to the DB.
     Re-importing a period supersedes the previous current salary (revision++)
     instead of destroying it.

Excel template + error-report generators live here too (spec §39, §40).
"""
from __future__ import annotations

import io

from django.db import IntegrityError, transaction
from django.utils import timezone
from openpyxl import Workbook

from apps.accounts.permissions import assert_can_access_unit
from apps.common.export_safety import safe_cell
from apps.employees.models import Employee
from apps.imports.models import ImportStatus, SalaryImport
from apps.imports.validators import (
    CANONICAL_FIELDS,
    DEFAULT_HEADERS,
    ExcelValidationService,
)
from apps.salaries.models import Salary


class SalaryImportService:
    # ------------------------------------------------------------------ #
    @staticmethod
    def _header_mapping_for(unit) -> dict:
        mapping = getattr(unit, "column_mapping", None)
        return mapping.mapping if mapping else {}

    @classmethod
    def validate_and_stage(cls, salary_import: SalaryImport) -> SalaryImport:
        salary_import.status = ImportStatus.VALIDATING
        salary_import.save(update_fields=["status"])

        service = ExcelValidationService(
            organization_unit=salary_import.organization_unit,
            header_mapping=cls._header_mapping_for(salary_import.organization_unit),
        )
        salary_import.file.open("rb")
        try:
            report = service.validate(salary_import.file)
        finally:
            salary_import.file.close()

        if report.fatal_error:
            salary_import.status = ImportStatus.FAILED
            salary_import.validation_payload = {"fatal_error": report.fatal_error}
            salary_import.total_rows = 0
            salary_import.save()
            return salary_import

        salary_import.total_rows = report.total_rows
        salary_import.valid_rows = report.valid_rows
        salary_import.error_rows = report.error_rows
        salary_import.telegram_linked_count = report.telegram_linked
        salary_import.telegram_unlinked_count = report.telegram_unlinked
        salary_import.validation_payload = report.to_payload()
        salary_import.status = (
            ImportStatus.VALID if report.error_rows == 0 else ImportStatus.HAS_ERRORS
        )
        salary_import.save()
        return salary_import

    # ------------------------------------------------------------------ #
    @classmethod
    @transaction.atomic
    def confirm_and_commit(cls, salary_import: SalaryImport, *, user) -> SalaryImport:
        """Write the valid rows. Only VALID or HAS_ERRORS imports can be confirmed."""
        assert_can_access_unit(user, salary_import.organization_unit_id)

        if salary_import.status not in {ImportStatus.VALID, ImportStatus.HAS_ERRORS}:
            raise ValueError("Bu importni tasdiqlab bo'lmaydi (holati mos emas).")

        salary_import.status = ImportStatus.PROCESSING
        salary_import.save(update_fields=["status"])

        payload = salary_import.validation_payload or {}
        rows = payload.get("rows", [])
        unit = salary_import.organization_unit

        for row in rows:
            if row.get("errors"):
                continue  # skip invalid rows — only valid ones are imported
            employee_id = row.get("employee_id")
            if not employee_id:
                # Validation guarantees every error-free row is matched to a
                # pre-registered Employee — this should be unreachable.
                continue
            # Branch isn't part of an employee's identity — it just
            # tracks who most recently reported them. Moving branches
            # (or being newly reused across branches) is normal here.
            Employee.objects.filter(
                pk=employee_id
            ).exclude(organization_unit=unit).update(organization_unit=unit)
            norm = row.get("normalized", {})
            upsert_kwargs = dict(
                salary_import=salary_import,
                employee_id=employee_id,
                gross=norm.get("gross_salary", "0"),
                advance=norm.get("advance", "0"),
                deductions=norm.get("deductions", "0"),
                net=norm.get("net_salary", "0"),
                components=row.get("components", []),
                payroll_jshshir=norm.get("jshshir", ""),
            )
            try:
                with transaction.atomic():
                    cls._upsert_salary(**upsert_kwargs)
            except IntegrityError:
                # Lost a race with another confirm of the very same
                # employee+branch+period (select_for_update can't lock a row
                # that doesn't exist yet — see _upsert_salary). The other
                # transaction has since committed revision 1, so retrying
                # once now finds it and correctly becomes revision 2 instead
                # of crashing the whole import.
                with transaction.atomic():
                    cls._upsert_salary(**upsert_kwargs)

        salary_import.status = ImportStatus.CONFIRMED
        salary_import.confirmed_at = timezone.now()
        salary_import.save(update_fields=["status", "confirmed_at"])
        return salary_import

    @staticmethod
    def _upsert_salary(*, salary_import, employee_id, gross, advance, deductions, net,
                       components=None, payroll_jshshir=""):
        from decimal import Decimal

        year, month = salary_import.period_year, salary_import.period_month
        unit = salary_import.organization_unit
        # Scoped by branch too: an employee can have a separate current
        # salary per branch for the same month (they worked at more than
        # one). Only re-importing THIS SAME branch's data revises it.
        existing = (
            Salary.objects.select_for_update()
            .filter(
                employee_id=employee_id,
                organization_unit=unit,
                period_year=year,
                period_month=month,
                is_current=True,
            )
            .first()
        )
        gross_d, advance_d = Decimal(str(gross)), Decimal(str(advance))
        deductions_d, net_d = Decimal(str(deductions)), Decimal(str(net))
        if existing is not None and (
            existing.gross_salary == gross_d
            and existing.advance == advance_d
            and existing.deductions == deductions_d
            and existing.net_salary == net_d
            and existing.payroll_jshshir == (payroll_jshshir or "")
            and existing.components == (components or [])
        ):
            # Byte-identical re-upload of the same period (accidental
            # double-upload of the same file) — nothing actually changed, so
            # don't bump the revision or trigger a redundant "your salary"
            # notification for no reason.
            return

        next_revision = 1
        if existing:
            next_revision = existing.revision + 1
            existing.is_current = False
            existing.save(update_fields=["is_current", "updated_at"])

        Salary.objects.create(
            employee_id=employee_id,
            organization_unit=unit,
            period_year=year,
            period_month=month,
            gross_salary=gross_d,
            advance=advance_d,
            deductions=deductions_d,
            net_salary=net_d,
            components=components or [],
            payroll_jshshir=payroll_jshshir or "",
            source_import=salary_import,
            is_current=True,
            revision=next_revision,
        )

    # ------------------------------------------------------------------ #
    # Excel template + error report (spec §39, §40)
    # ------------------------------------------------------------------ #
    @staticmethod
    def build_template(unit=None) -> bytes:
        mapping = {}
        if unit and getattr(unit, "column_mapping", None):
            mapping = unit.column_mapping.mapping
        headers = [mapping.get(f, DEFAULT_HEADERS[f]) for f in CANONICAL_FIELDS]

        wb = Workbook()
        ws = wb.active
        ws.title = "Salary"
        ws.append(headers)
        # Example row.
        ws.append(["EMP-001", "998901234567", "30101234567890", "Aliyev Ali",
                   8000000, 2000000, 100000, 5900000])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    def build_error_report(salary_import: SalaryImport) -> bytes:
        payload = salary_import.validation_payload or {}
        wb = Workbook()
        ws = wb.active
        ws.title = "Errors"
        ws.append(["Row", "F.I.Sh", "PINFL", "Error"])
        for row in payload.get("rows", []):
            if not row.get("errors"):
                continue
            norm = row.get("normalized", {})
            ws.append([
                row.get("row_number"),
                safe_cell(norm.get("full_name", "")),
                safe_cell(norm.get("jshshir", "")),
                "; ".join(row.get("errors", [])),
            ])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
