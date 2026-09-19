"""
SalaryNotificationService (spec §17, §18, §29, §30, §42).

Responsibilities:
  * Build the per-employee message rows for an import's current salaries,
    idempotently (get_or_create on salary+employee).
  * Two-factor gate before a message is ever eligible to send (see
    _eligible_status): (1) the employee's phone must be telegram-linked, AND
    (2) if this payroll row carried a JSHSHIR, it must match the employee's
    own on-file JSHSHIR. Phone alone is no longer enough — a phone match
    could be a typo/coincidence, so a mismatched or missing JSHSHIR holds
    the message back for HR review instead of sending on trust.
  * Format the message text with ONLY that employee's own data (§18, §54).
"""
from __future__ import annotations

from django.db import transaction

from apps.common.money import format_money as _fmt_money
from apps.imports.models import SalaryImport
from apps.notifications.models import (
    BroadcastMessage,
    BroadcastRecipient,
    MessageStatus,
    TelegramMessage,
)
from apps.salaries.models import Salary


def _eligible_status(salary: Salary, employee) -> str:
    """
    What status a (salary, employee) pair should have right now, applying
    both checks. Shared by prepare_for_import (new messages) and
    reset_for_resend (admin retry) so neither path can bypass the other.
    """
    if not employee.is_telegram_linked:
        return MessageStatus.TELEGRAM_NOT_CONNECTED
    payroll_jshshir = (salary.payroll_jshshir or "").strip()
    if payroll_jshshir:
        employee_jshshir = (employee.jshshir or "").strip()
        if not employee_jshshir or employee_jshshir != payroll_jshshir:
            return MessageStatus.JSHSHIR_MISMATCH
    # No JSHSHIR in this payroll row at all -> nothing to cross-check against,
    # fall back to the phone-link check alone (backward compatible).
    return MessageStatus.PENDING


TELEGRAM_MESSAGE_LIMIT = 4096


def format_salary_message(salary: Salary) -> str:
    """
    One message = one branch's salary for the employee (Salary is now
    scoped per branch per period, see apps.salaries.models.Salary). If an
    employee worked at two branches the same month, they get two separate
    messages like this one, each clearly naming its own branch — so they
    can tell the two incomes apart.

    Everything else the source Excel carried for this row (bonuses,
    allowances, itemized deductions, ...) is appended below, labeled
    exactly as in the file — see Salary.components / ExcelValidationService.
    """
    e = salary.employee
    text = (
        f"Assalomu alaykum, {e.full_name}!\n\n"
        f"🏢 Tashkilot: {salary.organization_unit.name}\n"
        f"📅 Hisoblangan davr: {salary.period_label}\n\n"
        f"💰 Hisoblangan ish haqi: {_fmt_money(salary.gross_salary)} so'm\n"
        f"💳 Avans: {_fmt_money(salary.advance)} so'm\n"
        f"➖ Ushlanmalar: {_fmt_money(salary.deductions)} so'm\n"
    )
    # Breakdown of "Ushlanmalar" — shown only when the source file actually
    # carried these columns (kept out of the message otherwise, since 0 would
    # misleadingly read as "nothing was withheld"). Social tax deliberately
    # excluded — it's an employer-side cost, never withheld from this
    # employee's own pay (see Salary.social_tax).
    breakdown = [
        ("   • NDFL (daromad solig'i)", salary.income_tax),
        ("   • INPS (pensiya jamg'armasi)", salary.pension_contribution),
        ("   • Profsoyuz badali", salary.union_dues),
    ]
    for label, value in breakdown:
        if value:
            text += f"{label}: {_fmt_money(value)} so'm\n"
    text += f"\n✅ Plastik kartaga tushadigan summa: {_fmt_money(salary.net_salary)} so'm"
    components = salary.components or []
    if components:
        lines = ["\n\n📋 Qo'shimcha ma'lumotlar:"]
        for c in components:
            lines.append(f"• {c['label']}: {_fmt_money(c['value'])}")
        text += "\n".join(lines)
    text += "\n\nBatafsil ma'lumot uchun bot menyusidan foydalanishingiz mumkin."
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        text = text[: TELEGRAM_MESSAGE_LIMIT - 20].rstrip() + "\n… (davomi qisqartirildi)"
    return text


