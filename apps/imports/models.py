"""
Salary import tracking (spec §16).

Each uploaded Excel is one SalaryImport row. It moves through a status lifecycle
and stores validation counts so the dashboard and history views can render
without recomputation.

A ColumnMapping lets each branch use its own Excel headers (spec §12) mapped to
canonical fields.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ImportStatus(models.TextChoices):
    UPLOADED = "UPLOADED", _("Uploaded")
    VALIDATING = "VALIDATING", _("Validating")
    VALID = "VALID", _("Valid")
    HAS_ERRORS = "HAS_ERRORS", _("Has errors")
    CONFIRMED = "CONFIRMED", _("Confirmed")
    PROCESSING = "PROCESSING", _("Processing")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")
    CANCELLED = "CANCELLED", _("Cancelled")


class SalaryImport(models.Model):
    # CASCADE: deleting a branch (apps.organizations.views.unit_delete) is a
    # deliberate full wipe — its import history goes with it.
    organization_unit = models.ForeignKey(
        "organizations.OrganizationUnit",
        on_delete=models.CASCADE,
        related_name="imports",
        verbose_name=_("organization unit"),
    )
    period_year = models.PositiveSmallIntegerField(_("period year"))
    period_month = models.PositiveSmallIntegerField(_("period month"))

    file = models.FileField(_("file"), upload_to="imports/%Y/%m/")
    file_name = models.CharField(_("file name"), max_length=255)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_imports",
        verbose_name=_("uploaded by"),
    )
    uploaded_at = models.DateTimeField(_("uploaded at"), auto_now_add=True)
    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)

    status = models.CharField(
        _("status"), max_length=16, choices=ImportStatus.choices,
        default=ImportStatus.UPLOADED,
    )

    total_rows = models.PositiveIntegerField(_("total rows"), default=0)
    valid_rows = models.PositiveIntegerField(_("valid rows"), default=0)
    error_rows = models.PositiveIntegerField(_("error rows"), default=0)
    telegram_linked_count = models.PositiveIntegerField(default=0)
    telegram_unlinked_count = models.PositiveIntegerField(default=0)

    # Cached validation result (rows + errors) so preview survives page reloads
    # without re-parsing the file. Stored only until confirmation.
    validation_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = _("salary import")
        verbose_name_plural = _("salary imports")
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["organization_unit", "period_year", "period_month"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"Import #{self.pk} {self.organization_unit_id} {self.period_year}-{self.period_month:02d}"

    @property
    def period_label(self) -> str:
        months = [
            "", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
            "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
        ]
        return f"{months[self.period_month]} {self.period_year}"


class ColumnMapping(models.Model):
    """
    Per-unit Excel header mapping (spec §12).

    Maps a canonical field name -> the header text used in that unit's Excel.
    If no mapping exists for a unit, a sensible default header set is used.
    """
    organization_unit = models.OneToOneField(
        "organizations.OrganizationUnit",
        on_delete=models.CASCADE,
        related_name="column_mapping",
    )
    # e.g. {"full_name": "F.I.Sh", "phone": "Telefon raqam", ...}
    mapping = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"ColumnMapping({self.organization_unit_id})"
