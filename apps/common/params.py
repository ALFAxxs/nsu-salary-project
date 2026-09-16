"""Safe parsing helpers for untrusted request query parameters."""
from __future__ import annotations


def to_int(value) -> int | None:
    """Parse a query-string value to int, or None if it isn't one.

    Filtering an integer field with a raw, unvalidated GET value (e.g.
    ``qs.filter(organization_unit_id=request.GET.get("unit"))``) raises an
    unhandled ValueError — and a 500 — the moment the value isn't numeric.
    Views should route query params through this before using them in a
    queryset filter.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
