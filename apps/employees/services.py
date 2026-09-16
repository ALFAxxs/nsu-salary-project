"""
EmployeeRegistrationService — links a Telegram account to an Employee (spec §8, §9).

Business rules enforced here (not in the bot handlers):
  * Two-factor: an employee is found by normalized phone, AND the typed
    JSHSHIR must match THAT SAME employee's on-file JSHSHIR. Phone alone is
    not enough to link — someone reusing/guessing a phone number still
    can't attach themselves to a stranger's payroll without also knowing
    their 14-digit JSHSHIR.
  * A phone+JSHSHIR pair may link to exactly one Telegram account. If the
    employee is already linked to a *different* telegram_id, we refuse and
    tell them to contact HR.
  * A telegram_id may not be claimed by two different employees.
  * Admin can unlink / relink (see unlink_employee).

Employees are pre-registered (HR registry / Employee CRUD) — but someone may
press /start on the bot before HR has entered them yet. TelegramContact is
the bridge — every /start+contact+JSHSHIR is recorded there regardless of
whether a matching Employee exists yet, so a later HR registration can
auto-link a brand-new Employee immediately instead of requiring a second
/start (see try_auto_link_from_contact).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from django.db import transaction
from django.utils import timezone

from apps.common.jshshir import is_valid_jshshir, normalize_jshshir
from apps.common.phone import normalize_phone
from apps.employees.models import Employee, TelegramContact


class LinkResult(str, Enum):
    LINKED = "LINKED"                    # newly linked
    ALREADY_LINKED_SAME = "ALREADY_SAME"  # this telegram already linked to this employee
    CONFLICT_EMPLOYEE = "CONFLICT_EMPLOYEE"  # employee linked to a different telegram
    CONFLICT_TELEGRAM = "CONFLICT_TELEGRAM"  # telegram already used by another employee
    NOT_FOUND = "NOT_FOUND"             # no employee for this phone
    AMBIGUOUS_PHONE = "AMBIGUOUS_PHONE"  # phone matches employees in >1 branch
    JSHSHIR_INVALID = "JSHSHIR_INVALID"  # not exactly 14 digits
    JSHSHIR_MISMATCH = "JSHSHIR_MISMATCH"  # phone matched someone, but JSHSHIR doesn't match THAT employee


@dataclass
class LinkOutcome:
    result: LinkResult
    employee: Employee | None = None


class EmployeeRegistrationService:
    @staticmethod
    @transaction.atomic
    def link_telegram(*, raw_phone: str, raw_jshshir: str, telegram_id: int,
                      telegram_username: str = "") -> LinkOutcome:
        phone = normalize_phone(raw_phone)
        jshshir = normalize_jshshir(raw_jshshir)

        # Remember this phone+JSHSHIR <-> Telegram pairing regardless of
        # whether an Employee exists yet — HR may register one later.
        EmployeeRegistrationService.record_contact(
            raw_phone=raw_phone, telegram_id=telegram_id,
            telegram_username=telegram_username, raw_jshshir=raw_jshshir,
        )

        if not phone:
            return LinkOutcome(LinkResult.NOT_FOUND)
        if not is_valid_jshshir(jshshir):
            return LinkOutcome(LinkResult.JSHSHIR_INVALID)

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

        # Second factor: the typed JSHSHIR must match THIS employee's
        # on-file JSHSHIR. A phone match alone is never enough to link.
        if not employee.jshshir or employee.jshshir != jshshir:
            return LinkOutcome(LinkResult.JSHSHIR_MISMATCH, employee)

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
        # A salary notification may already be sitting there waiting only
        # for this — send it now instead of making an admin re-click Send.
        from apps.notifications.services import SalaryNotificationService
        SalaryNotificationService.recheck_after_link(employee)
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
                       telegram_username: str = "", raw_jshshir: str = "") -> TelegramContact | None:
        """Upsert the phone(+JSHSHIR)<->telegram_id pairing. One phone, one
        Telegram account at a time — most recent /start wins (people do
        change SIMs). raw_jshshir may be invalid/empty (e.g. before the
        person finishes typing it, or an old-style call site) — stored as
        "" in that case, which simply means try_auto_link_from_contact can
        never verify this contact later."""
        phone = normalize_phone(raw_phone)
        if not phone:
            return None
        jshshir = normalize_jshshir(raw_jshshir)
        if not is_valid_jshshir(jshshir):
            jshshir = ""
        TelegramContact.objects.filter(
            normalized_phone=phone
        ).exclude(telegram_id=telegram_id).delete()
        contact, _ = TelegramContact.objects.update_or_create(
            telegram_id=telegram_id,
            defaults={
                "normalized_phone": phone,
                "telegram_username": telegram_username or "",
                "jshshir": jshshir,
            },
        )
        return contact

    @staticmethod
    def get_contact_for_phone(normalized_phone: str) -> TelegramContact | None:
        """Has this phone ever pressed /start on the bot? Used to auto-link
        a brand-new Employee at creation time."""
        if not normalized_phone:
            return None
        return TelegramContact.objects.filter(normalized_phone=normalized_phone).first()

    @staticmethod
    @transaction.atomic
    def try_auto_link_from_contact(employee: Employee) -> bool:
        """
        If this employee's phone+JSHSHIR already passed the bot's /start
        verification before the Employee record existed (or before its
        JSHSHIR was filled in), link it now — instantly, no second /start
        needed. Used every time an Employee's phone/JSHSHIR is set or
        changes, from the manual "Yangi xodim" / edit form
        (apps.employees.views). Returns whether a link was made.

        Same two-factor rule as the bot itself: the stored contact's
        JSHSHIR must match this employee's on-file JSHSHIR. A contact
        recorded before this requirement existed (blank jshshir) — or an
        employee with none on file — can never auto-link; phone alone was
        never enough.
        """
        if employee.telegram_id is not None:
            return False  # already linked (to this or another contact) — don't touch
        if not employee.jshshir:
            return False
        contact = TelegramContact.objects.filter(
            normalized_phone=employee.normalized_phone
        ).first()
        if contact is None or not contact.jshshir or contact.jshshir != employee.jshshir:
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
        # Same as the bot's own /start path: don't make HR separately visit
        # Notifications and click Send for a message that was only ever
        # blocked by this employee not being connected yet.
        from apps.notifications.services import SalaryNotificationService
        SalaryNotificationService.recheck_after_link(employee)
        return True
