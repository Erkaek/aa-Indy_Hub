# API views and external services
"""
API views and external service integrations for the Indy Hub module.
These views handle API calls, external data fetching, and service integrations.
"""

import logging
import requests
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

logger = logging.getLogger(__name__)


@login_required
def fuzzwork_price(request):
    """
    Get item prices from Fuzzwork API.
    
    This view fetches current market prices for EVE Online items
    from the Fuzzwork Market API service.
    """
    type_id = request.GET.get("type_id")
    if not type_id:
        return JsonResponse({"error": "type_id parameter required"}, status=400)

    try:
        # Fetch price data from Fuzzwork API
        response = requests.get(
            f"https://market.fuzzwork.co.uk/aggregates/?station=60003760&types={type_id}",
            timeout=5
        )
        response.raise_for_status()
        
        data = response.json()
        
        if type_id not in data:
            return JsonResponse({"error": "Item not found"}, status=404)
            
        item_data = data[type_id]
        
        return JsonResponse({
            "type_id": int(type_id),
            "buy": {
                "max": float(item_data.get("buy", {}).get("max", 0)),
                "min": float(item_data.get("buy", {}).get("min", 0)),
                "avg": float(item_data.get("buy", {}).get("avg", 0)),
                "volume": int(item_data.get("buy", {}).get("volume", 0))
            },
            "sell": {
                "max": float(item_data.get("sell", {}).get("max", 0)),
                "min": float(item_data.get("sell", {}).get("min", 0)), 
                "avg": float(item_data.get("sell", {}).get("avg", 0)),
                "volume": int(item_data.get("sell", {}).get("volume", 0))
            }
        })
        
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
        # Basic database connectivity check
        blueprint_count = Blueprint.objects.count()
        job_count = IndustryJob.objects.count()
        
        return JsonResponse({
            "status": "healthy",
            "timestamp": "2024-01-01T00:00:00Z",  # Would use timezone.now() in real implementation
            "data": {
                "blueprints": blueprint_count,
                "jobs": job_count
            }
        })
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JsonResponse({
            "status": "unhealthy", 
            "error": str(e)
        }, status=500)


def api_info(request):
    """
    API information and documentation endpoint.
    Returns available API endpoints and their descriptions.
    """
    endpoints = {
        "fuzzwork_price": {
            "url": "/api/fuzzwork-price/",
            "method": "GET",
            "parameters": {
                "type_id": "EVE Online type ID (required)"
            },
            "description": "Get market prices from Fuzzwork API"
        },
        "health_check": {
            "url": "/api/health/",
            "method": "GET", 
            "description": "Health check endpoint"
        }
    }
    
    return JsonResponse({
        "api_version": "1.0",
        "module": "indy_hub",
        "endpoints": endpoints
    })
