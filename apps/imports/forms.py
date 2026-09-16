"""Excel upload form with MIME / extension / size validation (spec §33)."""
from __future__ import annotations

import os

from django import forms
from django.conf import settings
from django.utils import timezone

from apps.organizations.models import OrganizationUnit

MONTH_CHOICES = [
    (1, "Yanvar"), (2, "Fevral"), (3, "Mart"), (4, "Aprel"),
    (5, "May"), (6, "Iyun"), (7, "Iyul"), (8, "Avgust"),
    (9, "Sentabr"), (10, "Oktabr"), (11, "Noyabr"), (12, "Dekabr"),
]


def _year_choices():
    # A fixed, pickable range rather than free text — recent years first,
    # one year ahead included for periods reported just before month-end.
    current = timezone.now().year
    return [(y, str(y)) for y in range(current + 1, current - 5, -1)]


class ImportUploadForm(forms.Form):
    organization_unit = forms.ModelChoiceField(
        queryset=OrganizationUnit.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Filial",
    )
    period_year = forms.TypedChoiceField(
        choices=_year_choices, coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}), label="Yil",
    )
    period_month = forms.TypedChoiceField(
        choices=MONTH_CHOICES, coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}), label="Oy",
    )
    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".xlsx,.xls"}),
        label="Excel fayl",
    )

    def __init__(self, *args, allowed_units=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_units is not None:
            self.fields["organization_unit"].queryset = allowed_units

    def clean_file(self):
        f = self.cleaned_data["file"]
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
            raise forms.ValidationError("Faqat .xlsx yoki .xls fayllar qabul qilinadi.")
        if f.size > settings.MAX_UPLOAD_SIZE:
            mb = settings.MAX_UPLOAD_SIZE // (1024 * 1024)
            raise forms.ValidationError(f"Fayl hajmi {mb} MB dan oshmasligi kerak.")
        content_type = getattr(f, "content_type", "")
        if content_type and content_type not in settings.ALLOWED_UPLOAD_MIME_TYPES:
            raise forms.ValidationError("Fayl turi noto'g'ri.")
        return f
