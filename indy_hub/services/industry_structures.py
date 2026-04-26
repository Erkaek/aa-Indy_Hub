"""Industry structure dogma resolution and installation cost helpers."""

from __future__ import annotations

# Standard Library
import re
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from functools import lru_cache

# Django
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection

# AA Example App
from indy_hub.models import (
    IndustryActivityMixin,
    IndustryStructure,
    IndustrySystemCostIndex,
)

ISK_QUANTUM = Decimal("1")
PERCENT_FACTOR = Decimal("100")
DEFAULT_SCC_SURCHARGE_PERCENT = Decimal("4")
COPYING_JOB_COST_BASE_PERCENT = Decimal("2")

RIG_SIZE_ATTRIBUTE_ID = 1547
RIG_COMPATIBLE_GROUP_ATTRIBUTE_IDS = (1298, 1299, 1300)

ENGINEERING_RIG_TIME_BONUS_ATTRIBUTE_ID = 2593
ENGINEERING_RIG_MATERIAL_BONUS_ATTRIBUTE_ID = 2594
ENGINEERING_RIG_COST_BONUS_ATTRIBUTE_ID = 2595
REACTION_RIG_TIME_BONUS_ATTRIBUTE_ID = 2713
REACTION_RIG_MATERIAL_BONUS_ATTRIBUTE_ID = 2714
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

SHIP_SUPPORTED_TYPE_FALLBACKS = {
    "ships_all": (
        "Assault Frigate",
        "Attack Battlecruiser",
        "Battleship",
        "Black Ops",
        "Blockade Runner",
        "Capital Industrial Ship",
        "Carrier",
        "Combat Battlecruiser",
        "Combat Recon Ship",
        "Command Destroyer",
        "Command Ship",
        "Corvette",
        "Covert Ops",
        "Cruiser",
        "Deep Space Transport",
        "Destroyer",
        "Dreadnought",
        "Electronic Attack Ship",
        "Exhumer",
        "Expedition Command Ship",
        "Expedition Frigate",
        "Flag Cruiser",
        "Force Auxiliary",
        "Force Recon Ship",
        "Freighter",
        "Frigate",
        "Hauler",
        "Heavy Assault Cruiser",
        "Heavy Interdiction Cruiser",
        "Industrial Command Ship",
        "Interceptor",
        "Interdictor",
        "Jump Freighter",
        "Lancer Dreadnought",
        "Logistics",
        "Logistics Frigate",
        "Marauder",
        "Mining Barge",
        "Shuttle",
        "Stealth Bomber",
        "Strategic Cruiser",
        "Supercarrier",
        "Tactical Destroyer",
        "Titan",
    ),
    "ships_capital": (
        "Capital Industrial Ship",
        "Carrier",
        "Dreadnought",
        "Force Auxiliary",
        "Freighter",
        "Jump Freighter",
        "Lancer Dreadnought",
        "Supercarrier",
        "Titan",
    ),
    "ships_small": (
        "Assault Frigate",
        "Command Destroyer",
        "Corvette",
        "Covert Ops",
        "Destroyer",
        "Electronic Attack Ship",
        "Expedition Frigate",
        "Frigate",
        "Interceptor",
        "Interdictor",
        "Logistics Frigate",
        "Shuttle",
        "Stealth Bomber",
        "Tactical Destroyer",
    ),
    "ships_medium": (
        "Attack Battlecruiser",
        "Combat Battlecruiser",
        "Combat Recon Ship",
        "Command Ship",
        "Cruiser",
        "Flag Cruiser",
        "Force Recon Ship",
        "Heavy Assault Cruiser",
        "Heavy Interdiction Cruiser",
        "Logistics",
        "Strategic Cruiser",
    ),
    "ships_large": (
        "Battleship",
        "Black Ops",
        "Blockade Runner",
        "Deep Space Transport",
        "Exhumer",
        "Expedition Command Ship",
        "Hauler",
        "Industrial Command Ship",
        "Marauder",
        "Mining Barge",
    ),
}

SUPPORTED_TYPE_FALLBACKS = {
    **SHIP_SUPPORTED_TYPE_FALLBACKS,
    "equipment": ("Module", "Subsystem", "Deployable", "Implant", "Cargo Container"),
    "ammo": ("Charge", "Script"),
    "drones": ("Drone", "Fighter"),
    "advanced_components": (
        "Advanced Component",
        "Hybrid Tech Component",
        "Tool",
        "Data Interface",
        "Subsystem Component",
    ),
    "advanced_capital_components": (
        "Advanced Capital Component",
        "Advanced Capital Construction Component",
    ),
    "basic_capital_components": (
        "Capital Component",
        "Capital Construction Component",
        "Construction Component",
        "Structure Component",
    ),
    "structures": (
        "Fuel Block",
        "Infrastructure Upgrade",
        "Sovereignty Structure",
        "Starbase Structure",
        "Structure",
        "Structure Module",
    ),
    "invention": ("Invention",),
    "me_research": ("ME Research",),
    "te_research": ("TE Research",),
    "copying": ("Copying",),
    "biochemical_reactions": ("Biochemical Reaction", "Biochemical Material"),
    "composite_reactions": (
        "Composite Reaction",
        "Composite",
        "Intermediate Material",
        "Unrefined Mineral",
    ),
    "hybrid_reactions": ("Hybrid Reaction",),
    "polymer_reactions": (
        "Polymer Reaction",
        "Hybrid Polymer",
        "Molecular-Forged Material",
    ),
}

RIG_EFFECT_SUPPORTED_TYPE_FAMILIES = {
    "rigallshipmanufacturematerialbonus": "ships_all",
    "rigallshipmanufacturetimebonus": "ships_all",
    "rigcapshipmanufacturematerialbonus": "ships_capital",
    "rigcapshipmanufacturetimebonus": "ships_capital",
    "rigsmallshipmanufacturematerialbonus": "ships_small",
    "rigsmallshipmanufacturetimebonus": "ships_small",
    "rigmedshipmanufacturematerialbonus": "ships_medium",
    "rigmedshipmanufacturetimebonus": "ships_medium",
    "riglargeshipmanufacturematerialbonus": "ships_large",
    "riglargeshipmanufacturetimebonus": "ships_large",
    "rigequipmentmanufacturematerialbonus": "equipment",
    "rigequipmentmanufacturetimebonus": "equipment",
    "rigammomanufacturematerialbonus": "ammo",
    "rigammomanufacturetimebonus": "ammo",
    "rigdronemanufacturematerialbonus": "drones",
    "rigdronemanufacturetimebonus": "drones",
    "rigadvcomponentmanufacturematerialbonus": "advanced_components",
    "rigadvcomponentmanufacturetimebonus": "advanced_components",
    "rigadvcapcomponentmanufacturematerialbonus": "advanced_capital_components",
    "rigadvcapcomponentmanufacturetimebonus": "advanced_capital_components",
    "rigcomponentmanufacturematerialbonus": "basic_capital_components",
    "rigcomponentmanufacturetimebonus": "basic_capital_components",
    "rigbascapcompmanufacturematerialbonus": "basic_capital_components",
    "rigbascapcompmanufacturetimebonus": "basic_capital_components",
    "rigstructuremanufacturematerialbonus": "structures",
    "rigstructuremanufacturetimebonus": "structures",
    "riginventioncostbonus": "invention",
    "riginventiontimebonus": "invention",
    "rigmeresearchcostbonus": "me_research",
    "rigmeresearchtimebonus": "me_research",
    "rigteresearchcostbonus": "te_research",
    "rigteresearchtimebonus": "te_research",
    "rigcopycostbonus": "copying",
    "rigcopytimebonus": "copying",
    "rigbiochemicalreactioncostbonus": "biochemical_reactions",
    "rigbiochemicalreactiontimebonus": "biochemical_reactions",
    "rigreactionbiomatbonus": "biochemical_reactions",
    "rigreactionbiotimebonus": "biochemical_reactions",
    "rigcompositereactioncostbonus": "composite_reactions",
    "rigcompositereactiontimebonus": "composite_reactions",
    "rigreactioncompmatbonus": "composite_reactions",
    "rigreactioncomptimebonus": "composite_reactions",
    "righybridreactioncostbonus": "hybrid_reactions",
    "righybridreactiontimebonus": "hybrid_reactions",
    "rigreactionhybmatbonus": "hybrid_reactions",
    "rigreactionhybtimebonus": "hybrid_reactions",
    "rigpolymerreactioncostbonus": "polymer_reactions",
    "rigpolymerreactiontimebonus": "polymer_reactions",
}

LIVE_OUTPUT_SUPPORTED_TYPE_FAMILIES = {
    "equipment": "manufacturing",
    "ammo": "manufacturing",
    "drones": "manufacturing",
    "advanced_components": "manufacturing",
    "advanced_capital_components": "manufacturing",
    "basic_capital_components": "manufacturing",
    "structures": "manufacturing",
    "biochemical_reactions": "reaction",
    "composite_reactions": "reaction",
    "hybrid_reactions": "reaction",
    "polymer_reactions": "reaction",
}

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


def _round_isk(value: Decimal) -> Decimal:
    return value.quantize(ISK_QUANTUM, rounding=ROUND_CEILING)


def _normalize_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _normalize_int(value: Decimal | int | float | str | None) -> int | None:
    normalized = _normalize_decimal(value)
    if normalized == 0 and value in {None, ""}:
        return None
    try:
        return int(normalized)
    except (TypeError, ValueError, ArithmeticError):
        return None


def _job_cost_base_multiplier(activity_id: int) -> Decimal:
    if int(activity_id or 0) == IndustryActivityMixin.ACTIVITY_COPYING:
        return COPYING_JOB_COST_BASE_PERCENT / PERCENT_FACTOR
    return Decimal("1")


def sde_item_types_loaded() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM eve_sde_itemtype")
        row = cursor.fetchone()
    return bool(row and row[0])


def security_status_to_band(security_status: Decimal | int | float | str | None) -> str:
    value = _normalize_decimal(security_status)
    if value >= Decimal("0.45"):
        return IndustryStructure.SecurityBand.HIGHSEC
    if value > Decimal("0"):
        return IndustryStructure.SecurityBand.LOWSEC
    return IndustryStructure.SecurityBand.NULLSEC


@lru_cache(maxsize=1)
def get_structure_type_options() -> list[tuple[int, str]]:
    return [(entry["type_id"], entry["name"]) for entry in get_structure_type_catalog()]


@lru_cache(maxsize=1)
def get_supported_industry_structure_type_ids() -> set[int]:
    return {int(entry["type_id"]) for entry in get_structure_type_catalog()}


def is_supported_industry_structure_type(structure_type_id: int | None) -> bool:
    if not structure_type_id:
        return False
    return int(structure_type_id) in get_supported_industry_structure_type_ids()


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=2048)
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


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=64)
def get_structure_type_catalog_entry(
    structure_type_id: int,
) -> dict[str, object] | None:
    for entry in get_structure_type_catalog():
        if int(entry["type_id"]) == int(structure_type_id):
            return entry
    return None


@lru_cache(maxsize=2048)
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


@lru_cache(maxsize=2048)
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


@lru_cache(maxsize=2048)
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


@lru_cache(maxsize=2048)
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


@lru_cache(maxsize=2048)
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
            SELECT DISTINCT e.name
            FROM eve_sde_typeeffect te
            JOIN eve_sde_dogmaeffect e ON te.dogma_effect_id = e.id
            WHERE te.item_type_id = %s AND e.name IS NOT NULL AND e.name != ''
            ORDER BY e.name
            """,
            [item_type_id],
        )
        dogma_effect_names = tuple(str(row[0]) for row in cursor.fetchall())

    return SDETypeSnapshot(
        type_id=int(item_row[0]),
        name=str(item_row[1]),
        group_id=int(item_row[2]) if item_row[2] is not None else None,
        dogma_attributes=dogma_attributes,
        dogma_effect_names=dogma_effect_names,
    )


def _multiplier_attribute_to_percent(value: Decimal) -> Decimal:
    if value <= 0:
        return Decimal("0")
    if value <= Decimal("1"):
        return (Decimal("1") - value) * PERCENT_FACTOR
    return Decimal("0")


def _security_modifier(
    dogma_attributes: dict[int, Decimal], security_band: str
) -> Decimal:
    if security_band == IndustryStructure.SecurityBand.LOWSEC:
        return dogma_attributes.get(LOWSEC_MODIFIER_ATTRIBUTE_ID, Decimal("1"))
    if security_band == IndustryStructure.SecurityBand.NULLSEC:
        return dogma_attributes.get(NULLSEC_MODIFIER_ATTRIBUTE_ID, Decimal("1"))
    return Decimal("1")


def _supported_type_family_from_effect_name(effect_name: str) -> str:
    normalized = effect_name.lower()
    return RIG_EFFECT_SUPPORTED_TYPE_FAMILIES.get(normalized, "")


def _normalize_supported_type_name(value: str | None) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = []
    for word in text.split():
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


@lru_cache(maxsize=2)
def _get_blueprint_output_name_rows(
    activity_name: str,
) -> tuple[tuple[str, str], ...]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT
                    COALESCE(g.name_en, g.name) AS group_name,
                    COALESCE(c.name_en, c.name) AS category_name
                FROM eve_sde_blueprintactivityproduct bap
                JOIN eve_sde_blueprintactivity ba ON ba.id = bap.blueprint_activity_id
                JOIN eve_sde_itemtype t ON t.id = bap.item_type_id
                JOIN eve_sde_itemgroup g ON g.id = t.group_id
                JOIN eve_sde_itemcategory c ON c.id = g.category_id
                WHERE ba.activity = %s
                                AND t.published = 1
                ORDER BY COALESCE(c.name_en, c.name), COALESCE(g.name_en, g.name)
                """,
                [activity_name],
            )

            rows = cursor.fetchall()
    except DatabaseError:
        return ()
    return tuple(
        (str(group_name or ""), str(category_name or ""))
        for group_name, category_name in rows
        if group_name or category_name
    )


def _live_output_row_matches_family(
    family_key: str,
    *,
    group_name: str,
    category_name: str,
) -> bool:
    normalized_group = _normalize_supported_type_name(group_name)
    normalized_category = _normalize_supported_type_name(category_name)

    if family_key == "equipment":
        return (
            normalized_category in {"module", "subsystem", "deployable", "implant"}
            or normalized_group == "cargo container"
        )
    if family_key == "ammo":
        return normalized_category == "charge" or normalized_group == "script"
    if family_key == "drones":
        return normalized_category == "drone" or normalized_group == "fighter"
    if family_key == "advanced_components":
        return normalized_category == "commodity" and (
            normalized_group
            in {
                "advanced component",
                "hybrid tech component",
                "tool",
                "data interface",
                "subsystem component",
            }
        )
    if family_key == "advanced_capital_components":
        return normalized_category == "commodity" and (
            normalized_group == "advanced capital component"
            or normalized_group == "advanced capital construction component"
        )
    if family_key == "basic_capital_components":
        return normalized_category == "commodity" and normalized_group in {
            "capital component",
            "capital construction component",
            "construction component",
            "structure component",
        }
    if family_key == "structures":
        return normalized_category == "structure" or normalized_group in {
            "fuel block",
            "infrastructure upgrade",
            "sovereignty structure",
            "starbase structure",
            "structure module",
        }
    if family_key == "biochemical_reactions":
        return normalized_group in {"biochemical reaction", "biochemical material"}
    if family_key == "composite_reactions":
        return normalized_group in {
            "composite reaction",
            "composite",
            "intermediate material",
            "unrefined mineral",
        }
    if family_key == "hybrid_reactions":
        return normalized_group == "hybrid reaction"
    if family_key == "polymer_reactions":
        return normalized_group in {
            "polymer reaction",
            "hybrid polymer",
            "molecular forged material",
        }
    return False


def _supported_type_names_from_live_outputs(family_key: str) -> tuple[str, ...]:
    activity_name = LIVE_OUTPUT_SUPPORTED_TYPE_FAMILIES.get(family_key)
    if not activity_name:
        return ()

    live_rows = _get_blueprint_output_name_rows(activity_name)
    if not live_rows:
        return ()

    use_category_labels = family_key in {"equipment", "ammo", "drones", "structures"}
    resolved_names: list[str] = []
    seen_names: set[str] = set()
    for group_name, category_name in live_rows:
        if not _live_output_row_matches_family(
            family_key,
            group_name=group_name,
            category_name=category_name,
        ):
            continue

        candidate_name = category_name if use_category_labels else group_name
        if not candidate_name or candidate_name in seen_names:
            continue
        seen_names.add(candidate_name)
        resolved_names.append(candidate_name)

    return tuple(resolved_names)


