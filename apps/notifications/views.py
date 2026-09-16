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
from apps.notifications.models import BroadcastMessage, MessageStatus, TelegramMessage
from apps.notifications.services import BroadcastService, SalaryNotificationService
from apps.notifications.tasks import dispatch_import_notifications, send_salary_message
from apps.organizations.models import OrganizationUnit


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


# --------------------------------------------------------------------- #
# Broadcasts — free-text announcement to every telegram-linked employee,
# or to one branch's. Superadmin-only: this reaches everyone at once,
# unlike everything else here which is scoped to the admin's own branch(es).
# --------------------------------------------------------------------- #
@login_required
@require_role_check("can_manage_admins")
def broadcast_list(request):
    broadcasts = BroadcastMessage.objects.select_related(
        "organization_unit", "created_by"
    ).prefetch_related("recipients")
    rows = []
    for b in broadcasts:
        recipients = list(b.recipients.all())
        rows.append({
            "broadcast": b,
            "total": len(recipients),
            "sent": sum(1 for r in recipients if r.status == MessageStatus.SENT),
            "failed": sum(1 for r in recipients
                         if r.status in (MessageStatus.FAILED, MessageStatus.BLOCKED)),
        })
    paginator = Paginator(rows, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "notifications/broadcast_list.html", {"page": page})


@login_required
@require_role_check("can_manage_admins")
def broadcast_create(request):
    units = OrganizationUnit.objects.filter(is_active=True).order_by("name")
    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        unit_id = request.POST.get("organization_unit") or ""
        unit = units.filter(pk=unit_id).first() if unit_id else None

        if not text:
            messages.error(request, "Xabar matni bo'sh bo'lishi mumkin emas.")
            return render(request, "notifications/broadcast_form.html", {
                "units": units, "text": text, "selected_unit": unit_id,
            })

        recipient_count = BroadcastService.eligible_employees(unit).count()
        if recipient_count == 0:
            messages.warning(request, "Bu doirada botga ulangan xodim topilmadi — hech kimga yuborilmadi.")
            return render(request, "notifications/broadcast_form.html", {
                "units": units, "text": text, "selected_unit": unit_id,
            })

        broadcast = BroadcastService.create_and_dispatch(
            text=text, organization_unit=unit, created_by=request.user,
        )
        AuditService.log(AuditAction.BROADCAST_SENT, obj=broadcast, metadata={
            "unit": unit.code if unit else "ALL", "recipients": recipient_count,
        })
        messages.success(request, f"Xabar {recipient_count} ta xodimga yuborish navbatga qo'yildi.")
        return redirect("notifications:broadcast_detail", pk=broadcast.pk)

    return render(request, "notifications/broadcast_form.html", {
        "units": units, "text": "", "selected_unit": "",
    })


@login_required
@require_role_check("can_manage_admins")
def broadcast_detail(request, pk: int):
    broadcast = BroadcastMessage.objects.select_related(
        "organization_unit", "created_by"
    ).filter(pk=pk).first()
    if broadcast is None:
        raise Http404
    recipients = broadcast.recipients.select_related("employee")
    counts = {
        "total": recipients.count(),
        "pending": recipients.filter(
            status__in=[MessageStatus.PENDING, MessageStatus.RETRYING, MessageStatus.SENDING]
        ).count(),
        "sent": recipients.filter(status=MessageStatus.SENT).count(),
        "failed": recipients.filter(
            status__in=[MessageStatus.FAILED, MessageStatus.BLOCKED]).count(),
    }
    failed = recipients.filter(
        status__in=[MessageStatus.FAILED, MessageStatus.BLOCKED]
    )[:200]
    return render(request, "notifications/broadcast_detail.html", {
        "broadcast": broadcast, "counts": counts, "failed": failed,
    })
