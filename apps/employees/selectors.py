"""Scoped read queries for employees (spec §34)."""
from __future__ import annotations

from apps.accounts.permissions import scope_employees
from apps.employees.models import Employee


def employees_for(user):
    return scope_employees(
        Employee.objects.select_related("organization_unit"), user
    )


def get_employee_for(user, employee_id: int) -> Employee | None:
    """
    Fetch an employee only if the user is allowed to see it (blocks ID
    tampering). Scoped by the employee's CURRENT branch first — but an
    employee who has since transferred elsewhere may still have Salary /
    TelegramMessage history that legitimately belongs to one of this user's
    OWN branches (e.g. the "JSHSHIR mismatch" list on an old import, or the
    Telegram-users page). Those pages link here, so a real historical
    relationship must still resolve instead of 404ing on a record the
    branch genuinely has a claim to.
    """
    emp = employees_for(user).filter(pk=employee_id).first()
    if emp is not None:
        return emp
    # Not in current-branch scope. A global-scope user would already have
    # matched above (accessible_unit_ids() is None -> unfiltered), so
    # reaching here always means unit_ids is a concrete branch-scoped list —
    # check for a genuine historical relationship before giving up.
    unit_ids = user.accessible_unit_ids()
    from apps.salaries.models import Salary
    has_history = Salary.objects.filter(
        employee_id=employee_id, organization_unit_id__in=unit_ids
    ).exists()
    if not has_history:
        return None
    return Employee.objects.select_related("organization_unit").filter(pk=employee_id).first()


def unconnected_employees_for(user):
    return employees_for(user).filter(telegram_id__isnull=True, is_active=True)
