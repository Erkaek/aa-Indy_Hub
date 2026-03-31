# API views and external services
"""
API views and external service integrations for the Indy Hub module.
These views handle API calls, external data fetching, and service integrations.
"""

# Standard Library
import json
import re
from datetime import timedelta
from decimal import Decimal
from math import ceil

# Django
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

from ..decorators import indy_hub_access_required, indy_hub_permission_required

# Local
from ..models import (
    BlueprintEfficiency,
    CustomPrice,
    ProductionConfig,
    ProductionSimulation,
)
from ..services.craft_materials import (
    compute_job_material_quantity,
    is_base_item_material_efficiency_exempt,
)
from ..services.craft_structures import (
    build_craft_structure_planner,
    compute_solar_system_jump_distances,
)
from ..services.craft_times import build_craft_time_map
from ..services.industry_skills import build_craft_character_advisor
from ..services.industry_structures import resolve_solar_system_reference
from ..utils.analytics import emit_view_analytics_event
from ..utils.menu_badge import compute_menu_badge_count

logger = get_extension_logger(__name__)

MENU_BADGE_CACHE_TTL_SECONDS = 45
SKILL_CACHE_TTL = timedelta(hours=1)


def _to_serializable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    return value


@login_required
@require_http_methods(["GET"])
def menu_badge_count(request):
    """Return current Indy Hub menu badge count for live menu update."""
    if not request.user.has_perm("indy_hub.can_access_indy_hub"):
        return JsonResponse({"count": 0}, status=403)

    cache_key = f"indy_hub:menu_badge_count:{request.user.id}"
    refresh_lock_key = f"indy_hub:menu_badge_count_refreshing:{request.user.id}"
    count = cache.get(cache_key)
    if count is None:
        try:
            if cache.add(refresh_lock_key, 1, 30):
                count = compute_menu_badge_count(int(request.user.id))
                cache.set(cache_key, count, MENU_BADGE_CACHE_TTL_SECONDS)
                cache.delete(refresh_lock_key)
            else:
                count = 0
        except Exception:
            count = 0
    return JsonResponse({"count": int(count or 0)})


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_http_methods(["GET"])
def craft_bp_payload(request, type_id: int):
    """Return the craft blueprint payload as JSON for a given number of runs.

    This is used by the V2 UI to simulate profitability across multiple run counts
    while allowing buy/prod decisions to change with cycle rounding effects.
    """
    emit_view_analytics_event(view_name="api.craft_bp_payload", request=request)

    debug_enabled = str(request.GET.get("indy_debug", "")).strip() in {
        "1",
        "true",
        "yes",
    } or str(request.GET.get("debug", "")).strip() in {"1", "true", "yes"}

    try:
        num_runs = max(1, int(request.GET.get("runs", 1)))
    except (TypeError, ValueError):
        num_runs = 1

    try:
        me = int(request.GET.get("me", 0) or 0)
    except (TypeError, ValueError):
        me = 0
    try:
        te = int(request.GET.get("te", 0) or 0)
    except (TypeError, ValueError):
        te = 0

    # Parse per-blueprint ME/TE overrides: me_<bpTypeId>, te_<bpTypeId>
    me_te_configs: dict[int, dict[str, int]] = {}
    for key, value in request.GET.items():
        if not value:
            continue
        if key.startswith("me_"):
            try:
                bp_type_id = int(key.replace("me_", ""))
                me_value = int(value)
                me_te_configs.setdefault(bp_type_id, {})["me"] = me_value
            except (ValueError, TypeError):
                continue
        elif key.startswith("te_"):
            try:
                bp_type_id = int(key.replace("te_", ""))
                te_value = int(value)
                me_te_configs.setdefault(bp_type_id, {})["te"] = te_value
            except (ValueError, TypeError):
                continue

    # Final product and output qty per run.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT product_eve_type_id, quantity
            FROM indy_hub_sdeindustryactivityproduct
            WHERE eve_type_id = %s AND activity_id IN (1, 11)
            LIMIT 1
            """,
            [type_id],
        )
        product_row = cursor.fetchone()

    product_type_id = product_row[0] if product_row else None
    output_qty_per_run = product_row[1] if product_row and len(product_row) > 1 else 1
    final_product_qty = (output_qty_per_run or 1) * num_runs

    debug_info: dict[str, object] = {}
    if debug_enabled:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM indy_hub_sdeindustryactivitymaterial
                    WHERE eve_type_id = %s AND activity_id IN (1, 11)
                    """,
                    [type_id],
                )
                mats_count = int(cursor.fetchone()[0])
            debug_info = {
                "db_vendor": connection.vendor,
                "requested_type_id": int(type_id),
                "num_runs": int(num_runs),
                "me": int(me),
                "te": int(te),
                "me_te_configs_count": int(len(me_te_configs)),
                "product_row_found": bool(product_row),
                "product_type_id": int(product_type_id) if product_type_id else None,
                "output_qty_per_run": int(output_qty_per_run or 1),
                "top_level_material_rows": mats_count,
            }
        except Exception as e:
            debug_info = {
                "debug_error": f"{type(e).__name__}: {str(e)}",
            }

    # Exact per-cycle recipes for craftable items (keyed by product type_id).
    # We expose both the currently configured input quantities and the raw ME 0
    # quantities because industry install cost uses the unmodified job inputs.
    recipe_map: dict[int, dict[str, object]] = {}
    recipe_cache: dict[tuple[int, int], dict[str, object]] = {}
    blueprint_product_type_cache: dict[int, int | None] = {}
    type_meta_cache: dict[int, tuple[int | None, int | None]] = {}

    if type_id:
        blueprint_product_type_cache[int(type_id)] = (
            int(product_type_id) if product_type_id else None
        )

    def get_blueprint_product_type_id(blueprint_type_id: int) -> int | None:
        blueprint_type_id = int(blueprint_type_id or 0)
        if blueprint_type_id in blueprint_product_type_cache:
            return blueprint_product_type_cache[blueprint_type_id]

        with connection.cursor() as lookup_cursor:
            lookup_cursor.execute(
                """
                SELECT product_eve_type_id
                FROM indy_hub_sdeindustryactivityproduct
                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                LIMIT 1
                """,
                [blueprint_type_id],
            )
            row = lookup_cursor.fetchone()

        resolved = int(row[0]) if row and row[0] else None
        blueprint_product_type_cache[blueprint_type_id] = resolved
        return resolved

    def get_type_meta(type_id_value: int) -> tuple[int | None, int | None]:
        numeric_type_id = int(type_id_value or 0)
        if not numeric_type_id:
            return (None, None)
        if numeric_type_id in type_meta_cache:
            return type_meta_cache[numeric_type_id]

        with connection.cursor() as meta_cursor:
            meta_cursor.execute(
                """
                SELECT t.meta_group_id_raw, g.category_id
                FROM eve_sde_itemtype t
                LEFT JOIN eve_sde_itemgroup g ON t.group_id = g.id
                WHERE t.id = %s
                LIMIT 1
                """,
                [numeric_type_id],
            )
            row = meta_cursor.fetchone()

        meta_group_id = int(row[0]) if row and row[0] is not None else None
        category_id = int(row[1]) if row and row[1] is not None else None
        type_meta_cache[numeric_type_id] = (meta_group_id, category_id)
        return type_meta_cache[numeric_type_id]

    def material_efficiency_applies(
        blueprint_type_id: int, material_type_id: int
    ) -> bool:
        product_type_id_for_blueprint = get_blueprint_product_type_id(blueprint_type_id)
        if not product_type_id_for_blueprint:
            return True

        parent_meta_group_id, parent_category_id = get_type_meta(
            product_type_id_for_blueprint
        )
        material_meta_group_id, material_category_id = get_type_meta(material_type_id)
        return not is_base_item_material_efficiency_exempt(
            parent_meta_group_id,
            parent_category_id,
            material_meta_group_id,
            material_category_id,
        )

    def build_recipe_entry(
        blueprint_type_id: int, blueprint_me: int = 0
    ) -> dict[str, object]:
        cache_key = (int(blueprint_type_id), int(blueprint_me))
        if cache_key in recipe_cache:
            return recipe_cache[cache_key]

        with connection.cursor() as recipe_cursor:
            recipe_cursor.execute(
                """
                SELECT quantity
                FROM indy_hub_sdeindustryactivityproduct
                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                LIMIT 1
                """,
                [blueprint_type_id],
            )
            output_row = recipe_cursor.fetchone()
            produced_per_cycle = int((output_row[0] if output_row else 1) or 1)

            recipe_cursor.execute(
                """
                SELECT material_eve_type_id, quantity
                FROM indy_hub_sdeindustryactivitymaterial
                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                """,
                [blueprint_type_id],
            )
            adjusted_inputs = []
            me0_inputs = []
            for mat_type_id, base_qty_per_cycle in recipe_cursor.fetchall():
                raw_qty_per_cycle = int(base_qty_per_cycle or 0)
                if raw_qty_per_cycle <= 0:
                    continue
                adjusted_qty_per_cycle = ceil(
                    raw_qty_per_cycle * (100 - int(blueprint_me or 0)) / 100
                )
                if adjusted_qty_per_cycle > 0:
                    adjusted_inputs.append(
                        {
                            "type_id": int(mat_type_id),
                            "quantity": int(adjusted_qty_per_cycle),
                        }
                    )
                me0_inputs.append(
                    {
                        "type_id": int(mat_type_id),
                        "quantity": int(raw_qty_per_cycle),
                    }
                )

        recipe_cache[cache_key] = {
            "produced_per_cycle": produced_per_cycle,
            "inputs_per_cycle": adjusted_inputs,
            "inputs_per_cycle_me0": me0_inputs,
        }
        return recipe_cache[cache_key]

    def get_materials_tree(
        bp_id,
        runs,
        blueprint_me=0,
        depth=0,
        max_depth=10,
        seen=None,
        me_te_map=None,
    ):
        if seen is None:
            seen = set()
        if me_te_map is None:
            me_te_map = {}
        if depth > max_depth or bp_id in seen:
            return []
        seen.add(bp_id)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.material_eve_type_id, t.name, m.quantity
                FROM indy_hub_sdeindustryactivitymaterial m
                JOIN eve_sde_itemtype t ON m.material_eve_type_id = t.id
                WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                """,
                [bp_id],
            )

            mats = []
            for row in cursor.fetchall():
                material_type_id = int(row[0])
                apply_material_efficiency = material_efficiency_applies(
                    bp_id, material_type_id
                )
                qty = compute_job_material_quantity(
                    row[2],
                    runs,
                    blueprint_me,
                    apply_material_efficiency=apply_material_efficiency,
                )
                mat = {
                    "type_id": material_type_id,
                    "type_name": row[1],
                    "quantity": qty,
                    "material_bonus_applicable": apply_material_efficiency,
                    "cycles": None,
                    "produced_per_cycle": None,
                    "total_produced": None,
                    "surplus": None,
                }

                # If craftable, compute cycles + recurse.
                with connection.cursor() as sub_cursor:
                    sub_cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM indy_hub_sdeindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat["type_id"]],
                    )
                    sub_bp_row = sub_cursor.fetchone()

                    if sub_bp_row:
                        sub_bp_id = sub_bp_row[0]
                        sub_cursor.execute(
                            """
                            SELECT quantity
                            FROM indy_hub_sdeindustryactivityproduct
                            WHERE eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [sub_bp_id],
                        )
                        prod_qty_row = sub_cursor.fetchone()
                        output_qty = prod_qty_row[0] if prod_qty_row else 1
                        cycles = ceil(mat["quantity"] / output_qty)
                        total_produced = cycles * output_qty
                        surplus = total_produced - mat["quantity"]
                        mat["cycles"] = cycles
                        mat["produced_per_cycle"] = output_qty
                        mat["total_produced"] = total_produced
                        mat["surplus"] = surplus

                        sub_bp_config = (me_te_map or {}).get(sub_bp_id, {})
                        sub_bp_me = sub_bp_config.get("me", 0)

                        # Key recipe map by produced item type_id (not blueprint id)
                        produced_type_id = int(mat["type_id"])
                        if produced_type_id not in recipe_map:
                            recipe_map[produced_type_id] = build_recipe_entry(
                                int(sub_bp_id),
                                int(sub_bp_me or 0),
                            )

                        mat["sub_materials"] = get_materials_tree(
                            sub_bp_id,
                            cycles,
                            sub_bp_me,
                            depth + 1,
                            max_depth,
                            seen.copy(),
                            me_te_map,
                        )
                    else:
                        mat["sub_materials"] = []

                mats.append(mat)
            return mats

    materials_tree = get_materials_tree(type_id, num_runs, me, me_te_map=me_te_configs)

    if product_type_id:
        recipe_map.setdefault(
            int(product_type_id),
            build_recipe_entry(int(type_id), int(me or 0)),
        )

    production_time_map = build_craft_time_map(
        recipe_map=recipe_map,
        product_type_id=product_type_id,
        product_type_name="",
        product_output_per_cycle=output_qty_per_run,
        root_blueprint_type_id=type_id,
    )
    craft_character_advisor = build_craft_character_advisor(
        user=request.user,
        production_time_map=production_time_map,
        skill_cache_ttl=SKILL_CACHE_TTL,
    )

    payload = {
        "type_id": type_id,
        "bp_type_id": type_id,
        "num_runs": num_runs,
        "me": me,
        "te": te,
        "product_type_id": product_type_id,
        "output_qty_per_run": output_qty_per_run,
        "product_output_per_cycle": output_qty_per_run,
        "final_product_qty": final_product_qty,
        "materials_tree": _to_serializable(materials_tree),
        "recipe_map": _to_serializable(recipe_map),
        "production_time_map": _to_serializable(production_time_map),
        "craft_character_advisor": _to_serializable(craft_character_advisor),
        "structure_planner": _to_serializable(
            build_craft_structure_planner(
                product_type_id=product_type_id,
                product_type_name="",
                product_output_per_cycle=output_qty_per_run,
                craft_cycles_summary={},
            )
        ),
    }

    if debug_enabled:
        payload["_debug"] = _to_serializable(debug_info)

    return JsonResponse(payload)


