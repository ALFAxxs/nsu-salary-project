"""Backfill Salary.organization_unit for rows created before the field existed.

Preference order per row: the unit that actually uploaded it
(source_import.organization_unit), falling back to the employee's current
unit only if the source import is gone.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Salary = apps.get_model("salaries", "Salary")
    for salary in Salary.objects.filter(organization_unit__isnull=True).select_related(
        "source_import", "employee"
    ):
        if salary.source_import_id and salary.source_import.organization_unit_id:
            salary.organization_unit_id = salary.source_import.organization_unit_id
        else:
            salary.organization_unit_id = salary.employee.organization_unit_id
        salary.save(update_fields=["organization_unit"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("salaries", "0002_add_organization_unit_nullable"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
