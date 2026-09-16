"""
Scoped DRF viewsets (spec §50).

Each viewset uses the same selector-based scoping as the web views, so a branch
user cannot retrieve another branch's records through the API — including by
requesting a specific ID (get_object() runs against the scoped queryset).
"""
from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import CanImportSalary, scope_organization_units
from apps.api.serializers import (
    EmployeeSerializer,
    OrganizationUnitSerializer,
    SalaryImportSerializer,
    SalarySerializer,
    TelegramMessageSerializer,
)
from apps.employees.selectors import employees_for
from apps.imports.selectors import imports_for
from apps.notifications.models import TelegramMessage
from apps.accounts.permissions import scope_messages
from apps.organizations.models import OrganizationUnit
from apps.salaries.selectors import salaries_for


class OrganizationUnitViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OrganizationUnitSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return scope_organization_units(
            OrganizationUnit.objects.filter(is_active=True), self.request.user
        )


class EmployeeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["full_name", "employee_code", "normalized_phone"]
    filterset_fields = ["organization_unit", "is_active"]

    def get_queryset(self):
        return employees_for(self.request.user)


class SalaryViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SalarySerializer
    # Mirrors salary_list/salary_export_csv (apps.salaries.views) — payroll
    # amounts are import/accounting data, not general branch-read data.
    permission_classes = [IsAuthenticated, CanImportSalary]
    search_fields = ["employee__full_name", "employee__employee_code"]
    filterset_fields = ["period_year", "period_month", "is_current"]

    def get_queryset(self):
        return salaries_for(self.request.user)


class SalaryImportViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SalaryImportSerializer
    # Mirror the web view's gating (apps.imports.views uses can_import_salary)
    # so HR/other non-import roles can't read import data via the API either.
    permission_classes = [IsAuthenticated, CanImportSalary]
    filterset_fields = ["organization_unit", "status", "period_year", "period_month"]

    def get_queryset(self):
        return imports_for(self.request.user)


class TelegramMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TelegramMessageSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status"]

    def get_queryset(self):
        return scope_messages(
            TelegramMessage.objects.select_related("employee", "salary"),
            self.request.user,
        )
