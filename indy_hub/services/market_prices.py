"""Helpers for public market price reference data."""

from __future__ import annotations

# Standard Library
from decimal import Decimal

# Third Party
import requests

# Django
from django.core.cache import cache

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)

MARKET_PRICES_CACHE_KEY = "indy_hub:market_prices:esi:v1"
MARKET_PRICES_CACHE_TTL_SECONDS = 3600


class MarketPriceError(Exception):
    """Raised when the public market prices request fails."""


def fetch_market_price_references(
    *, timeout: int = 15
) -> dict[int, dict[str, Decimal]]:
    """Return public CCP market price references keyed by type id.

    The ESI /markets/prices/ payload includes the CCP adjusted price used for
    industry job cost calculations.
    """
    cached = cache.get(MARKET_PRICES_CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    url = "https://esi.evetech.net/latest/markets/prices/?datasource=tranquility"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("Market prices request failed: %s", exc)
        raise MarketPriceError(str(exc)) from exc

    if not isinstance(payload, list):
        raise MarketPriceError(
            f"Unexpected market prices payload type: {type(payload)}"
        )

    result: dict[int, dict[str, Decimal]] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        try:
            type_id = int(entry.get("type_id") or 0)
        except (TypeError, ValueError):
            continue
        if type_id <= 0:
            continue

        adjusted_price = Decimal(str(entry.get("adjusted_price") or 0))
        average_price = Decimal(str(entry.get("average_price") or 0))
        result[type_id] = {
            "adjusted_price": adjusted_price,
            "average_price": average_price,
        }

    cache.set(MARKET_PRICES_CACHE_KEY, result, MARKET_PRICES_CACHE_TTL_SECONDS)
    return result


def fetch_adjusted_prices(
    type_ids: list[int] | list[str], *, timeout: int = 15
) -> dict[int, dict[str, Decimal]]:
    """Return adjusted/average prices for the requested type ids."""
    references = fetch_market_price_references(timeout=timeout)
    result: dict[int, dict[str, Decimal]] = {}
    for raw_type_id in type_ids:
        try:
            type_id = int(raw_type_id)
        except (TypeError, ValueError):
            continue
        if type_id <= 0:
            continue
        result[type_id] = references.get(
            type_id,
            {
                "adjusted_price": Decimal("0"),
                "average_price": Decimal("0"),
            },
        )
    return result
