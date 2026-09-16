"""
JSHSHIR (Jismoniy shaxsni identifikatsiya raqami — Uzbek personal ID number)
normalization, mirroring apps.common.phone. Exactly 14 digits when valid.
"""
from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")


def normalize_jshshir(raw) -> str:
    """Digits-only JSHSHIR, or "" if nothing usable was given."""
    if not raw:
        return ""
    return _NON_DIGITS.sub("", str(raw))


def is_valid_jshshir(value: str) -> bool:
    return bool(re.fullmatch(r"\d{14}", value or ""))
