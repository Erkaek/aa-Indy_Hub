"""Helpers for Indy Hub admin user access and scope checks."""

from __future__ import annotations

# Standard Library
from collections import defaultdict

# Alliance Auth
from esi.models import Token

PROBLEM_SEVERITY_BLOCKING = "blocking"
PROBLEM_SEVERITY_COMFORT = "comfort"
_SCOPE_REQUIREMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "blueprints",
        ("esi-characters.read_blueprints.v1", "esi-universe.read_structures.v1"),
    ),
    (
        "jobs",
        ("esi-industry.read_character_jobs.v1", "esi-universe.read_structures.v1"),
    ),
    ("assets", ("esi-assets.read_assets.v1",)),
    ("skills", ("esi-skills.read_skills.v1",)),
    ("online", ("esi-location.read_online.v1",)),
)


def _build_scope_status_from_scope_names(scope_names: set[str]) -> dict[str, object]:
    flags: dict[str, bool] = {}
    missing_labels: list[str] = []

    for label, required_scopes in _SCOPE_REQUIREMENTS:
        has_scope = all(scope_name in scope_names for scope_name in required_scopes)
        flags[label] = has_scope
        if not has_scope:
            missing_labels.append(label)

    return {
        "flags": flags,
        "is_complete": all(flags.values()),
        "missing_labels": missing_labels,
    }


def _merge_scope_statuses(
    statuses: list[dict[str, object]],
) -> dict[str, object]:
    if not statuses:
        return _build_scope_status_from_scope_names(set())

    merged_flags: dict[str, bool] = {
        label: False for label, _required_scopes in _SCOPE_REQUIREMENTS
    }
    for status in statuses:
        status_flags = status.get("flags") or {}
        for label in merged_flags:
            merged_flags[label] = merged_flags[label] or bool(status_flags.get(label))

    missing_labels = [
        label for label, has_scope in merged_flags.items() if not has_scope
    ]
    return {
        "flags": merged_flags,
        "is_complete": all(merged_flags.values()),
        "missing_labels": missing_labels,
    }


def collect_user_scope_status_map(user_ids: list[int]) -> dict[int, dict[str, object]]:
    normalized_user_ids = [int(user_id) for user_id in user_ids if user_id]
    scope_statuses_by_user_id: dict[int, list[dict[str, object]]] = defaultdict(list)

    if normalized_user_ids:
        tokens = (
            Token.objects.filter(user_id__in=normalized_user_ids)
            .require_valid()
            .prefetch_related("scopes")
        )
        for token in tokens:
            scope_names = {
                str(scope.name)
                for scope in token.scopes.all()
                if getattr(scope, "name", None)
            }
            scope_statuses_by_user_id[int(token.user_id)].append(
                _build_scope_status_from_scope_names(scope_names)
            )

    return {
        int(user_id): _merge_scope_statuses(
            scope_statuses_by_user_id.get(int(user_id), [])
        )
        for user_id in normalized_user_ids
    }


def collect_user_scope_status(user) -> dict[str, object]:
    user_id = getattr(user, "id", None)
    if not user_id:
        return _build_scope_status_from_scope_names(set())
    return collect_user_scope_status_map([int(user_id)]).get(
        int(user_id),
        _build_scope_status_from_scope_names(set()),
    )


def detect_admin_user_reminder_problems(
    *,
    missing_scopes: list[str] | None,
    notifications_off: bool,
    is_inactive: bool,
    health_level: str,
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []

    if missing_scopes:
        scope_list = ", ".join(missing_scopes)
        problems.append(
            {
                "severity": PROBLEM_SEVERITY_BLOCKING,
                "problem": f"Missing required Indy Hub scopes: {scope_list}.",
                "action": "Re-authorize the missing scopes from Indy Hub token management.",
            }
        )

    if notifications_off:
        problems.append(
            {
                "severity": PROBLEM_SEVERITY_COMFORT,
                "problem": "Industry job notifications are currently disabled.",
                "action": "Review Indy Hub settings and enable the notification cadence you want.",
            }
        )

    if is_inactive:
        problems.append(
            {
                "severity": PROBLEM_SEVERITY_COMFORT,
                "problem": "No recent Indy Hub activity was detected for this user.",
                "action": "Open Indy Hub, review the current setup, and confirm it is still actively used.",
            }
        )

    if not problems and health_level == "critical":
        problems.append(
            {
                "severity": PROBLEM_SEVERITY_BLOCKING,
                "problem": "Indy Hub health is critical based on the current account state.",
                "action": "Review Indy Hub settings and reconnect anything that is out of date.",
            }
        )

    return problems
