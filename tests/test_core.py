"""
Test suite (spec §51, §52).

The most important test is branch isolation: a Branch-1 admin must never see
Branch-2 data — not via selectors, not via the web views, not via the API.
"""
from __future__ import annotations

from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.common.phone import is_valid_uz_phone, normalize_phone
from apps.employees.models import Employee, TelegramContact
from apps.employees.selectors import employees_for, get_employee_for
from apps.employees.services import EmployeeRegistrationService, LinkResult
from apps.imports.models import ImportStatus, SalaryImport
from apps.imports.services import SalaryImportService
from apps.imports.validators import ExcelValidationService
from apps.notifications.models import MessageStatus, TelegramMessage
from apps.notifications.services import SalaryNotificationService
from apps.organizations.models import OrganizationUnit, UnitType
from apps.salaries.models import Salary
from apps.salaries.selectors import salaries_for


def make_org():
    hq = OrganizationUnit.objects.create(
        name="Bosh Office", code="HQ", type=UnitType.HEAD_OFFICE, is_head_office=True
    )
    b1 = OrganizationUnit.objects.create(name="Filial 1", code="BR-01")
    b2 = OrganizationUnit.objects.create(name="Filial 2", code="BR-02")
    return hq, b1, b2


class AdminPasswordPolicyTests(TestCase):
    """AUTH_PASSWORD_VALIDATORS (settings.py) must actually be enforced when
    an admin sets another staff account's password — set_password() alone
    silently bypasses it."""

    def test_weak_password_rejected(self):
        from apps.accounts.forms import AdminUserForm

        form = AdminUserForm(data={
            "username": "newstaff", "email": "", "first_name": "", "last_name": "",
            "phone": "", "role": Role.HR, "organization_unit": "",
            "is_active": "on", "password": "1",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_strong_password_accepted(self):
        from apps.accounts.forms import AdminUserForm

        _, b1, _ = make_org()
        form = AdminUserForm(data={
            "username": "newstaff2", "email": "", "first_name": "", "last_name": "",
            "phone": "", "role": Role.BRANCH_ADMIN, "organization_unit": b1.id,
            "is_active": "on", "password": "Xy7$correct-horse-battery",
        })
        self.assertTrue(form.is_valid(), form.errors)


class PhoneNormalizationTests(TestCase):
    def test_formats_collapse(self):
        for raw in ["+998 90 123 45 67", "998901234567", "+998901234567"]:
            self.assertEqual(normalize_phone(raw), "998901234567")

    def test_local_number_prefixed(self):
        self.assertEqual(normalize_phone("901234567"), "998901234567")

    def test_validity(self):
        self.assertTrue(is_valid_uz_phone("998901234567"))
        self.assertFalse(is_valid_uz_phone("99890"))


class TelegramLinkingTests(TestCase):
    """Two-factor /start verification: phone (contact-share) AND JSHSHIR
    (typed) must both match the SAME on-file Employee before telegram_id is
    ever linked. Phone alone is never enough."""

    def setUp(self):
        _, self.b1, _ = make_org()
        self.emp = Employee.objects.create(
            employee_code="E1", full_name="Ali", phone="998901234567",
            jshshir="30101234567890", organization_unit=self.b1,
        )

    def test_link_success(self):
        outcome = EmployeeRegistrationService.link_telegram(
            raw_phone="+998 90 123 45 67", raw_jshshir="30101234567890",
            telegram_id=111, telegram_username="ali")
        self.assertEqual(outcome.result, LinkResult.LINKED)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.telegram_id, 111)

    def test_wrong_jshshir_blocks_link(self):
        outcome = EmployeeRegistrationService.link_telegram(
            raw_phone="998901234567", raw_jshshir="99999999999999", telegram_id=111)
        self.assertEqual(outcome.result, LinkResult.JSHSHIR_MISMATCH)
        self.emp.refresh_from_db()
        self.assertIsNone(self.emp.telegram_id)  # nothing linked

    def test_employee_with_no_jshshir_on_file_can_never_link(self):
        self.emp.jshshir = None
        self.emp.save(update_fields=["jshshir"])
        outcome = EmployeeRegistrationService.link_telegram(
            raw_phone="998901234567", raw_jshshir="30101234567890", telegram_id=111)
        self.assertEqual(outcome.result, LinkResult.JSHSHIR_MISMATCH)
        self.emp.refresh_from_db()
        self.assertIsNone(self.emp.telegram_id)

    def test_invalid_jshshir_format_rejected(self):
        outcome = EmployeeRegistrationService.link_telegram(
            raw_phone="998901234567", raw_jshshir="123", telegram_id=111)
        self.assertEqual(outcome.result, LinkResult.JSHSHIR_INVALID)

    def test_conflict_when_already_linked(self):
        EmployeeRegistrationService.link_telegram(
            raw_phone="998901234567", raw_jshshir="30101234567890", telegram_id=111)
        outcome = EmployeeRegistrationService.link_telegram(
            raw_phone="998901234567", raw_jshshir="30101234567890", telegram_id=222)
        self.assertEqual(outcome.result, LinkResult.CONFLICT_EMPLOYEE)

    def test_not_found(self):
        outcome = EmployeeRegistrationService.link_telegram(
            raw_phone="998900000000", raw_jshshir="30101234567890", telegram_id=333)
        self.assertEqual(outcome.result, LinkResult.NOT_FOUND)

    def test_not_found_still_remembers_contact_with_jshshir(self):
        # No employee exists for this phone yet — but the person still
        # pressed /start and typed a JSHSHIR, so a later HR registration
        # with this phone+JSHSHIR must be able to auto-link them without
        # asking them to go through the bot a second time.
        EmployeeRegistrationService.link_telegram(
            raw_phone="998900000000", raw_jshshir="99988877766655",
            telegram_id=444, telegram_username="futureguy")
        contact = TelegramContact.objects.get(normalized_phone="998900000000")
        self.assertEqual(contact.telegram_id, 444)
        self.assertEqual(contact.jshshir, "99988877766655")

    def test_auto_link_from_contact_requires_matching_jshshir(self):
        # This phone+JSHSHIR pair pressed /start before any Employee record
        # for them existed.
        EmployeeRegistrationService.link_telegram(
            raw_phone="998900009999", raw_jshshir="11122233344455",
            telegram_id=555, telegram_username="newhire")

        # HR registers them with the WRONG JSHSHIR on file (typo) — must not
        # auto-link on phone alone.
        wrong = Employee.objects.create(
            full_name="Typo'd JSHSHIR", phone="998900009999",
            jshshir="00000000000000", organization_unit=self.b1)
        self.assertFalse(EmployeeRegistrationService.try_auto_link_from_contact(wrong))
        wrong.refresh_from_db()
        self.assertIsNone(wrong.telegram_id)

        # HR fixes the JSHSHIR to match what the person actually typed —
        # now it auto-links.
        wrong.jshshir = "11122233344455"
        wrong.save(update_fields=["jshshir"])
        self.assertTrue(EmployeeRegistrationService.try_auto_link_from_contact(wrong))
        wrong.refresh_from_db()
        self.assertEqual(wrong.telegram_id, 555)


class BranchIsolationTests(TestCase):
    """The mandatory test: Branch 1 admin cannot see Branch 2 data (spec §51)."""

    def setUp(self):
        self.hq, self.b1, self.b2 = make_org()
        self.e1 = Employee.objects.create(
            employee_code="B1-E1", full_name="Branch1 Emp", phone="998901110001",
            organization_unit=self.b1)
        self.e2 = Employee.objects.create(
            employee_code="B2-E1", full_name="Branch2 Emp", phone="998902220001",
            organization_unit=self.b2)
        self.admin1 = User.objects.create_user(
            username="b1admin", password="x", role=Role.BRANCH_ADMIN,
            organization_unit=self.b1)
        self.hqadmin = User.objects.create_user(
            username="hq", password="x", role=Role.HEAD_OFFICE_ADMIN,
            organization_unit=self.hq)

    def test_selector_scopes_employees(self):
        visible = set(employees_for(self.admin1).values_list("id", flat=True))
        self.assertIn(self.e1.id, visible)
        self.assertNotIn(self.e2.id, visible)

    def test_id_tampering_blocked(self):
        # Branch1 admin fetching Branch2 employee by ID -> None (would 404).
        self.assertIsNone(get_employee_for(self.admin1, self.e2.id))
        self.assertIsNotNone(get_employee_for(self.admin1, self.e1.id))

    def test_head_office_sees_all(self):
        visible = set(employees_for(self.hqadmin).values_list("id", flat=True))
        self.assertIn(self.e1.id, visible)
        self.assertIn(self.e2.id, visible)

    def test_web_detail_404_for_other_branch(self):
        c = Client()
        c.force_login(self.admin1)
        resp = c.get(reverse("employees:detail", args=[self.e2.id]))
        self.assertEqual(resp.status_code, 404)
        resp_ok = c.get(reverse("employees:detail", args=[self.e1.id]))
        self.assertEqual(resp_ok.status_code, 200)

    def test_api_scopes_employees(self):
        c = Client()
        c.force_login(self.admin1)
        resp = c.get("/api/employees/")
        self.assertEqual(resp.status_code, 200)
        ids = [row["id"] for row in resp.json()["results"]]
        self.assertIn(self.e1.id, ids)
        self.assertNotIn(self.e2.id, ids)

    def test_salary_scope(self):
        Salary.objects.create(employee=self.e2, organization_unit=self.b2,
                              period_year=2026, period_month=8,
                              net_salary=Decimal("1000000"))
        self.assertEqual(salaries_for(self.admin1).count(), 0)
        self.assertEqual(salaries_for(self.hqadmin).count(), 1)

    def test_employee_who_transferred_still_visible_to_old_branchs_history(self):
        # e1 was paid by b1, then transfers to b2 (a later import moves
        # e1.organization_unit to b2). b1's admin must still be able to open
        # e1's profile from b1's own historical Salary/notification pages —
        # e1 is no longer in b1's CURRENT scope, but b1 genuinely paid them.
        Salary.objects.create(employee=self.e1, organization_unit=self.b1,
                              period_year=2026, period_month=7,
                              net_salary=Decimal("2000000"))
        self.e1.organization_unit = self.b2
        self.e1.save(update_fields=["organization_unit"])

        self.assertIsNone(employees_for(self.admin1).filter(pk=self.e1.id).first())
        self.assertIsNotNone(get_employee_for(self.admin1, self.e1.id))
        # But a total stranger (e2, never paid by b1) still 404s for b1.
        self.assertIsNone(get_employee_for(self.admin1, self.e2.id))


class SalaryRevisionTests(TestCase):
    def setUp(self):
        _, self.b1, _ = make_org()
        self.emp = Employee.objects.create(
            employee_code="E1", full_name="Ali", phone="998901234567",
            organization_unit=self.b1)

    def test_reimport_creates_revision_not_duplicate(self):
        imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=8,
            file_name="x.xlsx", uploaded_by=User.objects.create_user("u", password="x", role=Role.SUPER_ADMIN),
        )
        SalaryImportService._upsert_salary(
            salary_import=imp, employee_id=self.emp.id,
            gross="8000000", advance="2000000", deductions="100000", net="5900000")
        SalaryImportService._upsert_salary(
            salary_import=imp, employee_id=self.emp.id,
            gross="8100000", advance="2000000", deductions="100000", net="6000000")
        # One current, one archived — no duplicate current.
        self.assertEqual(
            Salary.objects.filter(employee=self.emp, is_current=True).count(), 1)
        self.assertEqual(Salary.objects.filter(employee=self.emp).count(), 2)
        current = Salary.objects.get(employee=self.emp, is_current=True)
        self.assertEqual(current.revision, 2)
        self.assertEqual(current.net_salary, Decimal("6000000"))

    def test_unchanged_reimport_does_not_bump_revision(self):
        # Accidentally uploading the exact same file twice must not create a
        # second revision or (downstream) a redundant "your salary" resend.
        imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=9,
            file_name="x.xlsx",
            uploaded_by=User.objects.create_user("u9", password="x", role=Role.SUPER_ADMIN))
        kwargs = dict(
            salary_import=imp, employee_id=self.emp.id,
            gross="8000000", advance="2000000", deductions="100000", net="5900000")
        SalaryImportService._upsert_salary(**kwargs)
        SalaryImportService._upsert_salary(**kwargs)  # identical re-upload
        self.assertEqual(Salary.objects.filter(employee=self.emp).count(), 1)
        current = Salary.objects.get(employee=self.emp, is_current=True)
        self.assertEqual(current.revision, 1)


