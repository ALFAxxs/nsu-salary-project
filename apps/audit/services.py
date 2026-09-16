"""AuditService — single entry point for writing audit records."""
from __future__ import annotations

from apps.audit.middleware import get_client_ip, get_current_request
from apps.audit.models import AuditLog


class AuditService:
    @staticmethod
    def log(action: str, *, user=None, obj=None, object_type: str = "",
            object_id: str = "", metadata: dict | None = None, ip: str | None = None):
        request = get_current_request()
        if user is None and request is not None:
            candidate = getattr(request, "user", None)
            if candidate is not None and candidate.is_authenticated:
                user = candidate
        if ip is None:
            ip = get_client_ip(request)
        if obj is not None and not object_type:
            object_type = obj.__class__.__name__
            object_id = str(getattr(obj, "pk", ""))
        return AuditLog.objects.create(
            user=user if (user and getattr(user, "is_authenticated", False)) else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            ip_address=ip,
            metadata=metadata or {},
        )
