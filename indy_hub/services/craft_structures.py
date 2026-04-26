"""Helpers for structure selection and recommendations in the craft workspace."""

from __future__ import annotations

# Standard Library
import re
from collections import deque
from decimal import Decimal
from functools import lru_cache
from math import ceil

# Django
from django.db import connection

# AA Example App
from indy_hub.models import IndustryActivityMixin, IndustryStructure
from indy_hub.services.industry_structures import IndustryStructureResolvedBonus

PERCENT_FACTOR = Decimal("100")
SCC_SURCHARGE_RATE = Decimal("0.04")
COPYING_JOB_COST_BASE_RATE = Decimal("0.02")

SUPER_CAPITAL_GROUP_NAMES = {"Supercarrier", "Titan"}
CAPITAL_GROUP_NAMES = {
    "Capital Industrial Ship",
    "Carrier",
    "Dreadnought",
    "Force Auxiliary",
    "Freighter",
    "Jump Freighter",
    "Lancer Dreadnought",
}


def _normalize_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _combine_bonus_percentages(
    bonuses: list[IndustryStructureResolvedBonus],
    field_name: str,
) -> Decimal:
    multiplier = Decimal("1")
    for bonus in bonuses:
        percent = getattr(bonus, field_name, Decimal("0")) or Decimal("0")
        if percent <= 0:
            continue
        multiplier *= Decimal("1") - (percent / PERCENT_FACTOR)
    return (Decimal("1") - multiplier) * PERCENT_FACTOR


def _item_supported_by_bonus(
    bonus: IndustryStructureResolvedBonus,
    item_tags: set[str],
) -> bool:
    if not bonus.supported_type_names:
        return True
    supported_names = {
        _normalize_label(name) for name in bonus.supported_type_names if name
    }
    return bool(item_tags & supported_names)


def _normalize_label(value: str | None) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = []
    for word in text.split():
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def _reaction_service_category_for_item(group_name: str) -> str | None:
    normalized_group = _normalize_label(group_name)
    if normalized_group in {"biochemical reaction", "biochemical material"}:
        return "biochemical_reactions"
    if normalized_group in {
        "hybrid reaction",
        "hybrid polymer",
        "molecular forged material",
    }:
        return "hybrid_reactions"
    if normalized_group in {
        "composite reaction",
        "composite",
        "intermediate material",
        "unrefined mineral",
    }:
        return "composite_reactions"
    return None


def _service_category_for_item(activity_id: int, group_name: str) -> str | None:
    if activity_id in {
        IndustryActivityMixin.ACTIVITY_REACTIONS,
        IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
    }:
        return _reaction_service_category_for_item(group_name)
    if activity_id != IndustryActivityMixin.ACTIVITY_MANUFACTURING:
        return None
    if group_name in SUPER_CAPITAL_GROUP_NAMES:
        return "manufacturing_super_capitals"
    if group_name in CAPITAL_GROUP_NAMES:
        return "manufacturing_capitals"
    return "manufacturing"


def _structure_supports_item(
    structure: IndustryStructure,
    activity_id: int,
    service_category: str | None,
) -> bool:
    if activity_id == IndustryActivityMixin.ACTIVITY_MANUFACTURING:
        if service_category == "manufacturing_super_capitals":
            if not bool(structure.enable_manufacturing_super_capitals):
                return False
            if (
                not _normalize_decimal(
                    structure.manufacturing_super_capitals_tax_percent
                )
                and _normalize_decimal(structure.manufacturing_capitals_tax_percent) > 0
            ):
                return False
            return bool(structure.enable_manufacturing_super_capitals)
        if service_category == "manufacturing_capitals":
            if (
                structure.enable_manufacturing_super_capitals
                and not _normalize_decimal(structure.manufacturing_capitals_tax_percent)
                and _normalize_decimal(
                    structure.manufacturing_super_capitals_tax_percent
                )
                > 0
            ):
                return False
            return bool(structure.enable_manufacturing_capitals)
        return bool(structure.enable_manufacturing)

    if activity_id in {
        IndustryActivityMixin.ACTIVITY_REACTIONS,
        IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
    }:
        if service_category == "biochemical_reactions":
            return bool(structure.enable_biochemical_reactions)
        if service_category == "hybrid_reactions":
            return bool(structure.enable_hybrid_reactions)
        if service_category == "composite_reactions":
            return bool(structure.enable_composite_reactions)
        return bool(
            structure.enable_biochemical_reactions
            or structure.enable_hybrid_reactions
            or structure.enable_composite_reactions
        )

    return False