class ConcurrentImportConfirmTests(TestCase):
    """A phantom-row race: select_for_update() can't lock a row that doesn't
    exist yet, so two concurrent first-time confirms of the very same
    employee+branch+period used to crash with an uncaught IntegrityError
    instead of the second one cleanly becoming revision 2."""

    def setUp(self):
        _, self.b1, _ = make_org()
        self.emp = Employee.objects.create(
            employee_code="E1", full_name="Ali", phone="998901234567",
            organization_unit=self.b1)
        self.imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=8,
            file_name="x.xlsx",
            uploaded_by=User.objects.create_user("u10", password="x", role=Role.SUPER_ADMIN))

    def test_simulated_race_becomes_revision_two_not_a_crash(self):
        # Simulate the race directly: two "first-time" inserts for the same
        # key, as if select_for_update() had found nothing both times.
        from django.db import IntegrityError, transaction

        kwargs = dict(
            salary_import=self.imp, employee_id=self.emp.id,
            gross="5000000", advance="0", deductions="0", net="5000000")
        with transaction.atomic():
            SalaryImportService._upsert_salary(**kwargs)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Salary.objects.create(
                    employee_id=self.emp.id, organization_unit=self.b1,
                    period_year=2026, period_month=8,
                    gross_salary=Decimal("5000000"), net_salary=Decimal("5000000"),
                    source_import=self.imp, is_current=True, revision=1,
                )
        # The retry path in confirm_and_commit: a savepoint-wrapped retry
        # after the IntegrityError finds the row that's now there and
        # correctly becomes revision 2 instead of raising again.
        with transaction.atomic():
            SalaryImportService._upsert_salary(
                salary_import=self.imp, employee_id=self.emp.id,
                gross="5100000", advance="0", deductions="0", net="5100000")
        current = Salary.objects.get(employee=self.emp, is_current=True)
        self.assertEqual(current.revision, 2)
        self.assertEqual(Salary.objects.filter(employee=self.emp).count(), 2)


