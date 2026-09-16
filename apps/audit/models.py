"""
Audit log (spec §32).

Every sensitive action (login, imports, salary changes, notifications, admin
changes) is recorded with actor, target, IP and metadata. Salary *amounts* are
not stored in metadata — only identifiers — to keep confidential data out of
the audit trail (spec §46, §59).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditAction(models.TextChoices):
    LOGIN = "LOGIN", _("Login")
    LOGOUT = "LOGOUT", _("Logout")
    LOGIN_FAILED = "LOGIN_FAILED", _("Login failed")
    EMPLOYEE_CREATED = "EMPLOYEE_CREATED", _("Employee created")
    EMPLOYEE_UPDATED = "EMPLOYEE_UPDATED", _("Employee updated")
    SALARY_IMPORTED = "SALARY_IMPORTED", _("Salary imported")
    SALARY_MODIFIED = "SALARY_MODIFIED", _("Salary modified")
    SALARY_DELETED = "SALARY_DELETED", _("Salary deleted")
    EXCEL_UPLOADED = "EXCEL_UPLOADED", _("Excel uploaded")
    IMPORT_CONFIRMED = "IMPORT_CONFIRMED", _("Import confirmed")
    NOTIFICATION_SENT = "NOTIFICATION_SENT", _("Notification sent")
    NOTIFICATION_RESENT = "NOTIFICATION_RESENT", _("Notification resent")
    ADMIN_CREATED = "ADMIN_CREATED", _("Admin created")
    PERMISSION_CHANGED = "PERMISSION_CHANGED", _("Permission changed")
    TELEGRAM_LINKED = "TELEGRAM_LINKED", _("Telegram linked")
    TELEGRAM_UNLINKED = "TELEGRAM_UNLINKED", _("Telegram unlinked")
    BRANCH_CREATED = "BRANCH_CREATED", _("Branch created")
    BRANCH_UPDATED = "BRANCH_UPDATED", _("Branch updated")
    BRANCH_ACTIVATED = "BRANCH_ACTIVATED", _("Branch activated")
    BRANCH_DEACTIVATED = "BRANCH_DEACTIVATED", _("Branch deactivated")
    BRANCH_DELETED = "BRANCH_DELETED", _("Branch deleted")
    EMPLOYEE_ACTIVATED = "EMPLOYEE_ACTIVATED", _("Employee activated")
    EMPLOYEE_DEACTIVATED = "EMPLOYEE_DEACTIVATED", _("Employee deactivated")
    EMPLOYEE_DELETED = "EMPLOYEE_DELETED", _("Employee deleted")


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("audit log")
        verbose_name_plural = _("audit logs")
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["user"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} by {self.user_id}"
