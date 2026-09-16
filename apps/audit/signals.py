"""Auth signals -> audit log (login / logout / failed login)."""
from __future__ import annotations

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from apps.audit.models import AuditAction
from apps.audit.services import AuditService


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    AuditService.log(AuditAction.LOGIN, user=user)


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    AuditService.log(AuditAction.LOGOUT, user=user)


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    AuditService.log(
        AuditAction.LOGIN_FAILED,
        metadata={"username": credentials.get("username", "")},
    )
