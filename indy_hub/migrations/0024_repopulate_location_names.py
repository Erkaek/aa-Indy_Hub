"""Repopulate location names with authenticated lookups.

This migration is a follow-up to 0023. It re-runs the location name
resolution using the improved helper so that any rows previously filled
with placeholder values (for example, "Structure <id>") are
backfilled with the real structure or station names when available.
"""

from __future__ import annotations

# Standard Library
import json
import logging
from typing import Any

# Third Party
import requests

# Django
from django.db import migrations

# AA Example App
# Indy Hub
from indy_hub.utils.eve import resolve_location_name

logger = logging.getLogger(__name__)
ESI_BASE_URL = "https://esi.evetech.net/latest"
PLACEHOLDER_PREFIX = "Structure "


def _fetch_location_name(
    structure_id: int | None,
    cache: dict[int, str],
    *,
    character_id: int | None = None,
) -> str | None:
    if not structure_id:
        return None

    structure_id = int(structure_id)
    if structure_id in cache:
        return cache[structure_id]

    params = {"datasource": "tranquility"}
    name: str | None = None

    try:
        name = resolve_location_name(
            structure_id,
            character_id=character_id,
            force_refresh=True,
        )
    except Exception:  # pragma: no cover - defensive fallback in migrations
        logger.debug(
            "resolve_location_name failed for %s (character %s)",
            structure_id,
            character_id,
            exc_info=True,
        )

    if not name:
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
                    "Unexpected status %s resolving structure %s via /structures",
                    response.status_code,
                    structure_id,
                )
        except requests.RequestException as exc:
            logger.warning("ESI structure lookup failed for %s: %s", structure_id, exc)

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
            logger.warning("ESI station lookup failed for %s: %s", structure_id, exc)

    if name:
        cache[structure_id] = name

    return name


def _needs_update(current: str | None, location_id: int | None) -> bool:
    if not location_id:
        return False

    if not current:
        return True

    placeholder = f"{PLACEHOLDER_PREFIX}{int(location_id)}"
    return current.strip() == placeholder


def repopulate_location_names(apps, schema_editor):
    Blueprint = apps.get_model("indy_hub", "Blueprint")
    IndustryJob = apps.get_model("indy_hub", "IndustryJob")

    cache: dict[int, str] = {}

    blueprints = Blueprint.objects.exclude(location_id__isnull=True)
    for blueprint in blueprints.iterator(chunk_size=500):
        if not _needs_update(blueprint.location_name, blueprint.location_id):
            continue

        name = _fetch_location_name(
            blueprint.location_id,
            cache,
            character_id=getattr(blueprint, "character_id", None),
        )
        if name:
            blueprint.location_name = name
            blueprint.save(update_fields=["location_name"])

    jobs = IndustryJob.objects.exclude(station_id__isnull=True)
    for job in jobs.iterator(chunk_size=500):
        if not _needs_update(job.location_name, job.station_id):
            continue

        name = _fetch_location_name(
            job.station_id,
            cache,
            character_id=getattr(job, "character_id", None),
        )
        if name:
            job.location_name = name
            job.save(update_fields=["location_name"])


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0023_add_location_names_drop_facility_fields"),
    ]

    operations = [
        migrations.RunPython(repopulate_location_names, migrations.RunPython.noop),
    ]
