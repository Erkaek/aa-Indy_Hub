# Django
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0058_cached_corp_assets"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CachedCharacterAsset",
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
                ("character_id", models.BigIntegerField(db_index=True)),
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
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                        db_index=True,
                    ),
                ),
            ],
            options={
                "verbose_name": "Cached Character Asset",
                "verbose_name_plural": "Cached Character Assets",
            },
        ),
        migrations.AddIndex(
            model_name="cachedcharacterasset",
            index=models.Index(
                fields=["user", "character_id"], name="cca_user_char_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="cachedcharacterasset",
            index=models.Index(fields=["user", "location_id"], name="cca_user_loc_idx"),
        ),
        migrations.AddIndex(
            model_name="cachedcharacterasset",
            index=models.Index(fields=["user", "type_id"], name="cca_user_type_idx"),
        ),
    ]
