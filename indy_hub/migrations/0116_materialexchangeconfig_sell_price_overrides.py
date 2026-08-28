# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0115_reconcile_periodic_tasks"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="sell_price_overrides",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Fixed per-unit prices used when members sell configured item types to the hub.",
            ),
        ),
    ]
