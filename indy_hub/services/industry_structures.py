"""Industry structure dogma resolution and installation cost helpers."""

from __future__ import annotations

# Standard Library
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache, wraps

# Django
from django.core.exceptions import ValidationError
from django.db import connection

# AA Example App
from indy_hub.models import (
    IndustryActivityMixin,
    IndustryStructure,
    IndustrySystemCostIndex,
)
from indy_hub.services.industry_constants import (
    COPYING_JOB_COST_BASE_PERCENT,
    PERCENT_FACTOR,
    SCC_SURCHARGE_PERCENT,
)
from indy_hub.services.industry_rules import (
    INDUSTRY_RULES_VERSION,
    IndustryRulesError,
    get_industry_build_qualification,
    get_industry_rig_security_attribute_ids,
    get_industry_rule,
    get_industry_rules_for_hull,
    get_industry_rules_for_rig,
    get_industry_source_attribute_values,
    get_industry_source_metadata,
    is_known_industry_effect,
)
from indy_hub.services.industry_structure_primitives import (
    normalize_decimal as _normalize_decimal,
)
from indy_hub.services.industry_structure_primitives import (
    normalize_int as _normalize_int,
)
from indy_hub.services.industry_structure_primitives import round_isk as _round_isk
from indy_hub.services.industry_structure_primitives import (
    round_security_status as _round_security_status,
)
from indy_hub.services.industry_structure_primitives import (
    security_status_to_band,
)

# Backward-compatible export used by tests and callers importing from this module.
round_security_status = _round_security_status

DEFAULT_SCC_SURCHARGE_PERCENT = SCC_SURCHARGE_PERCENT
ALPHA_CLONE_TAX_PERCENT = Decimal("0.25")
# Bump when resolver semantics change without changing the pinned CCP dataset.
# Both structure and saved-project caches include this calculation token.
INDUSTRY_RESOLVER_VERSION = "resolver-v4"
_sde_cache_generation: str | None = None
_RULE_QUALIFICATION_SECTIONS = frozenset(
    {"TypeDogma", "TypeEffect", "DogmaEffect", "DogmaAttribute"}
)


def _rules_validation_errors(function):
    """Expose typed rule failures through the existing Django consumer boundary."""

    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except IndustryRulesError as exc:
            raise ValidationError(
                f"CCP industry rules unavailable: {exc}", code=exc.code
            ) from exc

    return checked


def _sde_lru_cache(*, maxsize: int):
    """Check SDE generation before a hit, including cached missing records."""

    def decorate(function):
        cached = lru_cache(maxsize=maxsize)(function)

        @wraps(function)
        def lookup(*args, **kwargs):
            get_industry_calculation_cache_token()
            return cached(*args, **kwargs)

        lookup.cache_clear = cached.cache_clear
        lookup.cache_info = cached.cache_info
        return lookup

    return decorate


def _get_industry_sde_sections():
    """Read metadata directly, without a cross-process singleton cache."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT sde_section, build_number, last_update
            FROM eve_sde_evesdesection
            WHERE sde_section IN (
                'ItemType', 'ItemGroup', 'ItemCategory', 'TypeDogma', 'TypeEffect',
                'DogmaEffect', 'DogmaAttribute', 'BlueprintActivity',
                'BlueprintActivityProduct', 'Stargate', 'SolarSystem'
            )
            ORDER BY sde_section
            """
        )
        return cursor.fetchall()


def get_industry_sde_qualification(
    *, source_type_id: int | None = None
) -> dict[str, object]:
    """Report archive qualification separately from the installed build number."""
    rows = _get_industry_sde_sections()
    builds = sorted(
        {
            int(row[1])
            for row in rows
            if row[0] in _RULE_QUALIFICATION_SECTIONS and row[1] is not None
        }
    )
    try:
        result = get_industry_build_qualification(builds, source_type_id=source_type_id)
    except IndustryRulesError as exc:
        return {
            "status": "incompatible",
            "build_numbers": builds,
            "reason": f"CCP industry rules unavailable: {exc}",
            "error_code": exc.code,
        }
    required = {"TypeDogma", "TypeEffect", "DogmaEffect", "DogmaAttribute"}
    present = {row[0] for row in rows if row[1] is not None}
    if required - present and result["status"] != "incompatible":
        result["status"] = "unqualified"
        result["reason"] = (
            "Missing installed SDE build metadata for "
            + ", ".join(sorted(required - present))
            + "; qualify the complete local Dogma import before calculating its bonuses."
        )
    result["attribute_build_numbers"] = sorted(
        {int(row[1]) for row in rows if row[0] == "TypeDogma" and row[1] is not None}
    )
    return result


def get_industry_calculation_cache_token() -> str:
    """Version derived data against validated rules, SDE and resolver semantics.

    Every process observes an SDE reload on its next calculation. No shared keys
    or intentionally frozen recipe snapshots are cleared. Validation precedes
    even a warm persistent-cache hit, so a malformed bundle cannot reuse it.
    """
    global _sde_cache_generation
    rows = _get_industry_sde_sections()
    availability = ""
    try:
        get_industry_build_qualification(
            int(row[1])
            for row in rows
            if row[0] in _RULE_QUALIFICATION_SECTIONS and row[1] is not None
        )
    except IndustryRulesError as exc:
        # A bad bundle invalidates calculations without blocking saved recipes,
        # stock edits or other project functions independent of these rules.
        availability = (
            ":unavailable-" + hashlib.sha256(str(exc).encode()).hexdigest()[:16]
        )
    generation = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:16]
    token = f"{INDUSTRY_RULES_VERSION}{availability}:{generation}:{INDUSTRY_RESOLVER_VERSION}"
    if _sde_cache_generation != token:
        clear_industry_sde_caches()
        _sde_cache_generation = token
    return token


def clear_industry_sde_caches() -> None:
    """Invalidate only the in-process SDE lookups used by industry calculations."""
    for name in (
        "get_type_snapshot",
        "get_structure_type_options",
        "get_supported_industry_structure_type_ids",
        "get_structure_type_catalog",
        "structure_type_supports_rigs",
        "get_industry_rig_options",
        "get_industry_rig_catalog",
        "get_grouped_industry_rig_options",
        "get_structure_type_catalog_entry",
        "is_rig_compatible_with_structure_type",
        "resolve_item_type_reference",
        "resolve_solar_system_reference",
        "resolve_solar_system_location_reference",
    ):
        lookup = globals().get(name)
        if lookup is not None and hasattr(lookup, "cache_clear"):
            lookup.cache_clear()
    # Local import avoids the planner's import of this module during startup.
    # AA Example App
    from indy_hub.services import craft_structures

    for lookup in (
        craft_structures._get_table_column_names,
        craft_structures._sde_name_expression,
        craft_structures._get_manufacturing_ship_group_names,
        craft_structures._get_super_capital_ship_group_names,
        craft_structures._get_capital_ship_group_names,
        craft_structures._load_stargate_adjacency,
    ):
        lookup.cache_clear()


RIG_SIZE_ATTRIBUTE_ID = 1547
RIG_COMPATIBLE_GROUP_ATTRIBUTE_IDS = (1298, 1299, 1300)

ENGINEERING_RIG_TIME_BONUS_ATTRIBUTE_ID = 2593
ENGINEERING_RIG_MATERIAL_BONUS_ATTRIBUTE_ID = 2594
ENGINEERING_RIG_COST_BONUS_ATTRIBUTE_ID = 2595
REACTION_RIG_TIME_BONUS_ATTRIBUTE_ID = 2713
REACTION_RIG_MATERIAL_BONUS_ATTRIBUTE_ID = 2714
HIGHSEC_MODIFIER_ATTRIBUTE_ID = 2355
LOWSEC_MODIFIER_ATTRIBUTE_ID = 2356
NULLSEC_MODIFIER_ATTRIBUTE_ID = 2357

STRUCTURE_ENGINEERING_MATERIAL_MULTIPLIER_ATTRIBUTE_ID = 2600
STRUCTURE_ENGINEERING_COST_MULTIPLIER_ATTRIBUTE_ID = 2601
STRUCTURE_ENGINEERING_TIME_MULTIPLIER_ATTRIBUTE_ID = 2602
STRUCTURE_REACTION_TIME_MULTIPLIER_ATTRIBUTE_ID = 2721

