from django.contrib import admin

from apps.accounts.permissions import scope_imports
from apps.imports.models import ColumnMapping, SalaryImport


@admin.register(SalaryImport)
class SalaryImportAdmin(admin.ModelAdmin):
    list_display = ("id", "organization_unit", "period_year", "period_month",
                    "status", "valid_rows", "error_rows")
    list_filter = ("status", "organization_unit")

    def get_queryset(self, request):
        return scope_imports(super().get_queryset(request), request.user)


@admin.register(ColumnMapping)
class ColumnMappingAdmin(admin.ModelAdmin):
    list_display = ("organization_unit", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        unit_ids = request.user.accessible_unit_ids()
        if unit_ids is None:
            return qs
        return qs.filter(organization_unit_id__in=unit_ids)

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.is_global_scope
