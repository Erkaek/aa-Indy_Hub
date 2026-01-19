"""Backfill Material Exchange transaction logs for completed orders."""

from django.core.management.base import BaseCommand

from indy_hub.models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeSellOrder,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
)


class Command(BaseCommand):
    help = (
        "Backfill Material Exchange transaction logs for completed orders "
        "that do not yet have transactions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--config-id",
            type=int,
            default=None,
            help="Only process orders for the specified MaterialExchangeConfig ID.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many transactions would be created without writing data.",
        )

    def handle(self, *args, **options):
        config_id = options["config_id"]
        dry_run = options["dry_run"]

        sell_qs = MaterialExchangeSellOrder.objects.filter(
            status=MaterialExchangeSellOrder.Status.COMPLETED
        ).prefetch_related("items")
        buy_qs = MaterialExchangeBuyOrder.objects.filter(
            status=MaterialExchangeBuyOrder.Status.COMPLETED
        ).prefetch_related("items")

        if config_id is not None:
            sell_qs = sell_qs.filter(config_id=config_id)
            buy_qs = buy_qs.filter(config_id=config_id)

        created = 0
        skipped = 0

        for order in sell_qs:
            if MaterialExchangeTransaction.objects.filter(sell_order=order).exists():
                skipped += 1
                continue

            for item in order.items.all():
                if dry_run:
                    created += 1
                    continue

                MaterialExchangeTransaction.objects.create(
                    config=order.config,
                    transaction_type="sell",
                    sell_order=order,
                    user=order.seller,
                    type_id=item.type_id,
                    type_name=item.type_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total_price=item.total_price,
                )

                stock_item, _created = MaterialExchangeStock.objects.get_or_create(
                    config=order.config,
                    type_id=item.type_id,
                    defaults={"type_name": item.type_name},
                )
                stock_item.quantity += item.quantity
                stock_item.save()

                created += 1

        for order in buy_qs:
            if MaterialExchangeTransaction.objects.filter(buy_order=order).exists():
                skipped += 1
                continue

            for item in order.items.all():
                if dry_run:
                    created += 1
                    continue

                MaterialExchangeTransaction.objects.create(
                    config=order.config,
                    transaction_type="buy",
                    buy_order=order,
                    user=order.buyer,
                    type_id=item.type_id,
                    type_name=item.type_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total_price=item.total_price,
                )

                try:
                    stock_item = order.config.stock_items.get(type_id=item.type_id)
                    stock_item.quantity = max(
                        stock_item.quantity - item.quantity,
                        0,
                    )
                    stock_item.save()
                except MaterialExchangeStock.DoesNotExist:
                    pass

                created += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {created} transactions would be created; {skipped} orders skipped."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {created} transactions; {skipped} orders already had transactions."
                )
            )
