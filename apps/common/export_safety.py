"""
Guards against CSV/Excel formula injection in generated exports.

Any exported .xlsx/.csv file may contain values that originated from an
uploaded Excel file (employee_code, names, etc.) — untrusted input from the
user's point of view. If such a value starts with '=', '+', '-', '@', a tab
or a carriage return, spreadsheet software (Excel, LibreOffice, Google
Sheets) may interpret it as a formula when the exported file is opened,
which is a well-known attack vector (CSV/Excel injection, OWASP).

Mitigation: prefix the value with a single quote. Excel treats a leading
apostrophe as "force text" and does not display it; other tools at worst
show a harmless leading quote instead of executing anything.
"""
from __future__ import annotations

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _FORMULA_PREFIXES:
        return "'" + text
    return text
