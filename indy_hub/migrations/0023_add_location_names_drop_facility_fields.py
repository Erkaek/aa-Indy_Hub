# Generated manually for location name population and schema cleanup
from __future__ import annotations

# Standard Library
import json
import logging
from typing import Any

# Third Party
import requests

# Django
from django.db import migrations, models

logger = logging.getLogger(__name__)
ESI_BASE_URL = "https://esi.evetech.net/latest"


def _fetch_location_name(structure_id: int | None, cache: dict[int, str]) -> str:
    if not structure_id:
        return ""

    structure_id = int(structure_id)
    if structure_id in cache:
        return cache[structure_id]

    params = {"datasource": "tranquility"}
    name: str | None = None

    # First attempt: structure endpoint (requires auth for player structures but returns 403 quickly)
    try:
        response = requests.get(
            f"{ESI_BASE_URL}/universe/structures/{structure_id}/",
            params=params,
            timeout=15,
        )
        if response.status_code == 200:
            try:
                payload: dict[str, Any] = response.json()
            except json.JSONDecodeError:
                payload = {}
            name = payload.get("name")
        elif response.status_code not in (401, 403, 404):
            logger.warning(
                "Unexpected status %s when resolving structure %s via /structures endpoint",
                response.status_code,
                structure_id,
            )
    except requests.RequestException as exc:
        logger.warning("ESI structure lookup failed for %s: %s", structure_id, exc)

    # Second attempt: public stations endpoint (covers NPC stations)
    if not name:
        try:
            response = requests.get(
                f"{ESI_BASE_URL}/universe/stations/{structure_id}/",
                params=params,
                timeout=15,
            )
            if response.status_code == 200:
                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    payload = {}
                name = payload.get("name")
        except requests.RequestException as exc:
            logger.warning(
                "ESI station lookup failed for %s: %s",
                structure_id,
                exc,
            )

    if not name:
        name = f"Structure {structure_id}"

    cache[structure_id] = name
    return name


def populate_location_names(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    cache: dict[int, str] = {}

    for blueprint in Blueprint.objects.exclude(location_id__isnull=True):
        location_name = _fetch_location_name(blueprint.location_id, cache)
        blueprint.location_name = location_name
        blueprint.save(update_fields=["location_name"])

    for job in IndustryJob.objects.exclude(station_id__isnull=True):
        location_name = _fetch_location_name(job.station_id, cache)
        job.location_name = location_name
        job.save(update_fields=["location_name"])


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0022_alter_blueprint_bp_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="blueprint",
            name="location_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="industryjob",
            name="location_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(populate_location_names, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="industryjob",
            name="facility_id",
        ),
        migrations.RemoveField(
            model_name="industryjob",
            name="blueprint_location_id",
        ),
        migrations.RemoveField(
            model_name="industryjob",
            name="output_location_id",
        ),
    ]
