"""Helpers for estimating copy job item values from SDE and market prices."""

from __future__ import annotations

# Standard Library
from decimal import Decimal, InvalidOperation
from typing import Any

# Django
from django.utils.translation import gettext_lazy as _

from .craft_times import compute_effective_cycle_seconds
from .industry_offer_helpers import format_duration_compact, format_percent_compact

__all__ = [
    "build_copy_duration_payload",
    "build_copy_estimated_item_values",
    "copy_estimate_cost_rate",
    "copy_estimate_decimal",
    "fetch_item_base_prices",
    "serialize_copy_request_price_estimate",
]


def copy_estimate_decimal(value: Any) -> Decimal:
    """Coerce arbitrary values to Decimal while tolerating invalid inputs."""

    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def copy_estimate_cost_rate(
    breakdown: Any,
    *,
    copying_job_cost_base_percent: Decimal | int | float | str,
) -> Decimal:
    """Compute effective copy installation cost rate from breakdown percentages."""

    percent_factor = Decimal("100")
    job_cost_base_rate = (
        copy_estimate_decimal(copying_job_cost_base_percent) / percent_factor
    )
    job_cost_multiplier = max(
        Decimal("0"),
        Decimal("1")
        - (
            copy_estimate_decimal(breakdown.total_job_cost_bonus_percent)
            / percent_factor
        ),
    )
    system_cost_rate = (
        copy_estimate_decimal(breakdown.system_cost_index_percent) / percent_factor
    ) * job_cost_multiplier
    tax_rate = copy_estimate_decimal(breakdown.facility_tax_percent) / percent_factor
    scc_rate = copy_estimate_decimal(breakdown.scc_surcharge_percent) / percent_factor
    return job_cost_base_rate * (system_cost_rate + tax_rate + scc_rate)


def serialize_copy_request_price_estimate(
    estimate: dict[str, Any],
) -> dict[str, Any]:
    """Normalize estimate payload values to strings for JSON responses."""

    def estimate_str(key: str, default: str = "0") -> str:
        value = estimate.get(key)
        return str(default if value is None else value)

    return {
        "amount": estimate_str("amount"),
        "amount_display": estimate_str("amount_display", ""),
        "estimated_item_unit_value": estimate_str("estimated_item_unit_value"),
        "job_cost_base_percent": estimate_str("job_cost_base_percent", ""),
        "system_cost_index_percent": estimate_str("system_cost_index_percent", ""),
        "job_cost_bonus_percent": estimate_str("job_cost_bonus_percent"),
        "facility_tax_percent": estimate_str("facility_tax_percent"),
        "scc_surcharge_percent": estimate_str("scc_surcharge_percent"),
    }


def fetch_item_base_prices(
    type_ids: list[int],
    *,
    connection: Any,
) -> dict[int, Decimal]:
    """Return published item base prices keyed by type ID."""

    normalized_type_ids = sorted({int(type_id) for type_id in type_ids if type_id})
    if not normalized_type_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(normalized_type_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, base_price FROM eve_sde_itemtype WHERE id IN ({placeholders}) AND COALESCE(published, 0) = 1",
            normalized_type_ids,
        )
        return {
            int(type_id): Decimal(str(base_price or 0))
            for type_id, base_price in cursor.fetchall()
        }