STRUCTURE_SOURCE = "structure"
RIG_SOURCE = "rig"

ENGINEERING_STRUCTURE_GROUP_ID = 1404
REFINERY_STRUCTURE_GROUP_ID = 1406
NPC_STATION_STRUCTURE_TYPE_ID = -1
NPC_STATION_STRUCTURE_TYPE_NAME = "NPC Station"

FALLBACK_STRUCTURE_TYPES = (
    (35835, "Athanor"),
    (35826, "Azbel"),
    (35825, "Raitaru"),
    (35827, "Sotiyo"),
    (35836, "Tatara"),
)

FALLBACK_STRUCTURE_TYPE_CATALOG = (
    {
        "type_id": NPC_STATION_STRUCTURE_TYPE_ID,
        "name": NPC_STATION_STRUCTURE_TYPE_NAME,
        "group_id": None,
        "rig_size": None,
        "supports_rigs": False,
    },
    {"type_id": 35825, "name": "Raitaru", "group_id": 1404, "rig_size": 2},
    {"type_id": 35826, "name": "Azbel", "group_id": 1404, "rig_size": 3},
    {"type_id": 35827, "name": "Sotiyo", "group_id": 1404, "rig_size": 4},
    {"type_id": 35835, "name": "Athanor", "group_id": 1406, "rig_size": 2},
    {"type_id": 35836, "name": "Tatara", "group_id": 1406, "rig_size": 3},
)

FALLBACK_RIG_CATALOG = (
    {
        "type_id": 37154,
        "name": "Standup M-Set Basic Small Ship Manufacturing Material Efficiency I",
        "family": "Manufacturing",
        "rig_size": 2,
        "compatible_group_ids": (1404, 1406),
    },
    {
        "type_id": 37169,
        "name": "Standup L-Set Advanced Large Ship Manufacturing Efficiency II",
        "family": "Manufacturing",
        "rig_size": 3,
        "compatible_group_ids": (1404, 1406),
    },
    {
        "type_id": 37181,
        "name": "Standup XL-Set Ship Manufacturing Efficiency II",
        "family": "Manufacturing",
        "rig_size": 4,
        "compatible_group_ids": (1404,),
    },
    {
        "type_id": 43879,
        "name": "Standup M-Set Invention Cost Optimization I",
        "family": "Science",
        "rig_size": 2,
        "compatible_group_ids": (1404, 1406),
    },
    {
        "type_id": 46640,
        "name": "Standup L-Set Reprocessing Monitor II",
        "family": "Reprocessing",
        "rig_size": 3,
        "compatible_group_ids": (1404, 1406),
    },
)

ENGINEERING_HULL_BONUS_MAPPING = {
    STRUCTURE_ENGINEERING_MATERIAL_MULTIPLIER_ATTRIBUTE_ID: {
        "activities": [IndustryActivityMixin.ACTIVITY_MANUFACTURING],
        "field": "material_efficiency_percent",
        "label": "Structure role bonus",
    },
    STRUCTURE_ENGINEERING_COST_MULTIPLIER_ATTRIBUTE_ID: {
        "activities": [
            IndustryActivityMixin.ACTIVITY_MANUFACTURING,
            IndustryActivityMixin.ACTIVITY_TE_RESEARCH,
            IndustryActivityMixin.ACTIVITY_ME_RESEARCH,
            IndustryActivityMixin.ACTIVITY_COPYING,
            IndustryActivityMixin.ACTIVITY_INVENTION,
        ],
        "field": "job_cost_percent",
        "label": "Structure role bonus",
    },
    STRUCTURE_ENGINEERING_TIME_MULTIPLIER_ATTRIBUTE_ID: {
        "activities": [
            IndustryActivityMixin.ACTIVITY_MANUFACTURING,
            IndustryActivityMixin.ACTIVITY_TE_RESEARCH,
            IndustryActivityMixin.ACTIVITY_ME_RESEARCH,
            IndustryActivityMixin.ACTIVITY_COPYING,
            IndustryActivityMixin.ACTIVITY_INVENTION,
        ],
        "field": "time_efficiency_percent",
        "label": "Structure role bonus",
    },
    STRUCTURE_REACTION_TIME_MULTIPLIER_ATTRIBUTE_ID: {
        "activities": [
            IndustryActivityMixin.ACTIVITY_REACTIONS,
            IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
        ],
        "field": "time_efficiency_percent",
        "label": "Structure role bonus",
    },
}

SUPPORTED_TYPES_LABEL = "Types supported"

ENGINEERING_ACTIVITY_DEFAULTS = {
    "enable_manufacturing": True,
    "enable_manufacturing_capitals": False,
    "enable_manufacturing_super_capitals": False,
    "enable_research": True,
    "enable_invention": True,
    "enable_biochemical_reactions": False,
    "enable_hybrid_reactions": False,
    "enable_composite_reactions": False,
}

REFINERY_ACTIVITY_DEFAULTS = {
    "enable_manufacturing": False,
    "enable_manufacturing_capitals": False,
    "enable_manufacturing_super_capitals": False,
    "enable_research": False,
    "enable_invention": False,
    "enable_biochemical_reactions": True,
    "enable_hybrid_reactions": True,
    "enable_composite_reactions": True,
}


@dataclass(frozen=True)
class SDETypeSnapshot:
    type_id: int
    name: str
    dogma_attributes: dict[int, Decimal]
    dogma_effect_names: tuple[str, ...]
    group_id: int | None = None
    # Installed ORM snapshots carry IDs paired with names. None preserves the
    # historical in-memory snapshot constructor used by integrations/fixtures.
    dogma_effect_ids: tuple[int, ...] | None = None


@dataclass(frozen=True)
class IndustryStructureResolvedBonus:
    source: str
    label: str
    activity_id: int
    supported_types_label: str = ""
    supported_type_names: tuple[str, ...] = ()
    material_efficiency_percent: Decimal = Decimal("0")
    time_efficiency_percent: Decimal = Decimal("0")
    job_cost_percent: Decimal = Decimal("0")
    # These IDs are authoritative. Names above are display/legacy metadata only.
    target_filter_id: int | None = None
    target_group_ids: tuple[int, ...] = ()
    target_category_ids: tuple[int, ...] = ()
    dogma_effect_name: str = ""
    modifying_attribute_id: int | None = None

    def get_activity_id_display(self) -> str:
        return dict(IndustryActivityMixin.INDUSTRY_ACTIVITY_CHOICES).get(
            self.activity_id,
            str(self.activity_id),
        )


@dataclass(frozen=True)
class IndustryStructureCostBreakdown:
    estimated_item_value: Decimal
    system_cost_index_percent: Decimal
    base_job_cost: Decimal
    structure_role_bonus_percent: Decimal
    rig_bonus_percent: Decimal
    total_job_cost_bonus_percent: Decimal
    adjusted_job_cost: Decimal
    facility_tax_percent: Decimal
    facility_tax: Decimal
    scc_surcharge_percent: Decimal
    scc_surcharge: Decimal
    alpha_clone_tax_percent: Decimal
    alpha_clone_tax: Decimal
    total_installation_cost: Decimal
    material_bonus_percent: Decimal
    time_bonus_percent: Decimal


@dataclass(frozen=True)
class IndustryStructureBonusSummary:
    activity_id: int
    activity_label: str
    material_efficiency_percent: Decimal
    time_efficiency_percent: Decimal
    job_cost_percent: Decimal
    entries: tuple[IndustryStructureResolvedBonus, ...] = ()


@dataclass(frozen=True)
class IndustryStructureRigBonusProfile:
    label: str
    supported_types_label: str
    supported_type_names: tuple[str, ...] = ()
    material_efficiency_percent: Decimal = Decimal("0")
    time_efficiency_percent: Decimal = Decimal("0")
    job_cost_percent: Decimal = Decimal("0")


