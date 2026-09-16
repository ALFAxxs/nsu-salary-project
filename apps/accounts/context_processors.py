"""Inject role-aware sidebar flags into every template."""
from __future__ import annotations


def sidebar_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    return {
        "nav_can_manage_branches": user.can_manage_branches(),
        "nav_can_manage_admins": user.can_manage_admins(),
        "nav_can_import": user.can_import_salary(),
        "nav_can_manage_employees": user.can_manage_employees(),
        "nav_is_global": user.is_global_scope,
    }
