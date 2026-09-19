from django.contrib import admin
from django.http import FileResponse, Http404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.accounts.permissions import scope_imports
from apps.imports.models import ColumnMapping, SalaryImport


@admin.register(SalaryImport)
class SalaryImportAdmin(admin.ModelAdmin):
    """
    The uploaded Excel itself is stored on `file` (apps.imports.views.import_upload
    saves it on every upload) and never linked to from the regular web app —
    Django admin is the only place anyone can view/download the original file
    or see who uploaded it and at what time.

    Downloads go through `download_file` below rather than a raw `file.url`
    link: nginx marks /media/ `internal` in production (deploy/nginx.conf),
    so a direct link 404s even for a logged-in admin. `file` is excluded from
    the change form for the same reason — Django's default widget would
    render that same broken raw link — and `file_link` (admin-authenticated,
    branch-scoped like everything else here) stands in for it everywhere.
    """
    list_display = ("id", "organization_unit", "period_year", "period_month",
                    "status", "valid_rows", "error_rows",
                    "file_link", "uploaded_by", "uploaded_at")
    list_filter = ("status", "organization_unit")
    exclude = ("file",)
    # Who uploaded what, and when, is a historical fact once recorded —
    # not something to silently reassign via the admin edit form.
    readonly_fields = ("file_link", "file_name", "uploaded_by", "uploaded_at")

    def get_queryset(self, request):
        return scope_imports(super().get_queryset(request), request.user)

    def get_urls(self):
        return [
            path(
                "<int:pk>/download/",
                self.admin_site.admin_view(self.download_file),
                name="imports_salaryimport_download",
            ),
        ] + super().get_urls()

    def download_file(self, request, pk):
        obj = self.get_queryset(request).filter(pk=pk).first()
        if obj is None or not obj.file:
            raise Http404
        filename = obj.file_name or obj.file.name.rsplit("/", 1)[-1]
        return FileResponse(obj.file.open("rb"), as_attachment=True, filename=filename)

    @admin.display(description="Fayl")
    def file_link(self, obj):
        if not obj.pk or not obj.file:
            return "—"
        url = reverse("admin:imports_salaryimport_download", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, obj.file_name or obj.file.name)


@admin.register(ColumnMapping)
class ColumnMappingAdmin(admin.ModelAdmin):
    list_display = ("organization_unit", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        unit_ids = request.user.accessible_unit_ids()
        if unit_ids is None:
            return qs
        return qs.filter(organization_unit_id__in=unit_ids)

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.is_global_scope