@dataclass(frozen=True)
class IndustryStructureSupportedTypeBonusRow:
    type_name: str
    material_efficiency_percent: Decimal = Decimal("0")
    time_efficiency_percent: Decimal = Decimal("0")
    job_cost_percent: Decimal = Decimal("0")


@dataclass(frozen=True)
class IndustryStructureActivityPreview:
    activity_id: int
    activity_label: str
    system_cost_index_percent: Decimal = Decimal("0")
    structure_role_bonus: IndustryStructureResolvedBonus | None = None
    rig_profiles: tuple[IndustryStructureRigBonusProfile, ...] = ()
    supported_type_rows: tuple[IndustryStructureSupportedTypeBonusRow, ...] = ()


@dataclass(frozen=True)
class IndustryStructureRigAdvisorRow:
    rig_type_id: int
    label: str
    family: str
    activity_id: int
    activity_label: str
    supported_types_label: str = ""
    supported_type_names: tuple[str, ...] = ()
    material_efficiency_percent: Decimal = Decimal("0")
    time_efficiency_percent: Decimal = Decimal("0")
    job_cost_percent: Decimal = Decimal("0")


def _job_cost_base_multiplier(activity_id: int) -> Decimal:
    if int(activity_id or 0) == IndustryActivityMixin.ACTIVITY_COPYING:
        return COPYING_JOB_COST_BASE_PERCENT / PERCENT_FACTOR
    return Decimal("1")


def sde_item_types_loaded() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM eve_sde_itemtype")
        row = cursor.fetchone()
    return bool(row and row[0])


@_sde_lru_cache(maxsize=1)
def get_structure_type_options() -> list[tuple[int, str]]:
    return [(entry["type_id"], entry["name"]) for entry in get_structure_type_catalog()]


@_sde_lru_cache(maxsize=1)
def get_supported_industry_structure_type_ids() -> set[int]:
    return {int(entry["type_id"]) for entry in get_structure_type_catalog()}


def is_supported_industry_structure_type(structure_type_id: int | None) -> bool:
    if not structure_type_id:
        return False
    return int(structure_type_id) in get_supported_industry_structure_type_ids()


@_sde_lru_cache(maxsize=1)
def get_structure_type_catalog() -> list[dict[str, object]]:
    catalog: list[dict[str, object]] = []
    for fallback in FALLBACK_STRUCTURE_TYPE_CATALOG:
        snapshot = get_type_snapshot(int(fallback["type_id"]))
        if snapshot is None:
            catalog.append(dict(fallback))
            continue
        catalog.append(
            {
                "type_id": snapshot.type_id,
                "name": snapshot.name,
                "group_id": snapshot.group_id or int(fallback["group_id"]),
                "rig_size": _normalize_int(
                    snapshot.dogma_attributes.get(RIG_SIZE_ATTRIBUTE_ID)
                )
                or int(fallback["rig_size"]),
                "supports_rigs": bool(fallback.get("supports_rigs", True)),
            }
        )
    return sorted(catalog, key=lambda entry: str(entry["name"]))


@_sde_lru_cache(maxsize=2048)
def structure_type_supports_rigs(structure_type_id: int | None) -> bool:
    if structure_type_id is None:
        return False
    entry = get_structure_type_catalog_entry(int(structure_type_id))
    if entry is None:
        return False
    return bool(entry.get("supports_rigs", True))


def get_default_enabled_structure_activities(
    structure_type_id: int | None,
) -> dict[str, bool]:
    defaults = dict(ENGINEERING_ACTIVITY_DEFAULTS)
    if not structure_type_id:
        return defaults

    entry = get_structure_type_catalog_entry(int(structure_type_id))
    group_id = None
    if entry is not None:
        group_id = _normalize_int(entry.get("group_id"))
        rig_size = _normalize_int(entry.get("rig_size"))
    else:
        snapshot = get_type_snapshot(int(structure_type_id))
        group_id = snapshot.group_id if snapshot is not None else None
        rig_size = None

    if rig_size is not None:
        defaults["enable_manufacturing_capitals"] = rig_size >= 3
        defaults["enable_manufacturing_super_capitals"] = rig_size >= 4

    if group_id == REFINERY_STRUCTURE_GROUP_ID:
        return dict(REFINERY_ACTIVITY_DEFAULTS)
    return defaults


def _is_research_enabled(enabled_activity_flags: dict[str, bool]) -> bool:
    return bool(
        enabled_activity_flags.get("enable_research")
        or enabled_activity_flags.get("enable_te_research")
        or enabled_activity_flags.get("enable_me_research")
        or enabled_activity_flags.get("enable_copying")
    )


def _has_reaction_activity(enabled_activity_flags: dict[str, bool]) -> bool:
    return bool(
        enabled_activity_flags.get("enable_reactions")
        or enabled_activity_flags.get("enable_biochemical_reactions")
        or enabled_activity_flags.get("enable_hybrid_reactions")
        or enabled_activity_flags.get("enable_composite_reactions")
    )


def _has_manufacturing_activity(enabled_activity_flags: dict[str, bool]) -> bool:
    return any(
        bool(enabled_activity_flags.get(field_name))
        for field_name in IndustryStructure.MANUFACTURING_ACTIVITY_FIELDS
    )


def get_enabled_activity_ids_from_flags(
    enabled_activity_flags: dict[str, bool] | None,
    *,
    structure_type_id: int | None,
) -> list[int]:
    resolved_flags = (
        enabled_activity_flags
        if enabled_activity_flags is not None
        else get_default_enabled_structure_activities(structure_type_id)
    )

    activity_ids: list[int] = []
    if _has_manufacturing_activity(resolved_flags):
        activity_ids.append(IndustryActivityMixin.ACTIVITY_MANUFACTURING)
    if _is_research_enabled(resolved_flags):
        activity_ids.extend(
            [
                IndustryActivityMixin.ACTIVITY_TE_RESEARCH,
                IndustryActivityMixin.ACTIVITY_ME_RESEARCH,
                IndustryActivityMixin.ACTIVITY_COPYING,
            ]
        )
    if bool(resolved_flags.get("enable_invention")):
        activity_ids.append(IndustryActivityMixin.ACTIVITY_INVENTION)
    if _has_reaction_activity(resolved_flags):
        activity_ids.extend(
            [
                IndustryActivityMixin.ACTIVITY_REACTIONS,
                IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
            ]
        )
    return activity_ids


@_sde_lru_cache(maxsize=1)
def get_industry_rig_options() -> list[tuple[int, str]]:
    return [
        (int(entry["type_id"]), str(entry["name"]))
        for entry in get_industry_rig_catalog()
    ]


def _rig_family_label(effect_names: tuple[str, ...]) -> str:
    normalized = " ".join(effect_names).lower()
    if "invention" in normalized or "research" in normalized or "copy" in normalized:
        return "Science"
    if (
        "reprocessing" in normalized
        or "refining" in normalized
        or "oreyield" in normalized
    ):
        return "Reprocessing"
    if "reaction" in normalized:
        return "Reactions"
    return "Manufacturing"


