"""Utilities for populating location names on blueprints and industry jobs."""

from __future__ import annotations

# Standard Library
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# Django
from django.conf import settings
from django.db import transaction

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.models import Blueprint, IndustryJob
from indy_hub.utils.eve import PLACEHOLDER_PREFIX, is_station_id, resolve_location_name

logger = get_extension_logger(__name__)

DEFAULT_TASK_PRIORITY = 6


@dataclass
class LocationTarget:
    characters: set[int]
    character_owners: dict[int, int]
    blueprints: list[tuple[int, str | None]]
    jobs: list[tuple[int, str | None]]
    known_name: str | None
    owners: set[int]

    @classmethod
    def empty(cls) -> LocationTarget:
        return cls(set(), {}, [], [], None, set())


def _is_placeholder(name: str | None) -> bool:
    if not name:
        return True
    return name.startswith(PLACEHOLDER_PREFIX)


def _register_location(
    location_targets: dict[int, LocationTarget],
    location_id: int | None,
    *,
    current_name: str | None,
    character_id: int | None,
    owner_user_id: int | None,
    bucket: str,
    object_id: int,
) -> None:
    if not location_id:
        return

    location_id = int(location_id)
    target = location_targets.setdefault(location_id, LocationTarget.empty())

    if character_id:
        character_id = int(character_id)
        target.characters.add(character_id)
        if owner_user_id:
            target.character_owners[character_id] = int(owner_user_id)

    if owner_user_id:
        target.owners.add(int(owner_user_id))

    if bucket == "blueprints":
        target.blueprints.append((object_id, current_name))
    else:
        target.jobs.append((object_id, current_name))

    if current_name and not _is_placeholder(current_name):
        target.known_name = current_name


def _gather_location_targets(
    *,
    location_ids: Iterable[int] | None = None,
    blueprint_queryset=None,
    industry_queryset=None,
    chunk_size: int = 500,
) -> dict[int, LocationTarget]:
    targets: dict[int, LocationTarget] = {}

    location_filter: Mapping[str, Any] | None = None
    job_filter: Mapping[str, Any] | None = None
    if location_ids is not None:
        normalized_ids = {int(value) for value in location_ids if value}
        if not normalized_ids:
            return {}
        location_filter = {"location_id__in": normalized_ids}
        job_filter = {"station_id__in": normalized_ids}

    blueprint_qs = blueprint_queryset or Blueprint.objects
    job_qs = industry_queryset or IndustryJob.objects

    if location_filter is not None:
        blueprint_qs = blueprint_qs.filter(**location_filter)
    blueprint_qs = blueprint_qs.exclude(location_id__isnull=True).values(
        "id",
        "location_id",
        "location_name",
        "character_id",
        "owner_user_id",
    )

    for row in blueprint_qs.iterator(chunk_size=chunk_size):
        _register_location(
            targets,
            row["location_id"],
            current_name=row["location_name"],
            character_id=row.get("character_id"),
            owner_user_id=row.get("owner_user_id"),
            bucket="blueprints",
            object_id=row["id"],
        )

    if job_filter is not None:
        job_qs = job_qs.filter(**job_filter)
    job_qs = job_qs.exclude(station_id__isnull=True).values(
        "id",
        "station_id",
        "location_name",
        "character_id",
        "owner_user_id",
    )

    for row in job_qs.iterator(chunk_size=chunk_size):
        _register_location(
            targets,
            row["station_id"],
            current_name=row["location_name"],
            character_id=row.get("character_id"),
            owner_user_id=row.get("owner_user_id"),
            bucket="jobs",
            object_id=row["id"],
        )

    return targets


def _enqueue_structure_refresh(structure_id: int) -> bool:
    """Schedule an asynchronous refresh for a given structure location.

    Utilise QueueOnce-enabled Celery task when available. Returns True when a task
    was queued successfully and False when queuing is skipped or failed.
    """

    try:
        eager = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
    except Exception:  # pragma: no cover - defensive fallback when settings unset
        eager = True

    if eager:
        return False

    try:
        # AA Example App
        from indy_hub.tasks.location import refresh_structure_location
    except Exception:  # pragma: no cover - task unavailable or import error
        logger.debug(
            "Unable to schedule structure %s refresh (task unavailable)",
            structure_id,
            exc_info=True,
        )
        return False

    try:
        refresh_structure_location.apply_async(
            kwargs={"structure_id": int(structure_id)},
            priority=DEFAULT_TASK_PRIORITY,
        )
        logger.debug(
            "Asynchronous refresh scheduled for structure %s",
            structure_id,
        )
        return True
    except Exception:  # pragma: no cover - broker indisponible
        logger.debug(
            "Unable to schedule structure %s refresh (broker unavailable)",
            structure_id,
            exc_info=True,
        )
        return False


