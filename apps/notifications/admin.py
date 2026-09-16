from django.contrib import admin

from apps.accounts.permissions import scope_messages
from apps.notifications.models import TelegramMessage


@admin.register(TelegramMessage)
class TelegramMessageAdmin(admin.ModelAdmin):
    list_display = ("employee", "salary", "status", "attempts", "sent_at")
    list_filter = ("status",)

    def get_queryset(self, request):
        return scope_messages(super().get_queryset(request), request.user)
