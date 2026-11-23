# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("indy_hub", "0118_industrydataqualification")]

    operations = [
        migrations.CreateModel(
            name="StaticLocationName",
            fields=[
                (
                    "location_id",
                    models.PositiveBigIntegerField(primary_key=True, serialize=False),
                ),
                ("name", models.CharField(max_length=250)),
                ("release", models.CharField(max_length=32)),
            ],
            options={"default_permissions": ()},
        ),
    ]
