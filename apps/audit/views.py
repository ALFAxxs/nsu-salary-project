"""Audit log viewer (super admin only)."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from apps.accounts.decorators import require_role_check
from apps.audit.models import AuditAction, AuditLog


@login_required
@require_role_check("can_manage_admins")
def audit_list(request):
    qs = AuditLog.objects.select_related("user").all()
    action = request.GET.get("action")
    if action:
        qs = qs.filter(action=action)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "audit/list.html", {
        "page": page, "actions": AuditAction.choices, "action": action,
    })
