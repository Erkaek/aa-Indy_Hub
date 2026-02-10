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

# Indy Hub
from ..models import Blueprint, CharacterRoles, CharacterSettings
from ..services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    ESIUnmodifiedError,
    get_retry_after_seconds,
    shared_client,
)
from .industry import (
    _get_adaptive_window_minutes,
    _is_user_active,
    _queue_staggered_user_tasks,
)

logger = get_extension_logger(__name__)

CORP_ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"

User = get_user_model()


@shared_task
def cleanup_inactive_user_data():
    """
    Clean up data for users who haven't been active for a long time.
    Runs weekly to maintain database performance.
    """
    # Define inactive threshold (6 months)
    inactive_threshold = timezone.now() - timedelta(days=180)

    # Since CharacterSettings don't track last_refresh_request anymore,
    # we'll identify inactive users by their Django last_login timestamp
    inactive_users = User.objects.filter(last_login__lt=inactive_threshold).exclude(
        last_login__isnull=True
    )

    count = 0
    for user in inactive_users:

        # Clean up old blueprint data for inactive users
        old_blueprints = Blueprint.objects.filter(
            owner_user=user, updated_at__lt=inactive_threshold
        )
        blueprint_count = old_blueprints.count()
        old_blueprints.delete()

        if blueprint_count > 0:
            count += 1
            logger.info(
                f"Cleaned up {blueprint_count} old blueprints for inactive user {user.username}"
            )

    logger.info(f"Cleaned up data for {count} inactive users")
    return {"inactive_users_cleaned": count}


@shared_task
def update_user_preferences_defaults():
    """
    Ensure all users have proper default notification preferences.
    Useful after adding new preference fields.
    """
    # Alternative: find users with no global settings (character_id=0)
    users_without_global_settings = User.objects.exclude(
        charactersettings__character_id=0
    )

    created = 0
    for user in users_without_global_settings:
        _, was_created = CharacterSettings.objects.get_or_create(
            user=user, character_id=0
        )
        if was_created:
            created += 1

    logger.info("Ensured global notification defaults for %s users", created)
    return {"defaults_created": created}


@shared_task
def generate_user_activity_report():
    """
    Generate activity statistics for users.
    Can be used for analytics and monitoring.
    """
    total_users = User.objects.count()
    # Since we no longer track last_refresh_request, use login activity for "active"
    active_users = (
        User.objects.filter(last_login__gte=timezone.now() - timedelta(days=30))
        .exclude(last_login__isnull=True)
        .count()
    )

    users_with_blueprints = (
        User.objects.filter(blueprints__isnull=False).distinct().count()
    )

    users_with_jobs = (
        User.objects.filter(industry_jobs__isnull=False).distinct().count()
    )

    users_with_notifications = CharacterSettings.objects.filter(
        character_id=0, jobs_notify_completed=True  # Global settings only
    ).count()

    report = {
        "total_users": total_users,
        "active_users_30d": active_users,
        "users_with_blueprints": users_with_blueprints,
        "users_with_jobs": users_with_jobs,
        "users_with_notifications_enabled": users_with_notifications,
        "generated_at": timezone.now().isoformat(),
    }

    logger.info(f"Generated user activity report: {report}")
    return report


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
    return {"status": "updated"}


@shared_task
def update_character_roles_snapshots(
    *, last_user_id: int | None = None, batch_size: int = 500
):
    """Queue per-user role refresh tasks in batches."""
    if batch_size <= 0:
        batch_size = 1

    user_qs = (
        Token.objects.all()
        .require_scopes([CORP_ROLES_SCOPE])
        .require_valid()
        .values_list("user_id", flat=True)
        .distinct()
        .order_by("user_id")
    )
    if last_user_id:
        user_qs = user_qs.filter(user_id__gt=last_user_id)

    user_ids = list(user_qs[:batch_size])
    if not user_ids:
        logger.info("No users remaining for role snapshot updates.")
        return {"queued": 0, "batch_size": batch_size, "done": True}

    window_minutes = _get_adaptive_window_minutes("roles", len(user_ids))
    queued = _queue_staggered_user_tasks(
        update_user_roles_snapshots,
        user_ids,
        window_minutes=window_minutes,
        priority=7,
    )

    if len(user_ids) == batch_size:
        update_character_roles_snapshots.apply_async(
            kwargs={"last_user_id": int(user_ids[-1]), "batch_size": batch_size},
            countdown=1,
        )

    logger.info(
        "Queued role refresh tasks for %s users (batch_size=%s, window=%s min)",
        queued,
        batch_size,
        window_minutes,
    )
    return {
        "queued": queued,
        "batch_size": batch_size,
        "window_minutes": window_minutes,
        "last_user_id": int(user_ids[-1]),
        "done": len(user_ids) < batch_size,
    }


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

    return {"updated": updated, "skipped": skipped, "failures": failures}
