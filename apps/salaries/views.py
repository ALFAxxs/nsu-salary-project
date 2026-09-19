"""Salary list + export (scoped, spec §26)."""
from __future__ import annotations

import csv

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.decorators import require_role_check
from apps.accounts.permissions import scope_organization_units
from apps.common.export_safety import safe_cell
from apps.common.params import to_int
from apps.organizations.models import OrganizationUnit
from apps.salaries.selectors import current_salaries_for


def _filter_salaries(request):
    qs = current_salaries_for(request.user)
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(
            Q(employee__full_name__icontains=search)
            | Q(employee__employee_code__icontains=search)
            | Q(employee__normalized_phone__icontains=search)
        )
    unit_id = request.GET.get("unit")
    unit_id_int = to_int(unit_id)
    if unit_id_int is not None:
        # Filter by which branch actually paid the salary, not the
        # employee's current branch — a transferred employee's old salary
        # still belongs to the branch that paid it.
        qs = qs.filter(organization_unit_id=unit_id_int)
    year = request.GET.get("year")
    month = request.GET.get("month")
    year_int = to_int(year)
    month_int = to_int(month)
    if year_int is not None:
        qs = qs.filter(period_year=year_int)
    if month_int is not None:
        qs = qs.filter(period_month=month_int)
    return qs.order_by("-period_year", "-period_month", "employee__full_name"), search, unit_id, year, month


@login_required
@require_role_check("can_import_salary")
def salary_list(request):
    qs, search, unit_id, year, month = _filter_salaries(request)
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))
    units = scope_organization_units(
        OrganizationUnit.objects.filter(is_active=True), request.user
    )
    return render(request, "salaries/list.html", {
        "page": page, "search": search, "units": units,
        "selected_unit": unit_id, "year": year, "month": month,
    })


@login_required
@require_role_check("can_import_salary")
def salary_export_csv(request):
    qs, *_ = _filter_salaries(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="salaries.csv"'
    writer = csv.writer(response)
    writer.writerow(["Employee code", "Full name", "Branch", "Period",
                     "Gross", "Advance", "Deductions",
                     "Income tax", "Pension contribution", "Union dues", "Social tax",
                     "Net"])
    for s in qs.select_related("employee", "organization_unit"):
        writer.writerow([
            safe_cell(s.employee.employee_code), safe_cell(s.employee.full_name),
            safe_cell(s.organization_unit.name), f"{s.period_year}-{s.period_month:02d}",
            s.gross_salary, s.advance, s.deductions,
            s.income_tax, s.pension_contribution, s.union_dues, s.social_tax,
            s.net_salary,
        ])
    return response
