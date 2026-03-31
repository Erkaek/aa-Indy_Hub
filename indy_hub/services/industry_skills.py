"""Industry skill snapshot helpers and craft capability resolution."""

from __future__ import annotations

# Standard Library
import re
from functools import lru_cache
from typing import Any

# Django
from django.db import connection
from django.db.models import Count
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from esi.models import Token

# AA Example App
from indy_hub.models import (
    Blueprint,
    IndustryActivityMixin,
    IndustryJob,
    IndustrySkillSnapshot,
)
from indy_hub.services.esi_client import ESITokenError, ESIUnmodifiedError
from indy_hub.utils.eve import get_character_name, get_type_name

SKILLS_SCOPE = "esi-skills.read_skills.v1"

SKILL_TYPE_IDS = {
    "industry": 3380,
    "mass_production": 3387,
    "advanced_industry": 3388,
    "science": 3402,
    "research": 3403,
    "laboratory_operation": 3406,
    "metallurgy": 3409,
    "supply_chain_management": 24268,
    "scientific_networking": 24270,
    "advanced_laboratory_operation": 24624,
    "advanced_mass_production": 24625,
    "reactions": 45746,
    "mass_reactions": 45748,
    "advanced_mass_reactions": 45749,
    "remote_reactions": 45750,
}

LEGACY_SNAPSHOT_FIELDS = {
    "mass_production_level": SKILL_TYPE_IDS["mass_production"],
    "advanced_mass_production_level": SKILL_TYPE_IDS["advanced_mass_production"],
    "laboratory_operation_level": SKILL_TYPE_IDS["laboratory_operation"],
    "advanced_laboratory_operation_level": SKILL_TYPE_IDS[
        "advanced_laboratory_operation"
    ],
    "mass_reactions_level": SKILL_TYPE_IDS["mass_reactions"],
    "advanced_mass_reactions_level": SKILL_TYPE_IDS["advanced_mass_reactions"],
}

LEGACY_TRAINED_SNAPSHOT_FIELDS = {
    "trained_mass_production_level": SKILL_TYPE_IDS["mass_production"],
    "trained_advanced_mass_production_level": SKILL_TYPE_IDS[
        "advanced_mass_production"
    ],
    "trained_laboratory_operation_level": SKILL_TYPE_IDS["laboratory_operation"],
    "trained_advanced_laboratory_operation_level": SKILL_TYPE_IDS[
        "advanced_laboratory_operation"
    ],
    "trained_mass_reactions_level": SKILL_TYPE_IDS["mass_reactions"],
    "trained_advanced_mass_reactions_level": SKILL_TYPE_IDS["advanced_mass_reactions"],
}

MANUFACTURING_ACTIVITY_IDS = {IndustryActivityMixin.ACTIVITY_MANUFACTURING}
RESEARCH_ACTIVITY_IDS = {
    IndustryActivityMixin.ACTIVITY_TE_RESEARCH,
    IndustryActivityMixin.ACTIVITY_ME_RESEARCH,
    IndustryActivityMixin.ACTIVITY_COPYING,
    IndustryActivityMixin.ACTIVITY_INVENTION,
}
REACTION_ACTIVITY_IDS = {
    IndustryActivityMixin.ACTIVITY_REACTIONS,
    IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
}

_TIME_BONUS_ATTRIBUTE_NAMES = {
    "manufacturingTimeBonus",
    "advancedIndustrySkillIndustryJobTimeBonus",
    "copySpeedBonus",
    "blueprintmanufactureTimeBonus",
    "mineralNeedResearchBonus",
    "reactionTimeBonus",
}
_SLOT_BONUS_ATTRIBUTE_NAMES = {
    "manufacturingSlotBonus",
    "laboratorySlotsBonus",
    "reactionSlotBonus",
}
_RELEVANT_SKILL_ATTRIBUTE_NAMES = (
    _TIME_BONUS_ATTRIBUTE_NAMES | _SLOT_BONUS_ATTRIBUTE_NAMES
)


