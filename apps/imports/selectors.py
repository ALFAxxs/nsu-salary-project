"""Scoped read queries for imports."""
from __future__ import annotations

from apps.accounts.permissions import scope_imports
from apps.imports.models import SalaryImport


def imports_for(user):
    return scope_imports(
        SalaryImport.objects.select_related("organization_unit", "uploaded_by"), user
    )


def get_import_for(user, import_id: int) -> SalaryImport | None:
    return imports_for(user).filter(pk=import_id).first()
