"""Utility helpers used by industry Celery tasks."""

# Standard Library
from datetime import datetime
from datetime import timezone as dt_timezone

# Django
from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def is_deadlock_error(exc: Exception) -> bool:
    if getattr(exc, "args", None):
        code = exc.args[0]
        if code in {1205, 1213}:
            return True
    message = str(exc)
    return "Deadlock found" in message or "Lock wait timeout exceeded" in message


def fetch_corptools_activity_rows(
    character_ids: list[int],
    *,
    logger,
) -> list[tuple[int, object]] | None:
    placeholders = ", ".join(["%s"] * len(character_ids))
    query = f"""
        SELECT ec.character_id, cca.last_known_login
        FROM corptools_characteraudit cca
        JOIN eveonline_evecharacter ec ON ec.id = cca.character_id
        WHERE ec.character_id IN ({placeholders})
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query, character_ids)
            return list(cursor.fetchall())
    except Exception as exc:  # pragma: no cover - optional integration
        logger.debug("Corptools activity lookup unavailable: %s", exc)
        return None


def coerce_online_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_datetime(value)
        if dt is None:
            return None
    else:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


def normalized_roles(roles: list[str] | tuple[str, ...] | None) -> set[str]:
    if not roles:
        return set()
    return {str(role).upper() for role in roles if role}


def coerce_mapping(payload: object) -> dict:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    for attr_name in ("model_dump", "dict", "to_dict"):
        func = getattr(payload, attr_name, None)
        if callable(func):
            try:
                data = func()
            except TypeError:
                data = func
            if isinstance(data, dict):
                return data
    try:
        return dict(payload)
    except Exception:
        return {}


def build_skill_level_map(skills: list[dict]) -> dict[int, dict[str, int]]:
    levels: dict[int, dict[str, int]] = {}
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_id = skill.get("skill_id")
        if not skill_id:
            continue
        active_level = int(skill.get("active_skill_level") or 0)
        trained_level = int(skill.get("trained_skill_level") or 0)
        levels[int(skill_id)] = {"active": active_level, "trained": trained_level}
    return levels


def extract_role_payload(payload: dict) -> dict[str, list[str]]:
    def _coerce_list(value: object) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item]
        return []

    return {
        "roles": _coerce_list(payload.get("roles")),
        "roles_at_hq": _coerce_list(payload.get("roles_at_hq")),
        "roles_at_base": _coerce_list(payload.get("roles_at_base")),
        "roles_at_other": _coerce_list(payload.get("roles_at_other")),
    }


def roles_from_snapshot(snapshot) -> set[str]:
    collected: set[str] = set()
    for key in ("roles", "roles_at_hq", "roles_at_base", "roles_at_other"):
        collected.update(normalized_roles(getattr(snapshot, key, None)))
    return collected
