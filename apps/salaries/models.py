"""
Salary records (spec §10, §11).

Design decisions:
  * Unique per (employee, organization_unit, period_year, period_month): one
    employee can be paid by more than one branch in the same month (they
    genuinely worked at both), so uniqueness is scoped per branch too — not
    just per employee+period. Each branch's own figure stays a separate,
    independently visible row; only re-importing the SAME branch's data for
    the same period revises that branch's own row.
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
  * Extra components (bonus, tax, pension, overtime...) live in a JSON `components`
    field plus a few first-class columns, so the model extends without migrations
    for every new payroll element (spec §11).
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

    gross_salary = models.DecimalField(
        _("gross salary"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    advance = models.DecimalField(
        _("advance"), max_digits=14, decimal_places=2, default=Decimal("0")
    )
    deductions = models.DecimalField(
        _("deductions"), max_digits=14, decimal_places=2, default=Decimal("0")
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
            # At most one CURRENT salary per employee, per period, PER BRANCH —
            # the same employee can have two current salaries for the same
            # month if two different branches both paid them.
            models.UniqueConstraint(
                fields=["employee", "organization_unit", "period_year", "period_month"],
                condition=models.Q(is_current=True),
                name="unique_current_salary_per_period_per_unit",
            ),
            models.CheckConstraint(
                condition=models.Q(period_month__gte=1) & models.Q(period_month__lte=12),
                name="salary_month_range",
            ),
            # No negative core amounts (spec §13).
            models.CheckConstraint(
                condition=(
                    models.Q(gross_salary__gte=0)
                    & models.Q(advance__gte=0)
                    & models.Q(deductions__gte=0)
                    & models.Q(net_salary__gte=0)
                ),
                name="salary_non_negative",
            ),
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
