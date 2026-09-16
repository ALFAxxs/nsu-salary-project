from django.contrib import admin

from apps.accounts.permissions import scope_organization_units
from apps.organizations.models import OrganizationUnit


@admin.register(OrganizationUnit)
class OrganizationUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "type", "is_head_office", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("name", "code")

    def get_queryset(self, request):
        return scope_organization_units(super().get_queryset(request), request.user)

    # Mirror apps.organizations.views permission gating (can_manage_branches)
    # here too — otherwise Django's default permission backend would require
    # explicit per-model permissions to be assigned via groups, which this
    # app never sets up (roles are the single source of truth for access).
    def has_add_permission(self, request):
        return request.user.is_active and request.user.can_manage_branches()

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.can_manage_branches()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.can_manage_branches()
