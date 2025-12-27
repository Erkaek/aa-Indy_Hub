# API views and external services
"""
API views and external service integrations for the Indy Hub module.
These views handle API calls, external data fetching, and service integrations.
"""

# Standard Library
import json
import logging

# Third Party
import requests

# Django
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

# Local
from ..models import (
    BlueprintEfficiency,
    CustomPrice,
    ProductionConfig,
    ProductionSimulation,
)

logger = logging.getLogger(__name__)


@login_required
def fuzzwork_price(request):
    """
    Get item prices from Fuzzwork API.

    This view fetches current market prices for EVE Online items
    from the Fuzzwork Market API service.
    Supports both single type_id and comma-separated multiple type_ids.
    """
    type_id = request.GET.get("type_id")
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
