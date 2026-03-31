"""Helpers for exposing craft production durations to the workspace payload."""

from __future__ import annotations

# Standard Library
from math import ceil
from typing import Any

# Django
from django.db import connection

# AA Example App
from indy_hub.models import IndustryActivityMixin, SDEBlueprintActivity

EVE_JOB_LAUNCH_WINDOW_SECONDS = 30 * 24 * 60 * 60


_ACTIVITY_LABELS = {
    IndustryActivityMixin.ACTIVITY_MANUFACTURING: "Manufacturing",
    IndustryActivityMixin.ACTIVITY_TE_RESEARCH: "TE Research",
    IndustryActivityMixin.ACTIVITY_ME_RESEARCH: "ME Research",
    IndustryActivityMixin.ACTIVITY_COPYING: "Copying",
    IndustryActivityMixin.ACTIVITY_INVENTION: "Invention",
    IndustryActivityMixin.ACTIVITY_REACTIONS: "Reactions",
    IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY: "Reactions",
}


def _activity_label(activity_id: int) -> str:
    return _ACTIVITY_LABELS.get(int(activity_id or 0), f"Activity {activity_id}")


def compute_effective_cycle_seconds(
    *,
    base_time_seconds: int | float | None,
    time_efficiency: int | float | None = 0,
    structure_time_bonus_percent: int | float | None = 0,
) -> int:
    numeric_base_time = max(0, int(base_time_seconds or 0))
    if numeric_base_time <= 0:
        return 0

    te_multiplier = max(0, 1 - ((float(time_efficiency or 0)) / 100))
    structure_multiplier = max(
        0, 1 - ((float(structure_time_bonus_percent or 0)) / 100)
    )
    return max(1, int(ceil(numeric_base_time * te_multiplier * structure_multiplier)))


def compute_max_runs_before_launch_window(
    effective_cycle_seconds: int | float | None,
) -> int:
    numeric_cycle_seconds = max(0, int(effective_cycle_seconds or 0))
    if numeric_cycle_seconds <= 0:
        return 0
    return max(1, int(ceil(EVE_JOB_LAUNCH_WINDOW_SECONDS / numeric_cycle_seconds)))


