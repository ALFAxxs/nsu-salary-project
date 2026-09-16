"""
Custom user model with role-based access control.

Roles map directly to the spec (section 5). Every admin user is scoped to an
OrganizationUnit; branch-level roles can only ever see their own unit, while
head-office / super-admin roles see everything. The scoping is enforced in the
querysets (selectors), never only in templates.
"""
from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", _("Super Admin")
    HEAD_OFFICE_ADMIN = "HEAD_OFFICE_ADMIN", _("Head Office Admin")
    BRANCH_ADMIN = "BRANCH_ADMIN", _("Branch Admin")
    ACCOUNTANT = "ACCOUNTANT", _("Accountant")
    HR = "HR", _("HR")


# Roles that can see every organization unit's data.
GLOBAL_ROLES = frozenset({Role.SUPER_ADMIN, Role.HEAD_OFFICE_ADMIN})


class User(AbstractUser):
    """
    Application user (staff of the company: HR, accountants, admins).

    NOT the same as an Employee — Employees are salary recipients who interact
    through the Telegram bot and never log into the web panel.
    """

    role = models.CharField(
        _("role"),
        max_length=32,
        choices=Role.choices,
        default=Role.BRANCH_ADMIN,
    )
    # Which unit this user is scoped to. Required for branch-level roles;
    # optional (null) only for SUPER_ADMIN who is global by definition.
    # CASCADE: deleting a branch (apps.organizations.views.unit_delete) is a
    # deliberate full wipe, including admin logins scoped to it.
    organization_unit = models.ForeignKey(
        "organizations.OrganizationUnit",
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        verbose_name=_("organization unit"),
    )
    phone = models.CharField(_("phone"), max_length=32, blank=True)

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    # --- Role helpers ----------------------------------------------------- #
    @property
    def is_global_scope(self) -> bool:
        """True if the user can access data across all organization units."""
        return self.is_superuser or self.role in GLOBAL_ROLES

    @property
    def is_branch_scope(self) -> bool:
        return not self.is_global_scope

    def can_manage_admins(self) -> bool:
        return self.is_superuser or self.role == Role.SUPER_ADMIN

    def can_manage_branches(self) -> bool:
        return self.role in {Role.SUPER_ADMIN, Role.HEAD_OFFICE_ADMIN} or self.is_superuser

    def can_import_salary(self) -> bool:
        return self.role in {
            Role.SUPER_ADMIN,
            Role.HEAD_OFFICE_ADMIN,
            Role.BRANCH_ADMIN,
            Role.ACCOUNTANT,
        } or self.is_superuser

    def can_manage_employees(self) -> bool:
        return self.role in {
            Role.SUPER_ADMIN,
            Role.HEAD_OFFICE_ADMIN,
            Role.BRANCH_ADMIN,
            Role.ACCOUNTANT,
            Role.HR,
        } or self.is_superuser

    def can_send_notifications(self) -> bool:
        return self.can_import_salary()

    def accessible_unit_ids(self) -> list[int] | None:
        """
        Return the list of OrganizationUnit ids this user may access, or None
        to mean "all units" (global scope). Used by selectors to filter.
        """
        if self.is_global_scope:
            return None
        return [self.organization_unit_id] if self.organization_unit_id else []