class MultiBranchSalaryTests(TestCase):
    """
    An employee who genuinely worked at two branches in the same month gets
    a salary reported by EACH — both must survive as separate current rows,
    each visible only to the branch that reported it, and re-importing the
    SAME branch's data for that month must still revise only that branch's
    own row, never touch the other branch's.
    """
    def setUp(self):
        _, self.b1, self.b2 = make_org()
        self.emp = Employee.objects.create(
            employee_code="", full_name="Ikki filialda ishlovchi",
            phone="998901234567", organization_unit=self.b1)
        self.admin1 = User.objects.create_user(
            "b1admin2", password="x", role=Role.BRANCH_ADMIN, organization_unit=self.b1)
        self.admin2 = User.objects.create_user(
            "b2admin2", password="x", role=Role.BRANCH_ADMIN, organization_unit=self.b2)

    def _import(self, unit):
        import uuid
        uploader = User.objects.create_user(
            f"u-{uuid.uuid4().hex[:12]}", password="x", role=Role.SUPER_ADMIN)
        return SalaryImport.objects.create(
            organization_unit=unit, period_year=2026, period_month=8, file_name="x.xlsx",
            uploaded_by=uploader)

    def test_both_branches_salary_survive_and_are_isolated(self):
        imp1 = self._import(self.b1)
        SalaryImportService._upsert_salary(
            salary_import=imp1, employee_id=self.emp.id,
            gross="5000000", advance="0", deductions="0", net="5000000")
        imp2 = self._import(self.b2)
        SalaryImportService._upsert_salary(
            salary_import=imp2, employee_id=self.emp.id,
            gross="3000000", advance="0", deductions="0", net="3000000")

        current = Salary.objects.filter(employee=self.emp, is_current=True)
        self.assertEqual(current.count(), 2)
        self.assertEqual(
            {s.organization_unit_id for s in current}, {self.b1.id, self.b2.id})

        # Each branch sees only its own contribution.
        b1_view = salaries_for(self.admin1).filter(employee=self.emp, is_current=True)
        self.assertEqual(list(b1_view.values_list("net_salary", flat=True)), [Decimal("5000000.00")])
        b2_view = salaries_for(self.admin2).filter(employee=self.emp, is_current=True)
        self.assertEqual(list(b2_view.values_list("net_salary", flat=True)), [Decimal("3000000.00")])

    def test_reimport_same_branch_revises_only_that_branchs_row(self):
        imp1 = self._import(self.b1)
        SalaryImportService._upsert_salary(
            salary_import=imp1, employee_id=self.emp.id,
            gross="5000000", advance="0", deductions="0", net="5000000")
        imp2 = self._import(self.b2)
        SalaryImportService._upsert_salary(
            salary_import=imp2, employee_id=self.emp.id,
            gross="3000000", advance="0", deductions="0", net="3000000")

        # Branch 1 re-imports a corrected figure for the same period.
        imp1b = self._import(self.b1)
        SalaryImportService._upsert_salary(
            salary_import=imp1b, employee_id=self.emp.id,
            gross="5500000", advance="0", deductions="0", net="5500000")

        current = Salary.objects.filter(employee=self.emp, is_current=True)
        self.assertEqual(current.count(), 2)  # still one per branch
        b1_current = current.get(organization_unit=self.b1)
        self.assertEqual(b1_current.net_salary, Decimal("5500000"))
        self.assertEqual(b1_current.revision, 2)
        b2_current = current.get(organization_unit=self.b2)
        self.assertEqual(b2_current.net_salary, Decimal("3000000"))  # untouched
        self.assertEqual(b2_current.revision, 1)


