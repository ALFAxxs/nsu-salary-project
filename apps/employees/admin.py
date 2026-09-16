from django.contrib import admin

from apps.accounts.permissions import scope_employees
from apps.employees.models import Employee, TelegramContact


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "employee_code", "normalized_phone",
                    "organization_unit", "is_telegram_linked")
    list_filter = ("organization_unit", "is_active")
    search_fields = ("full_name", "employee_code", "normalized_phone")

    def get_queryset(self, request):
        # Defense in depth: Django admin must respect the same branch
        # isolation as the web/API surfaces, even for is_staff users.
        return scope_employees(super().get_queryset(request), request.user)


@admin.register(TelegramContact)
class TelegramContactAdmin(admin.ModelAdmin):
    """
    Not tied to any organization unit (a contact may not match any employee
    yet), so this is global — visible to superusers only, same as User/AuditLog.
    """
    list_display = ("normalized_phone", "telegram_id", "telegram_username", "first_seen_at")
    search_fields = ("normalized_phone", "telegram_username")

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser
