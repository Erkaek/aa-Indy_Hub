"""Helpers for consistent Material Exchange transaction snapshots."""

# Standard Library
from decimal import ROUND_HALF_UP, Decimal

from ..models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeSellOrder,
    MaterialExchangeTransaction,
)

_ZERO_AMOUNT = Decimal("0.00")
_UNIT_PRICE_QUANTUM = Decimal("0.01")


def build_material_exchange_transaction_snapshot(order) -> dict:
    items = list(order.items.all())
    if not items:
        return {
            "type_id": 0,
            "type_name": "Empty order",
            "quantity": 0,
            "unit_price": _ZERO_AMOUNT,
            "total_price": _ZERO_AMOUNT,
        }

    first_item = items[0]
    total_quantity = sum(int(item.quantity) for item in items)
    total_price = sum((item.total_price for item in items), _ZERO_AMOUNT)

    if len(items) == 1:
        return {
            "type_id": first_item.type_id,
            "type_name": first_item.type_name or str(first_item.type_id),
            "quantity": first_item.quantity,
            "unit_price": first_item.unit_price,
            "total_price": first_item.total_price,
        }

    average_unit_price = _ZERO_AMOUNT
    if total_quantity:
        average_unit_price = (total_price / Decimal(total_quantity)).quantize(
            _UNIT_PRICE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    remaining_count = len(items) - 1
    suffix = "item" if remaining_count == 1 else "items"
    lead_name = first_item.type_name or str(first_item.type_id)

    return {
        "type_id": first_item.type_id,
        "type_name": f"{lead_name} + {remaining_count} more {suffix}",
        "quantity": total_quantity,
        "unit_price": average_unit_price,
        "total_price": total_price,
    }


def upsert_material_exchange_transaction(order):
    defaults = build_material_exchange_transaction_snapshot(order)
    defaults["config"] = order.config

    if isinstance(order, MaterialExchangeSellOrder):
        defaults["transaction_type"] = MaterialExchangeTransaction.TransactionType.SELL
        defaults["user"] = order.seller
        return MaterialExchangeTransaction.objects.update_or_create(
            sell_order=order,
            defaults=defaults,
        )

    if isinstance(order, MaterialExchangeBuyOrder):
        defaults["transaction_type"] = MaterialExchangeTransaction.TransactionType.BUY
        defaults["user"] = order.buyer
        return MaterialExchangeTransaction.objects.update_or_create(
            buy_order=order,
            defaults=defaults,
        )

    raise TypeError(f"Unsupported material exchange order type: {type(order)!r}")
