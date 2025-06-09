"""
Utility functions for Indy Hub with ESI protection system
"""

# Standard Library
import logging
from threading import Lock

# Django
from django.core.cache import cache

logger = logging.getLogger(__name__)

# ESI Protection System Configuration
ESI_RATE_LIMIT = 100  # requests per second
ESI_CIRCUIT_BREAKER_THRESHOLD = 3  # failures before circuit opens
ESI_CIRCUIT_BREAKER_TIMEOUT = 300  # seconds (5 minutes)
ESI_BACKOFF_INITIAL = 1  # initial backoff in seconds
ESI_BACKOFF_MAX = 60  # maximum backoff in seconds
ESI_BATCH_SIZE_TYPES = 20  # max types to batch at once
ESI_BATCH_SIZE_CHARACTERS = 10  # max characters to batch at once
ESI_BATCH_DELAY_TYPES = 0.2  # seconds between type requests
ESI_BATCH_DELAY_CHARACTERS = 0.3  # seconds between character requests


def get_esi_state():
    """Get default ESI state"""
    return {
        "last_request": 0,
        "request_count": 0,
        "circuit_breaker": {
            "failures": 0,
            "last_failure": 0,
            "is_open": False,
            "opened_at": 0,
        },
        "backoff": {"current": ESI_BACKOFF_INITIAL, "last_backoff": 0},
    }


_esi_lock = Lock()


def reset_esi_circuit_breaker():
    """Reset the ESI circuit breaker manually"""
    pass


def get_esi_protection_status():
    """Get current ESI protection status"""
    _esi_state = get_esi_state()  # Get state instead of using global
    with _esi_lock:
        return {
            "circuit_breaker_open": _esi_state["circuit_breaker"]["is_open"],
            "failures": _esi_state["circuit_breaker"]["failures"],
            "current_backoff": _esi_state["backoff"]["current"],
            "last_request": _esi_state["last_request"],
            "request_count": _esi_state["request_count"],
        }


def batch_cache_type_names(
    type_ids: list[int], max_batch_size: int = None
) -> dict[int, str]:
    """
    Batch cache type names with protection
    """
    if not type_ids:
        return {}

    # Use the implementation from models.py for consistency
    from .models import batch_cache_type_names as model_batch_cache_type_names

    return model_batch_cache_type_names(type_ids)


def batch_cache_character_names(
    character_ids: list[int], max_batch_size: int = None
) -> dict[int, str]:
    """
    Batch cache character names with protection
    """
    if not character_ids:
        return {}

    # Use the implementation from models.py for consistency
    from .models import batch_cache_character_names as model_batch_cache_character_names

    return model_batch_cache_character_names(character_ids)


def get_esi_status():
    """
    Get comprehensive ESI status for monitoring
    """
    # For now, return a simplified status based on protection state
    protection_status = get_esi_protection_status()

    return {
        "type_circuit_breaker": not protection_status["circuit_breaker_open"],
        "character_circuit_breaker": not protection_status["circuit_breaker_open"],
        "type_backoff": protection_status["current_backoff"] > ESI_BACKOFF_INITIAL,
        "character_backoff": protection_status["current_backoff"] > ESI_BACKOFF_INITIAL,
        "type_errors": protection_status["failures"],
        "character_errors": protection_status["failures"],
    }


def clear_esi_cache():
    """
    Clear all ESI-related cache data
    """
    # Clear type name cache
    cache.delete_pattern("type_name_*")
    # Clear character name cache
    cache.delete_pattern("character_name_*")
    # Clear circuit breaker states
    cache.delete_pattern("esi_circuit_breaker_*")
    cache.delete_pattern("esi_error_count_*")
    cache.delete_pattern("esi_backoff_*")

    # Reset protection state
    reset_esi_circuit_breaker()

    logger.info("ESI cache cleared successfully")


# Indy Hub utils entrypoint: only import utils from submodules
from .utils.industry import *  # noqa: E402, F401, F403
from .utils.user import *  # noqa: E402, F401, F403

# ...ajoute ici d'autres imports d'utilitaires par domaine si besoin...
