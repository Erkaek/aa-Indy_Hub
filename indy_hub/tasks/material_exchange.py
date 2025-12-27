"""
Material Exchange Celery tasks for stock sync, pricing, and payment verification.
"""

# Standard Library
import logging
from decimal import Decimal

# Third Party
from celery import shared_task

# Django
from django.db import connection, transaction
from django.utils import timezone

# Alliance Auth
from esi.clients import EsiClientProvider

# AA Example App
from indy_hub.models import (
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeStock,
)
from indy_hub.utils.eve import get_type_name

logger = logging.getLogger(__name__)

esi = EsiClientProvider()


@shared_task
def sync_material_exchange_stock():
    """
    Sync material stock from ESI corp assets for configured structure and hangar division.
    Updates MaterialExchangeStock quantities from actual corp inventory.
    """
    try:
        config = MaterialExchangeConfig.objects.first()
        if not config:
            logger.warning("Material Exchange not configured - skipping stock sync")
            return

        # Filter assets for specific structure and hangar division
        # hangar_division maps to flag: CorpSAG1 = division 1, etc.
        hangar_flag_map = {
            1: "CorpSAG1",
            2: "CorpSAG2",
            3: "CorpSAG3",
            4: "CorpSAG4",
            5: "CorpSAG5",
            6: "CorpSAG6",
            7: "CorpSAG7",
        }
        target_flag = hangar_flag_map.get(config.hangar_division)

        stock_updates = {}

        # Read directly from corptools_corpasset via SQL JOIN to find corp by EVE ID
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ca.type_id, COALESCE(SUM(ca.quantity), 0) AS total_qty
                    FROM corptools_corpasset ca
                    INNER JOIN eveonline_evecorporationinfo evc
                        ON ca.corporation_id = evc.id
                    WHERE evc.corporation_id = %s
                        AND ca.location_id = %s
                        AND ca.location_flag = %s
                    GROUP BY ca.type_id
                    """,
                    [
                        config.corporation_id,
                        config.structure_id,
                        target_flag,
                    ],
                )
                rows = cursor.fetchall()
                stock_updates = {int(tid): int(qty or 0) for tid, qty in rows}
                if stock_updates:
                    logger.info(
                        "Loaded %d asset types from corptools_corpasset for structure %s, division %s",
                        len(stock_updates),
                        config.structure_id,
                        config.hangar_division,
                    )
        except Exception as e:
            logger.warning(
                "Failed to fetch corp assets from corptools_corpasset: %s. Will try ESI fallback.",
                e,
            )

        # No ESI fallback: sync strictly from corptools_corpasset
        # If corptools is empty, MaterialExchangeStock reflects that (empty)
        logger.info(
            "Stock sync using corptools_corpasset only: %d asset types for structure %s, division %s",
            len(stock_updates),
            config.structure_id,
            config.hangar_division,
        )

        # Update MaterialExchangeStock with atomic transaction
        with transaction.atomic():
            # Desired set of type_ids based on current corp assets
            desired_ids = {int(tid) for tid in stock_updates.keys()}
            now = timezone.now()

            # Current set of type_ids in MaterialExchangeStock for this config
            existing_stocks = MaterialExchangeStock.objects.filter(
                config=config
            ).values_list("type_id", "quantity")
            current_data = {int(tid): int(qty) for tid, qty in existing_stocks}
            current_ids = set(current_data.keys())

            # Delete items that are no longer present
            to_delete = current_ids - desired_ids
            if to_delete:
                deleted_count, _ = MaterialExchangeStock.objects.filter(
                    config=config, type_id__in=list(to_delete)
                ).delete()
                logger.info(
                    "Deleted %d obsolete stock items for config %s",
                    deleted_count,
                    config.pk,
                )

            # If no assets found, ensure table reflects reality (empty)
            if not desired_ids and current_ids:
                deleted_count, _ = MaterialExchangeStock.objects.filter(
                    config=config
                ).delete()
                logger.info(
                    "Cleared all stock items for config %s (no assets in structure)",
                    config.pk,
                )

            # Separate new vs existing items for bulk operations
            to_create = []
            to_update = []

            for type_id, quantity in stock_updates.items():
                type_id = int(type_id)
                quantity = int(quantity or 0)
                type_name = get_type_name(type_id)

                if type_id not in current_ids:
                    # New item
                    to_create.append(
                        MaterialExchangeStock(
                            config=config,
                            type_id=type_id,
                            type_name=type_name,
                            quantity=quantity,
                            last_stock_sync=now,
                        )
                    )
                else:
                    # Existing item: update only if quantity or type_name changed
                    if quantity != current_data[type_id]:
                        to_update.append(
                            MaterialExchangeStock(
                                config=config,
                                type_id=type_id,
                                type_name=type_name,
                                quantity=quantity,
                                last_stock_sync=now,
                            )
                        )

            # Bulk create new items
            if to_create:
                MaterialExchangeStock.objects.bulk_create(
                    to_create,
                    batch_size=500,
                    ignore_conflicts=False,
                )
                logger.info(
                    "Created %d new stock items for config %s",
                    len(to_create),
                    config.pk,
                )

            # Bulk update existing items
            if to_update:
                MaterialExchangeStock.objects.bulk_update(
                    to_update,
                    fields=["quantity", "type_name", "last_stock_sync"],
                    batch_size=500,
                )
                logger.info(
                    "Updated %d stock items for config %s", len(to_update), config.pk
                )

            logger.debug(
                "Stock sync summary: created=%d, updated=%d, deleted=%d",
                len(to_create),
                len(to_update),
                len(to_delete),
            )

            config.last_stock_sync = now
            config.save(update_fields=["last_stock_sync"])

        logger.info(
            "Material Exchange stock sync completed: %s types updated",
            len(stock_updates),
        )

        # Auto-sync prices after stock updates so buy page has prices
        try:
            sync_material_exchange_prices()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Auto price sync failed after stock sync: %s", exc)

    except Exception as e:
        logger.exception(f"Error syncing material exchange stock: {e}")


@shared_task
def sync_material_exchange_prices():
    """
    Sync Jita buy/sell prices from Fuzzwork API for all stock items.
    Updates MaterialExchangeStock jita_buy_price and jita_sell_price.
    """
    try:
        # Third Party
        import requests

        stock_items = MaterialExchangeStock.objects.filter(quantity__gt=0)
        if not stock_items.exists():
            logger.info("No stock items to sync prices for")
            return

        # Collect all type_ids
        type_ids = list(stock_items.values_list("type_id", flat=True))

        # Fuzzwork API supports batch requests
        # https://market.fuzzwork.co.uk/aggregates/?station=60003760&types=34,35,36
        # Jita 4-4 = station_id 60003760
        jita_station_id = 60003760
        type_ids_str = ",".join(map(str, type_ids))

        url = f"https://market.fuzzwork.co.uk/aggregates/?station={jita_station_id}&types={type_ids_str}"

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            prices_data = response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch prices from Fuzzwork: {e}")
            return

        # Update stock prices
        with transaction.atomic():
            for stock_item in stock_items:
                type_id_str = str(stock_item.type_id)
                if type_id_str in prices_data:
                    price_info = prices_data[type_id_str]

                    # Fuzzwork returns buy/sell prices
                    jita_buy = Decimal(str(price_info.get("buy", {}).get("max", 0)))
                    jita_sell = Decimal(str(price_info.get("sell", {}).get("min", 0)))

                    stock_item.jita_buy_price = jita_buy
                    stock_item.jita_sell_price = jita_sell
                    stock_item.save(update_fields=["jita_buy_price", "jita_sell_price"])

                    logger.debug(
                        f"Price sync: {get_type_name(stock_item.type_id)} "
                        f"buy={jita_buy:,.2f} sell={jita_sell:,.2f}"
                    )

            # Update config timestamp
            config = MaterialExchangeConfig.objects.first()
            if config:
                config.last_price_sync = timezone.now()
                config.save(update_fields=["last_price_sync"])

        logger.info(
            f"Material Exchange prices sync completed: {len(type_ids)} types updated"
        )

    except Exception as e:
        logger.exception(f"Error syncing material exchange prices: {e}")


@shared_task
def verify_pending_sell_payments():
    """
    Verify payments for approved sell orders by checking ESI wallet journal.
    Auto-updates status to 'paid' when payment journal entry is found.
    """
    try:
        config = MaterialExchangeConfig.objects.first()
        if not config:
            logger.warning(
                "Material Exchange not configured - skipping payment verification"
            )
            return

        # Get approved sell orders waiting for payment
        pending_orders = MaterialExchangeSellOrder.objects.filter(
            status="approved",
            payment_journal_ref__isnull=True,
        ).select_related("seller")

        if not pending_orders.exists():
            logger.info("No pending sell orders to verify")
            return

        # Fetch corp wallet journal from ESI
        # Note: Requires corp wallet ESI scope
        try:
            # Get last 30 days of journal entries
            journal_data = esi.client.Wallet.get_corporations_corporation_id_wallets_division_journal(
                corporation_id=config.corporation_id,
                division=config.hangar_division,
            ).results()
        except Exception as e:
            logger.error(f"Failed to fetch wallet journal from ESI: {e}")
            return

        # Build index of journal entries by seller and amount
        # Looking for player_donation or corp_account_withdrawal entries
        verified_count = 0
        for order in pending_orders:
            expected_amount = float(order.total_price)
            seller_id = (
                order.seller.profile.main_character.character_id
                if hasattr(order.seller, "profile")
                else None
            )

            if not seller_id:
                continue

            # Search for matching journal entry
            for entry in journal_data:
                # Check if entry matches: correct seller, amount, and recent
                entry_amount = abs(entry.get("amount", 0))
                entry_first_party = entry.get("first_party_id")
                entry_second_party = entry.get("second_party_id")

                # Match if seller is involved and amount matches (within 1% tolerance for rounding)
                amount_matches = abs(entry_amount - expected_amount) < (
                    expected_amount * 0.01
                )
                seller_matches = seller_id in [entry_first_party, entry_second_party]

                if amount_matches and seller_matches:
                    # Found matching payment!
                    with transaction.atomic():
                        order.payment_journal_ref = str(entry.get("id"))
                        order.status = "paid"
                        order.save(update_fields=["payment_journal_ref", "status"])

                    logger.info(
                        f"Auto-verified payment for sell order #{order.id}: "
                        f"{order.seller.username} {entry_amount:,.0f} ISK"
                    )
                    verified_count += 1
                    break

        if verified_count > 0:
            logger.info(
                f"Material Exchange payment verification: {verified_count} orders verified"
            )

    except Exception as e:
        logger.exception(f"Error verifying material exchange payments: {e}")
