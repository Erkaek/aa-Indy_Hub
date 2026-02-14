"""Housekeeping tasks for stale data refresh."""

# Standard Library
from datetime import timedelta

# Third Party
from celery import shared_task

# Django
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Q

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

# Indy Hub
from ..app_settings import (
    LOCATION_LOOKUP_BUDGET,
    ONLINE_STATUS_STALE_HOURS,
    ROLE_SNAPSHOT_STALE_HOURS,
    SKILL_SNAPSHOT_STALE_HOURS,
    STRUCTURE_NAME_STALE_HOURS,
)
from ..models import (
    CachedStructureName,
    CharacterOnlineStatus,
    CharacterRoles,
    IndustrySkillSnapshot,
)
from ..services.asset_cache import STRUCTURE_PLACEHOLDER_TTL
from .industry import (
    ONLINE_SCOPE,
    SKILLS_SCOPE,
    STRUCTURE_SCOPE,
    _get_adaptive_window_minutes,
    _queue_staggered_user_tasks,
    _refresh_online_status_for_user,
)
from .location import cache_structure_names_bulk
from .user import CORP_ROLES_SCOPE, update_user_roles_snapshots
from .industry import update_user_skill_snapshots
from ..utils.eve import PLACEHOLDER_PREFIX

logger = get_extension_logger(__name__)
User = get_user_model()


def _select_users_for_stale_snapshots(
    *,
    token_pairs: list[tuple[int, int]],
    snapshot_model,
    stale_hours: int,
    now: timezone.datetime,
) -> set[int]:
    character_ids = [int(cid) for _uid, cid in token_pairs if cid]
    if not character_ids:
        return set()

    rows = snapshot_model.objects.filter(character_id__in=character_ids).values_list(
        "character_id",
        "last_updated",
    )
    snapshot_map = {int(cid): last for cid, last in rows}
    cutoff = now - timedelta(hours=stale_hours)

    stale_ids = {
        int(cid)
        for cid, last in snapshot_map.items()
        if last and last < cutoff
    }
    missing_ids = set(character_ids) - set(snapshot_map)
    target_ids = stale_ids | missing_ids

    if not target_ids:
        return set()

    return {int(uid) for uid, cid in token_pairs if int(cid) in target_ids}


@shared_task
def refresh_stale_snapshots() -> dict[str, int]:
    """Refresh skills/roles/online/structure names when missing or stale."""
    now = timezone.now()
    result = {
        "skills_users_queued": 0,
        "roles_users_queued": 0,
        "online_users_refreshed": 0,
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
        skill_user_ids = _select_users_for_stale_snapshots(
            token_pairs=[(int(uid), int(cid)) for uid, cid in skill_tokens if uid and cid],
            snapshot_model=IndustrySkillSnapshot,
            stale_hours=SKILL_SNAPSHOT_STALE_HOURS,
            now=now,
        )
        if skill_user_ids:
            window_minutes = _get_adaptive_window_minutes("skills", len(skill_user_ids))
            queued = _queue_staggered_user_tasks(
                update_user_skill_snapshots,
                sorted(skill_user_ids),
                window_minutes=window_minutes,
                priority=7,
            )
            result["skills_users_queued"] = queued
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
        role_user_ids = _select_users_for_stale_snapshots(
            token_pairs=[(int(uid), int(cid)) for uid, cid in role_tokens if uid and cid],
            snapshot_model=CharacterRoles,
            stale_hours=ROLE_SNAPSHOT_STALE_HOURS,
            now=now,
        )
        if role_user_ids:
            window_minutes = _get_adaptive_window_minutes("roles", len(role_user_ids))
            queued = _queue_staggered_user_tasks(
                update_user_roles_snapshots,
                sorted(role_user_ids),
                window_minutes=window_minutes,
                priority=7,
            )
            result["roles_users_queued"] = queued
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed stale roles refresh: %s", exc)

    try:
        # Online status snapshots
        online_tokens = list(
            Token.objects.filter()
            .require_scopes([ONLINE_SCOPE])
            .require_valid()
            .values_list("user_id", "character_id")
            .distinct()
        )
        if online_tokens:
            cutoff = now - timedelta(hours=ONLINE_STATUS_STALE_HOURS)
            tokens_by_user: dict[int, list[int]] = {}
            for uid, cid in online_tokens:
                if not uid or not cid:
                    continue
                tokens_by_user.setdefault(int(uid), []).append(int(cid))

            for user_id, char_ids in tokens_by_user.items():
                status_rows = CharacterOnlineStatus.objects.filter(
                    owner_user_id=user_id,
                    character_id__in=char_ids,
                ).values_list("character_id", "last_updated")
                status_map = {int(cid): last for cid, last in status_rows}
                missing = set(char_ids) - set(status_map)
                stale = {
                    int(cid)
                    for cid, last in status_map.items()
                    if last and last < cutoff
                }
                if missing or stale:
                    user = User.objects.filter(id=user_id).first()
                    if not user:
                        continue
                    _refresh_online_status_for_user(user=user, now=now)
                    result["online_users_refreshed"] += 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed stale online refresh: %s", exc)

    try:
        # Structure names
        structure_cutoff = now - timedelta(hours=STRUCTURE_NAME_STALE_HOURS)
        placeholder_cutoff = now - STRUCTURE_PLACEHOLDER_TTL
        stale_structure_ids = list(
            CachedStructureName.objects.filter(
                Q(last_resolved__lt=structure_cutoff)
                | Q(last_resolved__isnull=True)
                | Q(
                    name__startswith=PLACEHOLDER_PREFIX,
                    last_resolved__lt=placeholder_cutoff,
                )
            )
            .values_list("structure_id", flat=True)[:LOCATION_LOOKUP_BUDGET]
        )
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
    return result
