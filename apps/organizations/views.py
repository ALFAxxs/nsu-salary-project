"""Branch management (spec §37 — manage-branches roles only)."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_role_check
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.organizations.forms import OrganizationUnitForm
from apps.organizations.models import OrganizationUnit


@login_required
@require_role_check("can_manage_branches")
def unit_list(request):
    units = OrganizationUnit.objects.all()
    return render(request, "organizations/list.html", {"units": units})


@login_required
@require_role_check("can_manage_branches")
def unit_create(request):
    if request.method == "POST":
        form = OrganizationUnitForm(request.POST)
        if form.is_valid():
            unit = form.save()
            AuditService.log(AuditAction.BRANCH_CREATED, obj=unit,
                             metadata={"code": unit.code, "name": unit.name})
            messages.success(request, "Filial yaratildi.")
            return redirect("organizations:list")
    else:
        form = OrganizationUnitForm()
    return render(request, "organizations/form.html", {"form": form, "is_new": True})


@login_required
@require_role_check("can_manage_branches")
def unit_edit(request, pk):
    unit = get_object_or_404(OrganizationUnit, pk=pk)
    if request.method == "POST":
        form = OrganizationUnitForm(request.POST, instance=unit)
        if form.is_valid():
            unit = form.save()
            AuditService.log(AuditAction.BRANCH_UPDATED, obj=unit,
                             metadata={"code": unit.code, "name": unit.name})
            messages.success(request, "O'zgartirildi.")
            return redirect("organizations:list")
    else:
        form = OrganizationUnitForm(instance=unit)
    return render(request, "organizations/form.html", {"form": form, "is_new": False})


@login_required
@require_role_check("can_manage_branches")
@require_POST
def unit_toggle_active(request, pk):
    unit = get_object_or_404(OrganizationUnit, pk=pk)
    if unit.is_head_office and unit.is_active:
        messages.error(request, "Bosh ofisni nofaollashtirib bo'lmaydi.")
        return redirect("organizations:list")

    unit.is_active = not unit.is_active
    unit.save(update_fields=["is_active", "updated_at"])
    AuditService.log(
        AuditAction.BRANCH_ACTIVATED if unit.is_active else AuditAction.BRANCH_DEACTIVATED,
        obj=unit, metadata={"code": unit.code, "name": unit.name},
    )
    messages.success(
        request,
        f"'{unit.name}' {'faollashtirildi' if unit.is_active else 'nofaollashtirildi'}.",
    )
    return redirect("organizations:list")


@login_required
@require_role_check("can_manage_branches")
@require_POST
def unit_delete(request, pk):
    """
    Permanently delete a branch AND everything scoped to it: its employees
    (with their salary/notification history, via the model's own CASCADEs),
    its import history, and any admin logins assigned to it. There is no
    undo — this is a deliberate full wipe, not a soft delete (use
    unit_toggle_active for that).
    """
    unit = get_object_or_404(OrganizationUnit, pk=pk)
    if unit.is_head_office:
        messages.error(request, "Bosh ofisni o'chirib bo'lmaydi.")
        return redirect("organizations:list")
    if request.user.organization_unit_id == unit.pk:
        messages.error(
            request,
            "O'zingiz biriktirilgan filialni o'chira olmaysiz — bu akkauntingizni "
            "ham o'chirib yuboradi. Boshqa super admin orqali o'chirtiring.",
        )
        return redirect("organizations:list")

    name, code = unit.name, unit.code
    emp_count = unit.employees.count()
    user_count = unit.users.count()
    import_count = unit.imports.count()
    try:
        unit.delete()
    except ProtectedError:
        # Defensive fallback for any relation not already handled by CASCADE.
        messages.error(
            request,
            f"'{name}' filialini o'chirib bo'lmadi — unga bog'langan ba'zi "
            "ma'lumotlar to'sqinlik qilmoqda.",
        )
        return redirect("organizations:list")

    AuditService.log(
        AuditAction.BRANCH_DELETED, object_type="OrganizationUnit", object_id=str(pk),
        metadata={"code": code, "name": name, "employees": emp_count,
                  "admins": user_count, "imports": import_count},
    )
    messages.success(
        request,
        f"'{name}' o'chirildi ({emp_count} ta xodim, {user_count} ta admin login, "
        f"{import_count} ta import bilan birga).",
    )
    return redirect("organizations:list")
