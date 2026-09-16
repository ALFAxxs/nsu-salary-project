"""
TelegramMessage — one delivery record per (salary, employee) (spec §19, §30).

Idempotency: a unique constraint on (salary, employee) means a given salary can
have exactly one message row. "Send" creates rows if absent and is safe to press
twice. "Resend" explicitly resets an existing row for redelivery.
"""
from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class MessageStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    # Transient: a worker has claimed this row and is actively calling the
    # Bot API right now. Deliberately NOT in the claimable set below — this
    # is what stops a second dispatch of the same message from also sending
    # it (see apps.notifications.tasks.send_salary_message).
    SENDING = "SENDING", _("Sending")
    SENT = "SENT", _("Sent")
    FAILED = "FAILED", _("Failed")
    BLOCKED = "BLOCKED", _("Blocked by user")
    RETRYING = "RETRYING", _("Retrying")
    TELEGRAM_NOT_CONNECTED = "TELEGRAM_NOT_CONNECTED", _("Telegram not connected")
    # Phone is telegram-linked, but this payroll row's JSHSHIR doesn't match
    # the employee's own on-file JSHSHIR (or the employee has none on file
    # at all) — held back for HR review rather than sent on phone match alone.
    JSHSHIR_MISMATCH = "JSHSHIR_MISMATCH", _("JSHSHIR mismatch")


class TelegramMessage(models.Model):
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.CASCADE,
        related_name="telegram_messages",
    )
    salary = models.ForeignKey(
        "salaries.Salary",
        on_delete=models.CASCADE,
        related_name="telegram_messages",
    )
    telegram_id = models.BigIntegerField(_("telegram id"), null=True, blank=True)
    message_id = models.BigIntegerField(_("telegram message id"), null=True, blank=True)

    status = models.CharField(
        _("status"), max_length=32, choices=MessageStatus.choices,
        default=MessageStatus.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    error_message = models.TextField(_("error message"), blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("telegram message")
        verbose_name_plural = _("telegram messages")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["telegram_id"]),
            models.Index(fields=["employee"]),
            models.Index(fields=["salary"]),
        ]
        constraints = [
            # One message per salary+employee -> idempotent sends (spec §30).
            models.UniqueConstraint(
                fields=["salary", "employee"],
                name="unique_message_per_salary_employee",
            ),
        ]

    def __str__(self) -> str:
        return f"Msg emp={self.employee_id} salary={self.salary_id} {self.status}"


class BroadcastMessage(models.Model):
    """
    A free-text announcement to every telegram-linked employee, or to one
    branch's — separate from salary notifications entirely. Superadmin-only
    (see apps.accounts.models.User.can_manage_admins): this reaches
    everyone at once, so it isn't a per-branch admin action.
    """
    text = models.TextField(_("text"))
    # null = every branch; set = only that branch's currently-linked employees.
    organization_unit = models.ForeignKey(
        "organizations.OrganizationUnit",
        on_delete=models.CASCADE,
        related_name="broadcasts",
        null=True,
        blank=True,
        verbose_name=_("organization unit"),
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="broadcasts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("broadcast message")
        verbose_name_plural = _("broadcast messages")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        scope = self.organization_unit.name if self.organization_unit_id else "barcha filiallar"
        return f"Broadcast #{self.pk} ({scope}, {self.created_at:%Y-%m-%d %H:%M})"


class BroadcastRecipient(models.Model):
    """One delivery record per (broadcast, employee) — same shape and same
    atomic-claim send pattern as TelegramMessage, so a double dispatch of
    the send button can't double-send to hundreds of people either."""
    broadcast = models.ForeignKey(
        BroadcastMessage, on_delete=models.CASCADE, related_name="recipients"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="broadcast_recipients"
    )
    telegram_id = models.BigIntegerField(_("telegram id"))
    message_id = models.BigIntegerField(_("telegram message id"), null=True, blank=True)

    status = models.CharField(
        _("status"), max_length=32, choices=MessageStatus.choices,
        default=MessageStatus.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    error_message = models.TextField(_("error message"), blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("broadcast recipient")
        verbose_name_plural = _("broadcast recipients")
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["broadcast"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["broadcast", "employee"],
                name="unique_broadcast_recipient",
            ),
        ]

    def __str__(self) -> str:
        return f"BroadcastRecipient broadcast={self.broadcast_id} emp={self.employee_id} {self.status}"
