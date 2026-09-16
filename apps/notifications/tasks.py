"""
Celery tasks for sending salary notifications (spec §17, §29).

Design:
  * dispatch_import_notifications(import_id) fans out one send_salary_message
    task per pending message, throttled to respect Telegram rate limits.
  * send_salary_message(message_id) sends a single message via the Bot API using
    a synchronous HTTP call (aiohttp/aiogram not needed on the worker — a plain
    requests POST to the Bot API is simplest and safe for one message).
  * Transient failures (429, 5xx, network) -> retry up to TELEGRAM_MAX_ATTEMPTS.
    Permanent failures (403 blocked, 400 chat not found) -> FAILED/BLOCKED, no retry.

Salary amounts are NEVER written to logs (spec §46, §59).
"""
from __future__ import annotations

import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.notifications.models import BroadcastRecipient, MessageStatus, TelegramMessage
from apps.notifications.services import format_salary_message

logger = logging.getLogger("apps.notifications")

BOT_API = "https://api.telegram.org/bot{token}/sendMessage"
# Permanent (non-retryable) Telegram API descriptions we recognise.
_BLOCKED_MARKERS = ("bot was blocked", "user is deactivated", "chat not found")
# A message stuck in SENDING this long means the worker that claimed it died
# mid-send (crash, OOM-kill, ...) without ever reaching a terminal status —
# finalize_import recovers it instead of leaving it stuck forever.
STUCK_SENDING_TIMEOUT = timedelta(minutes=5)
# finalize_import re-checks every 30s while anything is still in flight;
# cap how long it will keep doing that for one import so a permanently
# wedged message can't make it poll forever.
MAX_FINALIZE_CHECKS = 240  # 240 * 30s = 2 hours


@shared_task(ignore_result=True)
def dispatch_import_notifications(import_id: int) -> None:
    from apps.imports.models import ImportStatus, SalaryImport
    from apps.notifications.services import SalaryNotificationService

    try:
        salary_import = SalaryImport.objects.get(pk=import_id)
    except SalaryImport.DoesNotExist:
        logger.error("dispatch: import %s not found", import_id)
        return

    salary_import.status = ImportStatus.PROCESSING
    salary_import.save(update_fields=["status"])

    message_ids = SalaryNotificationService.pending_message_ids(salary_import)
    rate = max(1, int(getattr(settings, "TELEGRAM_SEND_RATE_LIMIT", 25)))
    logger.info("dispatch: import %s -> %s messages", import_id, len(message_ids))

    for i, mid in enumerate(message_ids):
        # Spread sends across time to stay under the per-second budget.
        countdown = i // rate
        send_salary_message.apply_async(args=[mid], countdown=countdown)

    # Mark completed once all are queued; per-message status tells the real story.
    finalize_import.apply_async(
        args=[import_id],
        countdown=(len(message_ids) // rate) + 5,
    )


@shared_task(
    bind=True,
    max_retries=None,  # we manage retry count via the model + settings
    ignore_result=True,
)
def send_salary_message(self, message_id: int) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    max_attempts = int(getattr(settings, "TELEGRAM_MAX_ATTEMPTS", 3))

    # Atomic claim: only a row still PENDING/RETRYING can be claimed, and the
    # claim moves it to SENDING — a status NOT in that set. If this message
    # was dispatched twice (double click, or a resend racing an in-flight
    # retry), the second invocation's UPDATE matches zero rows and returns
    # here without ever calling the Bot API a second time.
    claimed = TelegramMessage.objects.filter(
        pk=message_id, status__in=[MessageStatus.PENDING, MessageStatus.RETRYING],
    ).update(status=MessageStatus.SENDING, updated_at=timezone.now())
    if not claimed:
        return

    try:
        message = (
            TelegramMessage.objects.select_related("salary__employee", "salary__organization_unit")
            .get(pk=message_id)
        )
    except TelegramMessage.DoesNotExist:
        return

    if not message.telegram_id:
        message.status = MessageStatus.TELEGRAM_NOT_CONNECTED
        message.save(update_fields=["status", "updated_at"])
        return
    if not token:
        message.status = MessageStatus.FAILED
        message.error_message = "TELEGRAM_BOT_TOKEN not configured"
        message.save(update_fields=["status", "error_message", "updated_at"])
        return

    message.attempts += 1
    text = format_salary_message(message.salary)

    try:
        resp = requests.post(
            BOT_API.format(token=token),
            json={"chat_id": message.telegram_id, "text": text},
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("send: network error emp=%s: %s", message.employee_id, exc)
        _handle_transient(self, message, str(exc), max_attempts)
        return

    if resp.status_code == 200:
        data = resp.json()
        message.status = MessageStatus.SENT
        message.sent_at = timezone.now()
        message.message_id = data.get("result", {}).get("message_id")
        message.error_message = ""
        message.save(update_fields=[
            "status", "sent_at", "message_id", "attempts", "error_message", "updated_at",
        ])
        logger.info("send: OK emp=%s", message.employee_id)
        return

    # Non-200: decide transient vs permanent.
    try:
        description = resp.json().get("description", "")
    except ValueError:
        description = resp.text[:200]

    lowered = description.lower()
    if resp.status_code == 403 or any(m in lowered for m in _BLOCKED_MARKERS):
        message.status = MessageStatus.BLOCKED
        message.error_message = description
        message.save(update_fields=["status", "attempts", "error_message", "updated_at"])
        logger.info("send: BLOCKED emp=%s", message.employee_id)
        return

    if resp.status_code == 429:
        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 3))
        _handle_transient(self, message, "429 rate limited", max_attempts,
                          countdown=retry_after)
        return

    if 500 <= resp.status_code < 600:
        _handle_transient(self, message, f"{resp.status_code} server error", max_attempts)
        return

    # Other 4xx -> permanent.
    message.status = MessageStatus.FAILED
    message.error_message = f"{resp.status_code}: {description}"
    message.save(update_fields=["status", "attempts", "error_message", "updated_at"])
    logger.info("send: FAILED emp=%s status=%s", message.employee_id, resp.status_code)


