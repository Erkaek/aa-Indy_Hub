"""Utility helpers for production simulation aggregates and formatting."""

from __future__ import annotations

# Standard Library
from decimal import Decimal

# Django
from django.db.models import QuerySet, Sum

from ..models import ProductionSimulation

SimulationStats = dict[str, object]


def summarize_simulations(
    simulations: QuerySet[ProductionSimulation],
) -> tuple[int, SimulationStats]:
    """Return aggregate statistics for a simulation queryset.

    The queryset is not evaluated until this function executes, so callers can
    apply additional filtering / ordering before passing it in.
    """

    total_simulations = simulations.count()

    stats_raw = simulations.aggregate(
        total_runs=Sum("runs"),
        total_items=Sum("total_items"),
        total_buy_items=Sum("total_buy_items"),
        total_prod_items=Sum("total_prod_items"),
        total_cost=Sum("estimated_cost"),
        total_revenue=Sum("estimated_revenue"),
        total_profit=Sum("estimated_profit"),
    )

    defaults: dict[str, Decimal | int] = {
        "total_runs": 0,
        "total_items": 0,
        "total_buy_items": 0,
        "total_prod_items": 0,
        "total_cost": Decimal("0"),
        "total_revenue": Decimal("0"),
        "total_profit": Decimal("0"),
    }

    stats: SimulationStats = {}
    for key, default in defaults.items():
        value = stats_raw.get(key)
        stats[key] = value if value is not None else default

    total_profit = stats.get("total_profit", Decimal("0"))
    if isinstance(total_profit, int):  # Guard against integer coercion
        total_profit = Decimal(total_profit)

    stats["average_profit"] = (
        (total_profit / total_simulations)
        if total_simulations and total_profit
        else Decimal("0")
    )

    latest_update = (
        simulations.order_by("-updated_at").values_list("updated_at", flat=True).first()
        if total_simulations
        else None
    )
    stats["latest_update"] = latest_update

    return total_simulations, stats
