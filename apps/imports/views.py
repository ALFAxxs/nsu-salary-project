"""
Import workflow views (spec §31):
  upload -> validate/preview -> confirm -> (then notifications app sends).
Every step is scoped and permission-checked.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_role_check
from apps.accounts.permissions import (
    assert_can_access_unit,
    scope_organization_units,
)
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.common.params import to_int
from apps.imports.forms import ImportUploadForm
from apps.imports.models import ImportStatus, SalaryImport
from apps.imports.selectors import get_import_for, imports_for
from apps.imports.services import SalaryImportService
from apps.notifications.services import SalaryNotificationService
from apps.organizations.models import OrganizationUnit


def _allowed_units(user):
    return scope_organization_units(
        OrganizationUnit.objects.filter(is_active=True), user
    )


@login_required
@require_role_check("can_import_salary")
def import_list(request):
    imports = imports_for(request.user).order_by("-uploaded_at")[:100]
    return render(request, "imports/list.html", {"imports": imports})


@login_required
@require_role_check("can_import_salary")
def import_upload(request):
    if request.method == "POST":
        form = ImportUploadForm(request.POST, request.FILES,
                                allowed_units=_allowed_units(request.user))
        if form.is_valid():
            unit = form.cleaned_data["organization_unit"]
            # Defense in depth: block cross-branch upload even if the select is tampered.
            assert_can_access_unit(request.user, unit.id)
            f = form.cleaned_data["file"]
            salary_import = SalaryImport.objects.create(
                organization_unit=unit,
                period_year=form.cleaned_data["period_year"],
                period_month=form.cleaned_data["period_month"],
                file=f,
                file_name=f.name,
                uploaded_by=request.user,
                status=ImportStatus.UPLOADED,
            )
            AuditService.log(AuditAction.EXCEL_UPLOADED, obj=salary_import,
                             metadata={"unit": unit.code, "file": f.name})
            SalaryImportService.validate_and_stage(salary_import)
            return redirect("imports:preview", pk=salary_import.pk)
    else:
        form = ImportUploadForm(allowed_units=_allowed_units(request.user))
    return render(request, "imports/upload.html", {"form": form})


@login_required
@require_role_check("can_import_salary")
def import_preview(request, pk: int):
    salary_import = get_import_for(request.user, pk)
    if salary_import is None:
        raise Http404
    payload = salary_import.validation_payload or {}
    rows = payload.get("rows", [])
    error_rows = [r for r in rows if r.get("errors")]
    valid_rows = [r for r in rows if not r.get("errors")]
    return render(request, "imports/preview.html", {
        "imp": salary_import,
        "error_rows": error_rows[:200],
        "valid_preview": valid_rows[:50],
        "fatal_error": payload.get("fatal_error"),
    })


@login_required
@require_role_check("can_import_salary")
@require_POST
def import_confirm(request, pk: int):
    salary_import = get_import_for(request.user, pk)
    if salary_import is None:
        raise Http404
    try:
        SalaryImportService.confirm_and_commit(salary_import, user=request.user)
    except (ValueError, PermissionDenied) as exc:
        messages.error(request, str(exc))
        return redirect("imports:preview", pk=pk)

    # Prepare notification rows (idempotent) but do NOT send yet (spec §17).
    summary = SalaryNotificationService.prepare_for_import(salary_import)
    AuditService.log(AuditAction.IMPORT_CONFIRMED, obj=salary_import,
                     metadata={"valid_rows": salary_import.valid_rows})
    text = (
        f"Import tasdiqlandi. {summary['linked']} ta xodim Telegramga ulangan, "
        f"{summary['unlinked']} ta ulanmagan."
    )
    if summary.get("mismatched"):
        text += f" Diqqat: {summary['mismatched']} ta xodimda JSHSHIR mos kelmadi — xabar yuborilmaydi."
    messages.success(request, text)
    return redirect("notifications:import_detail", pk=salary_import.pk)


@login_required
@require_role_check("can_import_salary")
@require_POST
def import_cancel(request, pk: int):
    salary_import = get_import_for(request.user, pk)
    if salary_import is None:
        raise Http404
    if salary_import.status in {ImportStatus.VALID, ImportStatus.HAS_ERRORS,
                                ImportStatus.UPLOADED, ImportStatus.FAILED}:
        salary_import.status = ImportStatus.CANCELLED
        salary_import.save(update_fields=["status"])
        messages.info(request, "Import bekor qilindi.")
    return redirect("imports:list")


@login_required
@require_role_check("can_import_salary")
def download_template(request):
    unit_id = to_int(request.GET.get("unit"))
    unit = None
    if unit_id is not None:
        unit = _allowed_units(request.user).filter(pk=unit_id).first()
    data = SalaryImportService.build_template(unit)
    resp = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="salary_template.xlsx"'
    return resp


@login_required
@require_role_check("can_import_salary")
def download_error_report(request, pk: int):
    salary_import = get_import_for(request.user, pk)
    if salary_import is None:
        raise Http404
    data = SalaryImportService.build_error_report(salary_import)
    resp = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="errors_import_{pk}.xlsx"'
    return resp
