# API views and external services
"""
API views and external service integrations for the Indy Hub module.
These views handle API calls, external data fetching, and service integrations.
"""

# Standard Library
import json
import logging
from decimal import Decimal
from math import ceil

# Third Party
import requests

# Django
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..decorators import indy_hub_access_required, indy_hub_permission_required

# Local
from ..models import (
    BlueprintEfficiency,
    CustomPrice,
    ProductionConfig,
    ProductionSimulation,
)

logger = logging.getLogger(__name__)


def _to_serializable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    return value


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_http_methods(["GET"])
def craft_bp_payload(request, type_id: int):
    """Return the craft blueprint payload as JSON for a given number of runs.

    This is used by the V2 UI to simulate profitability across multiple run counts
    while allowing buy/prod decisions to change with cycle rounding effects.
    """

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
            FROM eveuniverse_eveindustryactivityproduct
            WHERE eve_type_id = %s AND activity_id IN (1, 11)
            LIMIT 1
            """,
            [type_id],
        )
        product_row = cursor.fetchone()

    product_type_id = product_row[0] if product_row else None
    output_qty_per_run = product_row[1] if product_row and len(product_row) > 1 else 1
    final_product_qty = (output_qty_per_run or 1) * num_runs

    # Exact per-cycle recipes for craftable items (keyed by product type_id).
    # This avoids approximating recipes from tree occurrences in the frontend.
    recipe_map: dict[int, dict[str, object]] = {}
    recipe_cache: dict[tuple[int, int], dict[str, object]] = {}

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
                FROM eveuniverse_eveindustryactivitymaterial m
                JOIN eveuniverse_evetype t ON m.material_eve_type_id = t.id
                WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                """,
                [bp_id],
            )

            mats = []
            for row in cursor.fetchall():
                # IMPORTANT: ME rounding must be applied per-run (per job/cycle), then multiplied.
                # Doing ceil((base * runs) * (1 - ME)) underestimates for small base quantities.
                per_run_qty = ceil((row[2] or 0) * (100 - blueprint_me) / 100)
                qty = int(per_run_qty) * int(runs)
                mat = {
                    "type_id": row[0],
                    "type_name": row[1],
                    "quantity": qty,
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
                        FROM eveuniverse_eveindustryactivityproduct
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
                            FROM eveuniverse_eveindustryactivityproduct
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

                        # Build exact per-cycle recipe for this craftable output (mat["type_id"]).
                        # Cache by (blueprint_id, blueprint_me) because ME changes the rounded per-cycle quantities.
                        cache_key = (int(sub_bp_id), int(sub_bp_me))
                        if cache_key not in recipe_cache:
                            with connection.cursor() as recipe_cursor:
                                recipe_cursor.execute(
                                    """
                                    SELECT material_eve_type_id, quantity
                                    FROM eveuniverse_eveindustryactivitymaterial
                                    WHERE eve_type_id = %s AND activity_id IN (1, 11)
                                    """,
                                    [sub_bp_id],
                                )
                                inputs = []
                                for (
                                    mat_type_id,
                                    base_qty_per_cycle,
                                ) in recipe_cursor.fetchall():
                                    qty_per_cycle = ceil(
                                        (base_qty_per_cycle or 0)
                                        * (100 - sub_bp_me)
                                        / 100
                                    )
                                    if qty_per_cycle <= 0:
                                        continue
                                    inputs.append(
                                        {
                                            "type_id": int(mat_type_id),
                                            "quantity": int(qty_per_cycle),
                                        }
                                    )
                            recipe_cache[cache_key] = {
                                "produced_per_cycle": int(output_qty or 1),
                                "inputs_per_cycle": inputs,
                            }

                        # Key recipe map by produced item type_id (not blueprint id)
                        produced_type_id = int(mat["type_id"])
                        if produced_type_id not in recipe_map:
                            recipe_map[produced_type_id] = recipe_cache[cache_key]

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

    payload = {
        "type_id": type_id,
        "bp_type_id": type_id,
        "num_runs": num_runs,
        "me": me,
        "te": te,
        "product_type_id": product_type_id,
        "output_qty_per_run": output_qty_per_run,
        "final_product_qty": final_product_qty,
        "materials_tree": _to_serializable(materials_tree),
        "recipe_map": _to_serializable(recipe_map),
    }

    return JsonResponse(payload)


@login_required
def fuzzwork_price(request):
    """
    Get item prices from Fuzzwork API.

    This view fetches current market prices for EVE Online items
    from the Fuzzwork Market API service.
    Supports both single type_id and comma-separated multiple type_ids.
    """
    type_id = request.GET.get("type_id")
    full = str(request.GET.get("full", "")).strip().lower() in {"1", "true", "yes"}
    if not type_id:
        return JsonResponse({"error": "type_id parameter required"}, status=400)

    try:
        # Support multiple type IDs separated by commas
        type_ids = [t.strip() for t in type_id.split(",") if t.strip()]
        if not type_ids:
            return JsonResponse({"error": "Invalid type_id parameter"}, status=400)

        # Remove duplicates and join back
        unique_type_ids = list(set(type_ids))
        type_ids_str = ",".join(unique_type_ids)

        # Fetch price data from Fuzzwork API
        response = requests.get(
            f"https://market.fuzzwork.co.uk/aggregates/?station=60003760&types={type_ids_str}",
            timeout=10,
        )
        response.raise_for_status()

        data = response.json()

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

    except requests.RequestException as e:
        logger.error(f"Error fetching price data from Fuzzwork: {e}")
        return JsonResponse({"error": "Unable to fetch price data"}, status=503)
    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing price data: {e}")
        return JsonResponse({"error": "Invalid data received"}, status=500)


def health_check(request):
    """
    Simple health check endpoint for monitoring.
    Returns the status of the Indy Hub module.
    """
    from ..models import Blueprint, IndustryJob

    try:
        # Basic database connectivity check.
        # Avoid heavy table counts on every probe: cache briefly.
        cache_ttl_seconds = 60
        blueprint_count = cache.get("indy_hub.health.blueprint_count")
        job_count = cache.get("indy_hub.health.job_count")
        if blueprint_count is None:
            blueprint_count = Blueprint.objects.count()
            cache.set(
                "indy_hub.health.blueprint_count", blueprint_count, cache_ttl_seconds
            )
        if job_count is None:
            job_count = IndustryJob.objects.count()
            cache.set("indy_hub.health.job_count", job_count, cache_ttl_seconds)

        return JsonResponse(
            {
                "status": "healthy",
                "timestamp": timezone.now().isoformat(),
                "data": {"blueprints": blueprint_count, "jobs": job_count},
            }
        )

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JsonResponse({"status": "unhealthy", "error": str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def save_production_config(request):
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


@login_required
def load_production_config(request):
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


def api_info(request):
    """
    API information and documentation endpoint.
    Returns available API endpoints and their descriptions.
    """
    endpoints = {
        "fuzzwork_price": {
            "url": "/api/fuzzwork-price/",
            "method": "GET",
            "parameters": {"type_id": "EVE Online type ID (required)"},
            "description": "Get market prices from Fuzzwork API",
        },
        "health_check": {
            "url": "/api/health/",
            "method": "GET",
            "description": "Health check endpoint",
        },
    }

    return JsonResponse(
        {"api_version": "1.0", "module": "indy_hub", "endpoints": endpoints}
    )
