# Standard Library
import logging
from decimal import Decimal

# Django
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

logger = logging.getLogger(__name__)


def populate_industry_system_cost_indices(apps, schema_editor):
    try:
        # Django
        from django.conf import settings

        # AA Example App
        from indy_hub.tasks.system_cost_indices import sync_industry_system_cost_indices
    except Exception:
        sync_industry_system_cost_indices = None
        settings = None

    eager = (
        bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
        if settings
        else False
    )
    can_enqueue = sync_industry_system_cost_indices is not None and not eager

    if can_enqueue:
        try:
            result = sync_industry_system_cost_indices.delay(force_refresh=True)
        except Exception:
            logger.exception(
                "Unable to enqueue industry system cost index sync during migration; falling back to synchronous execution."
            )
        else:
            logger.info(
                "sync_industry_system_cost_indices enqueued during migration (task id: %s)",
                getattr(result, "id", "<unknown>"),
            )
            return

    # AA Example App
    from indy_hub.services.system_cost_indices import sync_system_cost_indices

    summary = sync_system_cost_indices(force_refresh=True)
    logger.info(
        "Industry system cost indices synced inline during migration: systems=%s entries=%s created=%s updated=%s unchanged=%s",
        summary.get("systems", 0),
        summary.get("entries_seen", 0),
        summary.get("created", 0),
        summary.get("updated", 0),
        summary.get("unchanged", 0),
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("indy_hub", "0088_sdesynccompatstate"),
    ]

    operations = [
        migrations.CreateModel(
            name="IndustryStructure",
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
                ("name", models.CharField(max_length=255)),
                ("personal_tag", models.CharField(blank=True, max_length=80)),
                ("structure_type_id", models.BigIntegerField(blank=True, null=True)),
                ("structure_type_name", models.CharField(blank=True, max_length=255)),
                ("solar_system_id", models.BigIntegerField(blank=True, null=True)),
                ("solar_system_name", models.CharField(blank=True, max_length=255)),
                (
                    "system_security_band",
                    models.CharField(
                        choices=[
                            ("highsec", "Highsec"),
                            ("lowsec", "Lowsec"),
                            ("nullsec", "Nullsec / Wormhole"),
                        ],
                        default="highsec",
                        max_length=16,
                    ),
                ),
                (
                    "external_structure_id",
                    models.BigIntegerField(blank=True, null=True),
                ),
                ("owner_corporation_id", models.BigIntegerField(blank=True, null=True)),
                (
                    "owner_corporation_name",
                    models.CharField(blank=True, max_length=255),
                ),
                (
                    "sync_source",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("esi_corporation", "Corporation ESI"),
                        ],
                        default="manual",
                        max_length=32,
                    ),
                ),
                (
                    "visibility_scope",
                    models.CharField(
                        choices=[("public", "Shared"), ("personal", "Personal Copy")],
                        default="public",
                        max_length=16,
                    ),
                ),
                ("enable_manufacturing", models.BooleanField(default=True)),
                ("enable_manufacturing_capitals", models.BooleanField(default=False)),
                (
                    "enable_manufacturing_super_capitals",
                    models.BooleanField(default=False),
                ),
                ("enable_research", models.BooleanField(default=True)),
                ("enable_invention", models.BooleanField(default=True)),
                ("enable_biochemical_reactions", models.BooleanField(default=False)),
                ("enable_hybrid_reactions", models.BooleanField(default=False)),
                ("enable_composite_reactions", models.BooleanField(default=False)),
                (
                    "manufacturing_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "manufacturing_capitals_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "manufacturing_super_capitals_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "research_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "invention_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "biochemical_reactions_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "hybrid_reactions_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                (
                    "composite_reactions_tax_percent",
                    models.DecimalField(
                        decimal_places=3,
                        default=Decimal("0"),
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                ("constellation_id", models.BigIntegerField(blank=True, null=True)),
                ("constellation_name", models.CharField(blank=True, max_length=255)),
                ("region_id", models.BigIntegerField(blank=True, null=True)),
                ("region_name", models.CharField(blank=True, max_length=255)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="personal_industry_structures",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_structure",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="personal_copies",
                        to="indy_hub.industrystructure",
                    ),
                ),
            ],
            options={
                "verbose_name": "Industry Structure",
                "verbose_name_plural": "Industry Structures",
                "default_permissions": (),
                "db_table": "indy_hub_industrystructure",
            },
        ),
        migrations.CreateModel(
            name="IndustrySystemCostIndex",
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
                ("solar_system_id", models.BigIntegerField()),
                ("solar_system_name", models.CharField(max_length=255)),
                (
                    "activity_id",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (1, "Manufacturing"),
                            (3, "TE Research"),
                            (4, "ME Research"),
                            (5, "Copying"),
                            (8, "Invention"),
                            (9, "Reactions"),
                            (11, "Reactions (Legacy)"),
                        ]
                    ),
                ),
                (
                    "cost_index_percent",
                    models.DecimalField(
                        decimal_places=5,
                        max_digits=8,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("100")),
                        ],
                    ),
                ),
                ("source_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Industry System Cost Index",
                "verbose_name_plural": "Industry System Cost Indices",
                "default_permissions": (),
                "db_table": "indy_hub_industrysystemcostindex",
                "unique_together": {("solar_system_id", "activity_id")},
            },
        ),
        migrations.CreateModel(
            name="IndustryStructureRig",
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
                    "slot_index",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(3),
                        ]
                    ),
                ),
                ("rig_type_id", models.BigIntegerField()),
                ("rig_type_name", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "structure",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="rigs",
                        to="indy_hub.industrystructure",
                    ),
                ),
            ],
            options={
                "verbose_name": "Industry Structure Rig",
                "verbose_name_plural": "Industry Structure Rigs",
                "default_permissions": (),
                "db_table": "indy_hub_industrystructurerig",
                "unique_together": {("structure", "slot_index")},
            },
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["structure_type_id"], name="indy_hub_in_structu_490efb_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["owner_corporation_id", "sync_source"],
                name="indy_hub_in_owner_c_2a0df3_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["external_structure_id"], name="indy_hub_in_externa_65b7c7_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["constellation_name"], name="indy_hub_in_constel_22f76f_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["region_name"], name="indy_hub_in_region__8e4ef4_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["visibility_scope", "owner_user"],
                name="indy_hub_in_visibili_215887_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructure",
            index=models.Index(
                fields=["source_structure"], name="indy_hub_in_source__2f4ab3_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="industrystructure",
            constraint=models.UniqueConstraint(
                condition=models.Q(("visibility_scope", "public")),
                fields=("name",),
                name="indy_hub_structure_public_name_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="industrystructure",
            constraint=models.UniqueConstraint(
                condition=models.Q(("visibility_scope", "personal")),
                fields=("owner_user", "name", "personal_tag"),
                name="indy_hub_structure_personal_owner_name_tag_uq",
            ),
        ),
        migrations.AddIndex(
            model_name="industrysystemcostindex",
            index=models.Index(
                fields=["solar_system_id", "activity_id"],
                name="indy_hub_in_solar_s_042d6c_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="industrysystemcostindex",
            index=models.Index(
                fields=["solar_system_name", "activity_id"],
                name="indy_hub_in_solar_s_044129_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructurerig",
            index=models.Index(
                fields=["structure", "slot_index"],
                name="indy_hub_in_structu_6d7958_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="industrystructurerig",
            index=models.Index(
                fields=["rig_type_id"], name="indy_hub_in_rig_typ_7b4661_idx"
            ),
        ),
        migrations.RunPython(
            populate_industry_system_cost_indices,
            migrations.RunPython.noop,
        ),
    ]
