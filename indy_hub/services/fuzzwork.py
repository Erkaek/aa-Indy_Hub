"""Helpers for Fuzzwork market API."""

from __future__ import annotations

# Standard Library
from decimal import Decimal

# Third Party
import requests

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)

FUZZWORK_TYPES_PER_REQUEST = 100


class FuzzworkError(Exception):
    """Raised when the Fuzzwork API request fails."""


def fetch_fuzzwork_aggregates(
    type_ids: list[int] | list[str],
    *,
    station_id: int = 60003760,
    timeout: int = 10,
) -> dict:
    """Return raw Fuzzwork aggregates payload for given type IDs."""
    if not type_ids:
        return {}

    unique_ids = list(
        dict.fromkeys(str(type_id).strip() for type_id in type_ids if type_id)
    )
    if not unique_ids:
        return {}

    aggregates: dict = {}
    for offset in range(0, len(unique_ids), FUZZWORK_TYPES_PER_REQUEST):
        chunk = unique_ids[offset : offset + FUZZWORK_TYPES_PER_REQUEST]
        type_ids_str = ",".join(chunk)
        url = (
            "https://market.fuzzwork.co.uk/aggregates/"
            f"?station={int(station_id)}&types={type_ids_str}"
        )

        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            chunk_data = response.json()
        except requests.RequestException as exc:
            logger.warning("Fuzzwork request failed: %s", exc)
            raise FuzzworkError(str(exc)) from exc

        if isinstance(chunk_data, dict):
            aggregates.update(chunk_data)

    return aggregates


def parse_fuzzwork_prices(
    data: dict,
    type_ids: list[int] | list[str],
) -> dict[int, dict[str, Decimal]]:
    """Parse Fuzzwork payload into Jita buy/sell prices."""
    prices: dict[int, dict[str, Decimal]] = {}
    for tid in {int(t) for t in type_ids if str(t).strip()}:
        info = data.get(str(tid), {})
        buy_price = Decimal(str(info.get("buy", {}).get("max", 0) or 0))
        sell_price = Decimal(str(info.get("sell", {}).get("min", 0) or 0))
        prices[tid] = {"buy": buy_price, "sell": sell_price}
    return prices


def fetch_fuzzwork_prices(
    type_ids: list[int] | list[str],
    *,
    station_id: int = 60003760,
    timeout: int = 10,
) -> dict[int, dict[str, Decimal]]:
    """Fetch and parse Jita buy/sell prices for given type IDs."""
    data = fetch_fuzzwork_aggregates(
        type_ids,
        station_id=station_id,
        timeout=timeout,
    )
    return parse_fuzzwork_prices(data, type_ids)