@_sde_lru_cache(maxsize=1)
def get_industry_rig_catalog() -> list[dict[str, object]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.id, t.name, e.name
            FROM eve_sde_itemtype t
            JOIN eve_sde_typeeffect te ON te.item_type_id = t.id
            JOIN eve_sde_dogmaeffect e ON e.id = te.dogma_effect_id
            WHERE t.name LIKE 'Standup %'
                    AND COALESCE(t.published, 0) = 1
                        AND (
                                e.name LIKE 'rig%'
                                OR e.name LIKE 'structureRig%'
                        )
            ORDER BY t.id, e.name
            """
        )

        rows = cursor.fetchall()

    if not rows:
        return [dict(entry) for entry in FALLBACK_RIG_CATALOG]

    grouped: dict[int, dict[str, object]] = {}
    for type_id, name, effect_name in rows:
        entry = grouped.setdefault(int(type_id), {"name": str(name), "effects": []})
        if effect_name:
            entry["effects"].append(str(effect_name))

    catalog: list[dict[str, object]] = []
    for type_id, entry in grouped.items():
        snapshot = get_type_snapshot(type_id)
        compatible_group_ids: list[int] = []
        rig_size = None
        if snapshot is not None:
            rig_size = _normalize_int(
                snapshot.dogma_attributes.get(RIG_SIZE_ATTRIBUTE_ID)
            )
            compatible_group_ids = [
                int(group_id)
                for attribute_id in RIG_COMPATIBLE_GROUP_ATTRIBUTE_IDS
                for group_id in [
                    _normalize_int(snapshot.dogma_attributes.get(attribute_id))
                ]
                if group_id is not None
            ]

        catalog.append(
            {
                "type_id": type_id,
                "name": str(entry["name"]),
                "family": _rig_family_label(tuple(entry["effects"])),
                "rig_size": rig_size,
                "compatible_group_ids": compatible_group_ids,
            }
        )

    return sorted(catalog, key=lambda item: (str(item["family"]), str(item["name"])))


def _is_rig_catalog_entry_compatible(
    rig_entry: dict[str, object],
    structure_entry: dict[str, int | str] | None,
) -> bool:
    if structure_entry is None:
        return True

    structure_group_id = _normalize_int(structure_entry.get("group_id"))
    structure_rig_size = _normalize_int(structure_entry.get("rig_size"))
    rig_size = _normalize_int(rig_entry.get("rig_size"))
    compatible_group_ids = {
        int(group_id)
        for group_id in rig_entry.get("compatible_group_ids", [])
        if group_id is not None
    }

    if (
        structure_rig_size is not None
        and rig_size is not None
        and rig_size != structure_rig_size
    ):
        return False
    if compatible_group_ids and structure_group_id not in compatible_group_ids:
        return False
    return True


@_sde_lru_cache(maxsize=1)
def get_grouped_industry_rig_options(
    structure_type_id: int | None = None,
) -> list[tuple[str, list[tuple[int, str]]]]:
    if structure_type_id is not None and not structure_type_supports_rigs(
        int(structure_type_id)
    ):
        return []

    structure_entry = None
    if structure_type_id:
        structure_entry = get_structure_type_catalog_entry(int(structure_type_id))

    families: dict[str, list[tuple[int, str]]] = {}
    for entry in get_industry_rig_catalog():
        if not _is_rig_catalog_entry_compatible(entry, structure_entry):
            continue
        families.setdefault(str(entry["family"]), []).append(
            (int(entry["type_id"]), str(entry["name"]))
        )

    ordered_labels = ["Manufacturing", "Science", "Reprocessing", "Reactions"]
    grouped_choices: list[tuple[str, list[tuple[int, str]]]] = []
    for label in ordered_labels:
        options = sorted(families.get(label, []), key=lambda row: row[1])
        if options:
            grouped_choices.append((label, options))
    for label in sorted(set(families.keys()) - set(ordered_labels)):
        grouped_choices.append((label, sorted(families[label], key=lambda row: row[1])))
    return grouped_choices


@_sde_lru_cache(maxsize=64)
def get_structure_type_catalog_entry(
    structure_type_id: int,
) -> dict[str, object] | None:
    for entry in get_structure_type_catalog():
        if int(entry["type_id"]) == int(structure_type_id):
            return entry
    return None


@_sde_lru_cache(maxsize=2048)
def is_rig_compatible_with_structure_type(
    *,
    rig_type_id: int | None,
    structure_type_id: int | None,
) -> bool:
    if not rig_type_id or not structure_type_id:
        return True

    structure_entry = get_structure_type_catalog_entry(int(structure_type_id))
    if structure_entry is None:
        return False

    rig_entry = next(
        (
            entry
            for entry in get_industry_rig_catalog()
            if int(entry["type_id"]) == int(rig_type_id)
        ),
        None,
    )
    if rig_entry is None:
        return False
    return _is_rig_catalog_entry_compatible(rig_entry, structure_entry)


def search_solar_system_options(
    query: str, limit: int = 8
) -> list[dict[str, str | int]]:
    normalized_query = (query or "").strip()
    if len(normalized_query) < 2:
        return []

    like_query = f"{normalized_query}%"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, security_status
            FROM eve_sde_solarsystem
            WHERE LOWER(name) LIKE LOWER(%s)
            ORDER BY
                CASE WHEN LOWER(name) = LOWER(%s) THEN 0 ELSE 1 END,
                name
            LIMIT %s
            """,
            [like_query, normalized_query, limit],
        )
        rows = cursor.fetchall()

    return [
        {
            "id": int(system_id),
            "name": str(name),
            "security_band": security_status_to_band(security_status),
        }
        for system_id, name, security_status in rows
    ]


@_sde_lru_cache(maxsize=2048)
def resolve_solar_system_reference(
    *, solar_system_id: int | None = None, solar_system_name: str | None = None
) -> tuple[int, str, str] | None:
    location_reference = resolve_solar_system_location_reference(
        solar_system_id=solar_system_id,
        solar_system_name=solar_system_name,
    )
    if location_reference is None:
        return None
    return (
        int(location_reference["solar_system_id"]),
        str(location_reference["solar_system_name"]),
        str(location_reference["system_security_band"]),
    )


@_sde_lru_cache(maxsize=2048)
def resolve_solar_system_location_reference(
    *, solar_system_id: int | None = None, solar_system_name: str | None = None
) -> dict[str, int | str | None] | None:
    if solar_system_id is None and not solar_system_name:
        return None

    with connection.cursor() as cursor:
        if solar_system_id is not None:
            cursor.execute(
                """
                SELECT
                    ss.id,
                    ss.name,
                    ss.security_status,
                    c.id,
                    c.name,
                    r.id,
                    r.name
                FROM eve_sde_solarsystem ss
                LEFT JOIN eve_sde_constellation c ON c.id = ss.constellation_id
                LEFT JOIN eve_sde_region r ON r.id = c.region_id
                WHERE ss.id = %s
                """,
                [solar_system_id],
            )
        else:
            cursor.execute(
                """
                SELECT
                    ss.id,
                    ss.name,
                    ss.security_status,
                    c.id,
                    c.name,
                    r.id,
                    r.name
                FROM eve_sde_solarsystem ss
                LEFT JOIN eve_sde_constellation c ON c.id = ss.constellation_id
                LEFT JOIN eve_sde_region r ON r.id = c.region_id
                WHERE LOWER(ss.name) = LOWER(%s)
                LIMIT 1
                """,
                [solar_system_name],
            )
        row = cursor.fetchone()

    if not row:
        return None
    return {
        "solar_system_id": int(row[0]),
        "solar_system_name": str(row[1]),
        "system_security_band": security_status_to_band(row[2]),
        "constellation_id": int(row[3]) if row[3] is not None else None,
        "constellation_name": str(row[4]) if row[4] else "",
        "region_id": int(row[5]) if row[5] is not None else None,
        "region_name": str(row[6]) if row[6] else "",
    }


@_sde_lru_cache(maxsize=2048)
def resolve_item_type_reference(
    *, item_type_id: int | None = None, item_type_name: str | None = None
) -> tuple[int, str] | None:
    if item_type_id is None and not item_type_name:
        return None

    if item_type_id == NPC_STATION_STRUCTURE_TYPE_ID or (
        item_type_name
        and str(item_type_name).strip().casefold()
        == NPC_STATION_STRUCTURE_TYPE_NAME.casefold()
    ):
        return NPC_STATION_STRUCTURE_TYPE_ID, NPC_STATION_STRUCTURE_TYPE_NAME

    with connection.cursor() as cursor:
        if item_type_id is not None:
            cursor.execute(
                "SELECT id, name FROM eve_sde_itemtype WHERE id = %s AND COALESCE(published, 0) = 1",
                [item_type_id],
            )
        else:
            cursor.execute(
                "SELECT id, name FROM eve_sde_itemtype WHERE LOWER(name) = LOWER(%s) AND COALESCE(published, 0) = 1 LIMIT 1",
                [item_type_name],
            )
        row = cursor.fetchone()

    if not row:
        return None
    return int(row[0]), str(row[1])


@_sde_lru_cache(maxsize=2048)
def get_type_snapshot(item_type_id: int) -> SDETypeSnapshot | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, group_id FROM eve_sde_itemtype WHERE id = %s AND COALESCE(published, 0) = 1",
            [item_type_id],
        )
        item_row = cursor.fetchone()
        if not item_row:
            return None

        cursor.execute(
            "SELECT dogma_attribute_id, value FROM eve_sde_typedogma WHERE item_type_id = %s",
            [item_type_id],
        )
        dogma_attributes = {
            int(attribute_id): _normalize_decimal(value)
            for attribute_id, value in cursor.fetchall()
            if attribute_id is not None and value is not None
        }

        cursor.execute(
            """
            SELECT DISTINCT te.dogma_effect_id, e.name
            FROM eve_sde_typeeffect te
            LEFT JOIN eve_sde_dogmaeffect e ON te.dogma_effect_id = e.id
            WHERE te.item_type_id = %s
            ORDER BY e.name, te.dogma_effect_id
            """,
            [item_type_id],
        )
        effect_rows = cursor.fetchall()
        if any(row[0] is None or not row[1] for row in effect_rows):
            raise ValidationError(
                f"Industrial source {item_type_id} has an incomplete Dogma effect "
                "association; refresh or repair the local SDE."
            )
        dogma_effect_ids = tuple(int(row[0]) for row in effect_rows)
        dogma_effect_names = tuple(str(row[1]) for row in effect_rows)

    return SDETypeSnapshot(
        type_id=int(item_row[0]),
        name=str(item_row[1]),
        group_id=int(item_row[2]) if item_row[2] is not None else None,
        dogma_attributes=dogma_attributes,
        dogma_effect_names=dogma_effect_names,
        dogma_effect_ids=dogma_effect_ids,
    )


def _multiplier_attribute_to_percent(value: Decimal) -> Decimal:
    if not value.is_finite() or not Decimal("0") < value <= Decimal("1"):
        raise ValidationError(
            f"Unsupported CCP hull multiplier {value!s}; expected a finite value in (0, 1]."
        )
    return (Decimal("1") - value) * PERCENT_FACTOR


def _security_modifier(
    dogma_attributes: dict[int, Decimal], security_band: str
) -> Decimal:
    if security_band == IndustryStructure.SecurityBand.LOWSEC:
        return dogma_attributes.get(LOWSEC_MODIFIER_ATTRIBUTE_ID, Decimal("1"))
    if security_band == IndustryStructure.SecurityBand.NULLSEC:
        return dogma_attributes.get(NULLSEC_MODIFIER_ATTRIBUTE_ID, Decimal("1"))
    return dogma_attributes.get(HIGHSEC_MODIFIER_ATTRIBUTE_ID, Decimal("1"))


@_rules_validation_errors
def _supported_type_names_for_effect(effect_name: str) -> tuple[str, ...]:
    """Return exact CCP filter labels for display, never product eligibility."""
    rule = get_industry_rule(effect_name)
    return rule.supported_type_names if rule is not None else ()


@_rules_validation_errors
def _rig_effect_mapping(effect_name: str) -> tuple[list[int], str] | None:
    """Compatibility export backed by the versioned CCP source bindings."""
    rule = get_industry_rule(effect_name)
    if rule is None:
        return None
    return list(rule.activity_ids), rule.field_name


def _rig_percent_for_field(
    dogma_attributes: dict[int, Decimal],
    *,
    modifying_attribute_id: int,
    security_band: str,
) -> Decimal:
    if modifying_attribute_id not in dogma_attributes:
        raise ValidationError(
            f"Industry rig is missing its CCP modifying attribute "
            f"{modifying_attribute_id}; refresh or repair the local SDE."
        )
    base_value = dogma_attributes[modifying_attribute_id]
    if not base_value.is_finite() or not Decimal("-100") <= base_value <= 0:
        raise ValidationError(
            f"Industry rig attribute {modifying_attribute_id} has unsupported "
            f"CCP negative-percent value {base_value!s}; expected [-100, 0]."
        )
    security_modifier = _security_modifier(dogma_attributes, security_band)
    if not security_modifier.is_finite() or security_modifier <= 0:
        raise ValidationError(
            f"Industry rig has unsupported CCP security multiplier {security_modifier!s}; "
            "expected a finite positive value."
        )
    percent = abs(base_value * security_modifier)
    if percent > PERCENT_FACTOR:
        raise ValidationError(
            f"Industry rig attribute {modifying_attribute_id} produces unsupported "
            f"reduction {percent!s}%; expected [0, 100]."
        )
    return percent


def _is_industry_bonus_effect(effect_name: str, effect_id: int | None = None) -> bool:
    if is_known_industry_effect(effect_name, effect_id=effect_id):
        return True
    # Kept only to diagnose unsupported historical/new aliases. Source IDs and
    # complete effect metadata remain authoritative when names do not match.
    normalized = effect_name.casefold()
    return normalized.startswith("rig") and any(
        word in normalized
        for word in ("manufacture", "reaction", "research", "invention", "copy")
    )


def _source_metadata(snapshot: SDETypeSnapshot, kind: str) -> dict:
    metadata = get_industry_source_metadata(snapshot.type_id)
    if metadata is None or metadata["kind"] != kind:
        raise ValidationError(
            f"Unknown industry source {snapshot.type_id} ({kind}); update the "
            "verified CCP source bindings before calculating its bonuses."
        )
    return metadata


def _validate_source_effects(snapshot: SDETypeSnapshot, metadata: dict) -> None:
    """Validate the fields actually available from django-eveonline-sde.

    modifierInfo is not stored by the installed ORM. Its operations/associations
    are checked against the archive by the generator, never invented at runtime.
    Historical in-memory snapshots omit IDs; real ORM snapshots always carry
    them and must match the full source effect set, including nonindustrial ones.
    """
    expected_names = {row["name"].casefold() for row in metadata["effects"].values()}
    for name in snapshot.dogma_effect_names:
        rule = get_industry_rule(name, rig_type_id=snapshot.type_id)
        canonical_name = rule.effect_name if rule is not None else name
        if canonical_name.casefold() not in expected_names:
            raise ValidationError(
                f"Unknown industry effect {name!r} on source {snapshot.type_id}; "
                "qualify the source archive before calculating its bonuses."
            )
    if snapshot.dogma_effect_ids is None:
        return
    if "group_id" in metadata and snapshot.group_id != metadata["group_id"]:
        raise ValidationError(
            f"Industry source {snapshot.type_id} group {snapshot.group_id!s} differs "
            f"from its qualified CCP group {metadata['group_id']!s}."
        )
    if len(snapshot.dogma_effect_ids) != len(snapshot.dogma_effect_names):
        raise ValidationError(
            f"Incomplete Dogma effect names/IDs on source {snapshot.type_id}."
        )
    expected = {int(key): row for key, row in metadata["effects"].items()}
    actual = dict(
        zip(snapshot.dogma_effect_ids, snapshot.dogma_effect_names, strict=True)
    )
    if (
        len(actual) != len(snapshot.dogma_effect_ids)
        or actual.keys() != expected.keys()
    ):
        raise ValidationError(
            f"CCP Dogma effect set differs on industry source {snapshot.type_id}: "
            f"missing {sorted(expected.keys() - actual.keys())}, "
            f"unknown {sorted(actual.keys() - expected.keys())}; "
            "qualify the source archive or repair the local SDE."
        )
    for effect_id, name in actual.items():
        expected_name = expected[effect_id]["name"]
        # Preserve old spellings only when their canonical bound effect is exact.
        rule = get_industry_rule(name, rig_type_id=snapshot.type_id)
        canonical_name = rule.effect_name if rule is not None else name
        if canonical_name.casefold() != expected_name.casefold():
            raise ValidationError(
                f"Unknown industry effect {name!r} (ID {effect_id}) on source "
                f"{snapshot.type_id}; expected {expected_name!r}."
            )
    qualification = get_industry_sde_qualification(source_type_id=snapshot.type_id)
    if qualification["status"] in {
        "incompatible",
        "unqualified",
    }:
        raise ValidationError(
            f"Industry source {snapshot.type_id} cannot be calculated: "
            f"{qualification['reason']}"
        )
    expected_attributes = get_industry_source_attribute_values(
        snapshot.type_id,
        build_numbers=qualification.get(
            "attribute_build_numbers", qualification["build_numbers"]
        ),
    )
    for raw_attribute_id, expected_value in expected_attributes.items():
        attribute_id = int(raw_attribute_id)
        actual_value = snapshot.dogma_attributes.get(attribute_id)
        expected_decimal = Decimal(str(expected_value))
        if actual_value != expected_decimal:
            raise ValidationError(
                f"Industry source {snapshot.type_id} attribute {attribute_id} differs "
                f"from the qualified CCP projection: expected {expected_decimal!s}, "
                f"found {actual_value!s}; qualify the source archive or repair the local SDE."
            )
    for raw_attribute_id, expected_value in metadata.get(
        "fitting_attribute_values", {}
    ).items():
        attribute_id = int(raw_attribute_id)
        value = snapshot.dogma_attributes.get(attribute_id)
        if value != Decimal(str(expected_value)):
            raise ValidationError(
                f"Industry source {snapshot.type_id} fitting attribute {attribute_id} "
                f"differs from its qualified CCP value {expected_value!s}: found {value!s}."
            )


@_rules_validation_errors
def resolve_structure_type_bonuses(
    structure_type_id: int | None,
    *,
    structure_type_name: str = "",
) -> list[IndustryStructureResolvedBonus]:
    if not structure_type_id or int(structure_type_id) == NPC_STATION_STRUCTURE_TYPE_ID:
        return []

    snapshot = get_type_snapshot(int(structure_type_id))
    if snapshot is None:
        raise ValidationError(
            f"Cannot resolve selected structure {structure_type_id} from the local SDE; "
            "its industry bonuses are unavailable."
        )

    metadata = _source_metadata(snapshot, "hull")
    rules = get_industry_rules_for_hull(int(structure_type_id))
    expected_attributes = {rule.attribute_id for rule in rules}
    missing_attributes = expected_attributes - snapshot.dogma_attributes.keys()
    if missing_attributes:
        raise ValidationError(
            f"Industry hull {structure_type_id} is missing its CCP modifying attributes "
            f"{', '.join(str(value) for value in sorted(missing_attributes))}; "
            "refresh or repair the local SDE."
        )
    _validate_source_effects(snapshot, metadata)

    label = structure_type_name or snapshot.name or "Structure role bonus"
    bonuses: list[IndustryStructureResolvedBonus] = []
    # Hull sources bind multipliers directly. They deliberately do not pretend
    # to be rig percentage effects and need no synthetic Dogma association.
    for rule in rules:
        raw_value = snapshot.dogma_attributes[rule.attribute_id]
        percent = _multiplier_attribute_to_percent(raw_value)
        if percent <= 0:
            continue
        for activity_id in rule.activity_ids:
            values = {
                "material_efficiency_percent": Decimal("0"),
                "time_efficiency_percent": Decimal("0"),
                "job_cost_percent": Decimal("0"),
            }
            values[rule.field_name] = percent
            bonuses.append(
                IndustryStructureResolvedBonus(
                    source=STRUCTURE_SOURCE,
                    label=label,
                    activity_id=activity_id,
                    modifying_attribute_id=rule.attribute_id,
                    **values,
                )
            )
    return bonuses


@_rules_validation_errors
def resolve_rig_type_bonuses(
    rig_type_id: int | None,
    *,
    rig_type_name: str = "",
    security_band: str,
) -> list[IndustryStructureResolvedBonus]:
    if not rig_type_id:
        return []
    if security_band not in {
        IndustryStructure.SecurityBand.HIGHSEC,
        IndustryStructure.SecurityBand.LOWSEC,
        IndustryStructure.SecurityBand.NULLSEC,
    }:
        raise ValidationError(f"Unsupported industry security band {security_band!r}.")

    snapshot = get_type_snapshot(int(rig_type_id))
    if snapshot is None:
        raise ValidationError(
            f"Cannot resolve selected rig {rig_type_id} from the local SDE; "
            "its industry bonuses are unavailable."
        )

    label = rig_type_name or snapshot.name or f"Rig {rig_type_id}"
    rules = {}
    for index, effect_name in enumerate(snapshot.dogma_effect_names):
        effect_id = (
            snapshot.dogma_effect_ids[index]
            if snapshot.dogma_effect_ids is not None
            and index < len(snapshot.dogma_effect_ids)
            else None
        )
        rule = get_industry_rule(effect_name, rig_type_id=int(rig_type_id))
        if rule is None:
            if _is_industry_bonus_effect(effect_name, effect_id=effect_id):
                raise ValidationError(
                    f"Unknown industry effect {effect_name!r} on rig {rig_type_id}; "
                    "update the pinned CCP industry rules before using this rig."
                )
            continue
        rules[rule.effect_name] = rule
    missing_effects = {
        rule.effect_name for rule in get_industry_rules_for_rig(int(rig_type_id))
    } - rules.keys()
    if missing_effects:
        raise ValidationError(
            f"Selected rig {rig_type_id} is missing its CCP industry effects "
            f"{', '.join(sorted(missing_effects))}; refresh or repair the local SDE."
        )
    missing_modifying_attributes = {
        rule.modifying_attribute_id for rule in rules.values()
    } - snapshot.dogma_attributes.keys()
    if missing_modifying_attributes:
        raise ValidationError(
            f"Industry rig {rig_type_id} is missing its CCP modifying attributes "
            f"{', '.join(str(value) for value in sorted(missing_modifying_attributes))}; "
            "refresh or repair the local SDE."
        )
    missing_security_attributes = (
        set(get_industry_rig_security_attribute_ids(int(rig_type_id)))
        - snapshot.dogma_attributes.keys()
    )
    if missing_security_attributes:
        raise ValidationError(
            f"Industry rig {rig_type_id} is missing its CCP security attributes "
            f"{', '.join(str(value) for value in sorted(missing_security_attributes))}; "
            "refresh or repair the local SDE."
        )
    metadata = _source_metadata(snapshot, "rig")
    # Validate every declared multiplier, not only the currently selected band.
    for attribute_id in metadata["security_attribute_ids"]:
        value = snapshot.dogma_attributes[attribute_id]
        if not value.is_finite() or value <= 0:
            raise ValidationError(
                f"Industry rig {rig_type_id} has unsupported CCP security attribute "
                f"{attribute_id} value {value!s}; expected a finite positive multiplier."
            )
    _validate_source_effects(snapshot, metadata)

    bonuses: list[IndustryStructureResolvedBonus] = []
    for rule in rules.values():
        activity_ids, field_name = rule.activity_ids, rule.field_name
        percent = _rig_percent_for_field(
            snapshot.dogma_attributes,
            modifying_attribute_id=rule.modifying_attribute_id,
            security_band=security_band,
        )
        if percent <= 0:
            continue
        supported_type_names = rule.supported_type_names
        for activity_id in activity_ids:
            values = {
                "material_efficiency_percent": Decimal("0"),
                "time_efficiency_percent": Decimal("0"),
                "job_cost_percent": Decimal("0"),
            }
            values[field_name] = percent
            bonuses.append(
                IndustryStructureResolvedBonus(
                    source=RIG_SOURCE,
                    label=label,
                    activity_id=activity_id,
                    supported_types_label=(
                        SUPPORTED_TYPES_LABEL if supported_type_names else ""
                    ),
                    supported_type_names=supported_type_names,
                    target_filter_id=rule.target_filter_id,
                    target_group_ids=rule.group_ids,
                    target_category_ids=rule.category_ids,
                    dogma_effect_name=rule.effect_name,
                    modifying_attribute_id=rule.modifying_attribute_id,
                    **values,
                )
            )
    return bonuses


def resolve_structure_bonuses(
    structure: IndustryStructure,
) -> list[IndustryStructureResolvedBonus]:
    bonuses = resolve_structure_type_bonuses(
        structure.structure_type_id,
        structure_type_name=structure.structure_type_name,
    )
    rig_rows = list(structure.rigs.all())
    rig_rows.sort(key=lambda rig: int(getattr(rig, "slot_index", 0) or 0))
    for rig in rig_rows:
        bonuses.extend(
            resolve_rig_type_bonuses(
                rig.rig_type_id,
                rig_type_name=rig.rig_type_name,
                security_band=structure.system_security_band,
            )
        )
    return bonuses


