"""
Notification views (spec §17, §30, §41).

Flow after import confirm:
  import_detail -> shows connected/unconnected/prepared counts + Send button
  send         -> dispatches Celery tasks (idempotent; connected only)
  resend       -> reset one message and re-dispatch it
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_role_check
from apps.accounts.permissions import scope_messages
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.imports.selectors import get_import_for
from apps.notifications.models import MessageStatus, TelegramMessage
from apps.notifications.services import SalaryNotificationService
from apps.notifications.tasks import dispatch_import_notifications, send_salary_message


@login_required
@require_role_check("can_send_notifications")
def notification_list(request):
    qs = scope_messages(
        TelegramMessage.objects.select_related(
            "employee", "salary", "salary__organization_unit"
        ),
        request.user,
    ).order_by("-created_at")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "notifications/list.html", {
        "page": page, "status": status, "statuses": MessageStatus.choices,
    })


@login_required
@require_role_check("can_send_notifications")
def import_detail(request, pk: int):
    salary_import = get_import_for(request.user, pk)
    if salary_import is None:
        raise Http404
    msgs = scope_messages(
        TelegramMessage.objects.select_related("employee"),
        request.user,
    ).filter(salary__source_import=salary_import)

    counts = {
        "prepared": msgs.count(),
        "pending": msgs.filter(status=MessageStatus.PENDING).count(),
        "sent": msgs.filter(status=MessageStatus.SENT).count(),
        "failed": msgs.filter(
            status__in=[MessageStatus.FAILED, MessageStatus.BLOCKED]).count(),
        "not_connected": msgs.filter(
            status=MessageStatus.TELEGRAM_NOT_CONNECTED).count(),
        "mismatched": msgs.filter(status=MessageStatus.JSHSHIR_MISMATCH).count(),
    }
    pending = msgs.filter(
        status=MessageStatus.PENDING
    ).select_related("employee")[:200]
    sent = msgs.filter(
        status=MessageStatus.SENT
    ).select_related("employee").order_by("-sent_at")[:200]
    failed = msgs.filter(
        status__in=[MessageStatus.FAILED, MessageStatus.BLOCKED]
    ).select_related("employee")[:200]
    unconnected = msgs.filter(
        status=MessageStatus.TELEGRAM_NOT_CONNECTED
    ).select_related("employee")[:200]
    mismatched = msgs.filter(
        status=MessageStatus.JSHSHIR_MISMATCH
    ).select_related("employee")[:200]
    return render(request, "notifications/import_detail.html", {
        "imp": salary_import, "counts": counts,
        "pending": pending, "sent": sent, "failed": failed,
        "unconnected": unconnected, "mismatched": mismatched,
    })


@login_required
@require_role_check("can_send_notifications")
@require_POST
def send_notifications(request, pk: int):
    salary_import = get_import_for(request.user, pk)
    if salary_import is None:
        raise Http404
    # Ensure rows exist (idempotent) then dispatch.
    SalaryNotificationService.prepare_for_import(salary_import)
    dispatch_import_notifications.delay(salary_import.pk)
    AuditService.log(AuditAction.NOTIFICATION_SENT, obj=salary_import,
                     metadata={"unit": salary_import.organization_unit.code})
    messages.success(request, "Xabarlar yuborish navbatga qo'yildi.")
    return redirect("notifications:import_detail", pk=pk)


@login_required
@require_role_check("can_send_notifications")
@require_POST
def resend_message(request, pk: int):
    message = scope_messages(
        TelegramMessage.objects.select_related("employee"), request.user
    ).filter(pk=pk).first()
    if message is None:
        raise Http404
    if message.status == MessageStatus.SENDING:
        messages.warning(request, "Xabar hozir yuborilmoqda, biroz kuting.")
        return redirect("notifications:list")
    SalaryNotificationService.reset_for_resend(message)
    if message.status == MessageStatus.PENDING:
        send_salary_message.delay(message.pk)
        AuditService.log(AuditAction.NOTIFICATION_RESENT, obj=message)
        messages.success(request, "Qayta yuborildi.")
    else:
        messages.warning(request, "Xodim Telegramga ulanmagan.")
    return redirect("notifications:list")
