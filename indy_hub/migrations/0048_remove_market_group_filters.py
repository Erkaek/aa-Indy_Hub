# Django
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0047_add_market_group_filters"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="materialexchangeconfig",
            name="allowed_market_groups_sell",
        ),
        migrations.RemoveField(
            model_name="materialexchangeconfig",
            name="allowed_market_groups_buy",
        ),
    ]