def _activity_label(activity_id: int) -> str:
    return dict(IndustryActivityMixin.INDUSTRY_ACTIVITY_CHOICES).get(
        activity_id, str(activity_id)
    )


@lru_cache(maxsize=1)
def _load_stargate_adjacency() -> dict[int, tuple[int, ...]]:
    adjacency: dict[int, set[int]] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT solar_system_id, destination_id
            FROM eve_sde_stargate
            WHERE solar_system_id IS NOT NULL
                        AND destination_id IS NOT NULL
            """
        )
        for source_system_id, destination_system_id in cursor.fetchall():
            source_id = int(source_system_id or 0)
            destination_id = int(destination_system_id or 0)
            if source_id <= 0 or destination_id <= 0:
                continue
            adjacency.setdefault(source_id, set()).add(destination_id)
            adjacency.setdefault(destination_id, set()).add(source_id)
    return {
        solar_system_id: tuple(sorted(destinations))
        for solar_system_id, destinations in adjacency.items()
    }


def compute_solar_system_jump_distances(
    origin_solar_system_id: int,
    target_solar_system_ids: list[int] | tuple[int, ...] | set[int],
) -> dict[int, int | None]:
    origin_id = int(origin_solar_system_id or 0)
    target_ids = {
        int(target_id)
        for target_id in (target_solar_system_ids or [])
        if int(target_id or 0) > 0
    }
    if origin_id <= 0 or not target_ids:
        return {}

    adjacency = _load_stargate_adjacency()
    distances: dict[int, int | None] = {target_id: None for target_id in target_ids}
    if origin_id in target_ids:
        distances[origin_id] = 0

    visited = {origin_id}
    queue: deque[tuple[int, int]] = deque([(origin_id, 0)])
    remaining_targets = {
        target_id for target_id in target_ids if target_id != origin_id
    }

    while queue and remaining_targets:
        current_system_id, current_distance = queue.popleft()
        for neighbor_system_id in adjacency.get(current_system_id, ()):
            if neighbor_system_id in visited:
                continue
            visited.add(neighbor_system_id)
            next_distance = current_distance + 1
            if neighbor_system_id in remaining_targets:
                distances[neighbor_system_id] = next_distance
                remaining_targets.remove(neighbor_system_id)
                if not remaining_targets:
                    break
            queue.append((neighbor_system_id, next_distance))

    return distances


def _structure_distance_penalty(
    anchor: dict[str, object] | None,
    candidate: dict[str, object],
) -> tuple[Decimal, str]:
    if not anchor:
        return Decimal("0"), "best"
    if int(anchor["structure_id"]) == int(candidate["structure_id"]):
        return Decimal("0"), "same_structure"
    if anchor.get("solar_system_id") and anchor.get("solar_system_id") == candidate.get(
        "solar_system_id"
    ):
        return Decimal("0.25"), "same_system"
    if anchor.get("constellation_id") and anchor.get(
        "constellation_id"
    ) == candidate.get("constellation_id"):
        return Decimal("1.10"), "same_constellation"
    if anchor.get("region_id") and anchor.get("region_id") == candidate.get(
        "region_id"
    ):
        return Decimal("2.75"), "same_region"
    return Decimal("6.00"), "remote"


def _distance_label(distance_band: str) -> str:
    return {
        "best": "Best standalone",
        "same_structure": "Same structure",
        "same_system": "Same system",
        "same_constellation": "Same constellation",
        "same_region": "Same region",
        "remote": "Remote",
    }.get(distance_band, "Remote")


def _distance_rank(distance_band: str) -> int:
    return {
        "best": 0,
        "same_structure": 0,
        "same_system": 1,
        "same_constellation": 2,
        "same_region": 3,
        "remote": 4,
    }.get(distance_band, 4)


def _structure_distance_rank_between(
    first: dict[str, object],
    second: dict[str, object],
) -> int:
    if int(first["structure_id"]) == int(second["structure_id"]):
        return 0
    if first.get("solar_system_id") and first.get("solar_system_id") == second.get(
        "solar_system_id"
    ):
        return 1
    if first.get("constellation_id") and first.get("constellation_id") == second.get(
        "constellation_id"
    ):
        return 2
    if first.get("region_id") and first.get("region_id") == second.get("region_id"):
        return 3
    return 4


def _installation_cost_percent(
    *,
    activity_id: int,
    job_cost_bonus_percent: Decimal,
    tax_percent: Decimal,
    system_cost_index_percent: Decimal,
) -> dict[str, Decimal]:
    base_multiplier = (
        COPYING_JOB_COST_BASE_RATE
        if int(activity_id or 0) == IndustryActivityMixin.ACTIVITY_COPYING
        else Decimal("1")
    )
    adjusted_job_cost_percent = (system_cost_index_percent * base_multiplier) * (
        Decimal("1") - (job_cost_bonus_percent / PERCENT_FACTOR)
    )
    facility_tax_percent = tax_percent * base_multiplier
    scc_surcharge_percent = (SCC_SURCHARGE_RATE * PERCENT_FACTOR) * base_multiplier
    total_installation_cost_percent = (
        adjusted_job_cost_percent + facility_tax_percent + scc_surcharge_percent
    )
    return {
        "adjusted_job_cost_percent": adjusted_job_cost_percent,
        "facility_tax_percent": facility_tax_percent,
        "scc_surcharge_percent": scc_surcharge_percent,
        "total_installation_cost_percent": total_installation_cost_percent,
    }


def _standalone_sort_key(option: dict[str, object]) -> tuple[object, ...]:
    return (
        -float(option["material_bonus_percent"]),
        -float(option["time_bonus_percent"]),
        float(option["total_installation_cost_percent"]),
        float(option["tax_percent"]),
        float(option["system_cost_index_percent"]),
        -float(option["job_cost_bonus_percent"]),
        option["structure_type_name"] or "",
        option["name"],
        option["system_name"],
    )


def _job_count_for_item(item: dict[str, object]) -> int:
    total_needed = int(item.get("total_needed") or 0)
    produced_per_cycle = max(1, int(item.get("produced_per_cycle") or 1))
    if total_needed <= 0:
        return 1
    return max(1, int(ceil(total_needed / produced_per_cycle)))


def _option_penalty_tuple(
    item: dict[str, object],
    option: dict[str, object],
    best_option: dict[str, object],
) -> tuple[Decimal, Decimal, Decimal]:
    weight = Decimal(str(_job_count_for_item(item)))
    material_penalty = (
        max(
            Decimal("0"),
            _normalize_decimal(best_option["material_bonus_percent"])
            - _normalize_decimal(option["material_bonus_percent"]),
        )
        * weight
    )
    installation_penalty = (
        max(
            Decimal("0"),
            _normalize_decimal(option["total_installation_cost_percent"])
            - _normalize_decimal(best_option["total_installation_cost_percent"]),
        )
        * weight
    )
    time_penalty = (
        max(
            Decimal("0"),
            _normalize_decimal(best_option["time_bonus_percent"])
            - _normalize_decimal(option["time_bonus_percent"]),
        )
        * weight
    )
    return material_penalty, installation_penalty, time_penalty


def _compute_network_dispersion(
    selected_options: list[dict[str, object]],
) -> tuple[int, int]:
    unique_options: list[dict[str, object]] = []
    seen_structure_ids: set[int] = set()
    for option in selected_options:
        structure_id = int(option["structure_id"])
        if structure_id in seen_structure_ids:
            continue
        seen_structure_ids.add(structure_id)
        unique_options.append(option)

    if len(unique_options) <= 1:
        return 0, 0

    pairwise_distances: dict[tuple[int, int], int] = {}
    max_distance_rank = 0
    for left_index, left_option in enumerate(unique_options):
        for right_index in range(left_index + 1, len(unique_options)):
            right_option = unique_options[right_index]
            distance_rank = _structure_distance_rank_between(left_option, right_option)
            pairwise_distances[(left_index, right_index)] = distance_rank
            pairwise_distances[(right_index, left_index)] = distance_rank
            if distance_rank > max_distance_rank:
                max_distance_rank = distance_rank

    visited = {0}
    mst_total_rank = 0
    while len(visited) < len(unique_options):
        best_edge = None
        best_edge_weight = None
        for source_index in visited:
            for target_index in range(len(unique_options)):
                if target_index in visited:
                    continue
                edge_weight = pairwise_distances[(source_index, target_index)]
                if best_edge_weight is None or edge_weight < best_edge_weight:
                    best_edge = target_index
                    best_edge_weight = edge_weight
        visited.add(int(best_edge))
        mst_total_rank += int(best_edge_weight or 0)

    return max_distance_rank, mst_total_rank


def _find_best_structure_assignment(
    planner_items: list[dict[str, object]],
) -> tuple[
    dict[int, dict[str, object]], tuple[Decimal, Decimal, Decimal, int, int, int] | None
]:
    searchable_items = [item for item in planner_items if item.get("options")]
    if not searchable_items:
        return {}, None

    for item in searchable_items:
        best_option = item["options"][0]
        item["_best_option"] = best_option
        item["_option_penalties"] = {
            int(option["structure_id"]): _option_penalty_tuple(
                item, option, best_option
            )
            for option in item["options"]
        }

    searchable_items.sort(
        key=lambda item: (
            not bool(item.get("is_final_product")),
            len(item.get("options") or []),
            -_job_count_for_item(item),
            item.get("type_name") or "",
        )
    )

    best_assignment: dict[int, dict[str, object]] = {}
    best_objective: tuple[Decimal, Decimal, Decimal, int, int, int] | None = None
    current_assignment: dict[int, dict[str, object]] = {}

    def should_prune(
        lower_bound: tuple[Decimal, Decimal, Decimal, int, int, int],
    ) -> bool:
        return best_objective is not None and lower_bound >= best_objective

    def search(
        item_index: int,
        chosen_structure_ids: set[int],
        material_penalty: Decimal,
        installation_penalty: Decimal,
        time_penalty: Decimal,
    ) -> None:
        nonlocal best_assignment, best_objective

        lower_bound = (
            material_penalty,
            installation_penalty,
            time_penalty,
            len(chosen_structure_ids),
            0,
            0,
        )
        if should_prune(lower_bound):
            return

        if item_index >= len(searchable_items):
            selected_options = list(current_assignment.values())
            max_distance_rank, mst_total_rank = _compute_network_dispersion(
                selected_options
            )
            objective = (
                material_penalty,
                installation_penalty,
                time_penalty,
                len(chosen_structure_ids),
                max_distance_rank,
                mst_total_rank,
            )
            if best_objective is None or objective < best_objective:
                best_objective = objective
                best_assignment = dict(current_assignment)
            return

        item = searchable_items[item_index]
        option_penalties = item["_option_penalties"]
        ordered_options = sorted(
            item["options"],
            key=lambda option: (
                0 if int(option["structure_id"]) in chosen_structure_ids else 1,
                option_penalties[int(option["structure_id"])],
                _standalone_sort_key(option),
            ),
        )

        for option in ordered_options:
            structure_id = int(option["structure_id"])
            penalties = option_penalties[structure_id]
            current_assignment[int(item["type_id"])] = option
            added_structure = structure_id not in chosen_structure_ids
            if added_structure:
                chosen_structure_ids.add(structure_id)
            search(
                item_index + 1,
                chosen_structure_ids,
                material_penalty + penalties[0],
                installation_penalty + penalties[1],
                time_penalty + penalties[2],
            )
            if added_structure:
                chosen_structure_ids.remove(structure_id)
            current_assignment.pop(int(item["type_id"]), None)

    search(0, set(), Decimal("0"), Decimal("0"), Decimal("0"))

    for item in searchable_items:
        item.pop("_best_option", None)
        item.pop("_option_penalties", None)

    return best_assignment, best_objective


def _fetch_craftable_item_rows(type_ids: list[int]) -> list[dict[str, object]]:
    if not type_ids:
        return []

    placeholders = ", ".join(["%s"] * len(type_ids))
    rows: list[dict[str, object]] = []
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                product.product_eve_type_id AS type_id,
                COALESCE(item.name_en, item.name) AS type_name,
                product.quantity AS produced_per_cycle,
                item.base_price AS base_price,
                product.activity_id AS activity_id,
                COALESCE(grp.name_en, grp.name) AS group_name,
                COALESCE(category.name_en, category.name) AS category_name
            FROM indy_hub_sdeindustryactivityproduct product
            JOIN eve_sde_itemtype item ON item.id = product.product_eve_type_id
            JOIN eve_sde_itemtype blueprint_item ON blueprint_item.id = product.eve_type_id
            JOIN eve_sde_itemgroup grp ON grp.id = item.group_id
            JOIN eve_sde_itemcategory category ON category.id = grp.category_id
            WHERE product.product_eve_type_id IN ({placeholders})
                        AND product.activity_id IN (1, 9, 11)
                        AND COALESCE(item.published, 0) = 1
                        AND COALESCE(blueprint_item.published, 0) = 1
            ORDER BY COALESCE(item.name_en, item.name)
            """,
            type_ids,
        )

        for (
            type_id,
            type_name,
            produced_per_cycle,
            base_price,
            activity_id,
            group_name,
            category_name,
        ) in cursor.fetchall():
            activity_id = int(
                activity_id or IndustryActivityMixin.ACTIVITY_MANUFACTURING
            )
            if activity_id == IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY:
                activity_id = IndustryActivityMixin.ACTIVITY_REACTIONS
            rows.append(
                {
                    "type_id": int(type_id),
                    "type_name": str(type_name or type_id),
                    "produced_per_cycle": int(produced_per_cycle or 1),
                    "base_price": float(base_price or 0),
                    "activity_id": activity_id,
                    "activity_label": _activity_label(activity_id),
                    "group_name": str(group_name or ""),
                    "category_name": str(category_name or ""),
                }
            )
    return rows


