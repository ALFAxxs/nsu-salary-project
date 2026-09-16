from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.accounts.models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "role", "organization_unit", "is_active")
    list_filter = ("role", "is_active", "organization_unit")
    fieldsets = UserAdmin.fieldsets + (
        ("Role", {"fields": ("role", "organization_unit", "phone")}),
    )

    # Account management (including the is_staff/is_superuser toggles) is
    # sensitive enough that only real superusers may reach it here — regular
    # admin roles manage users through apps.accounts (which never exposes
    # is_staff/is_superuser). Without this, any is_staff account could grant
    # itself global access and step around every branch-scoping rule.
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser
