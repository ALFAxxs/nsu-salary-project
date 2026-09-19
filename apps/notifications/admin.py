from django.contrib import admin

from apps.accounts.permissions import scope_messages
from apps.notifications.models import BroadcastMessage, BroadcastRecipient, TelegramMessage


@admin.register(TelegramMessage)
class TelegramMessageAdmin(admin.ModelAdmin):
    list_display = ("employee", "salary", "status", "attempts", "sent_at")
    list_filter = ("status",)

    def get_queryset(self, request):
        return scope_messages(super().get_queryset(request), request.user)


class _BroadcastPermissionMixin:
    """Superadmin-only, matching the web broadcast views (can_manage_admins)
    — a broadcast reaches every branch at once, so unlike everything else in
    this admin site it is never a per-branch admin action, not even here."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.can_manage_admins()

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.can_manage_admins()

    def has_add_permission(self, request):
        return request.user.is_active and request.user.can_manage_admins()

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.can_manage_admins()

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.can_manage_admins()


@admin.register(BroadcastMessage)
class BroadcastMessageAdmin(_BroadcastPermissionMixin, admin.ModelAdmin):
    list_display = ("id", "organization_unit", "created_by", "created_at")
    list_filter = ("organization_unit",)
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(BroadcastRecipient)
class BroadcastRecipientAdmin(_BroadcastPermissionMixin, admin.ModelAdmin):
    """Delivery record per (broadcast, employee) — created by the send
    pipeline, not by hand, so add is disabled like TelegramContact."""
    list_display = ("broadcast", "employee", "status", "attempts", "sent_at")
    list_filter = ("status",)

    def has_add_permission(self, request):
        return False
