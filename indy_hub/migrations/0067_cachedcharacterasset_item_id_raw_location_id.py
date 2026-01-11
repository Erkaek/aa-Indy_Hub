from __future__ import annotations

# Django
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0066_alter_jobnotificationdigestentry_corporation_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cachedcharacterasset",
            name="item_id",
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="cachedcharacterasset",
            name="raw_location_id",
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="cachedcharacterasset",
            index=models.Index(fields=["user", "item_id"], name="cca_user_item_idx"),
        ),
    ]
