"""Dashboard view — role-aware summary (spec §23, §24)."""
from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.reports.services import ReportService


@login_required
def index(request):
    today = date.today()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month

    summary = ReportService.dashboard_summary(request.user, year=year, month=month)
    context = {
        "summary": summary,
        "year": year,
        "month": month,
        "is_global": request.user.is_global_scope,
    }
    if request.user.is_global_scope:
        context["branches"] = ReportService.branch_breakdown(
            request.user, year=year, month=month
        )
    return render(request, "dashboard/index.html", context)


def number_to_words(request):
    """Public utility page — no login required. Converts a number into
    words (Uzbek, Russian, English) entirely client-side; the view only
    serves the static page, there is nothing to compute server-side."""
    return render(request, "tools/number_to_words.html")
