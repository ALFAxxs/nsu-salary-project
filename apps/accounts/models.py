"""
Custom user model with role-based access control.

Roles map directly to the spec (section 5). Every branch-scoped admin user
(BRANCH_ADMIN, ACCOUNTANT, HR) is scoped to one or more OrganizationUnits —
a single accountant or HR person commonly covers several small branches, so
this is a many-to-many, not a single FK. Head-office/super-admin roles see
everything regardless of what's assigned. The scoping is enforced in the
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
    # Which unit(s) this user is scoped to. At least one required for
    # branch-level roles (BRANCH_ADMIN/ACCOUNTANT/HR); left empty for
    # SUPER_ADMIN/HEAD_OFFICE_ADMIN, who are global by definition regardless
    # of what's assigned here. Many-to-many rather than a single FK: a real
    # org commonly has one accountant or HR person covering several small
    # branches. Deleting a branch (apps.organizations.views.unit_delete)
    # only unlinks it here — M2M has no on_delete to CASCADE the account
    # away, and a user with other units left should keep their login.
    organization_units = models.ManyToManyField(
        "organizations.OrganizationUnit",
        related_name="users",
        blank=True,
        verbose_name=_("organization units"),
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

    def can_view_reports(self) -> bool:
        """Hisobotlar (/reports/, /reports/moliyaviy/) — faqat bosh ofis
        admini va super admin ko'radi. Filial admin, buxgalter va HR bu
        sahifalarni umuman ko'rmaydi/kira olmaydi."""
        return self.role in {Role.SUPER_ADMIN, Role.HEAD_OFFICE_ADMIN} or self.is_superuser

    def accessible_unit_ids(self) -> list[int] | None:
        """
        Return the list of OrganizationUnit ids this user may access, or None
        to mean "all units" (global scope). Used by selectors to filter.
        """
        if self.is_global_scope:
            return None
        return list(self.organization_units.values_list("id", flat=True))

    @property
    def organization_units_display(self) -> str:
        """Comma-joined branch names for UI display (admin list, topbar) —
        "Global" for head office/super admin regardless of what's assigned,
        "—" for a branch-scoped user with none assigned yet."""
        if self.is_global_scope:
            return _("Global")
        names = list(self.organization_units.values_list("name", flat=True))
        return ", ".join(names) if names else "—"