def normalize_skill_levels(
    levels: dict[int, dict[str, int]] | dict[str, dict[str, int]] | None,
) -> dict[str, dict[str, int]]:
    normalized: dict[str, dict[str, int]] = {}
    for raw_skill_id, entry in (levels or {}).items():
        try:
            skill_id = int(raw_skill_id)
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict):
            active_level = int(entry.get("active") or 0)
            trained_level = int(entry.get("trained") or 0)
        else:
            active_level = int(entry or 0)
            trained_level = active_level
        normalized[str(skill_id)] = {
            "active": max(active_level, 0),
            "trained": max(trained_level, 0),
        }
    return normalized


def get_skill_level_from_mapping(
    levels: dict[str, dict[str, int]] | dict[int, dict[str, int]] | None,
    skill_id: int,
    *,
    trained: bool = False,
) -> int:
    normalized_skill_id = str(int(skill_id or 0))
    entry = (levels or {}).get(normalized_skill_id) or (levels or {}).get(
        int(skill_id or 0)
    )
    if not isinstance(entry, dict):
        return 0
    key = "trained" if trained else "active"
    return max(int(entry.get(key) or 0), 0)


def get_snapshot_skill_level(
    snapshot: IndustrySkillSnapshot | None,
    skill_id: int,
    *,
    trained: bool = False,
) -> int:
    if snapshot is None:
        return 0

    stored_levels = getattr(snapshot, "skill_levels", {}) or {}
    if stored_levels:
        return get_skill_level_from_mapping(stored_levels, skill_id, trained=trained)

    fallback_fields = (
        LEGACY_TRAINED_SNAPSHOT_FIELDS if trained else LEGACY_SNAPSHOT_FIELDS
    )
    for field_name, mapped_skill_id in fallback_fields.items():
        if mapped_skill_id == int(skill_id or 0):
            return max(int(getattr(snapshot, field_name, 0) or 0), 0)
    return 0


def build_skill_snapshot_defaults(
    levels: dict[int, dict[str, int]] | dict[str, dict[str, int]] | None,
) -> dict[str, object]:
    normalized_levels = normalize_skill_levels(levels)

    defaults: dict[str, object] = {"skill_levels": normalized_levels}
    for field_name, skill_id in LEGACY_SNAPSHOT_FIELDS.items():
        defaults[field_name] = get_skill_level_from_mapping(normalized_levels, skill_id)
    for field_name, skill_id in LEGACY_TRAINED_SNAPSHOT_FIELDS.items():
        defaults[field_name] = get_skill_level_from_mapping(
            normalized_levels,
            skill_id,
            trained=True,
        )
    return defaults


def skill_snapshot_stale(
    snapshot: IndustrySkillSnapshot | None, skill_cache_ttl
) -> bool:
    if not snapshot:
        return True
    if not getattr(snapshot, "skill_levels", None):
        return True
    return timezone.now() - snapshot.last_updated > skill_cache_ttl


def _slots_payload(total_value: int | None, used_value: int) -> dict[str, int | None]:
    if total_value is None:
        return {"total": None, "available": None, "used": None, "percent_used": 0}
    used_clamped = min(max(int(used_value or 0), 0), int(total_value))
    available = max(int(total_value) - used_clamped, 0)
    percent_used = int(round((used_clamped / total_value) * 100)) if total_value else 0
    return {
        "total": int(total_value),
        "available": available,
        "used": used_clamped,
        "percent_used": percent_used,
    }