@lru_cache(maxsize=1)
def _get_manufacturing_ship_group_names() -> tuple[str, ...]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(g.name_en, g.name) AS group_name
                FROM eve_sde_blueprintactivityproduct bap
                JOIN eve_sde_blueprintactivity ba ON ba.id = bap.blueprint_activity_id
                JOIN eve_sde_itemtype t ON t.id = bap.item_type_id
                JOIN eve_sde_itemgroup g ON g.id = t.group_id
                JOIN eve_sde_itemcategory c ON c.id = g.category_id
                WHERE ba.activity = 'manufacturing'
                                AND t.published = 1
                                AND COALESCE(c.name_en, c.name) = 'Ship'
                GROUP BY g.id, COALESCE(g.name_en, g.name)
                ORDER BY COALESCE(g.name_en, g.name)
                """
            )

            rows = cursor.fetchall()
    except DatabaseError:
        return ()
    return tuple(str(row[0]) for row in rows if row and row[0])


def _filter_ship_supported_type_names(
    family_key: str,
    ship_group_names: tuple[str, ...],
) -> tuple[str, ...]:
    lowered = {name: name.lower() for name in ship_group_names}
    if family_key == "ships_all":
        return ship_group_names

    keyword_sets = {
        "ships_capital": (
            "capital",
            "carrier",
            "dreadnought",
            "auxiliary",
            "freighter",
            "supercarrier",
            "titan",
        ),
        "ships_small": (
            "frigate",
            "destroyer",
            "corvette",
            "shuttle",
            "bomber",
            "interceptor",
            "interdictor",
            "covert ops",
            "electronic attack",
        ),
        "ships_medium": (
            "cruiser",
            "battlecruiser",
            "recon",
            "command ship",
            "logistics",
            "strategic cruiser",
        ),
        "ships_large": (
            "battleship",
            "black ops",
            "marauder",
            "mining barge",
            "exhumer",
            "hauler",
            "transport",
            "industrial command",
            "expedition command",
        ),
    }
    keywords = keyword_sets.get(family_key)
    if not keywords:
        return ()
    return tuple(
        name
        for name in ship_group_names
        if any(keyword in lowered[name] for keyword in keywords)
    )


def _supported_type_names_for_effect(effect_name: str) -> tuple[str, ...]:
    family_key = _supported_type_family_from_effect_name(effect_name)
    if not family_key:
        return ()

    if family_key.startswith("ships_"):
        ship_group_names = _get_manufacturing_ship_group_names()
        filtered_names = _filter_ship_supported_type_names(family_key, ship_group_names)
        if filtered_names:
            return filtered_names

    live_output_names = _supported_type_names_from_live_outputs(family_key)
    if live_output_names:
        return live_output_names

    return SUPPORTED_TYPE_FALLBACKS.get(family_key, ())


def _rig_effect_mapping(effect_name: str) -> tuple[list[int], str] | None:
    normalized = effect_name.lower()
    if normalized in {
        "structureengineeringrigsecuritymodification",
        "structurereactionrigsecuritymodification",
    }:
        return None
    if "manufacture" in normalized and "material" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return (
            [IndustryActivityMixin.ACTIVITY_MANUFACTURING],
            "material_efficiency_percent",
        )
    if "manufacture" in normalized and "time" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return (
            [IndustryActivityMixin.ACTIVITY_MANUFACTURING],
            "time_efficiency_percent",
        )
    if "invention" in normalized and "cost" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_INVENTION], "job_cost_percent")
    if "invention" in normalized and "time" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_INVENTION], "time_efficiency_percent")
    if "meresearch" in normalized and "cost" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_ME_RESEARCH], "job_cost_percent")
    if "meresearch" in normalized and "time" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_ME_RESEARCH], "time_efficiency_percent")
    if "teresearch" in normalized and "cost" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_TE_RESEARCH], "job_cost_percent")
    if "teresearch" in normalized and "time" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_TE_RESEARCH], "time_efficiency_percent")
    if "copy" in normalized and "cost" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_COPYING], "job_cost_percent")
    if "copy" in normalized and "time" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return ([IndustryActivityMixin.ACTIVITY_COPYING], "time_efficiency_percent")
    if "reaction" in normalized and (
        "material" in normalized or "matbonus" in normalized
    ):
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return (
            [
                IndustryActivityMixin.ACTIVITY_REACTIONS,
                IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
            ],
            "material_efficiency_percent",
        )
    if "reaction" in normalized and "time" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return (
            [
                IndustryActivityMixin.ACTIVITY_REACTIONS,
                IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
            ],
            "time_efficiency_percent",
        )
    if "reaction" in normalized and "cost" in normalized:
        if not _supported_type_family_from_effect_name(effect_name):
            return None
        return (
            [
                IndustryActivityMixin.ACTIVITY_REACTIONS,
                IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
            ],
            "material_efficiency_percent",
        )
    return None


def _rig_percent_for_field(
    dogma_attributes: dict[int, Decimal],
    *,
    field_name: str,
    security_band: str,
) -> Decimal:
    attribute_ids = {
        "time_efficiency_percent": (
            ENGINEERING_RIG_TIME_BONUS_ATTRIBUTE_ID,
            REACTION_RIG_TIME_BONUS_ATTRIBUTE_ID,
        ),
        "material_efficiency_percent": (
            ENGINEERING_RIG_MATERIAL_BONUS_ATTRIBUTE_ID,
            REACTION_RIG_MATERIAL_BONUS_ATTRIBUTE_ID,
        ),
        "job_cost_percent": (ENGINEERING_RIG_COST_BONUS_ATTRIBUTE_ID,),
    }.get(field_name)
    if not attribute_ids:
        return Decimal("0")

    base_value = Decimal("0")
    for attribute_id in attribute_ids:
        base_value = dogma_attributes.get(attribute_id, Decimal("0"))
        if base_value != 0:
            break
    if base_value == 0:
        return Decimal("0")

    security_modifier = _security_modifier(dogma_attributes, security_band)
    return abs(base_value * security_modifier)


def resolve_structure_type_bonuses(
    structure_type_id: int | None,
    *,
    structure_type_name: str = "",
) -> list[IndustryStructureResolvedBonus]:
    if not structure_type_id:
        return []

    snapshot = get_type_snapshot(int(structure_type_id))
    if snapshot is None:
        return []

    label = structure_type_name or snapshot.name or "Structure role bonus"
    bonuses: list[IndustryStructureResolvedBonus] = []
    for attribute_id, config in ENGINEERING_HULL_BONUS_MAPPING.items():
        raw_value = snapshot.dogma_attributes.get(attribute_id)
        if raw_value in {None, Decimal("0")}:
            continue
        percent = _multiplier_attribute_to_percent(raw_value)
        if percent <= 0:
            continue
        for activity_id in config["activities"]:
            values = {
                "material_efficiency_percent": Decimal("0"),
                "time_efficiency_percent": Decimal("0"),
                "job_cost_percent": Decimal("0"),
            }
            values[config["field"]] = percent
            bonuses.append(
                IndustryStructureResolvedBonus(
                    source=STRUCTURE_SOURCE,
                    label=label,
                    activity_id=activity_id,
                    **values,
                )
            )
    return bonuses


def resolve_rig_type_bonuses(
    rig_type_id: int | None,
    *,
    rig_type_name: str = "",
    security_band: str,
) -> list[IndustryStructureResolvedBonus]:
    if not rig_type_id:
        return []

    snapshot = get_type_snapshot(int(rig_type_id))
    if snapshot is None:
        return []

    label = rig_type_name or snapshot.name or f"Rig {rig_type_id}"
    bonuses: list[IndustryStructureResolvedBonus] = []
    for effect_name in snapshot.dogma_effect_names:
        mapping = _rig_effect_mapping(effect_name)
        if not mapping:
            continue
        activity_ids, field_name = mapping
        percent = _rig_percent_for_field(
            snapshot.dogma_attributes,
            field_name=field_name,
            security_band=security_band,
        )
        if percent <= 0:
            continue
        supported_type_names = _supported_type_names_for_effect(effect_name)
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
    for rig in structure.rigs.all().order_by("slot_index"):
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
