# Generated manually for location name population and schema cleanup
from __future__ import annotations

# Standard Library
import logging
from typing import Any

# Django
from django.db import migrations, models

# AA Example App
# Indy Hub
from indy_hub.utils.eve import (
    PLACEHOLDER_PREFIX,
    is_station_id,
    resolve_location_name,
)

logger = logging.getLogger(__name__)


def _is_placeholder(name: str | None) -> bool:
    if not name:
        return True
    return name.startswith(PLACEHOLDER_PREFIX)


def populate_location_names(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    location_targets: dict[int, dict[str, Any]] = {}

    def register_location(
        location_id: int | None,
        *,
        current_name: str | None,
        character_id: int | None,
        bucket: str,
        object_id: int,
    ) -> None:
        if not location_id:
            return

        location_id = int(location_id)
        target = location_targets.setdefault(
            location_id,
            {
                "characters": set(),
                "blueprints": [],
                "jobs": [],
                "known_name": None,
            },
        )

        if character_id:
            target["characters"].add(int(character_id))

        if bucket == "blueprints":
            target["blueprints"].append((object_id, current_name))
        else:
            target["jobs"].append((object_id, current_name))

        if current_name and not _is_placeholder(current_name):
            target["known_name"] = current_name

    blueprint_qs = Blueprint.objects.exclude(location_id__isnull=True).values(
        "id", "location_id", "location_name", "character_id"
    )
    for row in blueprint_qs.iterator(chunk_size=500):
        register_location(
            row["location_id"],
            current_name=row["location_name"],
            character_id=row.get("character_id"),
            bucket="blueprints",
            object_id=row["id"],
        )

    job_qs = IndustryJob.objects.exclude(station_id__isnull=True).values(
        "id", "station_id", "location_name", "character_id"
    )
    for row in job_qs.iterator(chunk_size=500):
        register_location(
            row["station_id"],
            current_name=row["location_name"],
            character_id=row.get("character_id"),
            bucket="jobs",
            object_id=row["id"],
        )

    if not location_targets:
        logger.info("No locations required updates for Blueprint/IndustryJob records")
        return

    logger.info(
        "Populating location names for %s unique location ids",
        len(location_targets),
    )

    resolved_names: dict[int, str] = {}

    for location_id, target in location_targets.items():
        known_name = target.get("known_name")
        if known_name:
            resolved_names[location_id] = known_name
            continue

        characters = list(sorted(target["characters"]))
        name: str | None = None

        for character_id in characters:
            try:
                name = resolve_location_name(
                    location_id,
                    character_id=character_id,
                    force_refresh=False,
                )
            except Exception:  # pragma: no cover - defensive fallback in migrations
                logger.debug(
                    "resolve_location_name failed for %s via character %s",
                    location_id,
                    character_id,
                    exc_info=True,
                )
                continue

            if name and not _is_placeholder(name):
                break

        if not name or _is_placeholder(name):
            refresh_lookup = bool(name and _is_placeholder(name))
            if refresh_lookup and is_station_id(location_id):
                refresh_lookup = False
            try:
                name = resolve_location_name(
                    location_id,
                    character_id=None,
                    force_refresh=refresh_lookup,
                )
            except Exception:  # pragma: no cover - defensive fallback
                logger.debug(
                    "resolve_location_name fallback failed for %s",
                    location_id,
                    exc_info=True,
                )
                name = None

        if not name:
            name = f"{PLACEHOLDER_PREFIX}{location_id}"

        resolved_names[location_id] = name

    blueprint_updates: list[Any] = []
    job_updates: list[Any] = []

    for location_id, target in location_targets.items():
        name = resolved_names[location_id]

        for blueprint_id, current_name in target["blueprints"]:
            if current_name == name:
                continue
            blueprint_updates.append(Blueprint(id=blueprint_id, location_name=name))

        for job_id, current_name in target["jobs"]:
            if current_name == name:
                continue
            job_updates.append(IndustryJob(id=job_id, location_name=name))

    if blueprint_updates:
        Blueprint.objects.bulk_update(
            blueprint_updates, ["location_name"], batch_size=500
        )
        logger.info(
            "Updated location names for %s blueprint records", len(blueprint_updates)
        )

    if job_updates:
        IndustryJob.objects.bulk_update(job_updates, ["location_name"], batch_size=500)
        logger.info(
            "Updated location names for %s industry job records", len(job_updates)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0022_alter_blueprint_bp_type"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_indyblueprint'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        "    \"ALTER TABLE `indy_hub_indyblueprint` ADD COLUMN `location_name` varchar(255) NOT NULL DEFAULT ''\",\n"
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_indyblueprint'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_indyblueprint` DROP COLUMN `location_name`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="blueprint",
                    name="location_name",
                    field=models.CharField(blank=True, max_length=255),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        "    \"ALTER TABLE `indy_hub_industryjob` ADD COLUMN `location_name` varchar(255) NOT NULL DEFAULT ''\",\n"
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'location_name'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `location_name`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="industryjob",
                    name="location_name",
                    field=models.CharField(blank=True, max_length=255),
                )
            ],
        ),
        migrations.RunPython(populate_location_names, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'facility_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `facility_id`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'facility_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` ADD COLUMN `facility_id` bigint NULL",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="industryjob",
                    name="facility_id",
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'blueprint_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `blueprint_location_id`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'blueprint_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` ADD COLUMN `blueprint_location_id` bigint NULL",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="industryjob",
                    name="blueprint_location_id",
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'output_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 1,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` DROP COLUMN `output_location_id`",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                    reverse_sql=(
                        "SET @col_exists := (\n"
                        "    SELECT COUNT(*) FROM information_schema.COLUMNS\n"
                        "    WHERE TABLE_SCHEMA = DATABASE()\n"
                        "      AND TABLE_NAME = 'indy_hub_industryjob'\n"
                        "      AND COLUMN_NAME = 'output_location_id'\n"
                        ");\n"
                        "SET @ddl := IF(@col_exists = 0,\n"
                        '    "ALTER TABLE `indy_hub_industryjob` ADD COLUMN `output_location_id` bigint NULL",\n'
                        "    'DO 0'\n"
                        ");\n"
                        "SET @col_exists := NULL;\n"
                        "PREPARE stmt FROM @ddl;\n"
                        "EXECUTE stmt;\n"
                        "DEALLOCATE PREPARE stmt;"
                    ),
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="industryjob",
                    name="output_location_id",
                )
            ],
        ),
    ]