def build_user_character_skill_contexts(
    user,
    *,
    fetch_character_skill_levels=None,
    update_skill_snapshot=None,
    skill_cache_ttl,
) -> list[dict[str, object]]:
    ownerships = CharacterOwnership.objects.filter(user=user).select_related(
        "character"
    )
    character_ids = [
        ownership.character.character_id
        for ownership in ownerships
        if ownership.character
    ]
    now = timezone.now()

    snapshots = {
        snapshot.character_id: snapshot
        for snapshot in IndustrySkillSnapshot.objects.filter(
            owner_user=user,
            character_id__in=character_ids,
        )
    }
    skill_token_ids = set(
        Token.objects.filter(user=user, character_id__in=character_ids)
        .require_scopes([SKILLS_SCOPE])
        .require_valid()
        .values_list("character_id", flat=True)
    )

    active_job_rows = (
        IndustryJob.objects.filter(
            owner_user=user,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            status="active",
            end_date__gt=now,
            character_id__in=character_ids,
        )
        .values("character_id", "activity_id")
        .annotate(total=Count("id"))
    )
    used_counts: dict[int, dict[str, int]] = {
        int(char_id): {"manufacturing": 0, "research": 0, "reactions": 0}
        for char_id in character_ids
    }
    for row in active_job_rows:
        char_id = int(row.get("character_id") or 0)
        activity_id = int(row.get("activity_id") or 0)
        total = int(row.get("total") or 0)
        if char_id not in used_counts:
            continue
        if activity_id in MANUFACTURING_ACTIVITY_IDS:
            used_counts[char_id]["manufacturing"] += total
        elif activity_id in RESEARCH_ACTIVITY_IDS:
            used_counts[char_id]["research"] += total
        elif activity_id in REACTION_ACTIVITY_IDS:
            used_counts[char_id]["reactions"] += total

    rows: list[dict[str, object]] = []
    for ownership in ownerships:
        char = ownership.character
        if not char:
            continue

        character_id = int(char.character_id)
        snapshot = snapshots.get(character_id)
        has_skill_token = character_id in skill_token_ids
        skills_missing = not has_skill_token

        if has_skill_token and fetch_character_skill_levels and update_skill_snapshot:
            try:
                if snapshot is None:
                    levels = fetch_character_skill_levels(
                        character_id, force_refresh=True
                    )
                    snapshot = update_skill_snapshot(user, character_id, levels)
                elif skill_snapshot_stale(snapshot, skill_cache_ttl):
                    levels = fetch_character_skill_levels(character_id)
                    snapshot = update_skill_snapshot(user, character_id, levels)
            except (ESIUnmodifiedError, ESITokenError):
                if snapshot is None:
                    skills_missing = True
            except Exception:
                if snapshot is None:
                    skills_missing = True

        if skills_missing:
            snapshot = None

        used = used_counts.get(
            character_id, {"manufacturing": 0, "research": 0, "reactions": 0}
        )
        manufacturing_total = snapshot.manufacturing_slots if snapshot else None
        research_total = snapshot.research_slots if snapshot else None
        reaction_total = snapshot.reaction_slots if snapshot else None

        rows.append(
            {
                "character_id": character_id,
                "name": get_character_name(character_id),
                "skills_missing": skills_missing,
                "snapshot": snapshot,
                "skill_levels": (
                    getattr(snapshot, "skill_levels", {}) if snapshot else {}
                ),
                "manufacturing": _slots_payload(
                    manufacturing_total, used["manufacturing"]
                ),
                "research": _slots_payload(research_total, used["research"]),
                "reactions": _slots_payload(reaction_total, used["reactions"]),
            }
        )

    return rows


def _activity_slot_key(activity_id: int) -> str | None:
    numeric_activity_id = int(activity_id or 0)
    if numeric_activity_id in MANUFACTURING_ACTIVITY_IDS:
        return "manufacturing"
    if numeric_activity_id in RESEARCH_ACTIVITY_IDS:
        return "research"
    if numeric_activity_id in REACTION_ACTIVITY_IDS:
        return "reactions"
    return None