def _handle_transient(task, message, error, max_attempts, countdown=None):
    if message.attempts >= max_attempts:
        message.status = MessageStatus.FAILED
        message.error_message = f"Max attempts reached: {error}"
        message.save(update_fields=["status", "attempts", "error_message", "updated_at"])
        return
    message.status = MessageStatus.RETRYING
    message.error_message = error
    message.save(update_fields=["status", "attempts", "error_message", "updated_at"])
    delay = countdown if countdown is not None else min(2 ** message.attempts, 60)
    task.retry(countdown=delay, exc=None)


@shared_task(ignore_result=True)
def finalize_import(import_id: int, checks: int = 0) -> None:
    from apps.imports.models import ImportStatus, SalaryImport

    try:
        salary_import = SalaryImport.objects.get(pk=import_id)
    except SalaryImport.DoesNotExist:
        return

    # Recover messages whose worker died mid-send (claimed via SENDING, then
    # never reached a terminal status) — otherwise they'd stay stuck forever
    # since SENDING is deliberately not re-claimable by a fresh dispatch.
    stale_cutoff = timezone.now() - STUCK_SENDING_TIMEOUT
    stale_ids = list(
        TelegramMessage.objects.filter(
            salary__source_import=salary_import,
            status=MessageStatus.SENDING,
            updated_at__lt=stale_cutoff,
        ).values_list("id", flat=True)
    )
    if stale_ids:
        TelegramMessage.objects.filter(id__in=stale_ids).update(status=MessageStatus.RETRYING)
        for mid in stale_ids:
            send_salary_message.apply_async(args=[mid], countdown=5)
        logger.warning("finalize: recovered %s stuck SENDING message(s) for import %s",
                       len(stale_ids), import_id)

    still_pending = TelegramMessage.objects.filter(
        salary__source_import=salary_import,
        status__in=[MessageStatus.PENDING, MessageStatus.RETRYING, MessageStatus.SENDING],
    ).exists()
    if not still_pending:
        salary_import.status = ImportStatus.COMPLETED
        salary_import.save(update_fields=["status"])
    elif checks >= MAX_FINALIZE_CHECKS:
        # Stop polling after ~2 hours — a message still stuck this long needs
        # a human, not another poll. Per-message status still tells the real
        # story on the import's own detail page.
        logger.warning("finalize: giving up polling import %s after %s checks",
                       import_id, checks)
        salary_import.status = ImportStatus.COMPLETED
        salary_import.save(update_fields=["status"])
    else:
        # Check again shortly; retries may still be in flight.
        finalize_import.apply_async(args=[import_id, checks + 1], countdown=30)