def _combine_bonus_percentages(
    bonuses: list[IndustryStructureResolvedBonus],
    *,
    activity_id: int,
    field_name: str,
) -> Decimal:
    multiplier = Decimal("1")
    for bonus in bonuses:
        if bonus.activity_id != activity_id:
            continue
        percent = getattr(bonus, field_name, Decimal("0")) or Decimal("0")
        reduction_ratio = percent / PERCENT_FACTOR
        multiplier *= Decimal("1") - reduction_ratio
    return (Decimal("1") - multiplier) * PERCENT_FACTOR


def summarize_selected_structure_bonuses(
    *,
    structure_type_id: int | None,
    solar_system_name: str = "",
    rig_type_ids: list[int] | tuple[int, ...] | None = None,
) -> list[IndustryStructureBonusSummary]:
    if not structure_type_id:
        return []

    solar_system_reference = resolve_solar_system_reference(
        solar_system_name=solar_system_name or None,
    )
    if solar_system_reference is None:
        return []

    _solar_system_id, _resolved_name, security_band = solar_system_reference
    structure_entry = get_structure_type_catalog_entry(int(structure_type_id))
    structure_type_name = str(structure_entry["name"]) if structure_entry else ""
    supports_rigs = structure_type_supports_rigs(structure_type_id)

    resolved_bonuses = resolve_structure_type_bonuses(
        int(structure_type_id),
        structure_type_name=structure_type_name,
    )
    for rig_type_id in rig_type_ids or [] if supports_rigs else []:
        if not rig_type_id:
            continue
        rig_reference = resolve_item_type_reference(item_type_id=int(rig_type_id))
        rig_type_name = rig_reference[1] if rig_reference else ""
        resolved_bonuses.extend(
            resolve_rig_type_bonuses(
                int(rig_type_id),
                rig_type_name=rig_type_name,
                security_band=security_band,
            )
        )

    activity_ids = sorted({bonus.activity_id for bonus in resolved_bonuses})
    summaries: list[IndustryStructureBonusSummary] = []
    for activity_id in activity_ids:
        activity_entries = tuple(
            bonus for bonus in resolved_bonuses if bonus.activity_id == activity_id
        )
        summaries.append(
            IndustryStructureBonusSummary(
                activity_id=activity_id,
                activity_label=dict(
                    IndustryActivityMixin.INDUSTRY_ACTIVITY_CHOICES
                ).get(
                    activity_id,
                    str(activity_id),
                ),
                material_efficiency_percent=_combine_bonus_percentages(
                    resolved_bonuses,
                    activity_id=activity_id,
                    field_name="material_efficiency_percent",
                ),
                time_efficiency_percent=_combine_bonus_percentages(
                    resolved_bonuses,
                    activity_id=activity_id,
                    field_name="time_efficiency_percent",
                ),
                job_cost_percent=_combine_bonus_percentages(
                    resolved_bonuses,
                    activity_id=activity_id,
                    field_name="job_cost_percent",
                ),
                entries=activity_entries,
            )
        )
    return summaries


def build_structure_activity_previews(
    *,
    structure_type_id: int | None,
    solar_system_name: str = "",
    rig_type_ids: list[int] | tuple[int, ...] | None = None,
    enabled_activity_flags: dict[str, bool] | None = None,
) -> list[IndustryStructureActivityPreview]:
    if not structure_type_id:
        return []

    solar_system_reference = resolve_solar_system_reference(
        solar_system_name=solar_system_name or None,
    )
    if solar_system_reference is None:
        return []

    solar_system_id, _resolved_name, security_band = solar_system_reference
    structure_entry = get_structure_type_catalog_entry(int(structure_type_id))
    structure_type_name = str(structure_entry["name"]) if structure_entry else ""
    supports_rigs = structure_type_supports_rigs(structure_type_id)
    enabled_activity_ids = get_enabled_activity_ids_from_flags(
        enabled_activity_flags,
        structure_type_id=structure_type_id,
    )

    resolved_bonuses = resolve_structure_type_bonuses(
        int(structure_type_id),
        structure_type_name=structure_type_name,
    )
    for rig_type_id in rig_type_ids or [] if supports_rigs else []:
        if not rig_type_id:
            continue
        rig_reference = resolve_item_type_reference(item_type_id=int(rig_type_id))
        rig_type_name = rig_reference[1] if rig_reference else ""
        resolved_bonuses.extend(
            resolve_rig_type_bonuses(
                int(rig_type_id),
                rig_type_name=rig_type_name,
                security_band=security_band,
            )
        )

    previews: list[IndustryStructureActivityPreview] = []
    for activity_id in enabled_activity_ids:
        if activity_id == IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY:
            continue

        structure_entries = [
            entry
            for entry in resolved_bonuses
            if entry.activity_id == activity_id and entry.source == STRUCTURE_SOURCE
        ]
        role_bonus = None
        if structure_entries:
            role_bonus = IndustryStructureResolvedBonus(
                source=STRUCTURE_SOURCE,
                label="Structure role bonus",
                activity_id=activity_id,
                material_efficiency_percent=_combine_bonus_percentages(
                    structure_entries,
                    activity_id=activity_id,
                    field_name="material_efficiency_percent",
                ),
                time_efficiency_percent=_combine_bonus_percentages(
                    structure_entries,
                    activity_id=activity_id,
                    field_name="time_efficiency_percent",
                ),
                job_cost_percent=_combine_bonus_percentages(
                    structure_entries,
                    activity_id=activity_id,
                    field_name="job_cost_percent",
                ),
            )

        rig_entries = [
            entry
            for entry in resolved_bonuses
            if entry.activity_id == activity_id and entry.source == RIG_SOURCE
        ]
        grouped_rig_entries: dict[
            tuple[str, str, tuple[str, ...]],
            list[IndustryStructureResolvedBonus],
        ] = {}
        for entry in rig_entries:
            key = (entry.label, entry.supported_types_label, entry.supported_type_names)
            grouped_rig_entries.setdefault(key, []).append(entry)

        rig_profiles = tuple(
            IndustryStructureRigBonusProfile(
                label=label,
                supported_types_label=supported_types_label,
                supported_type_names=supported_type_names,
                material_efficiency_percent=_combine_bonus_percentages(
                    entries,
                    activity_id=activity_id,
                    field_name="material_efficiency_percent",
                ),
                time_efficiency_percent=_combine_bonus_percentages(
                    entries,
                    activity_id=activity_id,
                    field_name="time_efficiency_percent",
                ),
                job_cost_percent=_combine_bonus_percentages(
                    entries,
                    activity_id=activity_id,
                    field_name="job_cost_percent",
                ),
            )
            for (
                label,
                supported_types_label,
                supported_type_names,
            ), entries in grouped_rig_entries.items()
        )

        grouped_supported_type_entries: dict[
            str, list[IndustryStructureResolvedBonus]
        ] = {}
        for entry in rig_entries:
            for type_name in entry.supported_type_names:
                grouped_supported_type_entries.setdefault(type_name, []).append(entry)

        supported_type_rows = tuple(
            IndustryStructureSupportedTypeBonusRow(
                type_name=type_name,
                material_efficiency_percent=_combine_bonus_percentages(
                    entries,
                    activity_id=activity_id,
                    field_name="material_efficiency_percent",
                ),
                time_efficiency_percent=_combine_bonus_percentages(
                    entries,
                    activity_id=activity_id,
                    field_name="time_efficiency_percent",
                ),
                job_cost_percent=_combine_bonus_percentages(
                    entries,
                    activity_id=activity_id,
                    field_name="job_cost_percent",
                ),
            )
            for type_name, entries in grouped_supported_type_entries.items()
        )

        system_cost_index = IndustrySystemCostIndex.objects.filter(
            solar_system_id=solar_system_id,
            activity_id=activity_id,
        ).first()
        if (
            system_cost_index is None
            and activity_id == IndustryActivityMixin.ACTIVITY_REACTIONS
        ):
            system_cost_index = IndustrySystemCostIndex.objects.filter(
                solar_system_id=solar_system_id,
                activity_id=IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
            ).first()

        previews.append(
            IndustryStructureActivityPreview(
                activity_id=activity_id,
                activity_label=dict(
                    IndustryActivityMixin.INDUSTRY_ACTIVITY_CHOICES
                ).get(
                    activity_id,
                    str(activity_id),
                ),
                system_cost_index_percent=(
                    system_cost_index.cost_index_percent
                    if system_cost_index is not None
                    else Decimal("0")
                ),
                structure_role_bonus=role_bonus,
                rig_profiles=rig_profiles,
                supported_type_rows=supported_type_rows,
            )
        )

    return previews


