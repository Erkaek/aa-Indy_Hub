# User-specific asynchronous tasks
"""
User-specific Celery tasks for the Indy Hub module.
These tasks handle user profile management, preferences, cleanup, etc.
"""

# Standard Library
from datetime import timedelta

# Third Party
from celery import shared_task

# Django
from django.contrib.auth import get_user_model
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

from ..app_settings import ROLE_SNAPSHOT_STALE_HOURS

# Indy Hub
from ..models import CharacterRoles
from ..services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    ESIUnmodifiedError,
    get_retry_after_seconds,
    shared_client,
)
from ..utils.analytics import emit_analytics_event
from ..utils.menu_badge import compute_menu_badge_count
from .industry import _is_user_active

logger = get_extension_logger(__name__)

CORP_ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"

User = get_user_model()

_MENU_BADGE_CACHE_TTL_SECONDS = 45
_MENU_BADGE_REFRESH_LOCK_TTL_SECONDS = 30


def _coerce_role_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return []


@shared_task
def update_character_roles_for_character(
    user_id: int,
    character_id: int,
    *,
    force_refresh: bool = False,
) -> dict:
    """Refresh stored corporation roles for a single character."""
    table_empty = not CharacterRoles.objects.exists()
    ownership = (
        CharacterOwnership.objects.filter(
            user_id=user_id, character__character_id=character_id
        )
        .select_related("character", "user")
        .first()
    )
    if not ownership:
        return {"status": "skipped", "reason": "ownership_missing"}

    token = (
        Token.objects.filter(user=ownership.user, character_id=character_id)
        .require_scopes([CORP_ROLES_SCOPE])
        .require_valid()
        .order_by("-created")
        .first()
    )
    if not token:
        return {"status": "skipped", "reason": "token_missing"}

    snapshot = CharacterRoles.objects.filter(character_id=character_id).first()
    now = timezone.now()
    snapshot_stale = bool(
        snapshot
        and (now - snapshot.last_updated) >= timedelta(hours=ROLE_SNAPSHOT_STALE_HOURS)
    )
    if snapshot and not snapshot_stale:
        return {"status": "skipped", "reason": "fresh"}
    try:
        payload = shared_client.fetch_character_corporation_roles(
            int(character_id),
            force_refresh=force_refresh or table_empty or snapshot is None,
        )
    except ESIUnmodifiedError:
        return {"status": "skipped", "reason": "not_modified"}
    except ESIRateLimitError as exc:
        delay = get_retry_after_seconds(exc)
        update_character_roles_for_character.apply_async(
            args=[user_id, int(character_id)],
            kwargs={"force_refresh": force_refresh},
            countdown=delay,
        )
        return {"status": "rate_limited", "retry_in": delay}
    except (
        ESITokenError,
        ESIForbiddenError,
        ESIClientError,
    ) as exc:
        logger.warning(
            "Failed to refresh corporation roles for character %s: %s",
            character_id,
            exc,
        )
        return {"status": "failed", "reason": str(exc)}

    if isinstance(payload, list):
        if not payload:
            logger.debug(
                "Empty corporation roles payload for character %s",
                character_id,
            )
            return {"status": "failed", "reason": "unexpected_payload"}
        payload = payload[0]
    if not isinstance(payload, dict):
        payload = shared_client._coerce_mapping(payload)
    if not isinstance(payload, dict):
        logger.debug(
            "Unexpected corporation roles payload type for character %s: %s",
            character_id,
            type(payload),
        )
        return {"status": "failed", "reason": "unexpected_payload"}

    CharacterRoles.objects.update_or_create(
        character_id=character_id,
        defaults={
            "owner_user": ownership.user,
            "corporation_id": getattr(ownership.character, "corporation_id", None),
            "roles": _coerce_role_list(payload.get("roles")),
            "roles_at_hq": _coerce_role_list(payload.get("roles_at_hq")),
            "roles_at_base": _coerce_role_list(payload.get("roles_at_base")),
            "roles_at_other": _coerce_role_list(payload.get("roles_at_other")),
        },
    )
    emit_analytics_event(
        task="user.update_character_roles",
        label="updated",
        result="success",
    )
    return {"status": "updated"}


@shared_task
def update_user_roles_snapshots(user_id: int) -> dict[str, int]:
    """Refresh role snapshots for all characters of a user."""
    user = User.objects.filter(id=user_id).first()
    if not user or not _is_user_active(user):
        return {"updated": 0, "skipped": 1, "failures": 0}

    ownerships = (
        CharacterOwnership.objects.filter(user_id=user_id)
        .select_related("character")
        .values_list("character__character_id", flat=True)
        .distinct()
    )
    updated = 0
    skipped = 0
    failures = 0
    for character_id in ownerships:
        if not character_id:
            skipped += 1
            continue
        result = update_character_roles_for_character(int(user_id), int(character_id))
        status = result.get("status") if isinstance(result, dict) else None
        if status == "updated":
            updated += 1
        elif status == "failed":
            failures += 1
        else:
            skipped += 1

    emit_analytics_event(
        task="user.update_user_roles_snapshots",
        label="completed",
        result="success" if failures == 0 else "warning",
        value=max(updated, 1),
    )
    return {"updated": updated, "skipped": skipped, "failures": failures}


@shared_task
def warm_menu_badge_count_cache(user_id: int) -> dict[str, int]:
    """Compute and cache Indy Hub menu badge count for one user."""
    # Django
    from django.core.cache import cache

    user_id = int(user_id)
    cache_key = f"indy_hub:menu_badge_count:{user_id}"
    refresh_lock_key = f"indy_hub:menu_badge_count_refreshing:{user_id}"

    count = 0
    try:
        count = compute_menu_badge_count(user_id)
        cache.set(cache_key, count, _MENU_BADGE_CACHE_TTL_SECONDS)
    finally:
        # Best-effort unlock so later refreshes can be scheduled.
        cache.delete(refresh_lock_key)

    return {"user_id": user_id, "count": int(count)}
