# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0048_remove_market_group_filters"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialexchangesellorder",
            name="order_reference",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Unique order reference (INDY-{id}) for contract matching",
                max_length=50,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="materialexchangebuyorder",
            name="order_reference",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Unique order reference (INDY-{id}) for contract matching",
                max_length=50,
                unique=True,
            ),
        ),
    ]
