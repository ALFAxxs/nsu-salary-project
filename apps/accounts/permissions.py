"""
Central permission / data-isolation layer.

Design principle (spec §34, §59): every queryset that touches employee or salary
data MUST be filtered by the requesting user's organization scope in the
*backend*. Branch users can never see another branch — not through the UI, not
through the API, and not by editing an ID in the URL.

Both the web views and the DRF API import from here so the rule is defined once.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from rest_framework.permissions import BasePermission


def scope_organization_units(qs: QuerySet, user) -> QuerySet:
    """Filter an OrganizationUnit queryset to what `user` may see."""
    unit_ids = user.accessible_unit_ids()
    if unit_ids is None:  # global scope
        return qs
    return qs.filter(id__in=unit_ids)


def scope_employees(qs: QuerySet, user) -> QuerySet:
    """Filter an Employee queryset to the user's organization scope."""
    unit_ids = user.accessible_unit_ids()
    if unit_ids is None:
        return qs
    return qs.filter(organization_unit_id__in=unit_ids)


def scope_salaries(qs: QuerySet, user) -> QuerySet:
    """
    Filter a Salary queryset to the user's organization scope.

    Scoped by Salary.organization_unit (which branch actually paid this
    salary) — NOT the employee's current organization_unit, which can move
    over time. A branch must keep seeing what it itself paid even after an
    employee transfers elsewhere; conversely an employee's new branch has
    no business seeing salary another branch paid before the transfer.
    """
    unit_ids = user.accessible_unit_ids()
    if unit_ids is None:
        return qs
    return qs.filter(organization_unit_id__in=unit_ids)


def scope_imports(qs: QuerySet, user) -> QuerySet:
    """Filter a SalaryImport queryset to the user's organization scope."""
    unit_ids = user.accessible_unit_ids()
    if unit_ids is None:
        return qs
    return qs.filter(organization_unit_id__in=unit_ids)


def scope_messages(qs: QuerySet, user) -> QuerySet:
    """Filter a TelegramMessage queryset to the user's organization scope.

    Scoped via the message's Salary.organization_unit — the branch that
    actually sent it — same reasoning as scope_salaries.
    """
    unit_ids = user.accessible_unit_ids()
    if unit_ids is None:
        return qs
    return qs.filter(salary__organization_unit_id__in=unit_ids)


def assert_can_access_unit(user, unit_id: int) -> None:
    """
    Raise PermissionDenied if `user` may not act on the given unit.

    Used by import/upload flows so a branch admin cannot upload data for a
    different branch even if they craft the request manually.
    """
    unit_ids = user.accessible_unit_ids()
    if unit_ids is None:
        return
    if unit_id not in unit_ids:
        raise PermissionDenied("You do not have access to this organization unit.")


# --------------------------------------------------------------------------- #
# DRF permission classes
# --------------------------------------------------------------------------- #
class IsAuthenticatedStaff(BasePermission):
    message = "Authentication required."

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)


class CanImportSalary(BasePermission):
    message = "You do not have permission to import salaries."

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated
                    and request.user.can_import_salary())


class CanManageBranches(BasePermission):
    message = "You do not have permission to manage branches."

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated
                    and request.user.can_manage_branches())
