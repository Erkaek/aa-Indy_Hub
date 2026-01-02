# Django
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "indy_hub",
            "0057_rename_indy_hub_me_buy_esi_con_idx_indy_hub_ma_esi_con_f75c4b_idx_and_more",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="CachedCorporationAsset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("corporation_id", models.BigIntegerField(db_index=True)),
                ("location_id", models.BigIntegerField(db_index=True)),
                (
                    "location_flag",
                    models.CharField(blank=True, db_index=True, max_length=50),
                ),
                ("type_id", models.BigIntegerField(db_index=True)),
                ("quantity", models.BigIntegerField(default=0)),
                ("is_singleton", models.BooleanField(default=False)),
                ("is_blueprint", models.BooleanField(default=False)),
                (
                    "synced_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
            ],
            options={
                "verbose_name": "Cached Corporation Asset",
                "verbose_name_plural": "Cached Corporation Assets",
                "indexes": [
                    models.Index(
                        fields=["corporation_id", "location_id"],
                        name="cca_corp_loc_idx",
                    ),
                    models.Index(
                        fields=["corporation_id", "type_id"], name="cca_corp_type_idx"
                    ),
                    models.Index(
                        fields=["corporation_id", "location_flag"],
                        name="cca_corp_flag_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CachedCorporationDivision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("corporation_id", models.BigIntegerField(db_index=True)),
                ("division", models.IntegerField()),
                ("name", models.CharField(max_length=255)),
                (
                    "synced_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["corporation_id", "division"], name="ccd_corp_div_idx"
                    ),
                ],
                "unique_together": {("corporation_id", "division")},
            },
        ),
        migrations.CreateModel(
            name="CachedStructureName",
            fields=[
                (
                    "structure_id",
                    models.BigIntegerField(primary_key=True, serialize=False),
                ),
                ("name", models.CharField(max_length=255)),
                (
                    "last_resolved",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
            ],
            options={
                "verbose_name": "Cached Structure Name",
                "verbose_name_plural": "Cached Structure Names",
            },
        ),
    ]
