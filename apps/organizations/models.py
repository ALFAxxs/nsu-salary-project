"""
Organization structure.

A single OrganizationUnit model represents both the head office and every branch
(spec §6). The head office is not special-cased by code — it is just a unit with
type=HEAD_OFFICE. This keeps the design scalable from 15 to 100+ branches with no
hard-coded IDs.
"""
from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class UnitType(models.TextChoices):
    HEAD_OFFICE = "HEAD_OFFICE", _("Head Office")
    BRANCH = "BRANCH", _("Branch")


class OrganizationUnit(models.Model):
    name = models.CharField(_("name"), max_length=255)
    # Auto-generated from the name (see save()) — not part of the create/edit
    # form. It's just a short display label now, never used to identify or
    # match anything (that's the phone number, entirely elsewhere).
    code = models.CharField(_("code"), max_length=32, unique=True, blank=True)
    type = models.CharField(
        _("type"), max_length=16, choices=UnitType.choices, default=UnitType.BRANCH
    )
    is_head_office = models.BooleanField(_("is head office"), default=False)
    is_active = models.BooleanField(_("is active"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("organization unit")
        verbose_name_plural = _("organization units")
        ordering = ["-is_head_office", "code"]
        constraints = [
            # There must be at most one head office in the whole system.
            models.UniqueConstraint(
                fields=["is_head_office"],
                condition=models.Q(is_head_office=True),
                name="unique_head_office",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def clean(self) -> None:
        # Keep type and the is_head_office flag consistent.
        if self.type == UnitType.HEAD_OFFICE and not self.is_head_office:
            raise ValidationError({"is_head_office": _("A HEAD_OFFICE unit must set is_head_office=True.")})
        if self.type == UnitType.BRANCH and self.is_head_office:
            raise ValidationError({"is_head_office": _("A BRANCH unit cannot be the head office.")})

    def save(self, *args, **kwargs):
        # Auto-generate the code from the name if one wasn't set; otherwise
        # just normalize it. Keep type/is_head_office flags coherent.
        self.code = self.code.strip().upper() if self.code else self._generate_code()
        if self.is_head_office:
            self.type = UnitType.HEAD_OFFICE
        elif self.type == UnitType.BRANCH:
            self.is_head_office = False
        super().save(*args, **kwargs)

    def _generate_code(self) -> str:
        base = re.sub(r"[^A-Z0-9]+", "-", (self.name or "").upper()).strip("-")[:28] or "UNIT"
        qs = OrganizationUnit.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        candidate, n = base, 1
        while qs.filter(code=candidate).exists():
            n += 1
            candidate = f"{base}-{n}"
        return candidate
