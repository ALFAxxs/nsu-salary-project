"""Manual employee create/edit form (spec: HR registry, scoped per branch)."""
from __future__ import annotations

from django import forms

from apps.common.jshshir import is_valid_jshshir, normalize_jshshir
from apps.employees.models import Employee, Gender


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "organization_unit", "full_name", "phone", "employee_code",
            "jshshir", "passport_number", "birth_date", "gender",
            "department", "position", "contract_type", "is_active",
        ]
        widgets = {
            "organization_unit": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "998901234567"}),
            "employee_code": forms.TextInput(attrs={"class": "form-control"}),
            "jshshir": forms.TextInput(attrs={"class": "form-control", "maxlength": 14}),
            "passport_number": forms.TextInput(attrs={"class": "form-control"}),
            # A native <input type="date"> requires its value in ISO
            # (yyyy-mm-dd) regardless of locale — Django's default widget
            # formatting follows DATE_INPUT_FORMATS (d.m.Y for uz), which the
            # browser then can't parse, so an existing birth_date silently
            # failed to show when editing. The browser still displays/lets
            # the user pick the date in their own locale (13.11.2020 etc.)
            # via its native picker; only the underlying value is ISO.
            "birth_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
            "gender": forms.Select(attrs={"class": "form-select"}, choices=[("", "—")] + list(Gender.choices)),
            "department": forms.TextInput(attrs={"class": "form-control"}),
            "position": forms.TextInput(attrs={"class": "form-control"}),
            "contract_type": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, allowed_units=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_units is not None:
            self.fields["organization_unit"].queryset = allowed_units
        self.fields["phone"].required = True
        self.fields["jshshir"].required = False

    def clean_jshshir(self):
        raw = self.cleaned_data.get("jshshir") or ""
        value = normalize_jshshir(raw)
        if not value:
            return ""
        if not is_valid_jshshir(value):
            raise forms.ValidationError("JSHSHIR aynan 14 ta raqamdan iborat bo'lishi kerak.")
        return value

    def clean_phone(self):
        from apps.common.phone import is_valid_uz_phone, normalize_phone

        raw = (self.cleaned_data.get("phone") or "").strip()
        normalized = normalize_phone(raw)
        if not is_valid_uz_phone(normalized):
            raise forms.ValidationError("Telefon raqami noto'g'ri formatda.")
        # normalized_phone (the actual unique field) is computed in
        # Employee.save() and excluded from this form, so Django's own
        # uniqueness check never sees it — check it here instead, or a
        # duplicate phone would only surface as a raw IntegrityError on save.
        clash = Employee.objects.filter(normalized_phone=normalized)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Bu telefon raqami boshqa xodimda allaqachon mavjud.")
        return raw
