# Django
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0116_materialexchangeconfig_sell_price_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="charactersettings",
            name="market_broker_fee_percent",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Last broker fee percentage entered in the Craft workspace.",
                max_digits=5,
                null=True,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
            ),
        ),
    ]
