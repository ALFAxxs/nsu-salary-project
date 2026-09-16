"""View decorators for role gating."""
from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied


def require_role_check(check_name: str):
    """Gate a view on a User.can_* method (e.g. 'can_import_salary')."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            method = getattr(request.user, check_name, None)
            if not callable(method) or not method():
                raise PermissionDenied("You do not have permission for this action.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