def _parse_target_system_ids(raw_values: list[str]) -> list[int]:
    target_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw_value in raw_values:
        for part in re.split(r"[\s,]+", str(raw_value or "")):
            if not part:
                continue
            try:
                target_id = int(part)
            except (TypeError, ValueError):
                continue
            if target_id <= 0 or target_id in seen_ids:
                continue
            seen_ids.add(target_id)
            target_ids.append(target_id)
    return target_ids


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_http_methods(["GET"])
def craft_structure_jump_distances(request):
    solar_system_id = request.GET.get("solar_system_id")
    solar_system_name = str(request.GET.get("solar_system_name", "")).strip()

    try:
        resolved_system_id = (
            int(solar_system_id) if solar_system_id not in (None, "") else None
        )
    except (TypeError, ValueError):
        resolved_system_id = None

    origin_reference = resolve_solar_system_reference(
        solar_system_id=resolved_system_id,
        solar_system_name=solar_system_name or None,
    )
    if origin_reference is None:
        return JsonResponse({"error": "solar_system_not_found"}, status=404)

    target_system_ids = _parse_target_system_ids(
        request.GET.getlist("target_system_ids")
    )
    if not target_system_ids:
        return JsonResponse({"error": "target_system_ids_required"}, status=400)

    origin_id, origin_name, origin_security_band = origin_reference
    jump_distances = compute_solar_system_jump_distances(origin_id, target_system_ids)

    return JsonResponse(
        {
            "origin": {
                "solar_system_id": origin_id,
                "solar_system_name": origin_name,
                "security_band": origin_security_band,
            },
            "distances": [
                {
                    "solar_system_id": target_system_id,
                    "jumps": jump_distances.get(target_system_id),
                }
                for target_system_id in target_system_ids
            ],
        }
    )


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def fuzzwork_price(request):
    emit_view_analytics_event(view_name="api.fuzzwork_price", request=request)
    """
    Get item prices from Fuzzwork API.

    This view fetches current market prices for EVE Online items
    from the Fuzzwork Market API service.
    Supports both single type_id and comma-separated multiple type_ids.
    """
    type_id = request.GET.get("type_id")
    full = str(request.GET.get("full", "")).strip().lower() in {"1", "true", "yes"}
    price_source = str(request.GET.get("price_source", "market")).strip().lower()
    if not type_id:
        return JsonResponse({"error": "type_id parameter required"}, status=400)

    try:
        # Support multiple type IDs separated by commas
        type_ids = [t.strip() for t in type_id.split(",") if t.strip()]
        if not type_ids:
            return JsonResponse({"error": "Invalid type_id parameter"}, status=400)

        # Remove duplicates and join back
        unique_type_ids = list(set(type_ids))
        if price_source == "adjusted":
            from ..services.market_prices import fetch_adjusted_prices

            adjusted_prices = fetch_adjusted_prices(unique_type_ids, timeout=10)
            if full:
                return JsonResponse(
                    {
                        str(tid): {
                            "adjusted_price": float(
                                price_data.get("adjusted_price", 0)
                            ),
                            "average_price": float(price_data.get("average_price", 0)),
                        }
                        for tid, price_data in adjusted_prices.items()
                    }
                )

            return JsonResponse(
                {
                    str(tid): float(price_data.get("adjusted_price", 0))
                    for tid, price_data in adjusted_prices.items()
                }
            )

        # Local
        from ..services.fuzzwork import fetch_fuzzwork_aggregates

        # Fetch price data from Fuzzwork API
        data = fetch_fuzzwork_aggregates(unique_type_ids, timeout=10)

        # Optional: return the full Fuzzwork payload for each requested typeId.
        # This is used by the "Calcul" tab for deep inspection.
        if full:
            result = {}
            for tid in unique_type_ids:
                # Fuzzwork keys are strings in the aggregates response.
                result[tid] = data.get(tid, {})
            return JsonResponse(result)

        # Return simplified price data (use sell.min for material costs, sell.min for products)
        result = {}
        for tid in unique_type_ids:
            if tid in data:
                item_data = data[tid]
                # Use sell.min as the default price (what you'd pay to buy)
                sell_min = float(item_data.get("sell", {}).get("min", 0))
                result[tid] = sell_min
            else:
                result[tid] = 0

        return JsonResponse(result)

    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing price data: {e}")
        return JsonResponse({"error": "Invalid data received"}, status=500)
    except Exception as e:
        if e.__class__.__name__ in {"FuzzworkError", "MarketPriceError"}:
            logger.error("Error fetching price data: %s", e)
            return JsonResponse({"error": "Unable to fetch price data"}, status=503)
        raise


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_http_methods(["POST"])
def save_production_config(request):
    emit_view_analytics_event(view_name="api.save_production_config", request=request)
    """
    Save complete production configuration to database.

    Expected JSON payload:
    {
        "blueprint_type_id": 12345,
        "blueprint_name": "Some Blueprint",
        "runs": 1,
        "simulation_name": "My Config",
        "active_tab": "materials",
        "items": [
            {"type_id": 11111, "mode": "prod", "quantity": 100},
            {"type_id": 22222, "mode": "buy", "quantity": 50}
        ],
        "blueprint_efficiencies": [
            {"blueprint_type_id": 12345, "material_efficiency": 10, "time_efficiency": 20}
        ],
        "custom_prices": [
            {"item_type_id": 11111, "unit_price": 1000.0, "is_sale_price": false},
            {"item_type_id": 99999, "unit_price": 50000.0, "is_sale_price": true}
        ],
        "estimated_cost": 125000.0,
        "estimated_revenue": 175000.0,
        "estimated_profit": 50000.0
    }
    """
    try:
        data = json.loads(request.body)
        blueprint_type_id = data.get("blueprint_type_id")
        runs = data.get("runs", 1)

        if not blueprint_type_id:
            return JsonResponse({"error": "blueprint_type_id is required"}, status=400)

        # Create or update the simulation
        simulation, created = ProductionSimulation.objects.get_or_create(
            user=request.user,
            blueprint_type_id=blueprint_type_id,
            runs=runs,
            defaults={
                "blueprint_name": data.get(
                    "blueprint_name", f"Blueprint {blueprint_type_id}"
                ),
                "simulation_name": data.get("simulation_name", ""),
                "active_tab": data.get("active_tab", "materials"),
                "estimated_cost": data.get("estimated_cost", 0),
                "estimated_revenue": data.get("estimated_revenue", 0),
                "estimated_profit": data.get("estimated_profit", 0),
            },
        )

        if not created:
            # Update the existing simulation
            simulation.blueprint_name = data.get(
                "blueprint_name", simulation.blueprint_name
            )
            simulation.simulation_name = data.get(
                "simulation_name", simulation.simulation_name
            )
            simulation.active_tab = data.get("active_tab", simulation.active_tab)
            simulation.estimated_cost = data.get(
                "estimated_cost", simulation.estimated_cost
            )
            simulation.estimated_revenue = data.get(
                "estimated_revenue", simulation.estimated_revenue
            )
            simulation.estimated_profit = data.get(
                "estimated_profit", simulation.estimated_profit
            )
            simulation.save()

        # 1. Save the Prod/Buy/Useless configurations
        items = data.get("items", [])
        if items:
            # Remove the previous configurations
            ProductionConfig.objects.filter(simulation=simulation).delete()

            # Create the new configurations
            configs = []
            for item in items:
                config = ProductionConfig(
                    user=request.user,
                    simulation=simulation,
                    blueprint_type_id=blueprint_type_id,
                    item_type_id=item["type_id"],
                    production_mode=item["mode"],
                    quantity_needed=item.get("quantity", 0),
                    runs=runs,
                )
                configs.append(config)

            ProductionConfig.objects.bulk_create(configs)

            # Update the simulation statistics
            simulation.total_items = len(items)
            simulation.total_buy_items = len([i for i in items if i["mode"] == "buy"])
            simulation.total_prod_items = len([i for i in items if i["mode"] == "prod"])

        # 2. Save the blueprint ME/TE efficiencies
        blueprint_efficiencies = data.get("blueprint_efficiencies", [])
        if blueprint_efficiencies:
            # Remove previous efficiencies
            BlueprintEfficiency.objects.filter(simulation=simulation).delete()

            # Create the new efficiencies
            efficiencies = []
            for eff in blueprint_efficiencies:
                efficiency = BlueprintEfficiency(
                    user=request.user,
                    simulation=simulation,
                    blueprint_type_id=eff["blueprint_type_id"],
                    material_efficiency=eff.get("material_efficiency", 0),
                    time_efficiency=eff.get("time_efficiency", 0),
                )
                efficiencies.append(efficiency)

            BlueprintEfficiency.objects.bulk_create(efficiencies)

        # 3. Save the custom prices
        custom_prices = data.get("custom_prices", [])
        if custom_prices:
            # Remove previous prices
            CustomPrice.objects.filter(simulation=simulation).delete()

            # Create the new prices
            prices = []
            for price in custom_prices:
                custom_price = CustomPrice(
                    user=request.user,
                    simulation=simulation,
                    item_type_id=price["item_type_id"],
                    unit_price=price.get("unit_price", 0),
                    is_sale_price=price.get("is_sale_price", False),
                )
                prices.append(custom_price)

            CustomPrice.objects.bulk_create(prices)

        simulation.save()

        return JsonResponse(
            {
                "success": True,
                "simulation_id": simulation.id,
                "simulation_created": created,
                "saved_items": len(items),
                "saved_efficiencies": len(blueprint_efficiencies),
                "saved_prices": len(custom_prices),
                "message": "Complete production configuration saved successfully",
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON data"}, status=400)
    except Exception as e:
        logger.error(f"Error saving production config: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def load_production_config(request):
    emit_view_analytics_event(view_name="api.load_production_config", request=request)
    """
    Load complete production configuration from database.

    Parameters:
    - blueprint_type_id: Required
    - runs: Optional (default 1)

    Returns:
    {
        "blueprint_type_id": 12345,
        "blueprint_name": "Some Blueprint",
        "runs": 1,
        "simulation_name": "My Config",
        "active_tab": "materials",
        "items": [
            {"type_id": 11111, "mode": "prod", "quantity": 100},
            {"type_id": 22222, "mode": "buy", "quantity": 50}
        ],
        "blueprint_efficiencies": [
            {"blueprint_type_id": 12345, "material_efficiency": 10, "time_efficiency": 20}
        ],
        "custom_prices": [
            {"item_type_id": 11111, "unit_price": 1000.0, "is_sale_price": false},
            {"item_type_id": 99999, "unit_price": 50000.0, "is_sale_price": true}
        ],
        "estimated_cost": 125000.0,
        "estimated_revenue": 175000.0,
        "estimated_profit": 50000.0
    }
    """
    blueprint_type_id = request.GET.get("blueprint_type_id")
    runs_param = request.GET.get("runs", 1)
    try:
        runs = int(runs_param)
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "runs must be an integer"},
            status=400,
        )
    if runs < 1:
        return JsonResponse(
            {"error": "runs must be >= 1"},
            status=400,
        )

    if not blueprint_type_id:
        return JsonResponse(
            {"error": "blueprint_type_id parameter required"}, status=400
        )

    try:
        simulation = None  # Load the simulation if it exists
        try:
            simulation = ProductionSimulation.objects.get(
                user=request.user, blueprint_type_id=blueprint_type_id, runs=runs
            )
        except ProductionSimulation.DoesNotExist:
            pass

        items = []  # Step 1: production/buy/useless configurations
        if simulation:
            configs = ProductionConfig.objects.filter(simulation=simulation)
            for config in configs:
                items.append(
                    {
                        "type_id": config.item_type_id,
                        "mode": config.production_mode,
                        "quantity": config.quantity_needed,
                    }
                )

        blueprint_efficiencies = []  # Step 2: blueprint ME/TE efficiencies
        if simulation:
            efficiencies = BlueprintEfficiency.objects.filter(simulation=simulation)
            for eff in efficiencies:
                blueprint_efficiencies.append(
                    {
                        "blueprint_type_id": eff.blueprint_type_id,
                        "material_efficiency": eff.material_efficiency,
                        "time_efficiency": eff.time_efficiency,
                    }
                )

        custom_prices = []  # Step 3: custom prices
        if simulation:
            prices = CustomPrice.objects.filter(simulation=simulation)
            for price in prices:
                custom_prices.append(
                    {
                        "item_type_id": price.item_type_id,
                        "unit_price": float(price.unit_price),
                        "is_sale_price": price.is_sale_price,
                    }
                )

        response_data = {
            "blueprint_type_id": int(blueprint_type_id),
            "runs": runs,
            "items": items,
            "blueprint_efficiencies": blueprint_efficiencies,
            "custom_prices": custom_prices,
        }

        if simulation:  # Add simulation metadata when it exists
            response_data.update(
                {
                    "simulation_id": simulation.id,
                    "blueprint_name": simulation.blueprint_name,
                    "simulation_name": simulation.simulation_name,
                    "active_tab": simulation.active_tab,
                    "estimated_cost": float(simulation.estimated_cost),
                    "estimated_revenue": float(simulation.estimated_revenue),
                    "estimated_profit": float(simulation.estimated_profit),
                    "total_items": simulation.total_items,
                    "total_buy_items": simulation.total_buy_items,
                    "total_prod_items": simulation.total_prod_items,
                }
            )

        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"Error loading production config: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)