def populate_location_names(
    *,
    location_ids: Iterable[int] | None = None,
    force_refresh: bool = False,
    chunk_size: int = 500,
    dry_run: bool = False,
    logger_override: logging.Logger | None = None,
    schedule_async: bool = True,
) -> dict[str, int]:
    """Populate location names for blueprints and industry jobs.

    Args:
        location_ids: optional iterable of structure/station IDs to limit scope.
        force_refresh: when True, ignores cached placeholder values for lookups.
        chunk_size: size used for queryset iteration and bulk updates.
        dry_run: when True, performs no writes and only reports the impact.
        logger_override: optional logger to emit progress messages.
        schedule_async: when True, queue a background refresh task for structures
            that still use a placeholder name after resolution.

    Returns:
        Summary dictionary with counts of updated objects.
    """

    active_logger = logger_override or logger
    targets = _gather_location_targets(
        location_ids=location_ids,
        chunk_size=chunk_size,
    )

    if not targets:
        active_logger.info(
            "No locations require updates for Blueprint/IndustryJob records"
        )
        return {"blueprints": 0, "jobs": 0, "locations": 0}

    active_logger.info(
        "Resolving location names for %s unique location IDs", len(targets)
    )

    blueprint_updates: list[Blueprint] = []
    job_updates: list[IndustryJob] = []
    queued_structures: set[int] = set()

    for location_id, target in targets.items():
        location_id = int(location_id)
        name = target.known_name
        if name and not _is_placeholder(name):
            resolved_name = name
        else:
            resolved_name = _resolve_location_name_for_target(
                location_id,
                target,
                force_refresh=force_refresh,
            )

        if not resolved_name:
            resolved_name = f"{PLACEHOLDER_PREFIX}{location_id}"

        if (
            schedule_async
            and not force_refresh
            and not is_station_id(location_id)
            and _is_placeholder(resolved_name)
            and location_id not in queued_structures
        ):
            if _enqueue_structure_refresh(location_id):
                queued_structures.add(location_id)

        for blueprint_id, current_name in target.blueprints:
            if current_name == resolved_name:
                continue
            blueprint_updates.append(
                Blueprint(id=blueprint_id, location_name=resolved_name)
            )

        for job_id, current_name in target.jobs:
            if current_name == resolved_name:
                continue
            job_updates.append(IndustryJob(id=job_id, location_name=resolved_name))

    blueprint_count = len(blueprint_updates)
    job_count = len(job_updates)
    if dry_run:
        active_logger.info(
            "Dry run: would update %s blueprints and %s industry jobs",
            blueprint_count,
            job_count,
        )
        return {
            "blueprints": blueprint_count,
            "jobs": job_count,
            "locations": len(targets),
        }

    with transaction.atomic():
        if blueprint_updates:
            Blueprint.objects.bulk_update(
                blueprint_updates, ["location_name"], batch_size=chunk_size
            )
            active_logger.info(
                "Updated location names for %s blueprint records", blueprint_count
            )

        if job_updates:
            IndustryJob.objects.bulk_update(
                job_updates, ["location_name"], batch_size=chunk_size
            )
            active_logger.info(
                "Updated location names for %s industry job records", job_count
            )

    return {
        "blueprints": blueprint_count,
        "jobs": job_count,
        "locations": len(targets),
    }


def _resolve_location_name_for_target(
    location_id: int,
    target: LocationTarget,
    *,
    force_refresh: bool,
) -> str | None:
    characters = sorted(target.characters)
    owners = sorted(target.owners)
    primary_owner_id = owners[0] if owners else None
    name: str | None = None

    for character_id in characters:
        owner_for_character = target.character_owners.get(
            character_id, primary_owner_id
        )
        try:
            name = resolve_location_name(
                location_id,
                character_id=character_id,
                owner_user_id=owner_for_character,
                force_refresh=False,
            )
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "resolve_location_name failed for %s via character %s",
                location_id,
                character_id,
                exc_info=True,
            )
            continue

        if name and not _is_placeholder(name):
            break

    if (not name or _is_placeholder(name)) and not is_station_id(location_id):
        refresh = force_refresh or (name and _is_placeholder(name))
        try:
            name = resolve_location_name(
                location_id,
                character_id=None,
                owner_user_id=primary_owner_id,
                force_refresh=refresh,
            )
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "resolve_location_name fallback failed for %s",
                location_id,
                exc_info=True,
            )
            name = None

    return name
