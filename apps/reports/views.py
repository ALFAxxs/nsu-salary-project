"""Reports (scoped, spec §27)."""
from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.accounts.decorators import require_role_check
from apps.reports.services import ReportService


@login_required
@require_role_check("can_view_reports")
def report_index(request):
    today = date.today()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    summary = ReportService.dashboard_summary(request.user, year=year, month=month)
    context = {"summary": summary, "year": year, "month": month,
               "is_global": request.user.is_global_scope}
    if request.user.is_global_scope:
        context["branches"] = ReportService.branch_breakdown(request.user, year=year, month=month)
    return render(request, "reports/index.html", context)


@login_required
@require_role_check("can_view_reports")
def salary_report(request):
    """Moliyaviy hisobot — per-branch payroll totals. Only head office
    admin/super admin can reach this view at all (can_view_reports) —
    branch admin, accountant, and HR are all excluded, not just scoped."""
    today = date.today()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    data = ReportService.salary_totals(request.user, year=year, month=month)
    context = {**data, "year": year, "month": month, "is_global": request.user.is_global_scope}
    return render(request, "reports/salary_report.html", context)