@lru_cache(maxsize=1)
def _required_skill_attribute_ids() -> dict[str, dict[int, int]]:
    mapping = {"skill": {}, "level": {}}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name
            FROM eve_sde_dogmaattribute
            WHERE name LIKE 'requiredSkill%'
            """
        )
        for attribute_id, name in cursor.fetchall():
            match = re.fullmatch(r"requiredSkill(\d+)(Level)?", str(name or ""))
            if not match:
                continue
            index = int(match.group(1))
            bucket = "level" if match.group(2) else "skill"
            mapping[bucket][index] = int(attribute_id)
    return mapping


def fetch_blueprint_skill_requirements(
    blueprint_type_ids: set[int] | list[int] | tuple[int, ...],
) -> dict[int, list[dict[str, object]]]:
    numeric_blueprint_type_ids = sorted(
        {
            int(type_id)
            for type_id in (blueprint_type_ids or [])
            if int(type_id or 0) > 0
        }
    )
    if not numeric_blueprint_type_ids:
        return {}

    attribute_ids = _required_skill_attribute_ids()
    relevant_attribute_ids = sorted(
        set(attribute_ids["skill"].values()) | set(attribute_ids["level"].values())
    )
    if not relevant_attribute_ids:
        return {}

    blueprint_placeholders = ", ".join(["%s"] * len(numeric_blueprint_type_ids))
    attribute_placeholders = ", ".join(["%s"] * len(relevant_attribute_ids))

    requirements_by_blueprint: dict[int, dict[int, dict[str, int]]] = {
        blueprint_type_id: {} for blueprint_type_id in numeric_blueprint_type_ids
    }
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT item_type_id, dogma_attribute_id, value
            FROM eve_sde_typedogma
            WHERE item_type_id IN ({blueprint_placeholders})
                        AND dogma_attribute_id IN ({attribute_placeholders})
            """,
            [*numeric_blueprint_type_ids, *relevant_attribute_ids],
        )

        rows = cursor.fetchall()

    skill_attribute_lookup = {
        value: key for key, value in attribute_ids["skill"].items()
    }
    level_attribute_lookup = {
        value: key for key, value in attribute_ids["level"].items()
    }

    for blueprint_type_id, attribute_id, value in rows:
        numeric_blueprint_type_id = int(blueprint_type_id or 0)
        numeric_attribute_id = int(attribute_id or 0)
        if numeric_attribute_id in skill_attribute_lookup:
            index = skill_attribute_lookup[numeric_attribute_id]
            requirements_by_blueprint[numeric_blueprint_type_id].setdefault(index, {})[
                "skill_id"
            ] = int(value or 0)
        elif numeric_attribute_id in level_attribute_lookup:
            index = level_attribute_lookup[numeric_attribute_id]
            requirements_by_blueprint[numeric_blueprint_type_id].setdefault(index, {})[
                "level"
            ] = int(value or 0)

    resolved: dict[int, list[dict[str, object]]] = {}
    for blueprint_type_id, entries in requirements_by_blueprint.items():
        requirements = []
        for index in sorted(entries.keys()):
            entry = entries[index]
            skill_id = int(entry.get("skill_id") or 0)
            level = int(entry.get("level") or 0)
            if skill_id <= 0 or level <= 0:
                continue
            requirements.append(
                {
                    "skill_id": skill_id,
                    "level": level,
                    "skill_name": get_type_name(skill_id),
                }
            )
        resolved[blueprint_type_id] = requirements
    return resolved


