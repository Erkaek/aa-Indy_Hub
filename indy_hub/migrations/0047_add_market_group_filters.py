# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0046_refactor_material_exchange_to_order_items"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="allowed_market_groups_buy",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of market group IDs allowed for buying. Empty = all stock items allowed.",
            ),
        ),
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="allowed_market_groups_sell",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of market group IDs allowed for selling. Empty = all production materials allowed.",
            ),
        ),
    ]
