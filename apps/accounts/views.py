"""Auth + admin-user management views."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_role_check
from apps.accounts.forms import AdminUserForm, StyledAuthenticationForm
from apps.accounts.models import User
from apps.audit.middleware import get_client_ip
from apps.audit.models import AuditAction
from apps.audit.services import AuditService


def _login_throttle_key(request) -> str:
    return f"login_throttle:{get_client_ip(request) or 'unknown'}"


class AppLoginView(LoginView):
    """
    Login view with a per-IP failed-attempt limit.

    REST_FRAMEWORK's "login" throttle scope only covers DRF endpoints — this
    is a plain Django view, so brute-forcing the password field here needs
    its own limiter. Counter lives in the shared cache (Redis in prod) so it
    holds across all gunicorn workers, not just the one that handled a given
    request.
    """
    template_name = "registration/login.html"
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            attempts = cache.get(_login_throttle_key(request), 0)
            if attempts >= settings.LOGIN_RATE_LIMIT_ATTEMPTS:
                messages.error(
                    request,
                    "Juda ko'p urinish. Iltimos, bir necha daqiqadan so'ng qayta urining.",
                )
                return render(request, self.template_name,
                             {"form": self.authentication_form()})
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        key = _login_throttle_key(self.request)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_RATE_LIMIT_WINDOW)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_login_throttle_key(self.request))
        return super().form_valid(form)


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@login_required
@require_role_check("can_manage_admins")
def admin_list(request):
    users = User.objects.select_related("organization_unit").order_by("username")
    return render(request, "accounts/admin_list.html", {"users": users})


@login_required
@require_role_check("can_manage_admins")
def admin_create(request):
    if request.method == "POST":
        form = AdminUserForm(request.POST)
        if form.is_valid():
            user = form.save()
            AuditService.log(AuditAction.ADMIN_CREATED, obj=user,
                             metadata={"role": user.role})
            messages.success(request, "Administrator yaratildi.")
            return redirect("accounts:admin_list")
    else:
        form = AdminUserForm()
    return render(request, "accounts/admin_form.html", {"form": form, "is_new": True})


@login_required
@require_role_check("can_manage_admins")
def admin_edit(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    old_role = user.role
    if request.method == "POST":
        form = AdminUserForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save()
            if old_role != user.role:
                AuditService.log(AuditAction.PERMISSION_CHANGED, obj=user,
                                 metadata={"from": old_role, "to": user.role})
            messages.success(request, "O'zgartirildi.")
            return redirect("accounts:admin_list")
    else:
        form = AdminUserForm(instance=user)
    return render(request, "accounts/admin_form.html", {"form": form, "is_new": False})
