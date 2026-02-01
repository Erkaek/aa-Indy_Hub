"""Helpers for consistent Material Exchange pricing.

The goal is to keep pricing logic identical across:
- MaterialExchangeStock computed properties
- Material Exchange buy/sell views (when using live Fuzzwork prices)

Prices are based on Jita buy/sell plus a configurable markup, with an optional
"bounds" mode that clamps prices inside the Jita buy/sell spread.
"""

from __future__ import annotations

# Standard Library
from decimal import Decimal


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def apply_markup_with_jita_bounds(
    *,
    jita_buy: Decimal,
    jita_sell: Decimal,
    base_choice: str,
    percent: Decimal,
    enforce_bounds: bool,
) -> Decimal:
    """Return price after applying markup and optional Jita buy/sell bounds.

    Rules (when enforce_bounds=True):
    - If base is Jita Sell and percent is negative: don't go below Jita Buy.
    - If base is Jita Buy and percent is positive: don't go above Jita Sell.

    This keeps computed prices inside the buy/sell spread.
    """

    jita_buy_d = _to_decimal(jita_buy)
    jita_sell_d = _to_decimal(jita_sell)
    percent_d = _to_decimal(percent)

    base = jita_sell_d if base_choice == "sell" else jita_buy_d
    price = base * (Decimal("1") + (percent_d / Decimal("100")))

    if enforce_bounds:
        if base_choice == "sell" and percent_d < 0 and jita_buy_d:
            price = max(price, jita_buy_d)
        if base_choice == "buy" and percent_d > 0 and jita_sell_d:
            price = min(price, jita_sell_d)

    return price


def compute_sell_price_to_member(
    *, config, jita_buy: Decimal, jita_sell: Decimal
) -> Decimal:
    """Price when member buys FROM hub (uses config.buy_markup_*)."""

    return apply_markup_with_jita_bounds(
        jita_buy=jita_buy,
        jita_sell=jita_sell,
        base_choice=getattr(config, "buy_markup_base", "buy"),
        percent=getattr(config, "buy_markup_percent", Decimal("0")),
        enforce_bounds=bool(getattr(config, "enforce_jita_price_bounds", False)),
    )


def compute_buy_price_from_member(
    *, config, jita_buy: Decimal, jita_sell: Decimal
) -> Decimal:
    """Price when member sells TO hub (uses config.sell_markup_*)."""

    return apply_markup_with_jita_bounds(
        jita_buy=jita_buy,
        jita_sell=jita_sell,
        base_choice=getattr(config, "sell_markup_base", "buy"),
        percent=getattr(config, "sell_markup_percent", Decimal("0")),
        enforce_bounds=bool(getattr(config, "enforce_jita_price_bounds", False)),
    )
