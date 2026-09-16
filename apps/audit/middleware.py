"""Store the current request in a thread-local so services can read the actor/IP."""
from __future__ import annotations

import threading

_local = threading.local()


def get_current_request():
    return getattr(_local, "request", None)


def get_client_ip(request) -> str | None:
    """
    Best-effort client IP for the audit trail.

    We sit behind exactly one reverse proxy (nginx, see deploy/nginx.conf),
    which sets X-Forwarded-For via $proxy_add_x_forwarded_for — that APPENDS
    the real connecting address to whatever the client already sent, it does
    not replace it. So the client fully controls every value except the
    *last* one. Trusting the first value (as a naive implementation would)
    lets any caller spoof the IP recorded against their actions. We take the
    last entry, which nginx itself appended and the client cannot forge.
    """
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.META.get("REMOTE_ADDR")


class CurrentRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None