def fetch_skill_bonus_attributes(
    skill_type_ids: set[int] | list[int] | tuple[int, ...],
) -> dict[int, dict[str, float]]:
    numeric_skill_type_ids = sorted(
        {
            int(skill_type_id)
            for skill_type_id in (skill_type_ids or [])
            if int(skill_type_id or 0) > 0
        }
    )
    if not numeric_skill_type_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(numeric_skill_type_ids))
    attr_placeholders = ", ".join(["%s"] * len(_RELEVANT_SKILL_ATTRIBUTE_NAMES))

    bonus_map = {skill_type_id: {} for skill_type_id in numeric_skill_type_ids}
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT td.item_type_id, da.name, td.value
            FROM eve_sde_typedogma td
            JOIN eve_sde_dogmaattribute da ON da.id = td.dogma_attribute_id
            WHERE td.item_type_id IN ({placeholders})
                        AND da.name IN ({attr_placeholders})
            """,
            [*numeric_skill_type_ids, *_RELEVANT_SKILL_ATTRIBUTE_NAMES],
        )

        for skill_type_id, attribute_name, value in cursor.fetchall():
            bonus_map[int(skill_type_id)][str(attribute_name)] = float(value or 0)
    return bonus_map


def _normalized_bonus_percent(raw_value: float | int | None) -> float:
    numeric_value = float(raw_value or 0)
    if numeric_value < 0:
        return abs(numeric_value)
    return numeric_value


def missing_skill_requirements(
    skill_levels: dict[str, dict[str, int]] | dict[int, dict[str, int]] | None,
    requirements: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    missing: list[dict[str, object]] = []
    for requirement in requirements or []:
        skill_id = int(requirement.get("skill_id") or 0)
        required_level = int(requirement.get("level") or 0)
        if skill_id <= 0 or required_level <= 0:
            continue
        current_level = get_skill_level_from_mapping(skill_levels, skill_id)
        if current_level >= required_level:
            continue
        missing.append(
            {
                "skill_id": skill_id,
                "skill_name": requirement.get("skill_name") or get_type_name(skill_id),
                "required_level": required_level,
                "current_level": current_level,
            }
        )
    return missing


def compute_activity_time_bonus_percent(
    skill_levels: dict[str, dict[str, int]] | dict[int, dict[str, int]] | None,
    *,
    activity_id: int,
    required_skill_ids: set[int] | list[int] | tuple[int, ...] | None,
    skill_bonus_attributes: dict[int, dict[str, float]] | None,
) -> float:
    numeric_activity_id = int(activity_id or 0)
    required_skill_id_set = {
        int(skill_id)
        for skill_id in (required_skill_ids or [])
        if int(skill_id or 0) > 0
    }
    total_bonus = 0.0

    for skill_id, attribute_values in (skill_bonus_attributes or {}).items():
        level = get_skill_level_from_mapping(skill_levels, int(skill_id))
        if level <= 0:
            continue

        if (
            numeric_activity_id in MANUFACTURING_ACTIVITY_IDS | RESEARCH_ACTIVITY_IDS
            and "advancedIndustrySkillIndustryJobTimeBonus" in attribute_values
        ):
            total_bonus += (
                _normalized_bonus_percent(
                    attribute_values["advancedIndustrySkillIndustryJobTimeBonus"]
                )
                * level
            )

        if numeric_activity_id in MANUFACTURING_ACTIVITY_IDS:
            if "manufacturingTimeBonus" in attribute_values and (
                int(skill_id) == SKILL_TYPE_IDS["industry"]
                or int(skill_id) in required_skill_id_set
            ):
                total_bonus += (
                    _normalized_bonus_percent(
                        attribute_values["manufacturingTimeBonus"]
                    )
                    * level
                )
        elif numeric_activity_id == IndustryActivityMixin.ACTIVITY_COPYING:
            if "copySpeedBonus" in attribute_values:
                total_bonus += (
                    _normalized_bonus_percent(attribute_values["copySpeedBonus"])
                    * level
                )
        elif numeric_activity_id == IndustryActivityMixin.ACTIVITY_TE_RESEARCH:
            if "blueprintmanufactureTimeBonus" in attribute_values:
                total_bonus += (
                    _normalized_bonus_percent(
                        attribute_values["blueprintmanufactureTimeBonus"]
                    )
                    * level
                )
        elif numeric_activity_id == IndustryActivityMixin.ACTIVITY_ME_RESEARCH:
            if "mineralNeedResearchBonus" in attribute_values:
                total_bonus += (
                    _normalized_bonus_percent(
                        attribute_values["mineralNeedResearchBonus"]
                    )
                    * level
                )
        elif numeric_activity_id in REACTION_ACTIVITY_IDS:
            if "reactionTimeBonus" in attribute_values:
                total_bonus += (
                    _normalized_bonus_percent(attribute_values["reactionTimeBonus"])
                    * level
                )

    return round(total_bonus, 2)


def build_craft_character_advisor(
    *,
    user,
    production_time_map: dict[int, dict[str, Any]] | dict[str, dict[str, Any]] | None,
    fetch_character_skill_levels=None,
    update_skill_snapshot=None,
    skill_cache_ttl,
) -> dict[str, object]:
    entries = [
        entry
        for entry in (production_time_map or {}).values()
        if isinstance(entry, dict)
    ]
    if not entries:
        return {
            "characters": [],
            "items": {},
            "summary": {
                "characters": 0,
                "eligible_items": 0,
                "blocked_items": 0,
                "missing_skill_data_characters": 0,
            },
        }

    character_contexts = build_user_character_skill_contexts(
        user,
        fetch_character_skill_levels=fetch_character_skill_levels,
        update_skill_snapshot=update_skill_snapshot,
        skill_cache_ttl=skill_cache_ttl,
    )

    blueprint_type_ids = {
        int(entry.get("blueprint_type_id") or entry.get("blueprintTypeId") or 0)
        for entry in entries
        if int(entry.get("blueprint_type_id") or entry.get("blueprintTypeId") or 0) > 0
    }
    requirement_map = fetch_blueprint_skill_requirements(blueprint_type_ids)
    relevant_skill_ids = {
        SKILL_TYPE_IDS["industry"],
        SKILL_TYPE_IDS["advanced_industry"],
        SKILL_TYPE_IDS["science"],
        SKILL_TYPE_IDS["research"],
        SKILL_TYPE_IDS["metallurgy"],
        SKILL_TYPE_IDS["reactions"],
    }
    for requirements in requirement_map.values():
        for requirement in requirements:
            relevant_skill_ids.add(int(requirement.get("skill_id") or 0))
    skill_bonus_attributes = fetch_skill_bonus_attributes(relevant_skill_ids)

    serialized_characters = [
        {
            "character_id": int(row["character_id"]),
            "name": row["name"],
            "skills_missing": bool(row["skills_missing"]),
            "manufacturing": row["manufacturing"],
            "research": row["research"],
            "reactions": row["reactions"],
        }
        for row in character_contexts
    ]

    items: dict[str, dict[str, object]] = {}
    eligible_item_count = 0
    blocked_item_count = 0

    for entry in entries:
        type_id = int(entry.get("type_id") or entry.get("typeId") or 0)
        blueprint_type_id = int(
            entry.get("blueprint_type_id") or entry.get("blueprintTypeId") or 0
        )
        activity_id = int(entry.get("activity_id") or entry.get("activityId") or 0)
        if type_id <= 0 or activity_id <= 0:
            continue

        slot_key = _activity_slot_key(activity_id)
        requirements = requirement_map.get(blueprint_type_id, [])
        required_skill_ids = {
            int(requirement.get("skill_id") or 0) for requirement in requirements
        }
        eligible_characters: list[dict[str, object]] = []
        blocked_characters: list[dict[str, object]] = []

        for row in character_contexts:
            slot_payload = row.get(slot_key) if slot_key else None
            if row["skills_missing"]:
                blocked_characters.append(
                    {
                        "character_id": int(row["character_id"]),
                        "name": row["name"],
                        "reason": "skills_missing",
                        "missing_skills": [],
                    }
                )
                continue

            skill_levels = row.get("skill_levels") or {}
            missing = missing_skill_requirements(skill_levels, requirements)
            if missing:
                blocked_characters.append(
                    {
                        "character_id": int(row["character_id"]),
                        "name": row["name"],
                        "reason": "missing_requirements",
                        "missing_skills": missing,
                    }
                )
                continue

            total_slots = int((slot_payload or {}).get("total") or 0)
            available_slots = int((slot_payload or {}).get("available") or 0)
            used_slots = int((slot_payload or {}).get("used") or 0)
            if total_slots <= 0:
                blocked_characters.append(
                    {
                        "character_id": int(row["character_id"]),
                        "name": row["name"],
                        "reason": "no_slots",
                        "missing_skills": [],
                    }
                )
                continue

            eligible_characters.append(
                {
                    "character_id": int(row["character_id"]),
                    "name": row["name"],
                    "time_bonus_percent": compute_activity_time_bonus_percent(
                        skill_levels,
                        activity_id=activity_id,
                        required_skill_ids=required_skill_ids,
                        skill_bonus_attributes=skill_bonus_attributes,
                    ),
                    "total_slots": total_slots,
                    "available_slots": available_slots,
                    "used_slots": used_slots,
                }
            )

        eligible_characters.sort(
            key=lambda character: (
                -(1 if int(character.get("available_slots") or 0) > 0 else 0),
                -float(character.get("time_bonus_percent") or 0),
                -int(character.get("available_slots") or 0),
                -int(character.get("total_slots") or 0),
                str(character.get("name") or ""),
            )
        )
        best_character = eligible_characters[0] if eligible_characters else None

        items[str(type_id)] = {
            "type_id": type_id,
            "type_name": entry.get("type_name")
            or entry.get("typeName")
            or str(type_id),
            "blueprint_type_id": blueprint_type_id,
            "activity_id": activity_id,
            "required_skills": requirements,
            "eligible_characters": eligible_characters,
            "blocked_characters": blocked_characters,
            "best_character": best_character,
        }

        if best_character:
            eligible_item_count += 1
        else:
            blocked_item_count += 1

    return {
        "characters": serialized_characters,
        "items": items,
        "summary": {
            "characters": len(serialized_characters),
            "eligible_items": eligible_item_count,
            "blocked_items": blocked_item_count,
            "missing_skill_data_characters": len(
                [row for row in serialized_characters if row.get("skills_missing")]
            ),
        },
    }
