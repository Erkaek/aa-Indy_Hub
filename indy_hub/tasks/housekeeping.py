"""Housekeeping tasks for stale data refresh."""

# Standard Library
from datetime import timedelta

# Third Party
from celery import shared_task

# Django
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

# Indy Hub
from ..app_settings import (
    LOCATION_LOOKUP_BUDGET,
    ROLE_SNAPSHOT_STALE_HOURS,
    SKILL_SNAPSHOT_STALE_HOURS,
    STRUCTURE_NAME_STALE_HOURS,
)
from ..models import (
    CachedStructureName,
    CharacterRoles,
    IndustrySkillSnapshot,
)
from ..services.asset_cache import STRUCTURE_PLACEHOLDER_TTL
from ..utils.analytics import emit_analytics_event
from ..utils.eve import PLACEHOLDER_PREFIX, has_structure_forbidden_cooldown
from .industry import (
    SKILLS_SCOPE,
    STRUCTURE_SCOPE,
    _get_adaptive_window_minutes,
    update_character_skill_snapshot_for_character,
)
from .location import cache_structure_names_bulk
from .user import CORP_ROLES_SCOPE, update_character_roles_for_character

logger = get_extension_logger(__name__)
User = get_user_model()


def _select_character_targets_for_stale_snapshots(
    *,
    token_pairs: list[tuple[int, int]],
    snapshot_model,
    stale_hours: int,
    now: timezone.datetime,
) -> list[tuple[int, int]]:
    character_ids = [int(cid) for _uid, cid in token_pairs if cid]
    if not character_ids:
        return []

    rows = snapshot_model.objects.filter(character_id__in=character_ids).values_list(
        "character_id",
        "last_updated",
    )
    snapshot_map = {int(cid): last for cid, last in rows}
    cutoff = now - timedelta(hours=stale_hours)

    stale_ids = {
        int(cid) for cid, last in snapshot_map.items() if last and last < cutoff
    }
    missing_ids = set(character_ids) - set(snapshot_map)
    target_ids = stale_ids | missing_ids

    if not target_ids:
        return []

    targets = {
        (int(uid), int(cid))
        for uid, cid in token_pairs
        if uid and cid and int(cid) in target_ids
    }
    return sorted(targets, key=lambda row: (row[0], row[1]))


def _queue_staggered_character_tasks(
    task,
    targets: list[tuple[int, int]],
    *,
    window_minutes: int,
    priority: int | None = None,
) -> int:
    if not targets:
        return 0

    total = len(targets)
    window_seconds = max(window_minutes * 60, 0)
    if total == 1 or window_seconds == 0:
        for user_id, character_id in targets:
            task.apply_async(args=(int(user_id), int(character_id)), priority=priority)
        return total

    spacing = window_seconds / total
    for index, (user_id, character_id) in enumerate(targets):
        countdown = int(round(index * spacing))
        task.apply_async(
            args=(int(user_id), int(character_id)),
            countdown=countdown,
            priority=priority,
        )
    return total


@shared_task
def refresh_stale_snapshots() -> dict[str, int]:
    """Refresh skills/roles/online/structure names when missing or stale."""
    now = timezone.now()
    result = {
        "skills_users_queued": 0,
        "roles_users_queued": 0,
        "skills_characters_queued": 0,
        "roles_characters_queued": 0,
        "structures_queued": 0,
    }
    try:
        # Skills snapshots
        skill_tokens = list(
            Token.objects.filter()
            .require_scopes([SKILLS_SCOPE])
            .require_valid()
            .values_list("user_id", "character_id")
            .distinct()
        )
        skill_targets = _select_character_targets_for_stale_snapshots(
            token_pairs=[
                (int(uid), int(cid)) for uid, cid in skill_tokens if uid and cid
            ],
            snapshot_model=IndustrySkillSnapshot,
            stale_hours=SKILL_SNAPSHOT_STALE_HOURS,
            now=now,
        )
        if skill_targets:
            window_minutes = _get_adaptive_window_minutes("skills", len(skill_targets))
            queued = _queue_staggered_character_tasks(
                update_character_skill_snapshot_for_character,
                skill_targets,
                window_minutes=window_minutes,
                priority=7,
            )
            result["skills_users_queued"] = len(
                {int(user_id) for user_id, _character_id in skill_targets}
            )
            result["skills_characters_queued"] = queued
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed stale skills refresh: %s", exc)

    try:
        # Roles snapshots
        role_tokens = list(
            Token.objects.filter()
            .require_scopes([CORP_ROLES_SCOPE])
            .require_valid()
            .values_list("user_id", "character_id")
            .distinct()
        )
        role_targets = _select_character_targets_for_stale_snapshots(
            token_pairs=[
                (int(uid), int(cid)) for uid, cid in role_tokens if uid and cid
            ],
            snapshot_model=CharacterRoles,
            stale_hours=ROLE_SNAPSHOT_STALE_HOURS,
            now=now,
        )
        if role_targets:
            window_minutes = _get_adaptive_window_minutes("roles", len(role_targets))
            queued = _queue_staggered_character_tasks(
                update_character_roles_for_character,
                role_targets,
                window_minutes=window_minutes,
                priority=7,
            )
            result["roles_users_queued"] = len(
                {int(user_id) for user_id, _character_id in role_targets}
            )
            result["roles_characters_queued"] = queued
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed stale roles refresh: %s", exc)

    try:
        # Structure names
        structure_cutoff = now - timedelta(hours=STRUCTURE_NAME_STALE_HOURS)
        placeholder_cutoff = now - STRUCTURE_PLACEHOLDER_TTL
        stale_structure_candidates = list(
            CachedStructureName.objects.filter(
                Q(last_resolved__lt=structure_cutoff)
                | Q(last_resolved__isnull=True)
                | Q(
                    name__startswith=PLACEHOLDER_PREFIX,
                    last_resolved__lt=placeholder_cutoff,
                )
            ).values_list("structure_id", flat=True)[
                : max(LOCATION_LOOKUP_BUDGET * 5, LOCATION_LOOKUP_BUDGET)
            ]
        )
        stale_structure_ids = [
            int(structure_id)
            for structure_id in stale_structure_candidates
            if not has_structure_forbidden_cooldown(int(structure_id))
        ][:LOCATION_LOOKUP_BUDGET]
        if stale_structure_ids:
            token = (
                Token.objects.filter()
                .require_scopes([STRUCTURE_SCOPE])
                .require_valid()
                .order_by("character_id")
                .first()
            )
            if token:
                cache_structure_names_bulk.delay(
                    stale_structure_ids,
                    character_id=int(token.character_id),
                    owner_user_id=int(token.user_id),
                )
                result["structures_queued"] = len(stale_structure_ids)
            else:
                logger.info(
                    "Skipping structure name refresh: no token with %s scope",
                    STRUCTURE_SCOPE,
                )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed stale structure refresh: %s", exc)

    logger.info("Stale refresh summary: %s", result)
    emit_analytics_event(
        task="housekeeping.refresh_stale_snapshots",
        label="completed",
        result="success",
        value=max(
            result.get("skills_characters_queued", 0)
            + result.get("roles_characters_queued", 0),
            1,
        ),
    )
    return result