# --------------------------------------------------------------------- #
# Broadcasts (superadmin-only free-text announcements) — same dispatch/
# claim/retry/finalize shape as the salary-notification tasks above, just
# against BroadcastRecipient instead of TelegramMessage.
# --------------------------------------------------------------------- #
@shared_task(ignore_result=True)
def dispatch_broadcast(broadcast_id: int) -> None:
    recipient_ids = list(
        BroadcastRecipient.objects.filter(
            broadcast_id=broadcast_id,
            status__in=[MessageStatus.PENDING, MessageStatus.RETRYING],
        ).values_list("id", flat=True)
    )
    rate = max(1, int(getattr(settings, "TELEGRAM_SEND_RATE_LIMIT", 25)))
    logger.info("dispatch_broadcast: broadcast %s -> %s recipients", broadcast_id, len(recipient_ids))

    for i, rid in enumerate(recipient_ids):
        countdown = i // rate
        send_broadcast_message.apply_async(args=[rid], countdown=countdown)

    finalize_broadcast.apply_async(
        args=[broadcast_id],
        countdown=(len(recipient_ids) // rate) + 5,
    )


@shared_task(
    bind=True,
    max_retries=None,
    ignore_result=True,
)
def send_broadcast_message(self, recipient_id: int) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    max_attempts = int(getattr(settings, "TELEGRAM_MAX_ATTEMPTS", 3))

    # Same atomic claim as send_salary_message — a double dispatch (double
    # click on Send, or a resend racing an in-flight retry) must not reach
    # the Bot API twice for the same recipient.
    claimed = BroadcastRecipient.objects.filter(
        pk=recipient_id, status__in=[MessageStatus.PENDING, MessageStatus.RETRYING],
    ).update(status=MessageStatus.SENDING, updated_at=timezone.now())
    if not claimed:
        return

    try:
        recipient = (
            BroadcastRecipient.objects.select_related("broadcast", "employee")
            .get(pk=recipient_id)
        )
    except BroadcastRecipient.DoesNotExist:
        return

    if not token:
        recipient.status = MessageStatus.FAILED
        recipient.error_message = "TELEGRAM_BOT_TOKEN not configured"
        recipient.save(update_fields=["status", "error_message", "updated_at"])
        return

    recipient.attempts += 1
    text = recipient.broadcast.text

    try:
        resp = requests.post(
            BOT_API.format(token=token),
            json={"chat_id": recipient.telegram_id, "text": text},
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("send_broadcast: network error emp=%s: %s", recipient.employee_id, exc)
        _handle_transient(self, recipient, str(exc), max_attempts)
        return

    if resp.status_code == 200:
        data = resp.json()
        recipient.status = MessageStatus.SENT
        recipient.sent_at = timezone.now()
        recipient.message_id = data.get("result", {}).get("message_id")
        recipient.error_message = ""
        recipient.save(update_fields=[
            "status", "sent_at", "message_id", "attempts", "error_message", "updated_at",
        ])
        logger.info("send_broadcast: OK emp=%s", recipient.employee_id)
        return

    try:
        description = resp.json().get("description", "")
    except ValueError:
        description = resp.text[:200]

    lowered = description.lower()
    if resp.status_code == 403 or any(m in lowered for m in _BLOCKED_MARKERS):
        recipient.status = MessageStatus.BLOCKED
        recipient.error_message = description
        recipient.save(update_fields=["status", "attempts", "error_message", "updated_at"])
        logger.info("send_broadcast: BLOCKED emp=%s", recipient.employee_id)
        return

    if resp.status_code == 429:
        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 3))
        _handle_transient(self, recipient, "429 rate limited", max_attempts,
                          countdown=retry_after)
        return

    if 500 <= resp.status_code < 600:
        _handle_transient(self, recipient, f"{resp.status_code} server error", max_attempts)
        return

    recipient.status = MessageStatus.FAILED
    recipient.error_message = f"{resp.status_code}: {description}"
    recipient.save(update_fields=["status", "attempts", "error_message", "updated_at"])
    logger.info("send_broadcast: FAILED emp=%s status=%s", recipient.employee_id, resp.status_code)


@shared_task(ignore_result=True)
def finalize_broadcast(broadcast_id: int, checks: int = 0) -> None:
    stale_cutoff = timezone.now() - STUCK_SENDING_TIMEOUT
    stale_ids = list(
        BroadcastRecipient.objects.filter(
            broadcast_id=broadcast_id,
            status=MessageStatus.SENDING,
            updated_at__lt=stale_cutoff,
        ).values_list("id", flat=True)
    )
    if stale_ids:
        BroadcastRecipient.objects.filter(id__in=stale_ids).update(status=MessageStatus.RETRYING)
        for rid in stale_ids:
            send_broadcast_message.apply_async(args=[rid], countdown=5)
        logger.warning("finalize_broadcast: recovered %s stuck SENDING recipient(s) for broadcast %s",
                       len(stale_ids), broadcast_id)

    still_pending = BroadcastRecipient.objects.filter(
        broadcast_id=broadcast_id,
        status__in=[MessageStatus.PENDING, MessageStatus.RETRYING, MessageStatus.SENDING],
    ).exists()
    if still_pending and checks < MAX_FINALIZE_CHECKS:
        finalize_broadcast.apply_async(args=[broadcast_id, checks + 1], countdown=30)