class SalaryNotificationService:
    @staticmethod
    @transaction.atomic
    def prepare_for_import(salary_import: SalaryImport) -> dict:
        """
        Create (or reuse) TelegramMessage rows for every current salary tied to
        this import. Returns a small summary. Idempotent.
        """
        salaries = (
            Salary.objects.select_related("employee")
            .filter(source_import=salary_import, is_current=True)
        )
        prepared = linked = unlinked = mismatched = 0
        for salary in salaries:
            emp = salary.employee
            status = _eligible_status(salary, emp)
            msg, created = TelegramMessage.objects.get_or_create(
                salary=salary,
                employee=emp,
                defaults={"telegram_id": emp.telegram_id, "status": status},
            )
            if not created:
                # Refresh eligibility for a not-yet-sent row (e.g. HR fixed
                # the employee's JSHSHIR, or they connected to the bot since).
                if msg.status in {
                    MessageStatus.PENDING,
                    MessageStatus.TELEGRAM_NOT_CONNECTED,
                    MessageStatus.JSHSHIR_MISMATCH,
                }:
                    msg.telegram_id = emp.telegram_id
                    msg.status = status
                    msg.save(update_fields=["telegram_id", "status", "updated_at"])
            prepared += 1
            if status == MessageStatus.PENDING:
                linked += 1
            elif status == MessageStatus.JSHSHIR_MISMATCH:
                mismatched += 1
            else:
                unlinked += 1
        return {"prepared": prepared, "linked": linked, "unlinked": unlinked, "mismatched": mismatched}

    @staticmethod
    def pending_message_ids(salary_import: SalaryImport) -> list[int]:
        """IDs of messages that should actually be dispatched (connected + not sent)."""
        return list(
            TelegramMessage.objects.filter(
                salary__source_import=salary_import,
                status__in=[MessageStatus.PENDING, MessageStatus.RETRYING],
                telegram_id__isnull=False,
            ).values_list("id", flat=True)
        )

    @staticmethod
    @transaction.atomic
    def reset_for_resend(message: TelegramMessage) -> TelegramMessage:
        """Admin 'Resend' — allow an already-sent/failed message to go again.
        Re-applies the same two-factor check as prepare_for_import, so a
        JSHSHIR mismatch can't be bypassed by resending."""
        message.telegram_id = message.employee.telegram_id
        message.status = _eligible_status(message.salary, message.employee)
        message.save(update_fields=["telegram_id", "status", "updated_at"])
        return message

    @staticmethod
    @transaction.atomic
    def recheck_after_link(employee) -> int:
        """
        Called right after an employee becomes telegram-linked — via the
        bot's own /start flow, or HR creating/editing an Employee that
        auto-links to a pending TelegramContact (see
        EmployeeRegistrationService). Any of THEIR already-prepared
        messages that were only held back for TELEGRAM_NOT_CONNECTED (or
        JSHSHIR_MISMATCH, in case that also got fixed around the same
        time) go out immediately instead of waiting for an admin to
        revisit that import and click "Send" again. Returns how many were
        dispatched.
        """
        from apps.notifications.tasks import send_salary_message

        candidates = TelegramMessage.objects.select_related("salary").filter(
            employee=employee,
            status__in=[MessageStatus.TELEGRAM_NOT_CONNECTED, MessageStatus.JSHSHIR_MISMATCH],
        )
        dispatched = 0
        for msg in candidates:
            status = _eligible_status(msg.salary, employee)
            if status != MessageStatus.PENDING:
                continue
            msg.telegram_id = employee.telegram_id
            msg.status = status
            msg.save(update_fields=["telegram_id", "status", "updated_at"])
            # Defer the Celery dispatch until this (possibly nested, e.g.
            # inside link_telegram's own atomic block) transaction actually
            # commits — a worker on a separate connection could otherwise
            # try to load this message before the write is durable and find
            # nothing, silently dropping the send.
            transaction.on_commit(lambda pk=msg.pk: send_salary_message.delay(pk))
            dispatched += 1
        return dispatched


class BroadcastService:
    """Free-text announcements to every telegram-linked employee, or to one
    branch's — separate from salary notifications. Gating (superadmin-only)
    is the caller's responsibility (see apps.notifications.views); this
    service only builds the recipient list and dispatches."""

    @staticmethod
    def eligible_employees(organization_unit=None):
        from apps.employees.models import Employee

        qs = Employee.objects.filter(is_active=True, telegram_id__isnull=False)
        if organization_unit is not None:
            qs = qs.filter(organization_unit=organization_unit)
        return qs

    @staticmethod
    @transaction.atomic
    def create_and_dispatch(*, text: str, organization_unit, created_by) -> BroadcastMessage:
        from apps.notifications.tasks import dispatch_broadcast

        broadcast = BroadcastMessage.objects.create(
            text=text, organization_unit=organization_unit, created_by=created_by,
        )
        recipients = list(BroadcastService.eligible_employees(organization_unit))
        BroadcastRecipient.objects.bulk_create([
            BroadcastRecipient(broadcast=broadcast, employee=emp, telegram_id=emp.telegram_id)
            for emp in recipients
        ])
        # Same reasoning as recheck_after_link: defer past this transaction's
        # commit so the worker's separate connection always finds the rows.
        transaction.on_commit(lambda: dispatch_broadcast.delay(broadcast.pk))
        return broadcast
