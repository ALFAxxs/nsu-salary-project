"""Employee list + detail + CRUD (scoped, spec §25, §28, §41)."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_role_check
from apps.accounts.permissions import scope_organization_units
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.common.params import to_int
from apps.employees.forms import EmployeeForm
from apps.employees.models import Employee, TelegramContact
from apps.employees.selectors import (
    employees_for,
    get_employee_for,
    unconnected_employees_for,
)
from apps.employees.services import EmployeeRegistrationService
from apps.organizations.models import OrganizationUnit
from apps.salaries.selectors import salaries_for


def _allowed_units(user):
    return scope_organization_units(
        OrganizationUnit.objects.filter(is_active=True), user
    )


@login_required
def employee_list(request):
    qs = employees_for(request.user)

    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(
            Q(full_name__icontains=search)
            | Q(employee_code__icontains=search)
            | Q(normalized_phone__icontains=search)
        )

    unit_id = request.GET.get("unit")
    unit_id_int = to_int(unit_id)
    if unit_id_int is not None:
        qs = qs.filter(organization_unit_id=unit_id_int)

    tg = request.GET.get("tg")
    if tg == "connected":
        qs = qs.filter(telegram_id__isnull=False)
    elif tg == "unconnected":
        qs = qs.filter(telegram_id__isnull=True)

    # Summary cards reflect THIS filtered view (search/unit/tg already
    # applied above) — so filtering to one branch shows that branch's own
    # counts, not the admin's whole scope. One query, computed before
    # pagination slices the queryset down to one page.
    stats = qs.aggregate(
        total=Count("id"),
        connected=Count("id", filter=Q(telegram_id__isnull=False)),
        unconnected=Count("id", filter=Q(telegram_id__isnull=True)),
        inactive=Count("id", filter=Q(is_active=False)),
    )

    paginator = Paginator(qs.order_by("full_name"), 25)
    page = paginator.get_page(request.GET.get("page"))

    # Unit filter options are themselves scoped.
    units = scope_organization_units(
        OrganizationUnit.objects.filter(is_active=True), request.user
    )
    return render(request, "employees/list.html", {
        "page": page, "search": search, "units": units,
        "selected_unit": unit_id, "tg": tg, "stats": stats,
    })


@login_required
def employee_detail(request, pk: int):
    employee = get_employee_for(request.user, pk)
    if employee is None:
        # Scoped lookup returns None for out-of-scope IDs -> 404, never leak.
        raise Http404
    salary_history = salaries_for(request.user).filter(
        employee=employee, is_current=True
    ).order_by("-period_year", "-period_month")
    return render(request, "employees/detail.html", {
        "employee": employee, "salary_history": salary_history,
    })


@login_required
def unconnected_list(request):
    qs = unconnected_employees_for(request.user).order_by("full_name")
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "employees/unconnected.html", {"page": page})


@login_required
@require_POST
def unlink_telegram(request, pk: int):
    employee = get_employee_for(request.user, pk)
    if employee is None:
        raise Http404
    EmployeeRegistrationService.unlink_telegram(employee=employee)
    AuditService.log(AuditAction.TELEGRAM_UNLINKED, obj=employee)
    messages.success(request, "Telegram akkaunt uzildi.")
    return redirect("employees:detail", pk=pk)


@login_required
@require_role_check("can_manage_employees")
def employee_create(request):
    if request.method == "POST":
        form = EmployeeForm(request.POST, allowed_units=_allowed_units(request.user))
        if form.is_valid():
            employee = form.save(commit=False)
            # Defense in depth: even if the <select> were tampered with, only
            # a unit this user can actually access is ever accepted, because
            # the queryset itself (not just client-side options) is scoped.
            if employee.organization_unit_id not in _allowed_units(request.user).values_list("id", flat=True):
                messages.error(request, "Siz bu filial uchun xodim qo'sha olmaysiz.")
                return render(request, "employees/form.html", {"form": form, "is_new": True})
            employee.save()
            AuditService.log(AuditAction.EMPLOYEE_CREATED, obj=employee,
                             metadata={"phone": employee.normalized_phone})
            # This phone may have already pressed /start on the bot before
            # today — link it now so they don't have to press it again.
            if EmployeeRegistrationService.try_auto_link_from_contact(employee):
                AuditService.log(AuditAction.TELEGRAM_LINKED, obj=employee)
                messages.success(request, "Xodim qo'shildi va botga avtomatik ulandi.")
            else:
                messages.success(request, "Xodim qo'shildi.")
            return redirect("employees:detail", pk=employee.pk)
    else:
        form = EmployeeForm(allowed_units=_allowed_units(request.user))
    return render(request, "employees/form.html", {"form": form, "is_new": True})


@login_required
@require_role_check("can_manage_employees")
def employee_edit(request, pk: int):
    employee = get_employee_for(request.user, pk)
    if employee is None:
        raise Http404
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee,
                            allowed_units=_allowed_units(request.user))
        if form.is_valid():
            employee = form.save()
            AuditService.log(AuditAction.EMPLOYEE_UPDATED, obj=employee)
            # Covers e.g. a corrected phone typo matching someone who
            # already pressed /start — same auto-link as on create.
            if EmployeeRegistrationService.try_auto_link_from_contact(employee):
                AuditService.log(AuditAction.TELEGRAM_LINKED, obj=employee)
            messages.success(request, "Xodim ma'lumotlari yangilandi.")
            return redirect("employees:detail", pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee, allowed_units=_allowed_units(request.user))
    return render(request, "employees/form.html", {"form": form, "is_new": False, "employee": employee})


@login_required
@require_role_check("can_manage_employees")
@require_POST
def employee_toggle_active(request, pk: int):
    employee = get_employee_for(request.user, pk)
    if employee is None:
        raise Http404
    employee.is_active = not employee.is_active
    employee.save(update_fields=["is_active", "updated_at"])
    AuditService.log(
        AuditAction.EMPLOYEE_ACTIVATED if employee.is_active else AuditAction.EMPLOYEE_DEACTIVATED,
        obj=employee,
    )
    messages.success(
        request,
        f"'{employee.full_name}' {'faollashtirildi' if employee.is_active else 'nofaollashtirildi'}.",
    )
    return redirect("employees:detail", pk=pk)


@login_required
@require_role_check("can_manage_employees")
@require_POST
def employee_delete(request, pk: int):
    """
    Permanently delete an employee AND their salary/notification history
    (Employee -> Salary -> TelegramMessage are CASCADE). There is no undo —
    use employee_toggle_active for a reversible "hide" instead.
    """
    employee = get_employee_for(request.user, pk)
    if employee is None:
        raise Http404
    name = employee.full_name
    salary_count = employee.salaries.count()
    AuditService.log(AuditAction.EMPLOYEE_DELETED, object_type="Employee",
                     object_id=str(pk), metadata={"full_name": name, "salaries": salary_count})
    employee.delete()
    messages.success(request, f"'{name}' o'chirildi ({salary_count} ta oylik yozuvi bilan birga).")
    return redirect("employees:list")


@login_required
@require_role_check("can_manage_branches")
def telegram_users_list(request):
    """
    Everyone who has ever pressed /start on the bot (TelegramContact) —
    the "1st kind" of person, as opposed to a real, branch-owned Employee
    record (the "2nd kind"). Not branch-scoped: a bare phone+telegram_id
    pairing isn't attributable to any branch until it's matched to an
    Employee, so — like the branches/admins pages — this is for global-scope
    roles only, not a specific branch's admin.
    """
    contacts = list(TelegramContact.objects.all().order_by("-first_seen_at"))
    phones = [c.normalized_phone for c in contacts]
    matched = {
        e.normalized_phone: e
        for e in Employee.objects.filter(normalized_phone__in=phones)
        .select_related("organization_unit")
    }
    rows = [{"contact": c, "employee": matched.get(c.normalized_phone)} for c in contacts]

    paginator = Paginator(rows, 30)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "employees/telegram_users.html", {
        "page": page,
        "matched_count": len(matched),
        "unmatched_count": len(contacts) - len(matched),
    })


@login_required
@require_role_check("can_manage_branches")
@require_POST
def telegram_user_delete(request, pk: int):
    contact = get_object_or_404(TelegramContact, pk=pk)
    phone = contact.normalized_phone
    contact.delete()
    messages.success(request, f"'{phone}' Telegram yozuvi o'chirildi.")
    return redirect("employees:telegram_users")