class NotificationIdempotencyTests(TestCase):
    def setUp(self):
        _, self.b1, _ = make_org()
        self.emp = Employee.objects.create(
            employee_code="E1", full_name="Ali", phone="998901234567",
            organization_unit=self.b1, telegram_id=555)
        self.emp2 = Employee.objects.create(
            employee_code="E2", full_name="Vali", phone="998907654321",
            organization_unit=self.b1)  # no telegram
        self.imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=8,
            file_name="x.xlsx",
            uploaded_by=User.objects.create_user("u", password="x", role=Role.SUPER_ADMIN))
        self.s1 = Salary.objects.create(
            employee=self.emp, organization_unit=self.b1, period_year=2026, period_month=8,
            net_salary=Decimal("5900000"), source_import=self.imp)
        self.s2 = Salary.objects.create(
            employee=self.emp2, organization_unit=self.b1, period_year=2026, period_month=8,
            net_salary=Decimal("5000000"), source_import=self.imp)

    def test_prepare_is_idempotent(self):
        SalaryNotificationService.prepare_for_import(self.imp)
        SalaryNotificationService.prepare_for_import(self.imp)  # twice
        self.assertEqual(TelegramMessage.objects.count(), 2)

    def test_unconnected_marked(self):
        SalaryNotificationService.prepare_for_import(self.imp)
        m2 = TelegramMessage.objects.get(employee=self.emp2)
        self.assertEqual(m2.status, MessageStatus.TELEGRAM_NOT_CONNECTED)
        # Only the connected employee is dispatchable.
        pending = SalaryNotificationService.pending_message_ids(self.imp)
        self.assertEqual(len(pending), 1)

    def test_send_task_never_calls_bot_api_twice_for_one_message(self):
        # Double-click "Send", or a resend racing an in-flight retry: both
        # invocations target the SAME message_id. The atomic claim (PENDING/
        # RETRYING -> SENDING) means only the first actually reaches the Bot
        # API; a second invocation for an already-claimed/sent row must be a
        # pure no-op, not a second real Telegram message.
        from unittest.mock import MagicMock, patch

        from apps.notifications.tasks import send_salary_message

        SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(employee=self.emp)
        self.assertEqual(msg.status, MessageStatus.PENDING)

        with patch("apps.notifications.tasks.settings.TELEGRAM_BOT_TOKEN", "test-token"), \
             patch("apps.notifications.tasks.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200, json=lambda: {"result": {"message_id": 1}})
            send_salary_message.run(msg.pk)
            send_salary_message.run(msg.pk)  # simulates the duplicate dispatch

        self.assertEqual(mock_post.call_count, 1)
        msg.refresh_from_db()
        self.assertEqual(msg.status, MessageStatus.SENT)


class JshshirGateTests(TestCase):
    """
    Phone-linked is no longer sufficient on its own: if the payroll row
    carried a JSHSHIR, it must match the employee's own on-file JSHSHIR or
    the message is held back (JSHSHIR_MISMATCH), never sent on a bare phone
    match. A row with no JSHSHIR at all keeps the old phone-only behavior.
    """
    def setUp(self):
        _, self.b1, _ = make_org()
        self.imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=8,
            file_name="x.xlsx",
            uploaded_by=User.objects.create_user("gate_u", password="x", role=Role.SUPER_ADMIN))

    def _salary(self, emp, payroll_jshshir=""):
        return Salary.objects.create(
            employee=emp, organization_unit=self.b1, period_year=2026, period_month=8,
            net_salary=Decimal("1000000"), source_import=self.imp,
            payroll_jshshir=payroll_jshshir)

    def test_no_jshshir_in_file_falls_back_to_phone_only(self):
        emp = Employee.objects.create(
            full_name="NoJshshirFile", phone="998901110011", organization_unit=self.b1,
            telegram_id=9001)  # no jshshir on file either
        salary = self._salary(emp, payroll_jshshir="")
        SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(salary=salary)
        self.assertEqual(msg.status, MessageStatus.PENDING)

    def test_matching_jshshir_is_pending(self):
        emp = Employee.objects.create(
            full_name="MatchJshshir", phone="998901110022", organization_unit=self.b1,
            telegram_id=9002, jshshir="30101234567890")
        salary = self._salary(emp, payroll_jshshir="30101234567890")
        SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(salary=salary)
        self.assertEqual(msg.status, MessageStatus.PENDING)

    def test_mismatched_jshshir_blocks_send(self):
        emp = Employee.objects.create(
            full_name="MismatchJshshir", phone="998901110033", organization_unit=self.b1,
            telegram_id=9003, jshshir="30101234567890")
        salary = self._salary(emp, payroll_jshshir="99999999999999")
        summary = SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(salary=salary)
        self.assertEqual(msg.status, MessageStatus.JSHSHIR_MISMATCH)
        self.assertEqual(summary["mismatched"], 1)
        self.assertEqual(SalaryNotificationService.pending_message_ids(self.imp), [])

    def test_file_jshshir_but_employee_has_none_on_file_blocks_send(self):
        emp = Employee.objects.create(
            full_name="NoJshshirOnRecord", phone="998901110044", organization_unit=self.b1,
            telegram_id=9004)  # never HR-onboarded with a JSHSHIR
        salary = self._salary(emp, payroll_jshshir="30101234567890")
        SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(salary=salary)
        self.assertEqual(msg.status, MessageStatus.JSHSHIR_MISMATCH)

    def test_not_telegram_linked_wins_over_jshshir(self):
        emp = Employee.objects.create(
            full_name="NotLinked", phone="998901110055", organization_unit=self.b1,
            jshshir="30101234567890")  # matching jshshir, but never pressed /start
        salary = self._salary(emp, payroll_jshshir="30101234567890")
        SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(salary=salary)
        self.assertEqual(msg.status, MessageStatus.TELEGRAM_NOT_CONNECTED)

    def test_resend_reapplies_the_gate_cannot_bypass_mismatch(self):
        emp = Employee.objects.create(
            full_name="ResendMismatch", phone="998901110066", organization_unit=self.b1,
            telegram_id=9006, jshshir="30101234567890")
        salary = self._salary(emp, payroll_jshshir="00000000000000")
        SalaryNotificationService.prepare_for_import(self.imp)
        msg = TelegramMessage.objects.get(salary=salary)
        self.assertEqual(msg.status, MessageStatus.JSHSHIR_MISMATCH)

        SalaryNotificationService.reset_for_resend(msg)
        msg.refresh_from_db()
        self.assertEqual(msg.status, MessageStatus.JSHSHIR_MISMATCH)  # still blocked

        # Fix the employee's on-file JSHSHIR to match, then resend succeeds.
        emp.jshshir = "00000000000000"
        emp.save(update_fields=["jshshir"])
        SalaryNotificationService.reset_for_resend(msg)
        msg.refresh_from_db()
        self.assertEqual(msg.status, MessageStatus.PENDING)


