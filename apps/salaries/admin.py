from django.contrib import admin

from apps.accounts.permissions import scope_salaries
from apps.salaries.models import Salary


@admin.register(Salary)
class SalaryAdmin(admin.ModelAdmin):
    list_display = ("employee", "organization_unit", "period_year", "period_month",
                    "net_salary", "is_current", "revision")
    list_filter = ("organization_unit", "period_year", "period_month", "is_current")

    def get_queryset(self, request):
        return scope_salaries(super().get_queryset(request), request.user)