def build_copy_estimated_item_values(
    requests: list[Any],
    *,
    connection: Any,
    fetch_adjusted_prices_fn: Any,
    market_price_error_type: type[Exception],
) -> dict[int, dict[str, Any]]:
    """Build estimated copy item values from manufacturing material baskets."""

    blueprint_type_ids = sorted(
        {int(req.type_id) for req in requests if getattr(req, "type_id", None)}
    )
    if not blueprint_type_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(blueprint_type_ids))

    product_type_by_blueprint: dict[int, int] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT ba.blueprint_item_type_id, p.item_type_id
            FROM eve_sde_blueprintactivityproduct p
            JOIN eve_sde_blueprintactivity ba ON ba.id = p.blueprint_activity_id
            JOIN eve_sde_itemtype blueprint_t ON blueprint_t.id = ba.blueprint_item_type_id
            JOIN eve_sde_itemtype product_t ON product_t.id = p.item_type_id
            WHERE ba.blueprint_item_type_id IN ({placeholders})
                AND ba.activity = 'manufacturing'
                AND COALESCE(blueprint_t.published, 0) = 1
                AND COALESCE(product_t.published, 0) = 1
            ORDER BY ba.blueprint_item_type_id ASC, p.item_type_id ASC
            """,
            blueprint_type_ids,
        )
        for blueprint_type_id, product_type_id in cursor.fetchall():
            product_type_by_blueprint.setdefault(
                int(blueprint_type_id),
                int(product_type_id),
            )

    materials_by_blueprint: dict[int, list[tuple[int, int]]] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT ba.blueprint_item_type_id, m.item_type_id, m.quantity
            FROM eve_sde_blueprintactivitymaterial m
            JOIN eve_sde_blueprintactivity ba ON ba.id = m.blueprint_activity_id
            JOIN eve_sde_itemtype blueprint_t ON blueprint_t.id = ba.blueprint_item_type_id
            JOIN eve_sde_itemtype material_t ON material_t.id = m.item_type_id
            WHERE ba.blueprint_item_type_id IN ({placeholders})
                AND ba.activity = 'manufacturing'
                AND COALESCE(blueprint_t.published, 0) = 1
                AND COALESCE(material_t.published, 0) = 1
            """,
            blueprint_type_ids,
        )
        for bp_type_id, material_type_id, quantity in cursor.fetchall():
            material_type_id = int(material_type_id or 0)
            quantity = int(quantity or 0)
            if material_type_id <= 0 or quantity <= 0:
                continue
            materials_by_blueprint.setdefault(int(bp_type_id), []).append(
                (material_type_id, quantity)
            )

    material_type_ids = sorted(
        {
            material_type_id
            for entries in materials_by_blueprint.values()
            for material_type_id, _qty in entries
        }
    )
    material_base_prices = fetch_item_base_prices(
        material_type_ids,
        connection=connection,
    )
    try:
        material_price_refs = fetch_adjusted_prices_fn(material_type_ids, timeout=10)
    except market_price_error_type:
        material_price_refs = {}

    def material_price(material_type_id: int) -> tuple[Decimal, str]:
        price_ref = material_price_refs.get(material_type_id, {})
        adjusted_price = Decimal(str(price_ref.get("adjusted_price") or 0))
        if adjusted_price > 0:
            return adjusted_price, "adjusted_price"
        average_price = Decimal(str(price_ref.get("average_price") or 0))
        if average_price > 0:
            return average_price, "average_price"
        base_price = material_base_prices.get(material_type_id, Decimal("0"))
        if base_price > 0:
            return base_price, "base_price"
        return Decimal("0"), ""

    source_rank = {"adjusted_price": 3, "average_price": 2, "base_price": 1, "": 0}

    result: dict[int, dict[str, Any]] = {}
    for req in requests:
        blueprint_type_id = int(req.type_id or 0)
        if blueprint_type_id <= 0:
            continue
        materials = materials_by_blueprint.get(blueprint_type_id) or []
        if not materials:
            continue

        per_run_value = Decimal("0")
        worst_source_rank = source_rank["adjusted_price"]
        worst_source = "adjusted_price"
        any_priced = False
        for material_type_id, quantity in materials:
            unit_price, src = material_price(material_type_id)
            if unit_price <= 0:
                continue
            any_priced = True
            per_run_value += unit_price * Decimal(quantity)
            rank = source_rank.get(src, 0)
            if rank < worst_source_rank:
                worst_source_rank = rank
                worst_source = src

        if not any_priced or per_run_value <= 0:
            continue

        runs_requested = max(int(getattr(req, "runs_requested", 1) or 1), 1)
        product_type_id = product_type_by_blueprint.get(blueprint_type_id)
        result[blueprint_type_id] = {
            "product_type_id": product_type_id,
            "unit_value": per_run_value,
            "estimated_item_value": per_run_value * runs_requested,
            "source": worst_source,
            "runs_requested": runs_requested,
        }

    return result


def build_copy_duration_payload(
    *,
    base_time_seconds: int | float | Decimal | None,
    runs_requested: int,
    copies_requested: int,
    structure_time_bonus_percent: Decimal | int | float | str | None = None,
    character_time_bonus_percent: Decimal | int | float | str | None = None,
) -> dict[str, object] | None:
    """Build duration summary payload for copy requests including bonuses."""

    numeric_base_time_seconds = max(0, int(base_time_seconds or 0))
    if numeric_base_time_seconds <= 0:
        return None

    structure_bonus = Decimal(str(structure_time_bonus_percent or 0))
    character_bonus = Decimal(str(character_time_bonus_percent or 0))
    normalized_runs_requested = max(int(runs_requested or 1), 1)
    normalized_copies_requested = max(int(copies_requested or 1), 1)

    effective_cycle_seconds = compute_effective_cycle_seconds(
        base_time_seconds=numeric_base_time_seconds,
        time_efficiency=float(character_bonus),
        structure_time_bonus_percent=float(structure_bonus),
    )
    per_copy_duration_seconds = effective_cycle_seconds * normalized_runs_requested
    total_duration_seconds = per_copy_duration_seconds * normalized_copies_requested

    meta_parts = [
        _("Per copy %(duration)s")
        % {"duration": format_duration_compact(per_copy_duration_seconds)}
    ]
    if structure_bonus > 0:
        meta_parts.append(
            _("Structure bonus -%(bonus)s%%")
            % {"bonus": format_percent_compact(structure_bonus)}
        )
    if character_bonus > 0:
        meta_parts.append(
            _("Character bonus -%(bonus)s%%")
            % {"bonus": format_percent_compact(character_bonus)}
        )
    else:
        meta_parts.append(_("Character skills not included."))

    return {
        "base_time_seconds": numeric_base_time_seconds,
        "effective_cycle_seconds": effective_cycle_seconds,
        "per_copy_duration_seconds": per_copy_duration_seconds,
        "per_copy_duration_display": format_duration_compact(per_copy_duration_seconds),
        "total_duration_seconds": total_duration_seconds,
        "total_duration_display": format_duration_compact(total_duration_seconds),
        "structure_time_bonus_percent": structure_bonus,
        "character_time_bonus_percent": character_bonus,
        "meta_label": " · ".join(str(part) for part in meta_parts),
    }
