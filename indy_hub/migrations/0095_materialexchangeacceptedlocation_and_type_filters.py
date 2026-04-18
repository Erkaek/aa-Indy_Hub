# Django
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def seed_accepted_locations(apps, schema_editor):
    MaterialExchangeConfig = apps.get_model("indy_hub", "MaterialExchangeConfig")
    MaterialExchangeAcceptedLocation = apps.get_model(
        "indy_hub", "MaterialExchangeAcceptedLocation"
    )

    for config in MaterialExchangeConfig.objects.all().iterator():
        structure_id = getattr(config, "structure_id", None)
        hangar_division = getattr(config, "hangar_division", None)
        if not structure_id or not hangar_division:
            continue
        MaterialExchangeAcceptedLocation.objects.get_or_create(
            config_id=config.pk,
            structure_id=structure_id,
            hangar_division=hangar_division,
            defaults={
                "structure_name": getattr(config, "structure_name", "") or "",
                "sort_order": 0,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0094_sync_material_exchange_transaction_totals"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="allowed_type_ids_buy",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of specific type IDs explicitly allowed for buying. Combined with market groups.",
            ),
        ),
        migrations.AddField(
            model_name="materialexchangeconfig",
            name="allowed_type_ids_sell",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of specific type IDs explicitly allowed for selling. Combined with market groups.",
            ),
        ),
        migrations.CreateModel(
            name="MaterialExchangeAcceptedLocation",
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
                (
                    "structure_id",
                    models.BigIntegerField(
                        help_text="Accepted structure or station ID for the hub"
                    ),
                ),
                ("structure_name", models.CharField(blank=True, max_length=255)),
                (
                    "hangar_division",
                    models.IntegerField(
                        default=1,
                        help_text="Corp hangar division (1-7) for this accepted location",
                        validators=[
                            MinValueValidator(1),
                            MaxValueValidator(7),
                        ],
                    ),
                ),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "config",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="accepted_locations",
                        to="indy_hub.materialexchangeconfig",
                    ),
                ),
            ],
            options={
                "verbose_name": "Material Exchange Accepted Location",
                "verbose_name_plural": "Material Exchange Accepted Locations",
                "default_permissions": (),
                "ordering": ["sort_order", "id"],
                "unique_together": {("config", "structure_id", "hangar_division")},
            },
        ),
        migrations.RunPython(seed_accepted_locations, migrations.RunPython.noop),
    ]
