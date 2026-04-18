# Standard Library
from decimal import ROUND_HALF_UP, Decimal

# Django
from django.db import migrations

_ZERO_AMOUNT = Decimal("0.00")
_UNIT_PRICE_QUANTUM = Decimal("0.01")


def _build_snapshot(order):
    items = list(order.items.all())
    if not items:
        return None

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


def sync_transaction_totals(apps, schema_editor):
    MaterialExchangeTransaction = apps.get_model(
        "indy_hub", "MaterialExchangeTransaction"
    )

    queryset = MaterialExchangeTransaction.objects.select_related(
        "sell_order",
        "buy_order",
    )

    for tx in queryset.iterator():
        order = tx.sell_order or tx.buy_order
        if order is None:
            continue

        snapshot = _build_snapshot(order)
        if not snapshot:
            continue

        update_fields = []
        for field_name, value in snapshot.items():
            if getattr(tx, field_name) != value:
                setattr(tx, field_name, value)
                update_fields.append(field_name)

        if update_fields:
            tx.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("indy_hub", "0093_blueprintcopyoffer_proposal_amount"),
    ]

    operations = [
        migrations.RunPython(
            sync_transaction_totals,
            migrations.RunPython.noop,
        ),
    ]
