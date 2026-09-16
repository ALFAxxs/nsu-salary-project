"""
EmployeeRegistrationService — links a Telegram account to an Employee (spec §8, §9).

Business rules enforced here (not in the bot handlers):
  * An employee is found by normalized phone.
  * A phone may link to exactly one Telegram account. If the employee is already
    linked to a *different* telegram_id, we refuse and tell them to contact HR.
  * A telegram_id may not be claimed by two different employees.
  * Admin can unlink / relink (see unlink_employee).

No employee is pre-registered in this system: a phone number becomes an
Employee only when it first appears in a monthly payroll Excel. But someone
may press /start on the bot before that happens. TelegramContact is the
bridge — every /start+contact is recorded there regardless of whether a
matching Employee exists yet, so a later Excel import can auto-link a
brand-new Employee immediately instead of requiring a second /start.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from django.db import transaction
from django.utils import timezone

from apps.common.phone import normalize_phone
from apps.employees.models import Employee, TelegramContact


class LinkResult(str, Enum):
    LINKED = "LINKED"                    # newly linked
    ALREADY_LINKED_SAME = "ALREADY_SAME"  # this telegram already linked to this employee
    CONFLICT_EMPLOYEE = "CONFLICT_EMPLOYEE"  # employee linked to a different telegram
    CONFLICT_TELEGRAM = "CONFLICT_TELEGRAM"  # telegram already used by another employee
    NOT_FOUND = "NOT_FOUND"             # no employee for this phone
    AMBIGUOUS_PHONE = "AMBIGUOUS_PHONE"  # phone matches employees in >1 branch


@dataclass
class LinkOutcome:
    result: LinkResult
    employee: Employee | None = None


class EmployeeRegistrationService:
    @staticmethod
    @transaction.atomic
    def link_telegram(*, raw_phone: str, telegram_id: int,
                      telegram_username: str = "") -> LinkOutcome:
        phone = normalize_phone(raw_phone)
        if not phone:
            return LinkOutcome(LinkResult.NOT_FOUND)

        # Remember this phone <-> Telegram pairing regardless of whether an
        # Employee exists yet — this month's payroll Excel may create one.
        EmployeeRegistrationService.record_contact(
            raw_phone=raw_phone, telegram_id=telegram_id,
            telegram_username=telegram_username,
        )

        # Lock the row we might modify to avoid race conditions on concurrent
        # /start. normalized_phone is globally unique at the DB level now, so
        # AMBIGUOUS_PHONE below is purely defensive — it should never trigger
        # in normal operation, but fetching as a list (instead of .first())
        # means a future relaxation of that constraint, or direct DB
        # tampering, fails safe (refuse + ask HR) instead of silently
        # picking an arbitrary match.
        candidates = list(
            Employee.objects.select_for_update()
            .filter(normalized_phone=phone, is_active=True)
        )
        if not candidates:
            return LinkOutcome(LinkResult.NOT_FOUND)
        if len(candidates) > 1:
            return LinkOutcome(LinkResult.AMBIGUOUS_PHONE)
        employee = candidates[0]

        # Employee already linked?
        if employee.telegram_id is not None:
            if employee.telegram_id == telegram_id:
                return LinkOutcome(LinkResult.ALREADY_LINKED_SAME, employee)
            return LinkOutcome(LinkResult.CONFLICT_EMPLOYEE, employee)

        # Is this telegram_id already claimed by someone else?
        clash = (
            Employee.objects.select_for_update()
            .filter(telegram_id=telegram_id)
            .exclude(pk=employee.pk)
            .first()
        )
        if clash is not None:
            return LinkOutcome(LinkResult.CONFLICT_TELEGRAM, employee)

        employee.telegram_id = telegram_id
        employee.telegram_username = telegram_username or ""
        employee.telegram_linked_at = timezone.now()
        employee.save(update_fields=[
            "telegram_id", "telegram_username", "telegram_linked_at", "updated_at",
        ])
        return LinkOutcome(LinkResult.LINKED, employee)

    @staticmethod
    @transaction.atomic
    def unlink_telegram(*, employee: Employee) -> Employee:
        """Admin action: detach the Telegram account so it can be re-linked."""
        employee.telegram_id = None
        employee.telegram_username = ""
        employee.telegram_linked_at = None
        employee.save(update_fields=[
            "telegram_id", "telegram_username", "telegram_linked_at", "updated_at",
        ])
        return employee

    @staticmethod
    def get_by_telegram_id(telegram_id: int) -> Employee | None:
        return Employee.objects.filter(
            telegram_id=telegram_id, is_active=True
        ).select_related("organization_unit").first()

    # --------------------------------------------------------------------- #
    # TelegramContact bridge — see module docstring.
    # --------------------------------------------------------------------- #
    @staticmethod
    @transaction.atomic
    def record_contact(*, raw_phone: str, telegram_id: int,
                       telegram_username: str = "") -> TelegramContact | None:
        """Upsert the phone<->telegram_id pairing. One phone, one Telegram
        account at a time — most recent /start wins (people do change SIMs)."""
        phone = normalize_phone(raw_phone)
        if not phone:
            return None
        TelegramContact.objects.filter(
            normalized_phone=phone
        ).exclude(telegram_id=telegram_id).delete()
        contact, _ = TelegramContact.objects.update_or_create(
            telegram_id=telegram_id,
            defaults={"normalized_phone": phone, "telegram_username": telegram_username or ""},
        )
        return contact

    @staticmethod
    def get_contact_for_phone(normalized_phone: str) -> TelegramContact | None:
        """Has this phone ever pressed /start on the bot? Used by Excel
        import to auto-link a brand-new Employee at creation time."""
        if not normalized_phone:
            return None
        return TelegramContact.objects.filter(normalized_phone=normalized_phone).first()

    @staticmethod
    @transaction.atomic
    def try_auto_link_from_contact(employee: Employee) -> bool:
        """
        If this employee's phone already pressed /start on the bot before
        the Employee record existed, link it now — instantly, no second
        /start needed. Used every time an Employee's phone is set or
        changes, whichever of the two ways an Employee comes to exist:
        a payroll Excel row (apps.imports.services) or the manual "Yangi
        xodim" / edit form (apps.employees.views) — both must behave the
        same way. Returns whether a link was made.
        """
        if employee.telegram_id is not None:
            return False  # already linked (to this or another contact) — don't touch
        contact = TelegramContact.objects.filter(
            normalized_phone=employee.normalized_phone
        ).first()
        if contact is None:
            return False
        # telegram_id is globally unique — someone else may already hold
        # this exact one. Never crash over it: just leave unlinked.
        already_claimed = Employee.objects.filter(
            telegram_id=contact.telegram_id
        ).exclude(pk=employee.pk).exists()
        if already_claimed:
            return False
        employee.telegram_id = contact.telegram_id
        employee.telegram_username = contact.telegram_username
        employee.telegram_linked_at = timezone.now()
        employee.save(update_fields=[
            "telegram_id", "telegram_username", "telegram_linked_at", "updated_at",
        ])
        return True
