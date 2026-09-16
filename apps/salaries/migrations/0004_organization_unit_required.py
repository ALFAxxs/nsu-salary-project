import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("salaries", "0003_backfill_organization_unit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="salary",
            name="organization_unit",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="salaries",
                to="organizations.organizationunit",
                verbose_name="organization unit",
            ),
        ),
    ]
