"""
ReportService (spec §23, §24, §27).

All aggregates respect the user's organization scope by building on the scoped
selectors. Head office sees per-branch breakdowns; branch users see only their
own unit.
"""
from __future__ import annotations

from django.db.models import Count, Q, Sum

from apps.accounts.permissions import scope_employees, scope_imports
from apps.employees.models import Employee
from apps.imports.models import SalaryImport
from apps.notifications.models import MessageStatus, TelegramMessage
from apps.organizations.models import OrganizationUnit
from apps.salaries.models import Salary
from apps.salaries.selectors import current_salaries_for


class ReportService:
    @staticmethod
    def dashboard_summary(user, *, year: int | None = None, month: int | None = None) -> dict:
        employees = scope_employees(Employee.objects.all(), user)
        total_employees = employees.count()
        connected = employees.filter(telegram_id__isnull=False).count()

        salaries = current_salaries_for(user)
        if year and month:
            salaries = salaries.filter(period_year=year, period_month=month)

        imports = scope_imports(SalaryImport.objects.all(), user)
        if year and month:
            imports = imports.filter(period_year=year, period_month=month)

        msgs = TelegramMessage.objects.all()
        if not user.is_global_scope:
            unit_ids = user.accessible_unit_ids()
            msgs = msgs.filter(salary__organization_unit_id__in=unit_ids)
        if year and month:
            msgs = msgs.filter(salary__period_year=year, salary__period_month=month)

        sent = msgs.filter(status=MessageStatus.SENT).count()
        failed = msgs.filter(status__in=[MessageStatus.FAILED, MessageStatus.BLOCKED]).count()

        return {
            "total_employees": total_employees,
            "telegram_connected": connected,
            "telegram_unconnected": total_employees - connected,
            "current_imports": imports.count(),
            "messages_sent": sent,
            "messages_failed": failed,
            "total_payroll": salaries.aggregate(s=Sum("net_salary"))["s"] or 0,
        }

    @staticmethod
    def branch_breakdown(user, *, year: int | None = None, month: int | None = None) -> list[dict]:
        """Per-unit rows for the head-office dashboard table (spec §23).

        Built from a handful of aggregated queries (one GROUP BY per model)
        instead of looping per unit, so this stays flat as branch count grows.
        """
        from apps.accounts.permissions import scope_organization_units

        units = list(scope_organization_units(
            OrganizationUnit.objects.filter(is_active=True), user
        ))
        unit_ids = [u.id for u in units]

        emp_stats = {
            row["organization_unit_id"]: row
            for row in Employee.objects.filter(organization_unit_id__in=unit_ids)
            .values("organization_unit_id")
            .annotate(
                employees=Count("id"),
                connected=Count("id", filter=Q(telegram_id__isnull=False)),
            )
        }

        # Salary/message stats are keyed by which branch actually paid/sent
        # them (Salary.organization_unit) — not by the employee's current
        # branch, which can differ if they've since transferred.
        sal_qs = Salary.objects.filter(
            organization_unit_id__in=unit_ids, is_current=True
        )
        if year and month:
            sal_qs = sal_qs.filter(period_year=year, period_month=month)
        imported_stats = {
            row["organization_unit_id"]: row["imported"]
            for row in sal_qs.values("organization_unit_id")
            .annotate(imported=Count("id"))
        }

        msg_qs = TelegramMessage.objects.filter(salary__organization_unit_id__in=unit_ids)
        if year and month:
            msg_qs = msg_qs.filter(salary__period_year=year, salary__period_month=month)
        msg_stats = {
            row["salary__organization_unit_id"]: row
            for row in msg_qs.values("salary__organization_unit_id")
            .annotate(
                sent=Count("id", filter=Q(status=MessageStatus.SENT)),
                failed=Count(
                    "id",
                    filter=Q(status__in=[MessageStatus.FAILED, MessageStatus.BLOCKED]),
                ),
            )
        }

        rows = []
        for unit in units:
            e = emp_stats.get(unit.id, {})
            m = msg_stats.get(unit.id, {})
            rows.append({
                "unit": unit,
                "employees": e.get("employees", 0),
                "connected": e.get("connected", 0),
                "imported": imported_stats.get(unit.id, 0),
                "sent": m.get("sent", 0),
                "failed": m.get("failed", 0),
            })
        return rows

    @staticmethod
    def salary_totals(user, *, year: int, month: int) -> dict:
        """
        Per-branch payroll totals for one period (spec: filial/oy bo'yicha
        yalpi/avans/ushlab qolingan/sof umumiy hisobot) — HR-restricted, see
        User.can_view_salary_report. Branch/accountant admins only ever get
        their own unit (accessible_unit_ids scopes `units` below to one row);
        head office/super admin get every branch plus a grand total.

        "employees" is a distinct headcount (Count(..., distinct=True)) since
        one employee can have more than one current Salary row this period
        (two branches, or two positions in one branch) — the money sums
        below intentionally still include every such row, since each is a
        real, separate payment.
        """
        from apps.accounts.permissions import scope_organization_units

        units = list(scope_organization_units(
            OrganizationUnit.objects.filter(is_active=True), user
        ))
        unit_ids = [u.id for u in units]

        sal_qs = Salary.objects.filter(
            organization_unit_id__in=unit_ids, is_current=True,
            period_year=year, period_month=month,
        )
        stats = {
            row["organization_unit_id"]: row
            for row in sal_qs.values("organization_unit_id").annotate(
                employees=Count("employee", distinct=True),
                gross=Sum("gross_salary"),
                advance=Sum("advance"),
                deductions=Sum("deductions"),
                income_tax=Sum("income_tax"),
                pension_contribution=Sum("pension_contribution"),
                union_dues=Sum("union_dues"),
                social_tax=Sum("social_tax"),
                net=Sum("net_salary"),
            )
        }

        rows = []
        for unit in units:
            s = stats.get(unit.id, {})
            rows.append({
                "unit": unit,
                "employees": s.get("employees") or 0,
                "gross": s.get("gross") or 0,
                "advance": s.get("advance") or 0,
                "deductions": s.get("deductions") or 0,
                "income_tax": s.get("income_tax") or 0,
                "pension_contribution": s.get("pension_contribution") or 0,
                "union_dues": s.get("union_dues") or 0,
                "social_tax": s.get("social_tax") or 0,
                "net": s.get("net") or 0,
            })

        totals = {
            "employees": sum(r["employees"] for r in rows),
            "gross": sum(r["gross"] for r in rows),
            "advance": sum(r["advance"] for r in rows),
            "deductions": sum(r["deductions"] for r in rows),
            "income_tax": sum(r["income_tax"] for r in rows),
            "pension_contribution": sum(r["pension_contribution"] for r in rows),
            "union_dues": sum(r["union_dues"] for r in rows),
            "social_tax": sum(r["social_tax"] for r in rows),
            "net": sum(r["net"] for r in rows),
        }
        return {"rows": rows, "totals": totals}
