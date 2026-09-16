"""
Employee — a salary recipient (spec §7).

Distinct from accounts.User: employees do not log into the web panel. They
interact only through the Telegram bot, and are identified by ONE thing only:
their normalized phone number — the Excel <-> Telegram join key, and the
sole global identity of a person in this system. employee_code is purely a
descriptive label some branches may put in their Excel; it is never used to
look anyone up. organization_unit reflects whichever branch most recently
reported salary for this phone — it is not part of the person's identity,
so the same phone showing up under a different branch's Excel simply moves
them there (see SalaryImportService.confirm_and_commit).
"""
from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.phone import normalize_phone


class Gender(models.TextChoices):
    MALE = "M", _("Erkak")
    FEMALE = "F", _("Ayol")


class Employee(models.Model):
    # Descriptive only — never unique, never used to identify anyone.
    employee_code = models.CharField(_("employee code"), max_length=64, blank=True)
    full_name = models.CharField(_("full name"), max_length=255)
    phone = models.CharField(_("phone"), max_length=32)
    # Canonical digits-only phone — globally unique: the ONE identity key
    # tying Excel rows, Telegram accounts and salary history together.
    normalized_phone = models.CharField(
        _("normalized phone"), max_length=20, unique=True, db_index=True
    )

    # HR registry fields (spec: employee onboarding record, distinct from
    # payroll). jshshir is a second identity key alongside phone — used by
    # payroll-import matching (apps.imports.validators) and by the bot's
    # two-factor /start verification (apps.employees.services.link_telegram)
    # — kept globally unique like phone, but optional: most employees are
    # entered without it until HR backfills the full registry.
    jshshir = models.CharField(
        _("JSHSHIR"), max_length=14, unique=True, null=True, blank=True
    )
    passport_number = models.CharField(_("passport seria va raqami"), max_length=20, blank=True)
    birth_date = models.DateField(_("tug'ilgan sana"), null=True, blank=True)
    gender = models.CharField(_("jinsi"), max_length=1, choices=Gender.choices, blank=True)
    department = models.CharField(_("bo'lim"), max_length=255, blank=True)
    position = models.CharField(_("lavozim"), max_length=255, blank=True)
    contract_type = models.CharField(_("shartnoma turi"), max_length=50, blank=True)
    # Whichever branch most recently uploaded a payroll Excel naming this
    # phone. Purely informational/current-scope, not part of identity.
    # CASCADE: deleting a branch is a deliberate "wipe everything about it"
    # action (apps.organizations.views.unit_delete) — its employees, and
    # their salaries/messages (already CASCADE below), go with it.
    organization_unit = models.ForeignKey(
        "organizations.OrganizationUnit",
        on_delete=models.CASCADE,
        related_name="employees",
        verbose_name=_("organization unit"),
    )

    # Telegram linkage (spec §8, §9). telegram_id is unique when present.
    telegram_id = models.BigIntegerField(
        _("telegram id"), null=True, blank=True, unique=True
    )
    telegram_username = models.CharField(
        _("telegram username"), max_length=255, blank=True
    )
    telegram_linked_at = models.DateTimeField(
        _("telegram linked at"), null=True, blank=True
    )

    is_active = models.BooleanField(_("is active"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("employee")
        verbose_name_plural = _("employees")
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["normalized_phone"]),
            models.Index(fields=["organization_unit"]),
            models.Index(fields=["telegram_id"]),
            models.Index(fields=["employee_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} [{self.employee_code}]" if self.employee_code else self.full_name

    def save(self, *args, **kwargs):
        self.normalized_phone = normalize_phone(self.phone)
        # Blank -> NULL, not "" — CharField(unique=True) would otherwise let
        # only one employee ever have an empty JSHSHIR.
        self.jshshir = self.jshshir.strip() if self.jshshir and self.jshshir.strip() else None
        super().save(*args, **kwargs)

    @property
    def is_telegram_linked(self) -> bool:
        return self.telegram_id is not None


class TelegramContact(models.Model):
    """
    Anyone who has ever pressed /start on the bot and shared their contact —
    independent of whether they are a known (HR-registered) Employee yet.

    The bot's /start flow requires BOTH phone (via contact-share) AND
    JSHSHIR (typed, 14 digits) before it will actually link telegram_id to
    an Employee — see EmployeeRegistrationService.link_telegram. This table
    is the bridge for the case where the phone/JSHSHIR were entered before
    a matching Employee existed yet: when HR later creates that Employee,
    try_auto_link_from_contact() re-checks the SAME two-factor match against
    what's stored here and links immediately if it still holds, without
    requiring the person to go through the bot a second time. A row with no
    matching Employee never gets anything sent to it — this table alone
    never triggers a message.
    """
    normalized_phone = models.CharField(
        _("normalized phone"), max_length=20, unique=True, db_index=True
    )
    telegram_id = models.BigIntegerField(_("telegram id"), unique=True)
    telegram_username = models.CharField(_("telegram username"), max_length=255, blank=True)
    # Entered by the person during /start. Blank only for contacts recorded
    # before this requirement existed — such rows can never auto-link
    # (see try_auto_link_from_contact), since there's nothing to verify.
    jshshir = models.CharField(_("JSHSHIR"), max_length=14, blank=True, default="")
    first_seen_at = models.DateTimeField(_("first seen at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("telegram contact")
        verbose_name_plural = _("telegram contacts")

    def __str__(self) -> str:
        return f"TelegramContact({self.normalized_phone} -> {self.telegram_id})"
