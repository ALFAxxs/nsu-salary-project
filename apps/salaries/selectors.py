"""Scoped read queries for salaries (spec §34)."""
from __future__ import annotations

from apps.accounts.permissions import scope_salaries
from apps.salaries.models import Salary


def salaries_for(user):
    return scope_salaries(
        Salary.objects.select_related("employee", "organization_unit"), user
    )


def current_salaries_for(user):
    return salaries_for(user).filter(is_current=True)


def get_salary_for(user, salary_id: int) -> Salary | None:
    return salaries_for(user).filter(pk=salary_id).first()
