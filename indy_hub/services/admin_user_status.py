"""Maintain the local read model used by the admin-users listing."""

from __future__ import annotations

# Django
from django.contrib.auth import get_user_model
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import UserProfile

# AA Example App
from indy_hub.models import AdminUserStatus, CharacterSettings, IndyHubUserUsage
from indy_hub.services.admin_user_bulk_actions import collect_user_scope_status_map
from indy_hub.services.user_health import build_user_settings_health_status

User = get_user_model()

_STATUS_UPDATE_FIELDS = [
    "main_character_id",
    "main_character_name",
    "corporation_id",
    "corporation_name",
    "scope_blueprints",
    "scope_jobs",
    "scope_assets",
    "scope_skills",
    "scope_online",
    "scope_complete",
    "scope_score",
    "settings_score",
    "notifications_enabled",
    "last_used_at",
    "activity_30d_count",
    "total_usage_count",
    "updated_at",
]


def rebuild_admin_user_statuses(user_ids: list[int]) -> int:
    """Rebuild a bounded set of statuses using database-local state only."""

    normalized_ids = sorted({int(user_id) for user_id in user_ids if user_id})
    if not normalized_ids:
        return 0

    existing_user_ids = list(
        User.objects.filter(id__in=normalized_ids).values_list("id", flat=True)
    )
    if not existing_user_ids:
        return 0

    profile_by_user_id = {
        int(row["user_id"]): row
        for row in UserProfile.objects.filter(user_id__in=existing_user_ids).values(
            "user_id",
            "main_character__character_id",
            "main_character__character_name",
            "main_character__corporation_id",
            "main_character__corporation_name",
        )
    }
    settings_by_user_id = {
        int(obj.user_id): obj
        for obj in CharacterSettings.objects.filter(
            user_id__in=existing_user_ids,
            character_id=0,
        ).only(
            "user_id",
            "copy_sharing_scope",
            "allow_copy_requests",
            "jobs_notify_frequency",
            "jobs_notify_completed",
        )
    }
    usage_by_user_id = {
        int(obj.user_id): obj
        for obj in IndyHubUserUsage.objects.filter(user_id__in=existing_user_ids).only(
            "user_id",
            "last_used_at",
            "activity_30d_count",
            "total_usage_count",
        )
    }
    scope_by_user_id = collect_user_scope_status_map(existing_user_ids)
    existing_statuses = {
        int(status.user_id): status
        for status in AdminUserStatus.objects.filter(user_id__in=existing_user_ids)
    }

    now = timezone.now()
    new_statuses: list[AdminUserStatus] = []
    updated_statuses: list[AdminUserStatus] = []
    for user_id in existing_user_ids:
        normalized_user_id = int(user_id)
        profile = profile_by_user_id.get(normalized_user_id) or {}
        settings_status = build_user_settings_health_status(
            settings_by_user_id.get(normalized_user_id)
        )
        usage = usage_by_user_id.get(normalized_user_id)
        scope_status = scope_by_user_id.get(normalized_user_id) or {}
        scope_flags = dict(scope_status.get("flags") or {})

        status = existing_statuses.get(normalized_user_id)
        if status is None:
            status = AdminUserStatus(user_id=normalized_user_id)
            new_statuses.append(status)
        else:
            updated_statuses.append(status)

        status.main_character_id = profile.get("main_character__character_id")
        status.main_character_name = str(
            profile.get("main_character__character_name") or ""
        )
        status.corporation_id = profile.get("main_character__corporation_id")
        status.corporation_name = str(
            profile.get("main_character__corporation_name") or ""
        )
        status.scope_blueprints = bool(scope_flags.get("blueprints"))
        status.scope_jobs = bool(scope_flags.get("jobs"))
        status.scope_assets = bool(scope_flags.get("assets"))
        status.scope_skills = bool(scope_flags.get("skills"))
        # Retained for schema compatibility; online status is no longer an
        # Indy Hub authorization requirement.
        status.scope_online = False
        status.scope_complete = bool(scope_status.get("is_complete"))
        current_scope_count = sum(
            [
                status.scope_blueprints,
                status.scope_jobs,
                status.scope_assets,
                status.scope_skills,
            ]
        )
        status.scope_score = {0: 0, 1: 12, 2: 25, 3: 38, 4: 50}[current_scope_count]
        status.settings_score = int(settings_status["score"])
        status.notifications_enabled = bool(settings_status["notifications_enabled"])
        status.last_used_at = usage.last_used_at if usage else None
        status.activity_30d_count = int(usage.activity_30d_count or 0) if usage else 0
        status.total_usage_count = int(usage.total_usage_count or 0) if usage else 0
        status.updated_at = now

    if new_statuses:
        AdminUserStatus.objects.bulk_create(new_statuses, batch_size=500)
    if updated_statuses:
        AdminUserStatus.objects.bulk_update(
            updated_statuses,
            _STATUS_UPDATE_FIELDS,
            batch_size=500,
        )
    return len(existing_user_ids)


def update_admin_user_status_usage(usage: IndyHubUserUsage) -> None:
    """Apply a cheap usage-only update, rebuilding if the row is not present yet."""

    updated = AdminUserStatus.objects.filter(user_id=usage.user_id).update(
        last_used_at=usage.last_used_at,
        activity_30d_count=int(usage.activity_30d_count or 0),
        total_usage_count=int(usage.total_usage_count or 0),
        updated_at=timezone.now(),
    )
    if not updated:
        rebuild_admin_user_statuses([int(usage.user_id)])