class ImportValidationTests(TestCase):
    def setUp(self):
        _, self.b1, self.b2 = make_org()
        self.emp = Employee.objects.create(
            employee_code="B1-E1", full_name="Ali", phone="998901110001",
            organization_unit=self.b1)
        self.other = Employee.objects.create(
            employee_code="B2-E1", full_name="Vali", phone="998902220001",
            organization_unit=self.b2)

    def _excel(self, rows):
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["employee_code", "phone", "full_name", "gross_salary",
                   "advance", "deductions", "net_salary"])
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_valid_and_error_rows(self):
        f = self._excel([
            ["B1-E1", "998901110001", "Ali", 8000000, 2000000, 100000, 5900000],  # valid, existing
            ["", "998900000000", "Nobody", 1000000, 0, 0, 1000000],  # unregistered phone -> error
            ["", "998900000001", "", 1000000, 0, 0, 1000000],  # unregistered phone, no name -> error
            ["B1-E1", "998901110001", "Ali", 8000000, 2000000, 100000, -5],  # negative + dup
        ])
        report = ExcelValidationService(organization_unit=self.b1).validate(f)
        self.assertIsNone(report.fatal_error)
        self.assertEqual(report.total_rows, 4)
        self.assertEqual(report.valid_rows, 1)
        self.assertEqual(report.error_rows, 3)
        # Employees are pre-registered — an unseen phone is never a new hire,
        # even when a name is present. It's a validation error instead.
        unregistered_row = report.rows[1]
        self.assertFalse(unregistered_row.is_valid)
        self.assertIsNone(unregistered_row.employee_id)
        self.assertTrue(any("ro'yxatda topilmadi" in e for e in unregistered_row.errors))

    def test_file_with_no_phone_column_matches_by_jshshir(self):
        # Real payroll exports often carry only JSHSHIR/ПИНФЛ, no phone at
        # all — phone lives in the HR registry, not in payroll. Such a file
        # must not be rejected outright, and rows must still match by
        # JSHSHIR alone.
        self.emp.jshshir = "30101234567890"
        self.emp.save(update_fields=["jshshir"])

        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["jshshir", "full_name", "gross_salary", "advance", "deductions", "net_salary"])
        ws.append(["30101234567890", "Ali", 8000000, 2000000, 100000, 5900000])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        report = ExcelValidationService(organization_unit=self.b1).validate(buf)
        self.assertIsNone(report.fatal_error)
        self.assertEqual(report.error_rows, 0)
        self.assertEqual(report.rows[0].employee_id, self.emp.id)

    def test_jshshir_only_row_unmatched_is_error_not_crash(self):
        f = self._excel([
            ["", "", "Notanish", 1000000, 0, 0, 1000000],  # no phone, no jshshir, has name
        ])
        report = ExcelValidationService(organization_unit=self.b1).validate(f)
        self.assertEqual(report.error_rows, 1)
        self.assertTrue(any(
            "telefon yoki JSHSHIR" in e for e in report.rows[0].errors))

    def test_phone_matches_globally_and_moves_employee_between_branches(self):
        # b2's employee ("Vali", 998902220001) shows up in b1's Excel instead
        # (e.g. they transferred, or this is simply who reports them now).
        # A phone is one global identity, not a per-branch one: this must
        # reuse the SAME Employee row and move it to b1, never duplicate it.
        f = self._excel([
            ["ignored-code", "998902220001", "Vali", 5000000, 0, 0, 5000000],
        ])
        report = ExcelValidationService(organization_unit=self.b1).validate(f)
        self.assertEqual(report.error_rows, 0)
        row = report.rows[0]
        self.assertEqual(row.employee_id, self.other.id)

        imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=8,
            file_name="x.xlsx",
            uploaded_by=User.objects.create_user("u3", password="x", role=Role.SUPER_ADMIN))
        imp.validation_payload = report.to_payload()
        imp.valid_rows, imp.error_rows = report.valid_rows, report.error_rows
        imp.status = ImportStatus.VALID
        imp.save()
        SalaryImportService.confirm_and_commit(imp, user=imp.uploaded_by)

        self.assertEqual(
            Employee.objects.filter(normalized_phone="998902220001").count(), 1)
        self.other.refresh_from_db()
        self.assertEqual(self.other.organization_unit_id, self.b1.id)
        self.assertTrue(Salary.objects.filter(employee=self.other, net_salary=5000000).exists())

    def test_unmatched_phone_is_error_and_never_creates_employee(self):
        # Even if this phone already pressed /start on the bot, it must not
        # be auto-created as an Employee from a payroll row — employees only
        # enter the system via the HR registry / Employee CRUD now.
        TelegramContact.objects.create(
            normalized_phone="998900000099", telegram_id=777777, telegram_username="newguy")
        f = self._excel([
            ["", "998900000099", "Yangi Xodim", 3000000, 0, 0, 3000000],
        ])
        report = ExcelValidationService(organization_unit=self.b1).validate(f)
        row = report.rows[0]
        self.assertFalse(row.is_valid)
        self.assertIsNone(row.employee_id)
        self.assertTrue(any("ro'yxatda topilmadi" in e for e in row.errors))

        imp = SalaryImport.objects.create(
            organization_unit=self.b1, period_year=2026, period_month=8,
            file_name="x.xlsx",
            uploaded_by=User.objects.create_user("u2", password="x", role=Role.SUPER_ADMIN))
        imp.validation_payload = report.to_payload()
        imp.valid_rows, imp.error_rows = report.valid_rows, report.error_rows
        imp.status = ImportStatus.HAS_ERRORS
        imp.save()

        SalaryImportService.confirm_and_commit(imp, user=imp.uploaded_by)

        self.assertFalse(Employee.objects.filter(normalized_phone="998900000099").exists())
        self.assertFalse(Salary.objects.filter(net_salary=3000000).exists())
