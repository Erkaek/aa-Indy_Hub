"""Helpers for public market price reference data."""

from __future__ import annotations

# Standard Library
import warnings
from decimal import Decimal, InvalidOperation

# Django
from django.core.cache import cache

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException

# AA Example App
from indy_hub.services.esi_client import (
    ESIClientError,
    ESIUnmodifiedError,
    get_rate_limit_reset_seconds,
    shared_client,
)

logger = get_extension_logger(__name__)

MARKET_PRICES_CACHE_KEY = "indy_hub:market_prices:esi:v1"
MARKET_PRICES_CACHE_TTL_SECONDS = 3600


class MarketPriceError(Exception):
    """Raised when the public market prices request fails."""

    def __init__(self, message, *, status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def fetch_market_price_references(
    *, timeout: int = 15
) -> dict[int, dict[str, Decimal]]:
    """Return public CCP market price references keyed by type id.

    The ESI /markets/prices/ payload includes the CCP adjusted price used for
    industry job cost calculations. ``timeout`` remains a deprecated compatibility
    keyword; the shared native transport's configured timeouts apply on a miss.
    """
    cached = cache.get(MARKET_PRICES_CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    warnings.warn(
        f"market price timeout={timeout!r} is deprecated and does not override "
        "django-esi's shared ESI_REQUESTS_*_TIMEOUT settings",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        payload = shared_client.fetch_market_prices()
    except ESIUnmodifiedError as exc:
        # Another request may have populated the unchanged business cache while
        # this one was in flight. Reuse only data still present under its TTL.
        cached = cache.get(MARKET_PRICES_CACHE_KEY)
        if isinstance(cached, dict):
            return cached
        raise MarketPriceError(str(exc), status_code=304) from exc
    except (ESIBucketLimitException, ESIErrorLimitException) as exc:
        raise MarketPriceError(
            str(exc),
            status_code=429 if isinstance(exc, ESIBucketLimitException) else 420,
            retry_after=get_rate_limit_reset_seconds(exc),
        ) from exc
    except ESIClientError as exc:
        logger.warning("Market prices request failed: %s", exc)
        raise MarketPriceError(
            str(exc), status_code=exc.status_code, retry_after=exc.retry_after
        ) from exc

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

        try:
            adjusted_price = Decimal(str(entry.get("adjusted_price") or 0))
            average_price = Decimal(str(entry.get("average_price") or 0))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise MarketPriceError(f"Invalid market price for type {type_id}") from exc
        if not adjusted_price.is_finite() or not average_price.is_finite():
            raise MarketPriceError(f"Non-finite market price for type {type_id}")
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
