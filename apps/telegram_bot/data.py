"""
Bot-facing data access.

The bot runs as a separate asyncio process (aiogram 3.x) but uses the Django ORM
for all data. Blocking ORM calls are wrapped with sync_to_async so they don't
stall the event loop.

Employees only ever see their OWN salary rows (spec §22, §54) — every query is
keyed by telegram_id.
"""
from __future__ import annotations

from asgiref.sync import sync_to_async

from apps.employees.models import Employee
from apps.employees.services import EmployeeRegistrationService
from apps.salaries.models import Salary


@sync_to_async
def link_contact(*, phone: str, telegram_id: int, username: str = "") -> tuple[str, str]:
    """Attempt to link a Telegram contact. Returns (result_code, employee_name)."""
    outcome = EmployeeRegistrationService.link_telegram(
        raw_phone=phone, telegram_id=telegram_id, telegram_username=username,
    )
    name = outcome.employee.full_name if outcome.employee else ""
    return outcome.result.value, name


@sync_to_async
def get_employee(telegram_id: int) -> dict | None:
    emp = EmployeeRegistrationService.get_by_telegram_id(telegram_id)
    if emp is None:
        return None
    return {
        "id": emp.id,
        "full_name": emp.full_name,
        "employee_code": emp.employee_code,
        "unit": emp.organization_unit.name,
    }


@sync_to_async
def get_current_salary(telegram_id: int) -> list[dict]:
    """
    The most recent period's salary — as a LIST, because an employee can have
    more than one current salary for the same month if more than one branch
    paid them (spec: they worked at two branches, both reported them).
    """
    emp = Employee.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if emp is None:
        return []
    latest = (
        Salary.objects.filter(employee=emp, is_current=True)
        .order_by("-period_year", "-period_month")
        .values("period_year", "period_month")
        .first()
    )
    if latest is None:
        return []
    salaries = Salary.objects.filter(
        employee=emp, is_current=True,
        period_year=latest["period_year"], period_month=latest["period_month"],
    ).select_related("organization_unit").order_by("organization_unit__name")
    return [_salary_dict(s) for s in salaries]


@sync_to_async
def list_salary_years(telegram_id: int) -> list[int]:
    """Distinct years this employee has salary history for, newest first."""
    emp = Employee.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if emp is None:
        return []
    return list(
        Salary.objects.filter(employee=emp, is_current=True)
        .order_by("-period_year")
        .values_list("period_year", flat=True)
        .distinct()
    )


@sync_to_async
def list_salary_months(telegram_id: int, year: int) -> list[int]:
    """Distinct months (1-12) this employee has salary history for in `year`."""
    emp = Employee.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if emp is None:
        return []
    return list(
        Salary.objects.filter(employee=emp, is_current=True, period_year=year)
        .order_by("-period_month")
        .values_list("period_month", flat=True)
        .distinct()
    )


@sync_to_async
def get_salary_detail(telegram_id: int, year: int, month: int) -> list[dict]:
    """All of this employee's salaries for one period — one per branch that
    paid them that month, so a list even in the common single-branch case."""
    emp = Employee.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if emp is None:
        return []
    salaries = Salary.objects.filter(
        employee=emp, period_year=year, period_month=month, is_current=True
    ).select_related("organization_unit").order_by("organization_unit__name")
    return [_salary_dict(s) for s in salaries]


def _salary_dict(s: Salary) -> dict:
    return {
        "period_year": s.period_year,
        "period_month": s.period_month,
        "period_label": s.period_label,
        "unit": s.organization_unit.name,
        "gross": s.gross_salary,
        "advance": s.advance,
        "deductions": s.deductions,
        "net": s.net_salary,
        "components": s.components or [],
    }
