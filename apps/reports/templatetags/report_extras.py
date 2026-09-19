from django import template

from apps.common.money import format_money

register = template.Library()


@register.filter(name="money")
def money(value):
    """Space-grouped thousands, whole so'm — also collapses the excess
    decimal precision a Sum() aggregate can produce on a DecimalField
    (e.g. "944645.880000000" -> "944 646")."""
    return format_money(value)
