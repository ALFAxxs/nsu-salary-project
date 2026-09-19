"""
Salary records (spec §10, §11).

Design decisions:
  * Unique per (employee, organization_unit, period_year, period_month,
    payroll_employee_code): one employee can be paid by more than one
    branch in the same month (they genuinely worked at both), so
    uniqueness is scoped per branch too — not just per employee+period.
    The same is true WITHIN one branch: a real payroll export can list the
    same person twice under two different tabel numbers (two concurrent
    positions/stakes, or an old record carrying a leftover balance
    alongside a new one) — payroll_employee_code (a snapshot of that row's
    own employee_code) is what tells those apart, so each stays its own
    row and gets its own notification, exactly like the cross-branch case.
    Only re-importing the SAME branch+tabel-number's data for the same
    period revises that specific row.
  * organization_unit is a snapshot of who paid this specific salary, set
    once at creation and never changed — deliberately independent of
    Employee.organization_unit, which just tracks whoever most recently
    reported the employee and can move over time. Without this, an
    employee transferring branches would silently rewrite which branch
    "owns" (and can see) their past payroll.
  * Re-importing the same period (same branch) does NOT silently overwrite.
    Instead we keep the old row as a revision (is_current=False) and create
    a new current row. This preserves an audit trail of corrections (spec
    §10 "import revision/history").
  * Extra components (the 50+ bonus/allowance/overtime line items a real 1C
    payroll export can carry, whose exact set and wording genuinely differs
    file to file — see apps.imports.validators) live in a JSON `components`
    field, so the model extends without migrations for those. The handful of
    figures proven stable across every branch's export (gross/advance/
    deductions/net, income_tax, pension_contribution, union_dues, social_tax)
    are first-class columns instead, for reliable reporting (spec §11).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _


class Salary(models.Model):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.CASCADE,
        related_name="salaries",
        verbose_name=_("employee"),
    )
    # Which branch actually paid/reported this salary — fixed at creation,
    # independent of the employee's current organization_unit (see above).
    organization_unit = models.ForeignKey(
        "organizations.OrganizationUnit",
        on_delete=models.CASCADE,
        related_name="salaries",
        verbose_name=_("organization unit"),
    )
    period_year = models.PositiveSmallIntegerField(_("period year"))
    period_month = models.PositiveSmallIntegerField(_("period month"))  # 1..12

    # JSHSHIR as it appeared in THIS payroll row (if the file had that
    # column) — a snapshot, kept even if the employee's own on-file JSHSHIR
    # later changes. Used as a second identity check before a notification
    # is sent (see apps.notifications.services._eligible_status): phone
    # being telegram-linked is no longer enough on its own — this must also
    # match Employee.jshshir, or the message is held back for review.
    payroll_jshshir = models.CharField(_("payroll JSHSHIR"), max_length=14, blank=True)
    # This row's own employee_code/tabel number, as it appeared in the
    # payroll file — the disambiguator when the same employee (same phone
    # or JSHSHIR) appears more than once in one branch's file for the same
    # period (two positions, two stakes, ...). Blank is its own valid value
    # for files with no employee_code column at all — in that case there's
    # only ever one row per employee per branch per period, same as before.
    payroll_employee_code = models.CharField(_("payroll employee code"), max_length=64, blank=True)

    gross_salary = models.DecimalField(
        _("gross salary"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    advance = models.DecimalField(
        _("advance"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    deductions = models.DecimalField(
        _("deductions"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    # Breakdown of `deductions` — unlike the 50+ bonus/allowance line items
    # (which vary too much file-to-file to ever be stable columns, see
    # Salary.components below), these four appear under this exact same
    # header text in every real payroll export we've compared (10 files,
    # 8 branches): НДФЛ, ИНПС, "Удержание членских профсоюзных взносов",
    # Социальный налог. Optional (default 0) since older imports and any
    # future file missing one of these columns still validate fine.
    income_tax = models.DecimalField(
        _("income tax (NDFL)"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    pension_contribution = models.DecimalField(
        _("pension contribution (INPS)"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    union_dues = models.DecimalField(
        _("union dues"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    # Employer-side cost, NOT withheld from the employee's own pay (so it
    # plays no part in net_salary) — kept for company-cost reporting only,
    # and deliberately left out of the employee-facing message.
    social_tax = models.DecimalField(
        _("social tax"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    net_salary = models.DecimalField(
        _("net salary"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    currency = models.CharField(_("currency"), max_length=8, default="UZS")

    # Every extra column from the source Excel not already captured above —
    # bonuses, allowances, per-item deductions, etc. — as
    # [{"label": <original column header>, "value": <cell value>}, ...],
    # shown to the employee alongside the core figures (spec §11: extends
    # without a schema change per new payroll element). Populated by
    # ExcelValidationService / SalaryImportService; see apps.imports.validators.
    components = models.JSONField(_("components"), default=list, blank=True)

    # Revision tracking. Only one row per (employee, year, month) has
    # is_current=True; superseded rows are kept for history.
    is_current = models.BooleanField(_("is current"), default=True)
    revision = models.PositiveIntegerField(_("revision"), default=1)

    source_import = models.ForeignKey(
        "imports.SalaryImport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="salaries",
        verbose_name=_("source import"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("salary")
        verbose_name_plural = _("salaries")
        ordering = ["-period_year", "-period_month", "-revision"]
        indexes = [
            models.Index(fields=["employee", "period_year", "period_month"]),
            models.Index(fields=["period_year", "period_month"]),
            models.Index(fields=["organization_unit"]),
        ]
        constraints = [
            # At most one CURRENT salary per employee, per period, per branch,
            # per payroll tabel-number — the same employee can have more than
            # one current salary for the same month+branch if the payroll
            # file itself lists them more than once (two positions/stakes),
            # each under its own employee_code. Re-importing the SAME
            # employee_code's row for that period+branch revises it in place;
            # a DIFFERENT employee_code for the same person is a separate row.
            models.UniqueConstraint(
                fields=["employee", "organization_unit", "period_year", "period_month",
                       "payroll_employee_code"],
                condition=models.Q(is_current=True),
                name="unique_current_salary_per_period_per_unit_per_code",
            ),
            models.CheckConstraint(
                condition=models.Q(period_month__gte=1) & models.Q(period_month__lte=12),
                name="salary_month_range",
            ),
            # Negative core amounts ARE allowed — a real payroll balance can
            # legitimately go negative (e.g. an employee who owes money back
            # after an overpayment), so it's shown to them as-is rather than
            # rejected.
        ]

    def __str__(self) -> str:
        return (
            f"{self.employee_id}@{self.organization_unit_id} "
            f"{self.period_year}-{self.period_month:02d} net={self.net_salary}"
        )

    @property
    def period_label(self) -> str:
        months = [
            "", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
            "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
        ]
        return f"{months[self.period_month]} {self.period_year}"
