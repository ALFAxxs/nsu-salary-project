"""DRF serializers for the read-mostly API (spec §50)."""
from __future__ import annotations

from rest_framework import serializers

from apps.employees.models import Employee
from apps.imports.models import SalaryImport
from apps.notifications.models import TelegramMessage
from apps.organizations.models import OrganizationUnit
from apps.salaries.models import Salary


class OrganizationUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationUnit
        fields = ["id", "name", "code", "type", "is_head_office", "is_active"]


class EmployeeSerializer(serializers.ModelSerializer):
    organization_unit_name = serializers.CharField(
        source="organization_unit.name", read_only=True)
    is_telegram_linked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "employee_code", "full_name", "normalized_phone",
                  "organization_unit", "organization_unit_name",
                  "is_telegram_linked", "telegram_username", "is_active"]


class SalarySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    period_label = serializers.CharField(read_only=True)

    class Meta:
        model = Salary
        fields = ["id", "employee", "employee_name", "period_year", "period_month",
                  "period_label", "gross_salary", "advance", "deductions",
                  "income_tax", "pension_contribution", "union_dues", "social_tax",
                  "net_salary", "currency", "is_current", "revision"]


class SalaryImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryImport
        fields = ["id", "organization_unit", "period_year", "period_month",
                  "file_name", "status", "total_rows", "valid_rows", "error_rows",
                  "telegram_linked_count", "telegram_unlinked_count", "uploaded_at"]


class TelegramMessageSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = TelegramMessage
        fields = ["id", "employee", "employee_name", "salary", "status",
                  "attempts", "sent_at", "error_message"]