def build_craft_structure_planner(
    *,
    product_type_id: int | None,
    product_type_name: str,
    product_output_per_cycle: int,
    craft_cycles_summary: dict[int, dict[str, object]],
    include_all_options: bool = True,
) -> dict[str, object]:
    craftable_type_ids: list[int] = []
    if product_type_id:
        craftable_type_ids.append(int(product_type_id))
    for type_id in craft_cycles_summary.keys():
        numeric_id = int(type_id)
        if numeric_id not in craftable_type_ids:
            craftable_type_ids.append(numeric_id)

    if not craftable_type_ids:
        return {"items": [], "structures": [], "summary": {"has_structures": False}}

    item_rows = _fetch_craftable_item_rows(craftable_type_ids)
    item_rows_by_type_id = {int(row["type_id"]): row for row in item_rows}

    if product_type_id and product_type_id not in item_rows_by_type_id:
        item_rows_by_type_id[int(product_type_id)] = {
            "type_id": int(product_type_id),
            "type_name": product_type_name,
            "produced_per_cycle": int(product_output_per_cycle or 1),
            "activity_id": IndustryActivityMixin.ACTIVITY_MANUFACTURING,
            "activity_label": _activity_label(
                IndustryActivityMixin.ACTIVITY_MANUFACTURING
            ),
            "group_name": "",
            "category_name": "",
        }

    structures = list(
        IndustryStructure.objects.prefetch_related("rigs").order_by(
            "solar_system_name",
            "name",
            "id",
        )
    )

    serialized_structures = [
        {
            "structure_id": int(structure.id),
            "name": structure.name,
            "system_name": structure.solar_system_name,
            "constellation_name": structure.constellation_name,
            "region_name": structure.region_name,
            "solar_system_id": int(structure.solar_system_id or 0),
            "constellation_id": int(structure.constellation_id or 0),
            "region_id": int(structure.region_id or 0),
            "structure_type_name": structure.structure_type_name,
        }
        for structure in structures
    ]

    if not structures:
        return {
            "items": [],
            "structures": serialized_structures,
            "summary": {
                "has_structures": False,
                "message": "No registered structures available.",
            },
        }

    resolved_bonuses_by_structure = {
        int(structure.id): IndustryStructure.get_resolved_bonuses(structure)
        for structure in structures
    }

    planner_items: list[dict[str, object]] = []
    standalone_recommendations: dict[int, dict[str, object]] = {}

    for type_id in craftable_type_ids:
        item_row = item_rows_by_type_id.get(int(type_id))
        if item_row is None:
            continue

        group_name = str(item_row.get("group_name") or "")
        category_name = str(item_row.get("category_name") or "")
        item_tags = {
            _normalize_label(value) for value in {group_name, category_name} if value
        }
        activity_id = int(item_row["activity_id"])
        service_category = _service_category_for_item(activity_id, group_name)
        total_needed = int(
            (craft_cycles_summary.get(type_id) or {}).get("total_needed")
            or (craft_cycles_summary.get(str(type_id)) or {}).get("total_needed")
            or 0
        )
        if product_type_id and int(type_id) == int(product_type_id):
            total_needed = max(total_needed, int(product_output_per_cycle or 1))

        options: list[dict[str, object]] = []
        for structure in structures:
            if not _structure_supports_item(structure, activity_id, service_category):
                continue

            applicable_bonuses = [
                bonus
                for bonus in resolved_bonuses_by_structure[int(structure.id)]
                if bonus.activity_id == activity_id
                and _item_supported_by_bonus(bonus, item_tags)
            ]
            rig_bonuses = [
                bonus
                for bonus in applicable_bonuses
                if str(getattr(bonus, "source", "")).strip().casefold() == "rig"
            ]
            structure_bonuses = [
                bonus
                for bonus in applicable_bonuses
                if str(getattr(bonus, "source", "")).strip().casefold() != "rig"
            ]
            material_bonus_percent = _combine_bonus_percentages(
                applicable_bonuses,
                "material_efficiency_percent",
            )
            job_cost_bonus_percent = _combine_bonus_percentages(
                applicable_bonuses,
                "job_cost_percent",
            )
            time_bonus_percent = _combine_bonus_percentages(
                applicable_bonuses,
                "time_efficiency_percent",
            )
            rig_material_bonus_percent = _combine_bonus_percentages(
                rig_bonuses,
                "material_efficiency_percent",
            )
            rig_time_bonus_percent = _combine_bonus_percentages(
                rig_bonuses,
                "time_efficiency_percent",
            )
            structure_material_bonus_percent = _combine_bonus_percentages(
                structure_bonuses,
                "material_efficiency_percent",
            )
            structure_time_bonus_percent = _combine_bonus_percentages(
                structure_bonuses,
                "time_efficiency_percent",
            )
            system_index = structure.get_system_cost_index(activity_id)
            if (
                system_index is None
                and activity_id == IndustryActivityMixin.ACTIVITY_REACTIONS
            ):
                system_index = structure.get_system_cost_index(
                    IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY
                )

            system_cost_index_percent = _normalize_decimal(
                getattr(system_index, "cost_index_percent", Decimal("0"))
            )
            tax_percent = _normalize_decimal(
                structure.get_activity_tax_percent(
                    activity_id, service_category=service_category
                )
            )
            installation_cost_percent = _installation_cost_percent(
                activity_id=activity_id,
                job_cost_bonus_percent=job_cost_bonus_percent,
                tax_percent=tax_percent,
                system_cost_index_percent=system_cost_index_percent,
            )
            options.append(
                {
                    "structure_id": int(structure.id),
                    "name": structure.name,
                    "structure_type_name": structure.structure_type_name,
                    "system_name": structure.solar_system_name,
                    "constellation_name": structure.constellation_name,
                    "region_name": structure.region_name,
                    "solar_system_id": int(structure.solar_system_id or 0),
                    "constellation_id": int(structure.constellation_id or 0),
                    "region_id": int(structure.region_id or 0),
                    "material_bonus_percent": float(material_bonus_percent),
                    "job_cost_bonus_percent": float(job_cost_bonus_percent),
                    "time_bonus_percent": float(time_bonus_percent),
                    "rig_material_bonus_percent": float(rig_material_bonus_percent),
                    "rig_time_bonus_percent": float(rig_time_bonus_percent),
                    "structure_material_bonus_percent": float(
                        structure_material_bonus_percent
                    ),
                    "structure_time_bonus_percent": float(structure_time_bonus_percent),
                    "tax_percent": float(tax_percent),
                    "system_cost_index_percent": float(system_cost_index_percent),
                    "adjusted_job_cost_percent": float(
                        installation_cost_percent["adjusted_job_cost_percent"]
                    ),
                    "facility_tax_percent": float(
                        installation_cost_percent["facility_tax_percent"]
                    ),
                    "scc_surcharge_percent": float(
                        installation_cost_percent["scc_surcharge_percent"]
                    ),
                    "total_installation_cost_percent": float(
                        installation_cost_percent["total_installation_cost_percent"]
                    ),
                    "service_category": service_category,
                }
            )

        options.sort(key=_standalone_sort_key)
        if options:
            standalone_recommendations[int(type_id)] = options[0]

        planner_items.append(
            {
                "type_id": int(item_row["type_id"]),
                "type_name": str(item_row["type_name"]),
                "activity_id": activity_id,
                "activity_label": str(item_row["activity_label"]),
                "group_name": group_name,
                "category_name": category_name,
                "service_category": service_category,
                "base_price": float(item_row.get("base_price") or 0),
                "estimated_item_value": float(
                    max(0, float(item_row.get("base_price") or 0))
                    * int(item_row.get("produced_per_cycle") or 1)
                ),
                "produced_per_cycle": int(item_row.get("produced_per_cycle") or 1),
                "total_needed": int(total_needed or 0),
                "is_final_product": bool(
                    product_type_id and int(item_row["type_id"]) == int(product_type_id)
                ),
                "options": options,
            }
        )

    planner_items.sort(
        key=lambda item: (
            not bool(item["is_final_product"]),
            item["activity_id"],
            item["type_name"],
        )
    )

    assignment_by_type_id, optimization_objective = _find_best_structure_assignment(
        planner_items
    )

    anchor_option = None
    for item in planner_items:
        if item["is_final_product"] and int(item["type_id"]) in assignment_by_type_id:
            anchor_option = assignment_by_type_id[int(item["type_id"])]
            break
    if anchor_option is None:
        for item in planner_items:
            assigned_option = assignment_by_type_id.get(int(item["type_id"]))
            if assigned_option is not None:
                anchor_option = assigned_option
                break

    selected_structure_ids: set[int] = set()
    for item in planner_items:
        adjusted_options: list[dict[str, object]] = []
        selected_option = assignment_by_type_id.get(int(item["type_id"]))
        selected_structure_id = (
            int(selected_option["structure_id"])
            if selected_option is not None
            else None
        )

        for option in item["options"]:
            distance_penalty, distance_band = _structure_distance_penalty(
                anchor_option, option
            )
            adjusted_option = dict(option)
            adjusted_option["distance_band"] = distance_band
            adjusted_option["distance_label"] = _distance_label(distance_band)
            adjusted_option["distance_rank"] = _distance_rank(distance_band)
            adjusted_option["adjusted_score"] = float(
                -adjusted_option["distance_rank"] - float(distance_penalty)
            )
            adjusted_options.append(adjusted_option)

        adjusted_options.sort(
            key=lambda option: (
                (
                    0
                    if selected_structure_id
                    and int(option["structure_id"]) == selected_structure_id
                    else 1
                ),
                _standalone_sort_key(option),
                int(option["distance_rank"]),
            )
        )
        if include_all_options:
            item["options"] = adjusted_options
        elif adjusted_options:
            compact_options = [
                option
                for option in adjusted_options
                if selected_structure_id
                and int(option["structure_id"]) == selected_structure_id
            ]
            item["options"] = compact_options or [adjusted_options[0]]
        else:
            item["options"] = []
        if selected_option is not None:
            item["recommended_structure_id"] = int(selected_option["structure_id"])
            item["recommended_structure_name"] = str(selected_option["name"])
            selected_distance_penalty, selected_distance_band = (
                _structure_distance_penalty(
                    anchor_option,
                    selected_option,
                )
            )
            item["recommended_distance_label"] = _distance_label(selected_distance_band)
            item["recommended_distance_rank"] = _distance_rank(selected_distance_band)
            item["recommended_distance_penalty"] = float(selected_distance_penalty)
            selected_structure_ids.add(int(selected_option["structure_id"]))
        else:
            item["recommended_structure_id"] = None
            item["recommended_structure_name"] = ""
            item["recommended_distance_label"] = ""
            item["recommended_distance_rank"] = None
            item["recommended_distance_penalty"] = 0.0

    return {
        "items": planner_items,
        "structures": serialized_structures,
        "summary": {
            "has_structures": bool(serialized_structures),
            "has_full_options": bool(include_all_options),
            "anchor_structure_id": (
                int(anchor_option["structure_id"]) if anchor_option else None
            ),
            "anchor_structure_name": (
                str(anchor_option["name"]) if anchor_option else ""
            ),
            "selected_structure_count": len(selected_structure_ids),
            "structure_count": len(serialized_structures),
            "item_count": len(planner_items),
            "optimization_objective": (
                [
                    float(optimization_objective[0]),
                    float(optimization_objective[1]),
                    float(optimization_objective[2]),
                    int(optimization_objective[3]),
                    int(optimization_objective[4]),
                    int(optimization_objective[5]),
                ]
                if optimization_objective is not None
                else None
            ),
        },
    }
