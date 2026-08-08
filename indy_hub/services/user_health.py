"""User health score helpers for Indy Hub admin views."""

from __future__ import annotations

# AA Example App
from indy_hub.models import CharacterSettings

SCOPE_WEIGHT_TOTAL = 50
PARAMETER_WEIGHT_TOTAL = 20
RECENT_ACTIVITY_WEIGHT_TOTAL = 30

HEALTH_LEVEL_GOOD = "good"
HEALTH_LEVEL_MEDIUM = "medium"
HEALTH_LEVEL_CRITICAL = "critical"


def _score_scope_coverage(scope_flags: dict[str, bool]) -> int:
    if not scope_flags:
        return 0
    total_scopes = len(scope_flags)
    if total_scopes <= 0:
        return 0
    enabled_count = sum(1 for value in scope_flags.values() if value)
    return round((enabled_count / total_scopes) * SCOPE_WEIGHT_TOTAL)


def build_user_settings_health_status(
    settings_obj: CharacterSettings | None,
) -> dict[str, int | bool]:
    """Return the settings-only scalars shared by health views and read models."""

    if settings_obj is None:
        return {"score": 0, "notifications_enabled": False}

    score = 0
    valid_scopes = dict(CharacterSettings.COPY_SHARING_SCOPE_CHOICES)
    valid_frequencies = dict(CharacterSettings.JOB_NOTIFICATION_FREQUENCY_CHOICES)

    sharing_scope = settings_obj.copy_sharing_scope
    if sharing_scope in valid_scopes:
        sharing_enabled = sharing_scope != CharacterSettings.SCOPE_NONE
        if bool(settings_obj.allow_copy_requests) == sharing_enabled:
            score += 10

    notify_frequency = settings_obj.jobs_notify_frequency
    notifications_enabled = notify_frequency in valid_frequencies and (
        notify_frequency != CharacterSettings.NOTIFY_DISABLED
    )
    if notify_frequency in valid_frequencies:
        if bool(settings_obj.jobs_notify_completed) == notifications_enabled:
            score += 10

    return {
        "score": score,
        "notifications_enabled": notifications_enabled,
    }


def _score_parameter_coherence(settings_obj: CharacterSettings | None) -> int:
    return int(build_user_settings_health_status(settings_obj)["score"])


def _score_recent_activity(*, is_inactive: bool, activity_30d_count: int) -> int:
    if is_inactive:
        return 0
    if int(activity_30d_count or 0) >= 5:
        return RECENT_ACTIVITY_WEIGHT_TOTAL
    return RECENT_ACTIVITY_WEIGHT_TOTAL // 2


def get_user_health_level(score: int) -> str:
    if score >= 80:
        return HEALTH_LEVEL_GOOD
    if score >= 50:
        return HEALTH_LEVEL_MEDIUM
    return HEALTH_LEVEL_CRITICAL


def build_user_health_score(
    *,
    scope_flags: dict[str, bool] | None,
    settings_obj: CharacterSettings | None,
    is_inactive: bool,
    activity_30d_count: int,
) -> dict[str, object]:
    """Build a simple and readable user health score out of 100."""

    scope_score = _score_scope_coverage(scope_flags or {})
    parameter_score = _score_parameter_coherence(settings_obj)
    activity_score = _score_recent_activity(
        is_inactive=is_inactive,
        activity_30d_count=activity_30d_count,
    )
    score = max(0, min(100, scope_score + parameter_score + activity_score))
    level = get_user_health_level(score)

    return {
        "score": score,
        "level": level,
        "breakdown": {
            "scope_coverage": scope_score,
            "parameter_coherence": parameter_score,
            "recent_activity": activity_score,
        },
    }
