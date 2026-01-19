# Generated manually

# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0068_materialexchangeconfig_enforce_jita_price_bounds"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="allowed_market_groups_buy",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of market group IDs allowed for buying. Empty = all industry market groups allowed.",
            ),
        ),
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="allowed_market_groups_sell",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of market group IDs allowed for selling. Empty = all industry market groups allowed.",
            ),
        ),
    ]
