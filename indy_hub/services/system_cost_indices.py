"""Synchronization helpers for public ESI industry system cost indices."""

from __future__ import annotations

# Standard Library
from decimal import Decimal

# Django
from django.db import connection, transaction
from django.utils import timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.models import IndustryActivityMixin, IndustrySystemCostIndex
from indy_hub.services.esi_client import shared_client

logger = get_extension_logger(__name__)

ESI_ACTIVITY_ID_MAP = {
    "manufacturing": [IndustryActivityMixin.ACTIVITY_MANUFACTURING],
    "researching_time_efficiency": [IndustryActivityMixin.ACTIVITY_TE_RESEARCH],
    "researching_material_efficiency": [IndustryActivityMixin.ACTIVITY_ME_RESEARCH],
    "copying": [IndustryActivityMixin.ACTIVITY_COPYING],
    "invention": [IndustryActivityMixin.ACTIVITY_INVENTION],
    "reaction": [
        IndustryActivityMixin.ACTIVITY_REACTIONS,
        IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
    ],
}
PERCENT_FACTOR = Decimal("100")


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _resolve_solar_system_names(system_ids: set[int]) -> dict[int, str]:
    if not system_ids:
        return {}

    resolved: dict[int, str] = {}
    try:
        with connection.cursor() as cursor:
            for chunk in _chunked(sorted(system_ids), 900):
                placeholders = ", ".join(["%s"] * len(chunk))
                cursor.execute(
                    f"SELECT id, name FROM eve_sde_solarsystem WHERE id IN ({placeholders})",
                    chunk,
                )
                for solar_system_id, name in cursor.fetchall():
                    resolved[int(solar_system_id)] = str(name)
    except Exception:
        resolved = {}

    missing_ids = sorted(system_ids - set(resolved.keys()))
    if missing_ids:
        try:
            resolved.update(shared_client.resolve_ids_to_names(missing_ids))
        except Exception:
            logger.warning(
                "Unable to resolve some solar system names for cost indices",
                exc_info=True,
            )
    return resolved


def sync_system_cost_indices(*, force_refresh: bool = False) -> dict[str, int]:
    payload = shared_client.fetch_industry_systems(force_refresh=force_refresh)
    if not payload:
        return {
            "systems": 0,
            "entries_seen": 0,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
        }

    system_ids = {
        int(entry.get("solar_system_id"))
        for entry in payload
        if entry.get("solar_system_id") is not None
    }
    system_name_map = _resolve_solar_system_names(system_ids)
    now = timezone.now()

    existing_by_key = {
        (row.solar_system_id, row.activity_id): row
        for row in IndustrySystemCostIndex.objects.filter(
            solar_system_id__in=system_ids
        )
    }

    to_create: list[IndustrySystemCostIndex] = []
    to_update: list[IndustrySystemCostIndex] = []
    unchanged = 0
    entries_seen = 0

    for system_entry in payload:
        solar_system_id = system_entry.get("solar_system_id")
        if solar_system_id is None:
            continue
        solar_system_id = int(solar_system_id)
        solar_system_name = system_name_map.get(solar_system_id, str(solar_system_id))

        for cost_entry in system_entry.get("cost_indices", []) or []:
            activity_name = str(cost_entry.get("activity") or "").strip().lower()
            activity_ids = ESI_ACTIVITY_ID_MAP.get(activity_name, [])
            if not activity_ids:
                continue
            cost_ratio = Decimal(str(cost_entry.get("cost_index") or "0"))
            cost_percent = cost_ratio * PERCENT_FACTOR
            for activity_id in activity_ids:
                entries_seen += 1
                key = (solar_system_id, int(activity_id))
                existing = existing_by_key.get(key)
                if existing is None:
                    to_create.append(
                        IndustrySystemCostIndex(
                            solar_system_id=solar_system_id,
                            solar_system_name=solar_system_name,
                            activity_id=activity_id,
                            cost_index_percent=cost_percent,
                            source_updated_at=now,
                        )
                    )
                    continue

                changed = False
                if existing.solar_system_name != solar_system_name:
                    existing.solar_system_name = solar_system_name
                    changed = True
                if existing.cost_index_percent != cost_percent:
                    existing.cost_index_percent = cost_percent
                    changed = True
                if existing.source_updated_at != now:
                    existing.source_updated_at = now
                    changed = True

                if changed:
                    to_update.append(existing)
                else:
                    unchanged += 1

    if to_create:
        for chunk_start in range(0, len(to_create), 500):
            chunk = to_create[chunk_start : chunk_start + 500]
            with transaction.atomic():
                IndustrySystemCostIndex.objects.bulk_create(chunk, batch_size=500)
    if to_update:
        update_fields = [
            "solar_system_name",
            "cost_index_percent",
            "source_updated_at",
            "updated_at",
        ]
        for chunk_start in range(0, len(to_update), 200):
            chunk = to_update[chunk_start : chunk_start + 200]
            with transaction.atomic():
                IndustrySystemCostIndex.objects.bulk_update(
                    chunk,
                    update_fields,
                    batch_size=200,
                )

    return {
        "systems": len(system_ids),
        "entries_seen": entries_seen,
        "created": len(to_create),
        "updated": len(to_update),
        "unchanged": unchanged,
    }
