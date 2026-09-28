"""Forms for auth + admin user management."""
from __future__ import annotations

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from apps.accounts.models import Role, User


class StyledAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.update({"class": "form-control"})


class AdminUserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        required=False,
        help_text="Yangi foydalanuvchi uchun majburiy. Tahrirda bo'sh qoldirsangiz o'zgarmaydi.",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone",
                  "role", "organization_units", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "role": forms.Select(attrs={"class": "form-select"}),
            "organization_units": forms.SelectMultiple(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_password(self):
        # AUTH_PASSWORD_VALIDATORS (min length, common-password check, ...)
        # is never applied automatically here — set_password() bypasses it,
        # unlike Django's own UserCreationForm — so it must be called
        # explicitly or the policy in settings.py is enforced nowhere.
        pwd = self.cleaned_data.get("password")
        if pwd:
            password_validation.validate_password(pwd, user=self.instance)
        return pwd

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        units = cleaned.get("organization_units")
        # Branch-scoped roles require at least one unit (one accountant/HR
        # person can cover several small branches, so more than one is fine).
        if role in {Role.BRANCH_ADMIN, Role.ACCOUNTANT, Role.HR} and not units:
            self.add_error("organization_units", "Bu rol uchun kamida bitta filial tanlanishi shart.")
        if not self.instance.pk and not cleaned.get("password"):
            self.add_error("password", "Yangi foydalanuvchi uchun parol majburiy.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        pwd = self.cleaned_data.get("password")
        if pwd:
            user.set_password(pwd)
        if commit:
            user.save()
            self.save_m2m()  # persists the organization_units selection
        return user
