from django import forms

from apps.organizations.models import OrganizationUnit, UnitType


class OrganizationUnitForm(forms.ModelForm):
    class Meta:
        model = OrganizationUnit
        # code is intentionally excluded — auto-generated from name (see
        # OrganizationUnit.save()), never a manual input.
        fields = ["name", "type", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "type": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("type") == UnitType.HEAD_OFFICE:
            self.instance.is_head_office = True
        else:
            self.instance.is_head_office = False
        return cleaned