def get_blueprint_max_production_limit(*, blueprint_type_id: int) -> int | None:
    numeric_blueprint_type_id = int(blueprint_type_id or 0)
    if numeric_blueprint_type_id <= 0:
        return None

    table_names = set(connection.introspection.table_names())
    if "eve_sde_blueprintactivity" in table_names:
        table_name = "eve_sde_blueprintactivity"
    elif "indy_hub_sdeblueprintactivity" in table_names:
        table_name = "indy_hub_sdeblueprintactivity"
    else:
        return None

    with connection.cursor() as cursor:
        table_description = connection.introspection.get_table_description(
            cursor,
            table_name,
        )
    available_columns = {column.name for column in table_description}
    if "max_production_limit" not in available_columns:
        return None
    if "blueprint_item_type_id" in available_columns:
        blueprint_key_column = "blueprint_item_type_id"
    elif "eve_type_id" in available_columns:
        blueprint_key_column = "eve_type_id"
    else:
        return None

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT MIN(max_production_limit)
            FROM {table_name}
            WHERE {blueprint_key_column} = %s
            AND max_production_limit IS NOT NULL
            AND max_production_limit > 0
            """,
            [numeric_blueprint_type_id],
        )
        row = cursor.fetchone()

    max_production_limit = row[0] if row else None
    if max_production_limit is None:
        return None
    return int(max_production_limit)


def get_max_manufacturing_runs_before_launch_window(
    *,
    blueprint_type_id: int,
    time_efficiency: int | float | None = 0,
    structure_time_bonus_percent: int | float | None = 0,
) -> int | None:
    numeric_blueprint_type_id = int(blueprint_type_id or 0)
    if numeric_blueprint_type_id <= 0:
        return None

    base_time_seconds = (
        SDEBlueprintActivity.objects.filter(
            eve_type_id=numeric_blueprint_type_id,
            activity_id=IndustryActivityMixin.ACTIVITY_MANUFACTURING,
        )
        .values_list("time", flat=True)
        .first()
    )
    if base_time_seconds is None:
        return None

    effective_cycle_seconds = compute_effective_cycle_seconds(
        base_time_seconds=base_time_seconds,
        time_efficiency=time_efficiency,
        structure_time_bonus_percent=structure_time_bonus_percent,
    )
    return compute_max_runs_before_launch_window(effective_cycle_seconds)


def get_max_copy_runs_per_request(
    *,
    blueprint_type_id: int,
    time_efficiency: int | float | None = 0,
    structure_time_bonus_percent: int | float | None = 0,
) -> int | None:
    native_limit = get_blueprint_max_production_limit(
        blueprint_type_id=blueprint_type_id,
    )
    launch_window_limit = get_max_manufacturing_runs_before_launch_window(
        blueprint_type_id=blueprint_type_id,
        time_efficiency=time_efficiency,
        structure_time_bonus_percent=structure_time_bonus_percent,
    )
    candidate_limits = [
        int(limit)
        for limit in (native_limit, launch_window_limit)
        if limit is not None and int(limit) > 0
    ]
    if not candidate_limits:
        return None
    return min(candidate_limits)


def build_craft_time_map(
    *,
    recipe_map: dict[int, dict[str, Any]] | dict[str, dict[str, Any]] | None,
    product_type_id: int | None,
    product_type_name: str,
    product_output_per_cycle: int,
    root_blueprint_type_id: int | None,
) -> dict[int, dict[str, Any]]:
    craftable_type_ids: list[int] = []
    if product_type_id:
        craftable_type_ids.append(int(product_type_id))

    for raw_type_id in (recipe_map or {}).keys():
        try:
            numeric_type_id = int(raw_type_id)
        except (TypeError, ValueError):
            continue
        if numeric_type_id not in craftable_type_ids:
            craftable_type_ids.append(numeric_type_id)

    if not craftable_type_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(craftable_type_ids))
    rows: list[tuple[Any, ...]] = []
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                product.product_eve_type_id AS type_id,
                COALESCE(item.name_en, item.name) AS type_name,
                product.eve_type_id AS blueprint_type_id,
                product.activity_id AS activity_id,
                product.quantity AS produced_per_cycle,
                COALESCE(activity_time.time, 0) AS base_time_seconds
            FROM indy_hub_sdeindustryactivityproduct product
            JOIN eve_sde_itemtype item ON item.id = product.product_eve_type_id
            LEFT JOIN indy_hub_sdeblueprintactivity activity_time
                ON activity_time.eve_type_id = product.eve_type_id
                                AND activity_time.activity_id = product.activity_id
            WHERE product.product_eve_type_id IN ({placeholders})
                        AND product.activity_id IN (1, 9, 11)
            ORDER BY
                CASE product.activity_id
                    WHEN 1 THEN 0
                    WHEN 9 THEN 1
                    WHEN 11 THEN 2
                    ELSE 99
                END,
                COALESCE(item.name_en, item.name)
            """,
            craftable_type_ids,
        )
        rows = list(cursor.fetchall())

    time_map: dict[int, dict[str, Any]] = {}
    for (
        type_id,
        type_name,
        blueprint_type_id,
        activity_id,
        produced_per_cycle,
        base_time_seconds,
    ) in rows:
        numeric_type_id = int(type_id or 0)
        if numeric_type_id <= 0 or numeric_type_id in time_map:
            continue
        numeric_activity_id = int(
            activity_id or IndustryActivityMixin.ACTIVITY_MANUFACTURING
        )
        time_map[numeric_type_id] = {
            "type_id": numeric_type_id,
            "type_name": str(type_name or numeric_type_id),
            "blueprint_type_id": int(blueprint_type_id or 0),
            "activity_id": numeric_activity_id,
            "activity_label": _activity_label(numeric_activity_id),
            "produced_per_cycle": int(produced_per_cycle or 1),
            "base_time_seconds": max(0, int(base_time_seconds or 0)),
        }

    if (
        product_type_id
        and int(product_type_id) not in time_map
        and root_blueprint_type_id
    ):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    product.activity_id,
                    product.quantity,
                    COALESCE(activity_time.time, 0) AS base_time_seconds
                FROM indy_hub_sdeindustryactivityproduct product
                LEFT JOIN indy_hub_sdeblueprintactivity activity_time
                    ON activity_time.eve_type_id = product.eve_type_id
                                        AND activity_time.activity_id = product.activity_id
                WHERE product.eve_type_id = %s
                                AND product.product_eve_type_id = %s
                                AND product.activity_id IN (1, 9, 11)
                ORDER BY
                    CASE product.activity_id
                        WHEN 1 THEN 0
                        WHEN 9 THEN 1
                        WHEN 11 THEN 2
                        ELSE 99
                    END
                LIMIT 1
                """,
                [int(root_blueprint_type_id), int(product_type_id)],
            )
            row = cursor.fetchone()

        if row:
            activity_id, produced_per_cycle, base_time_seconds = row
            numeric_activity_id = int(
                activity_id or IndustryActivityMixin.ACTIVITY_MANUFACTURING
            )
            time_map[int(product_type_id)] = {
                "type_id": int(product_type_id),
                "type_name": str(product_type_name or product_type_id),
                "blueprint_type_id": int(root_blueprint_type_id),
                "activity_id": numeric_activity_id,
                "activity_label": _activity_label(numeric_activity_id),
                "produced_per_cycle": int(
                    produced_per_cycle or product_output_per_cycle or 1
                ),
                "base_time_seconds": max(0, int(base_time_seconds or 0)),
            }

    return time_map