def build_structure_rig_advisor_rows(
    *,
    structure_type_id: int | None,
    solar_system_name: str = "",
    enabled_activity_flags: dict[str, bool] | None = None,
) -> list[IndustryStructureRigAdvisorRow]:
    if not structure_type_id:
        return []

    solar_system_reference = resolve_solar_system_reference(
        solar_system_name=solar_system_name or None,
    )
    if solar_system_reference is None:
        return []

    if not structure_type_supports_rigs(structure_type_id):
        return []

    _solar_system_id, _resolved_name, security_band = solar_system_reference
    structure_entry = get_structure_type_catalog_entry(int(structure_type_id))
    if structure_entry is None:
        return []

    enabled_activity_ids = get_enabled_activity_ids_from_flags(
        enabled_activity_flags,
        structure_type_id=structure_type_id,
    )
    ordered_activity_labels = dict(IndustryActivityMixin.INDUSTRY_ACTIVITY_CHOICES)
    compatible_rigs = [
        entry
        for entry in get_industry_rig_catalog()
        if _is_rig_catalog_entry_compatible(entry, structure_entry)
    ]

    advisor_rows: list[IndustryStructureRigAdvisorRow] = []
    for rig_entry in compatible_rigs:
        rig_type_id = int(rig_entry["type_id"])
        rig_label = str(rig_entry["name"])
        rig_family = str(rig_entry.get("family") or "Other")
        rig_bonuses = resolve_rig_type_bonuses(
            rig_type_id,
            rig_type_name=rig_label,
            security_band=security_band,
        )
        if not rig_bonuses:
            continue

        for activity_id in enabled_activity_ids:
            if activity_id == IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY:
                continue

            activity_entries = [
                entry
                for entry in rig_bonuses
                if entry.activity_id == activity_id and entry.source == RIG_SOURCE
            ]
            if not activity_entries:
                continue

            grouped_entries: dict[
                tuple[str, tuple[str, ...]],
                list[IndustryStructureResolvedBonus],
            ] = {}
            for entry in activity_entries:
                key = (entry.supported_types_label, entry.supported_type_names)
                grouped_entries.setdefault(key, []).append(entry)

            for (
                supported_types_label,
                supported_type_names,
            ), entries in grouped_entries.items():
                advisor_rows.append(
                    IndustryStructureRigAdvisorRow(
                        rig_type_id=rig_type_id,
                        label=rig_label,
                        family=rig_family,
                        activity_id=activity_id,
                        activity_label=ordered_activity_labels.get(
                            activity_id,
                            str(activity_id),
                        ),
                        supported_types_label=supported_types_label,
                        supported_type_names=supported_type_names,
                        material_efficiency_percent=_combine_bonus_percentages(
                            entries,
                            activity_id=activity_id,
                            field_name="material_efficiency_percent",
                        ),
                        time_efficiency_percent=_combine_bonus_percentages(
                            entries,
                            activity_id=activity_id,
                            field_name="time_efficiency_percent",
                        ),
                        job_cost_percent=_combine_bonus_percentages(
                            entries,
                            activity_id=activity_id,
                            field_name="job_cost_percent",
                        ),
                    )
                )

    return sorted(
        advisor_rows,
        key=lambda row: (
            row.activity_id,
            row.family,
            row.label,
            row.supported_type_names,
        ),
    )


def calculate_installation_cost(
    *,
    structure: IndustryStructure,
    activity_id: int,
    estimated_item_value: Decimal | int | float | str,
    system_cost_index: IndustrySystemCostIndex | None = None,
) -> IndustryStructureCostBreakdown:
    estimated_value = _normalize_decimal(estimated_item_value)
    if estimated_value < 0:
        raise ValidationError("Estimated item value cannot be negative.")

    resolved_cost_index = system_cost_index or structure.get_system_cost_index(
        activity_id
    )
    if resolved_cost_index is None:
        raise ValidationError(
            f"No system cost index configured for activity {activity_id} "
            f"on structure {structure.name}."
        )

    structure_role_multiplier = Decimal("1")
    rig_multiplier = Decimal("1")
    for bonus in structure.get_resolved_bonuses():
        if bonus.activity_id != activity_id:
            continue
        percent = _normalize_decimal(bonus.job_cost_percent)
        reduction_ratio = percent / PERCENT_FACTOR
        if bonus.source == STRUCTURE_SOURCE:
            structure_role_multiplier *= Decimal("1") - reduction_ratio
        else:
            rig_multiplier *= Decimal("1") - reduction_ratio

    total_job_cost_multiplier = structure_role_multiplier * rig_multiplier
    job_cost_base = estimated_value * _job_cost_base_multiplier(activity_id)
    base_job_cost = job_cost_base * resolved_cost_index.cost_index_ratio
    adjusted_job_cost = base_job_cost * total_job_cost_multiplier

    facility_tax_percent = _normalize_decimal(
        structure.get_activity_tax_percent(activity_id)
    )
    scc_surcharge_percent = DEFAULT_SCC_SURCHARGE_PERCENT
    facility_tax = job_cost_base * (facility_tax_percent / PERCENT_FACTOR)
    scc_surcharge = job_cost_base * (scc_surcharge_percent / PERCENT_FACTOR)
    alpha_clone_tax_percent = ALPHA_CLONE_TAX_PERCENT
    # `alpha_clone_tax` is always exposed as the would-be charge for an Alpha
    # clone (0.25% of job cost base). It is NOT folded into
    # `total_installation_cost` here — callers know whether the producer is an
    # alpha clone and add the surcharge themselves so the same breakdown can
    # be reused regardless of the selected character.
    alpha_clone_tax = job_cost_base * (alpha_clone_tax_percent / PERCENT_FACTOR)

    structure_role_bonus_percent = (
        Decimal("1") - structure_role_multiplier
    ) * PERCENT_FACTOR
    rig_bonus_percent = (Decimal("1") - rig_multiplier) * PERCENT_FACTOR
    total_job_cost_bonus_percent = (
        Decimal("1") - total_job_cost_multiplier
    ) * PERCENT_FACTOR

    return IndustryStructureCostBreakdown(
        estimated_item_value=_round_isk(estimated_value),
        system_cost_index_percent=resolved_cost_index.cost_index_percent,
        base_job_cost=_round_isk(base_job_cost),
        structure_role_bonus_percent=structure_role_bonus_percent,
        rig_bonus_percent=rig_bonus_percent,
        total_job_cost_bonus_percent=total_job_cost_bonus_percent,
        adjusted_job_cost=_round_isk(adjusted_job_cost),
        facility_tax_percent=facility_tax_percent,
        facility_tax=_round_isk(facility_tax),
        scc_surcharge_percent=scc_surcharge_percent,
        scc_surcharge=_round_isk(scc_surcharge),
        alpha_clone_tax_percent=alpha_clone_tax_percent,
        alpha_clone_tax=_round_isk(alpha_clone_tax),
        total_installation_cost=_round_isk(
            adjusted_job_cost + facility_tax + scc_surcharge
        ),
        material_bonus_percent=structure.get_total_bonus_percent(
            activity_id,
            "material_efficiency_percent",
        ),
        time_bonus_percent=structure.get_total_bonus_percent(
            activity_id,
            "time_efficiency_percent",
        ),
    )
