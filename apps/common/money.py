"""
Shared money display formatting — space-grouped thousands, rounded to whole
so'm (this app never shows tiyin/cents anywhere, in messages or reports).

Also papers over the excess decimal precision a Sum() aggregate can produce
on a DecimalField (e.g. "944645.880000000") — round-tripping through float
before formatting collapses that back to a clean whole number.
"""
from __future__ import annotations


def format_money(value) -> str:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    return f"{n:,}".replace(",", " ")
