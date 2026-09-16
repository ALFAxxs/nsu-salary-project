"""
Phone normalization (spec §7).

All Uzbek phone formats collapse to a canonical digits-only form:
    +998 90 123 45 67  ->  998901234567
    998901234567        ->  998901234567
    +998901234567       ->  998901234567
    901234567           ->  998901234567   (bare 9-digit local -> +998 prefixed)

This canonical form is the join key between Excel rows and Telegram contacts.
"""
from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")
UZ_COUNTRY_CODE = "998"


def normalize_phone(raw: str | None) -> str:
    """
    Return the canonical digits-only phone, or "" if it can't be normalized.

    We never raise here — validation layers decide what to do with "".
    """
    if not raw:
        return ""
    digits = _NON_DIGITS.sub("", str(raw))
    if not digits:
        return ""

    # Bare 9-digit local number (e.g. 901234567) -> prepend country code.
    if len(digits) == 9:
        digits = UZ_COUNTRY_CODE + digits
    # Number starting with a single 0 then 9 digits (090...) -> local, strip 0.
    elif len(digits) == 10 and digits.startswith("0"):
        digits = UZ_COUNTRY_CODE + digits[1:]

    return digits


def is_valid_uz_phone(normalized: str) -> bool:
    """A valid normalized UZ mobile number is 998 + 9 digits = 12 chars."""
    return bool(re.fullmatch(rf"{UZ_COUNTRY_CODE}\d{{9}}", normalized or ""))
