# Generated manually for django-esi 9 / Alliance Auth v5 compatibility.
# Compat-only: aligns choice labels in `corporationsharingsetting` with the
# current model definition. No DDL is emitted because Django ChoiceField
# choices are state-only metadata.
#
# This migration is intentionally minimal so it remains a no-op on databases
# regardless of Django 4.2 (AA v4) or Django 5.2 (AA v5).

# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0097_remove_industry_structure_partial_unique_constraints"),
    ]

    operations = [
        migrations.AlterField(
            model_name="corporationsharingsetting",
            name="blueprint_catalog_scope",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("corporation", "Corporation"),
                    ("alliance", "Alliance"),
                    ("everyone", "Everyone"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="corporationsharingsetting",
            name="job_catalog_scope",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("corporation", "Corporation"),
                    ("alliance", "Alliance"),
                    ("everyone", "Everyone"),
                ],
                default="none",
                max_length=20,
            ),
        ),
    ]
