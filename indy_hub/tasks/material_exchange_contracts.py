"""
Material Exchange contract validation and processing tasks.
Handles ESI contract checking, validation, and PM notifications for sell/buy orders.
"""

# Standard Library
import logging
from decimal import Decimal, InvalidOperation

# Third Party
from celery import shared_task

# Django
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# AA Example App
# Local
from indy_hub.models import (
    CachedStructureName,
    ESIContract,
    ESIContractItem,
    MaterialExchangeBuyOrder,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
)
from indy_hub.notifications import notify_multi, notify_user
from indy_hub.services.asset_cache import resolve_structure_names
from indy_hub.services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    shared_client,
)

logger = logging.getLogger(__name__)

# Cache for structure names to avoid repeated ESI lookups
_structure_name_cache: dict[int, str] = {}


def _get_location_name(
    location_id: int, esi_client=None, *, corporation_id: int | None = None
) -> str | None:
    """Resolve a location name from ESI, with caching and signed/unsigned support."""

    # Handle potential unsigned IDs coming from ESI
    def to_signed(n: int) -> int:
        if n > 9223372036854775807:
            return n - 18446744073709551616
        return n

    def to_unsigned(n: int) -> int:
        if n < 0:
            return n + 18446744073709551616
        return n

    # Try original ID
    name = _get_structure_name(location_id, esi_client, corporation_id=corporation_id)
    if name:
        return name

    # Try variant (signed/unsigned) via ESI
    variant = to_signed(location_id) if location_id > 0 else to_unsigned(location_id)
    if variant != location_id:
        return _get_structure_name(variant, esi_client, corporation_id=corporation_id)

    return None


def _get_structure_name(
    location_id: int, esi_client, *, corporation_id: int | None = None
) -> str | None:
    """
    Get the name of a structure from ESI, with caching.

    Returns the structure name or None if lookup fails.
    Uses cache to avoid repeated ESI calls for the same structure.
    """
    if location_id in _structure_name_cache:
        return _structure_name_cache[location_id]

    # Prefer persistent DB cache first
    try:
        cached = (
            CachedStructureName.objects.filter(structure_id=int(location_id))
            .values_list("name", flat=True)
            .first()
        )
        if cached:
            _structure_name_cache[int(location_id)] = str(cached)
            return str(cached)
    except Exception:
        pass

    # Prefer shared Indy Hub resolver (handles corp structure cache + token selection,
    # and supports managed negative hangar ids when corporation_id is provided).
    try:
        resolved = resolve_structure_names(
            [int(location_id)],
            corporation_id=int(corporation_id) if corporation_id is not None else None,
        ).get(int(location_id))
        if resolved:
            _structure_name_cache[int(location_id)] = str(resolved)
            return str(resolved)
    except Exception:
        pass

    if not esi_client:
        return None

    try:
        get_structure_info = getattr(esi_client, "get_structure_info", None)
        if callable(get_structure_info):
            structure_info = get_structure_info(location_id)
            structure_name = (
                structure_info.get("name") if isinstance(structure_info, dict) else None
            )
            if structure_name:
                _structure_name_cache[int(location_id)] = str(structure_name)
                try:
                    CachedStructureName.objects.update_or_create(
                        structure_id=int(location_id),
                        defaults={
                            "name": str(structure_name),
                            "last_resolved": timezone.now(),
                        },
                    )
                except Exception:
                    pass
                return str(structure_name)
    except Exception as exc:
        logger.debug(
            "Failed to fetch structure name for location %s: %s",
            location_id,
            exc,
        )

    return None


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="500/m",
    time_limit=600,
    soft_time_limit=580,
)
def sync_esi_contracts():
    """
    Fetch corporation contracts from ESI and store/update them in the database.

    This task:
    1. Fetches all active Material Exchange configs
    2. For each config, fetches corporation contracts from ESI
    3. Stores/updates contracts and their items in the database
    4. Removes stale contracts (expired/deleted from ESI)

    Should be run periodically (e.g., every 5-15 minutes).
    """
    configs = MaterialExchangeConfig.objects.filter(is_active=True)

    for config in configs:
        try:
            _sync_contracts_for_corporation(config.corporation_id)
        except Exception as exc:
            logger.error(
                "Failed to sync contracts for corporation %s: %s",
                config.corporation_id,
                exc,
                exc_info=True,
            )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 5},
    rate_limit="300/m",
    time_limit=900,
    soft_time_limit=880,
)
def run_material_exchange_cycle():
    """
    End-to-end cycle: sync contracts, validate pending sell orders,
    validate pending buy orders, then check completion of approved orders.
    Intended to be scheduled in Celery Beat to simplify orchestration.
    """
    # Step 1: sync cached contracts
    sync_esi_contracts()

    # Step 2: validate pending sell orders using cached contracts
    validate_material_exchange_sell_orders()

    # Step 3: validate pending buy orders using cached contracts
    validate_material_exchange_buy_orders()

    # Step 4: check completion/payment for approved orders
    check_completed_material_exchange_contracts()


def _sync_contracts_for_corporation(corporation_id: int):
    """Sync ESI contracts for a single corporation."""
    logger.info("Syncing ESI contracts for corporation %s", corporation_id)

    try:
        # Get character with required scope
        character_id = _get_character_for_scope(
            corporation_id,
            "esi-contracts.read_corporation_contracts.v1",
        )

        # Fetch contracts from ESI
        contracts = shared_client.fetch_corporation_contracts(
            corporation_id=corporation_id,
            character_id=character_id,
        )

        logger.info(
            "Fetched %s contracts from ESI for corporation %s",
            len(contracts),
            corporation_id,
        )

    except ESITokenError as exc:
        logger.warning(
            "Cannot sync contracts for corporation %s - missing ESI scope: %s",
            corporation_id,
            exc,
        )
        return
    except (ESIRateLimitError, ESIClientError, ESIForbiddenError) as exc:
        logger.error(
            "Failed to fetch contracts from ESI for corporation %s: %s",
            corporation_id,
            exc,
            exc_info=True,
        )
        return

    # Track synced contract IDs
    synced_contract_ids = []
    indy_contracts_count = 0

    with transaction.atomic():
        for contract_data in contracts:
            contract_id = contract_data.get("contract_id")
            if not contract_id:
                continue

            # Filter: only process contracts with "INDY" in title
            contract_title = contract_data.get("title", "")
            if "INDY" not in contract_title.upper():
                continue

            indy_contracts_count += 1
            synced_contract_ids.append(contract_id)

            # Create or update contract
            contract, created = ESIContract.objects.update_or_create(
                contract_id=contract_id,
                defaults={
                    "issuer_id": contract_data.get("issuer_id", 0),
                    "issuer_corporation_id": contract_data.get(
                        "issuer_corporation_id", 0
                    ),
                    "assignee_id": contract_data.get("assignee_id", 0),
                    "acceptor_id": contract_data.get("acceptor_id", 0),
                    "contract_type": contract_data.get("type", "unknown"),
                    "status": contract_data.get("status", "unknown"),
                    "title": contract_data.get("title", ""),
                    "start_location_id": contract_data.get("start_location_id"),
                    "end_location_id": contract_data.get("end_location_id"),
                    "price": Decimal(str(contract_data.get("price") or 0)),
                    "reward": Decimal(str(contract_data.get("reward") or 0)),
                    "collateral": Decimal(str(contract_data.get("collateral") or 0)),
                    "date_issued": contract_data.get("date_issued"),
                    "date_expired": contract_data.get("date_expired"),
                    "date_accepted": contract_data.get("date_accepted"),
                    "date_completed": contract_data.get("date_completed"),
                    "corporation_id": corporation_id,
                },
            )

            # Fetch and store contract items for item_exchange contracts
            # Only fetch items for contracts where items are accessible (outstanding/in_progress)
            # Completed/expired contracts return 404 for items endpoint
            contract_status = contract_data.get("status", "")
            if contract_data.get("type") == "item_exchange" and contract_status in [
                "outstanding",
                "in_progress",
            ]:
                try:
                    contract_items = shared_client.fetch_corporation_contract_items(
                        corporation_id=corporation_id,
                        contract_id=contract_id,
                        character_id=character_id,
                    )

                    # Clear existing items and create new ones
                    ESIContractItem.objects.filter(contract=contract).delete()

                    for item_data in contract_items:
                        ESIContractItem.objects.create(
                            contract=contract,
                            record_id=item_data.get("record_id", 0),
                            type_id=item_data.get("type_id", 0),
                            quantity=item_data.get("quantity", 0),
                            is_included=item_data.get("is_included", False),
                            is_singleton=item_data.get("is_singleton", False),
                        )

                    logger.info(
                        "Contract %s: synced %s items",
                        contract_id,
                        len(contract_items),
                    )

                except ESIClientError as exc:
                    # 404 is normal for contracts without items or expired contracts
                    if "404" in str(exc):
                        logger.debug(
                            "Contract %s has no items (404) - skipping items sync",
                            contract_id,
                        )
                    else:
                        logger.warning(
                            "Failed to fetch items for contract %s: %s",
                            contract_id,
                            exc,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch items for contract %s: %s",
                        contract_id,
                        exc,
                    )

        # Remove contracts that are no longer in ESI response
        # Keep contracts from the last 30 days to maintain history
        cutoff_date = timezone.now() - timezone.timedelta(days=30)
        deleted_count, _ = (
            ESIContract.objects.filter(
                corporation_id=corporation_id,
                last_synced__lt=timezone.now() - timezone.timedelta(minutes=20),
                date_issued__gte=cutoff_date,
            )
            .exclude(contract_id__in=synced_contract_ids)
            .delete()
        )

        if deleted_count > 0:
            logger.info(
                "Removed %s stale contracts for corporation %s",
                deleted_count,
                corporation_id,
            )

    logger.info(
        "Successfully synced %s INDY contracts (filtered from %s total) for corporation %s",
        indy_contracts_count,
        len(contracts),
        corporation_id,
    )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="500/m",
    time_limit=600,
    soft_time_limit=580,
)
def validate_material_exchange_sell_orders():
    """
    Validate pending sell orders against cached ESI contracts in the database.

    Workflow:
    1. Find all pending sell orders
    2. Query cached contracts from database
    3. Match contracts to orders by:
        - Contract type = item_exchange
        - Contract issuer = member
        - Contract acceptor = corporation
        - Items match (type_id, quantity)
    4. Update order status & notify users

    Note: Contracts are synced separately by sync_esi_contracts task.
    """
    config = MaterialExchangeConfig.objects.filter(is_active=True).first()
    if not config:
        logger.warning("No active Material Exchange config found")
        return

    pending_orders = MaterialExchangeSellOrder.objects.filter(
        config=config,
        status=MaterialExchangeSellOrder.Status.DRAFT,
    )

    if not pending_orders.exists():
        logger.debug("No pending sell orders to validate")
        return

    # Get contracts from database instead of ESI
    # Filter to item_exchange contracts for this corporation
    contracts = ESIContract.objects.filter(
        corporation_id=config.corporation_id,
        contract_type="item_exchange",
    ).prefetch_related("items")

    if not contracts.exists():
        logger.warning(
            "No cached contracts found for corporation %s. "
            "Run sync_esi_contracts task first.",
            config.corporation_id,
        )
        return

    logger.info(
        "Validating %s pending sell orders against %s cached contracts",
        pending_orders.count(),
        contracts.count(),
    )

    # Create ESI client for structure name lookups
    try:
        esi_client = shared_client
    except Exception:
        esi_client = None
        logger.warning("ESI client not available for structure name lookups")

    # Process each pending order
    for order in pending_orders:
        try:
            _validate_sell_order_from_db(config, order, contracts, esi_client)
        except Exception as exc:
            logger.error(
                "Error validating sell order %s: %s",
                order.id,
                exc,
                exc_info=True,
            )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="500/m",
    time_limit=600,
    soft_time_limit=580,
)
def validate_material_exchange_buy_orders():
    """
    Validate pending buy orders against cached ESI contracts in the database.

    Workflow:
    1. Find all buy orders awaiting validation
    2. Query cached contracts from database
    3. Match contracts to orders by:
        - Contract type = item_exchange
        - Issuer corporation = config.corporation_id
        - Assignee = buyer's character
        - Items and price match
    4. Update order status & notify users

    Note: Contracts are synced separately by sync_esi_contracts task.
    """
    config = MaterialExchangeConfig.objects.filter(is_active=True).first()
    if not config:
        logger.warning("No active Material Exchange config found")
        return

    pending_orders = MaterialExchangeBuyOrder.objects.filter(
        config=config,
        status=MaterialExchangeBuyOrder.Status.AWAITING_VALIDATION,
    )

    if not pending_orders.exists():
        logger.debug("No pending buy orders to validate")
        return

    # Notify buyers of awaiting validation orders on first processing
    for order in pending_orders:
        if not order.notes or "Pending contract" not in order.notes:
            items_str = ", ".join(item.type_name for item in order.items.all())
            notify_user(
                order.buyer,
                _("⏳ Buy Order Awaiting Validation"),
                _(
                    f"Your buy order {order.order_reference} is awaiting validation.\n"
                    f"Items: {items_str}\n"
                    f"Total cost: {order.total_price:,.2f} ISK\n\n"
                    f"The corporation is preparing your contract. Stand by."
                ),
                level="info",
                link=f"/indy_hub/material-exchange/buy-orders/{order.id}/",
            )

    contracts = ESIContract.objects.filter(
        corporation_id=config.corporation_id,
        contract_type="item_exchange",
    ).prefetch_related("items")

    if not contracts.exists():
        logger.warning(
            "No cached contracts found for corporation %s. "
            "Run sync_esi_contracts task first.",
            config.corporation_id,
        )
        return

    logger.info(
        "Validating %s pending buy orders against %s cached contracts",
        pending_orders.count(),
        contracts.count(),
    )

    try:
        esi_client = shared_client
    except Exception:
        esi_client = None
        logger.warning("ESI client not available for structure name lookups")

    for order in pending_orders:
        try:
            _validate_buy_order_from_db(config, order, contracts, esi_client)
        except Exception as exc:
            logger.error(
                "Error validating buy order %s: %s",
                order.id,
                exc,
                exc_info=True,
            )


def _validate_sell_order_from_db(config, order, contracts, esi_client=None):
    """
    Validate a single sell order against cached database contracts.

    Contract matching criteria:
    - type = item_exchange
    - issuer_id = seller's main character
    - assignee_id = config.corporation_id (recipient)
    - start_location_id or end_location_id = structure_id (matched by name if available)
    - items match exactly
    - price matches
    """
    order_ref = f"INDY-{order.id}"

    # Find seller's characters
    seller_character_ids = _get_user_character_ids(order.seller)
    if not seller_character_ids:
        logger.warning(
            "Sell order %s: seller %s has no character", order.id, order.seller
        )
        notify_user(
            order.seller,
            _("Sell Order Error"),
            _("Your sell order cannot be validated: no linked EVE character found."),
            level="warning",
        )
        order.status = MaterialExchangeSellOrder.Status.REJECTED
        order.notes = "Seller has no linked EVE character"
        order.save(update_fields=["status", "notes", "updated_at"])
        return

    items_list = "\n".join(
        f"- {item.type_name}: {item.quantity}x @ {item.unit_price:,.2f} ISK each"
        for item in order.items.all()
    )

    matching_contract = None
    ref_missing = False
    last_price_issue: str | None = None
    last_reason: str | None = None
    contract_with_correct_ref: dict | None = None  # Track contracts with correct title

    for contract in contracts:
        # Track contracts with correct order reference in title (for better diagnostics)
        title = contract.title or ""
        has_correct_ref = order_ref in title

        # Basic criteria
        criteria_match = _matches_sell_order_criteria_db(
            contract, order, config, seller_character_ids, esi_client
        )
        if not criteria_match:
            # Store contract info if it has correct ref but wrong structure
            if has_correct_ref and not contract_with_correct_ref:
                contract_with_correct_ref = {
                    "contract_id": contract.contract_id,
                    "issue": "structure location mismatch",
                    "start_location_id": contract.start_location_id,
                    "end_location_id": contract.end_location_id,
                }
            continue

        # Items check
        if not _contract_items_match_order_db(contract, order):
            last_reason = "items mismatch"
            continue

        # Price check
        price_ok, price_msg = _contract_price_matches_db(contract, order)
        if not price_ok:
            last_price_issue = price_msg
            last_reason = price_msg
            continue

        # Title reference check (optional)
        ref_missing = not has_correct_ref

        matching_contract = contract
        break

    if matching_contract:
        order.status = MaterialExchangeSellOrder.Status.VALIDATED
        order.contract_validated_at = timezone.now()
        order.esi_contract_id = matching_contract.contract_id
        order.notes = (
            f"Contract validated: {matching_contract.contract_id} @ "
            f"{matching_contract.price:,.2f} ISK"
        )
        if ref_missing:
            order.notes += f" (title missing {order_ref})"
        order.save(
            update_fields=[
                "status",
                "esi_contract_id",
                "contract_validated_at",
                "notes",
                "updated_at",
            ]
        )

        notify_user(
            order.seller,
            _("✅ Sell Order Validated"),
            _(
                f"Your sell order {order.order_reference} has been validated!\n"
                f"Contract #{matching_contract.contract_id} for {order.total_price:,.2f} ISK verified.\n\n"
                f"Status: Awaiting corporation to accept the contract.\n"
                f"Once accepted, you will receive payment."
                + (
                    f"\n\nNote: Your contract title is missing the reference {order_ref}."
                    if ref_missing
                    else ""
                )
            ),
            level="success",
            link=f"/indy_hub/material-exchange/sell-orders/{order.id}/",
        )

        admins = _get_admins_for_config(config)
        notify_multi(
            admins,
            _("Sell Order Validated"),
            _(
                f"{order.seller.username} wants to sell:\n{items_list}\n\n"
                f"Total: {order.total_price:,.2f} ISK\n"
                f"Contract #{matching_contract.contract_id} at {matching_contract.price:,.2f} ISK verified from database.\n\n"
                f"Awaiting corporation to accept the contract."
                + (
                    f"\nNote: Contract title missing reference {order_ref}."
                    if ref_missing
                    else ""
                )
            ),
            level="success",
            link=f"/indy_hub/material-exchange/sell-orders/{order.id}/",
        )

        logger.info(
            "Sell order %s validated: contract %s verified",
            order.id,
            matching_contract.contract_id,
        )
    elif contract_with_correct_ref:
        # Contract found with correct title but wrong structure
        order.status = MaterialExchangeSellOrder.Status.REJECTED
        order.notes = (
            f"Contract {contract_with_correct_ref['contract_id']} has the correct title ({order_ref}) "
            f"but wrong location. Expected: {config.structure_name or f'Structure {config.structure_id}'}\n"
            f"Contract is at location {contract_with_correct_ref.get('start_location_id') or contract_with_correct_ref.get('end_location_id')}"
        )
        order.save(update_fields=["status", "notes", "updated_at"])

        notify_user(
            order.seller,
            _("Sell Order Rejected: Wrong Contract Location"),
            _(
                f"Your sell order {order_ref} was rejected.\n\n"
                f"You submitted contract #{contract_with_correct_ref['contract_id']} which has the correct title, "
                f"but it's located at the wrong structure.\n\n"
                f"Required location: {config.structure_name or f'Structure {config.structure_id}'}\n"
                f"Your contract is at location {contract_with_correct_ref.get('start_location_id') or contract_with_correct_ref.get('end_location_id')}\n\n"
                f"Please create a new contract at the correct location."
            ),
            level="danger",
        )

        logger.warning(
            "Sell order %s rejected: contract %s has correct title but wrong structure",
            order.id,
            contract_with_correct_ref["contract_id"],
        )
    else:
        # No contract found - only notify if status is changing or notes have significantly changed
        new_notes = (
            "Waiting for matching contract. Please create an item exchange contract with:\n"
            f"- Title including {order_ref}\n"
            f"- Recipient (assignee): {_get_corp_name(config.corporation_id)}\n"
            f"- Location: {config.structure_name or f'Structure {config.structure_id}'}\n"
            f"- Price: {order.total_price:,.2f} ISK\n"
            f"- Items: {', '.join(item.type_name for item in order.items.all())}"
            + (f"\nLast checked issue: {last_price_issue}" if last_price_issue else "")
        )

        # Only notify on first pending status (when notes change significantly)
        notes_changed = order.notes != new_notes
        order.notes = new_notes
        order.save(update_fields=["notes", "updated_at"])

        if notes_changed or not order.updated_at:
            notify_user(
                order.seller,
                _("Sell Order Pending: waiting for contract"),
                _(
                    f"We didn't find a matching contract yet for your sell order {order_ref}.\n"
                    f"Please submit an item exchange contract matching the above requirements."
                    + (f"\nLatest issue seen: {last_reason}" if last_reason else "")
                ),
                level="warning",
            )

        logger.info("Sell order %s pending: no matching contract yet", order.id)


def _validate_buy_order_from_db(config, order, contracts, esi_client=None):
    """Validate a single buy order against cached database contracts."""

    order_ref = order.order_reference or f"INDY-{order.id}"

    buyer_character_ids = _get_user_character_ids(order.buyer)
    if not buyer_character_ids:
        logger.warning("Buy order %s: buyer %s has no character", order.id, order.buyer)
        notify_user(
            order.buyer,
            _("Buy Order Error"),
            _("Your buy order cannot be validated: no linked EVE character found."),
            level="warning",
        )
        order.status = MaterialExchangeBuyOrder.Status.REJECTED
        order.notes = "Buyer has no linked EVE character"
        order.save(update_fields=["status", "notes", "updated_at"])
        return

    items_list = "\n".join(
        f"- {item.type_name}: {item.quantity}x @ {item.unit_price:,.2f} ISK each"
        for item in order.items.all()
    )

    matching_contract = None
    ref_missing = False
    last_price_issue: str | None = None
    last_reason: str | None = None

    for contract in contracts:
        title = contract.title or ""
        has_correct_ref = order_ref in title

        criteria_match = _matches_buy_order_criteria_db(
            contract, order, config, buyer_character_ids, esi_client
        )
        if not criteria_match:
            continue

        if not _contract_items_match_order_db(contract, order):
            last_reason = "items mismatch"
            continue

        price_ok, price_msg = _contract_price_matches_db(contract, order)
        if not price_ok:
            last_price_issue = price_msg
            last_reason = price_msg
            continue

        ref_missing = not has_correct_ref
        matching_contract = contract
        break

    now = timezone.now()

    if matching_contract:
        order.status = MaterialExchangeBuyOrder.Status.VALIDATED
        order.contract_validated_at = now
        order.esi_contract_id = matching_contract.contract_id
        order.notes = (
            f"Contract validated: {matching_contract.contract_id} @ "
            f"{matching_contract.price:,.2f} ISK"
            + (f" (title missing {order_ref})" if ref_missing else "")
        )
        order.save(
            update_fields=[
                "status",
                "esi_contract_id",
                "contract_validated_at",
                "notes",
                "updated_at",
            ]
        )

        order.items.update(
            esi_contract_id=matching_contract.contract_id,
            esi_contract_validated=True,
            esi_validation_checked_at=now,
        )

        notify_user(
            order.buyer,
            _("Buy Order Ready"),
            _(
                f"Your buy order {order.order_reference} is ready.\n"
                f"Contract #{matching_contract.contract_id} for {order.total_price:,.2f} ISK has been validated.\n\n"
                f"Please accept the in-game contract to receive your items."
            ),
            level="success",
        )

        admins = _get_admins_for_config(config)
        notify_multi(
            admins,
            _("Buy Order Validated"),
            _(
                f"{order.buyer.username} will receive:\n{items_list}\n\n"
                f"Total: {order.total_price:,.2f} ISK\n"
                f"Contract #{matching_contract.contract_id} verified from database."
                + (
                    f"\nNote: Contract title missing reference {order_ref}."
                    if ref_missing
                    else ""
                )
            ),
            level="success",
            link=f"/indy_hub/material-exchange/buy-orders/{order.id}/",
        )

        logger.info(
            "Buy order %s validated: contract %s verified",
            order.id,
            matching_contract.contract_id,
        )
        return

    # No matching contract found yet
    new_notes = "\n".join(
        [
            f"Pending contract for {order_ref}.",
            "Ensure corp issues item exchange contract to buyer",
            f"Price: {order.total_price:,.2f} ISK",
            (last_price_issue or ""),
            (last_reason or ""),
        ]
    ).strip()

    notes_changed = order.notes != new_notes
    order.notes = new_notes
    order.save(update_fields=["notes", "updated_at"])

    if notes_changed or not order.updated_at:
        notify_user(
            order.buyer,
            _("Buy Order Pending: waiting for contract"),
            _(
                f"We didn't find a matching contract yet for your buy order {order_ref}.\n"
                f"The corporation needs to issue an item exchange contract to you."
                + (f"\nLatest issue seen: {last_reason}" if last_reason else "")
            ),
            level="info",
        )

    logger.info("Buy order %s pending: no matching contract yet", order.id)


def _matches_sell_order_criteria_db(
    contract, order, config, seller_character_ids, esi_client=None
):
    """
    Check if a database contract matches sell order basic criteria.

    Location matching:
    - Prefer matching by structure name (handles signed/unsigned ID variants)
    - Fall back to ID matching only if name lookup fails
    """
    # Issuer must be the seller
    if contract.issuer_id not in seller_character_ids:
        return False

    # Assignee must be the corporation (recipient of the contract)
    if contract.assignee_id != config.corporation_id:
        return False

    # Check location by name to handle signed/unsigned variants and service-module IDs
    contract_start_name = _get_location_name(
        contract.start_location_id,
        esi_client,
        corporation_id=int(config.corporation_id),
    )
    contract_end_name = _get_location_name(
        contract.end_location_id,
        esi_client,
        corporation_id=int(config.corporation_id),
    )
    config_location_name = config.structure_name

    # Try name matching first
    if contract_start_name and contract_start_name == config_location_name:
        return True
    if contract_end_name and contract_end_name == config_location_name:
        return True

    # Fall back to ID matching if name lookup failed
    if contract.start_location_id == config.structure_id:
        return True
    if contract.end_location_id == config.structure_id:
        return True

    return False


def _matches_buy_order_criteria_db(
    contract, order, config, buyer_character_ids, esi_client=None
):
    """Check if a database contract matches buy order basic criteria."""

    # Issuer corporation must be the hub corporation
    if contract.issuer_corporation_id != config.corporation_id:
        return False

    # Assignee must be one of the buyer's characters
    if contract.assignee_id not in buyer_character_ids:
        return False

    contract_start_name = _get_location_name(
        contract.start_location_id,
        esi_client,
        corporation_id=int(config.corporation_id),
    )
    contract_end_name = _get_location_name(
        contract.end_location_id,
        esi_client,
        corporation_id=int(config.corporation_id),
    )
    config_location_name = config.structure_name

    if contract_start_name and contract_start_name == config_location_name:
        return True
    if contract_end_name and contract_end_name == config_location_name:
        return True

    if contract.start_location_id == config.structure_id:
        return True
    if contract.end_location_id == config.structure_id:
        return True

    return False


def _contract_items_match_order_db(contract, order):
    """Check if database contract items exactly match the sell order items."""
    # Only validate included items (not requested)
    included_items = contract.items.filter(is_included=True)

    order_items = list(order.items.all())

    if included_items.count() != len(order_items):
        return False

    # Check each order item has a matching contract item
    for order_item in order_items:
        found = included_items.filter(
            type_id=order_item.type_id, quantity=order_item.quantity
        ).exists()
        if not found:
            return False

    return True


def _contract_price_matches_db(contract, order) -> tuple[bool, str]:
    """Validate database contract price against order total."""
    try:
        contract_price = Decimal(str(contract.price)).quantize(Decimal("0.01"))
        expected_price = Decimal(str(order.total_price)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return False, "invalid contract price"

    if contract_price != expected_price:
        return False, (
            f"price {contract_price:,.2f} ISK vs expected {expected_price:,.2f} ISK"
        )

    return True, f"price {contract_price:,.2f} ISK OK"


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    rate_limit="1000/m",
    time_limit=300,
    soft_time_limit=280,
)
def handle_material_exchange_buy_order_created(order_id):
    """
    Send immediate notification to admins when a buy order is created.
    Resilient task with auto-retry and rate limiting.
    """
    try:
        order = (
            MaterialExchangeBuyOrder.objects.select_related("config", "buyer")
            .prefetch_related("items")
            .get(id=order_id)
        )
    except MaterialExchangeBuyOrder.DoesNotExist:
        logger.warning("Buy order %s not found", order_id)
        return

    config = order.config
    admins = _get_admins_for_config(config)

    items = list(order.items.all())
    total_qty = order.total_quantity
    total_price = order.total_price

    preview_lines = []
    for item in items[:5]:
        preview_lines.append(
            f"- {item.type_name or item.type_id}: {item.quantity:,}x @ {item.unit_price:,.2f} ISK"
        )
    if len(items) > 5:
        preview_lines.append(_("…"))

    preview = "\n".join(preview_lines) if preview_lines else _("(no items)")

    notify_multi(
        admins,
        _("New Buy Order"),
        _(
            f"{order.buyer.username} created a buy order {order.order_reference}.\n"
            f"Items: {len(items)} (qty: {total_qty:,})\n"
            f"Total: {total_price:,.2f} ISK\n\n"
            f"Preview:\n{preview}\n\n"
            f"Review and approve to proceed with delivery."
        ),
        level="info",
        link=f"/indy_hub/material-exchange/buy-orders/{order.id}/",
    )

    logger.info("Buy order %s notification sent to admins", order_id)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="500/m",
    time_limit=600,
    soft_time_limit=580,
)
def handle_material_exchange_buy_orders_batch(order_ids):
    """
    Batch notification for multiple buy orders created in close succession.
    Reduces task queue overhead by consolidating 10 orders → 1 task.
    """
    if not order_ids:
        logger.warning("No order IDs provided to batch handler")
        return

    # Fetch all orders in one query with prefetch
    orders = (
        MaterialExchangeBuyOrder.objects.filter(id__in=order_ids)
        .select_related("config", "buyer")
        .prefetch_related("items")
    )

    if not orders.exists():
        logger.warning("No buy orders found for ids: %s", order_ids)
        return

    # Group by config to send consolidated notifications
    orders_by_config = {}
    for order in orders:
        key = order.config.id
        if key not in orders_by_config:
            orders_by_config[key] = []
        orders_by_config[key].append(order)

    # Send batch notification per config
    for config_id, config_orders in orders_by_config.items():
        if not config_orders:
            continue

        config = config_orders[0].config
        admins = _get_admins_for_config(config)

        # Build consolidated message
        total_items = sum(len(list(o.items.all())) for o in config_orders)
        total_qty = sum(o.total_quantity for o in config_orders)
        total_price = sum(o.total_price for o in config_orders)

        preview_lines = []
        preview_lines.append(
            _("Batch of {} new buy orders created:").format(len(config_orders))
        )
        for order in config_orders[:3]:
            items = list(order.items.all())
            preview_lines.append(
                f"  • {order.buyer.username} - {order.order_reference} ({len(items)} items, {order.total_price:,.2f} ISK)"
            )
        if len(config_orders) > 3:
            preview_lines.append(
                _("…and {} more orders").format(len(config_orders) - 3)
            )

        preview = "\n".join(preview_lines) if preview_lines else _("(no items)")

        notify_multi(
            admins,
            _(f"New Buy Orders Batch ({len(config_orders)} orders)"),
            _(
                f"Total items: {total_items} (qty: {total_qty:,})\n"
                f"Total price: {total_price:,.2f} ISK\n\n"
                f"{preview}\n\n"
                f"Review and approve orders to proceed with delivery."
            ),
            level="info",
            link="/indy_hub/material-exchange/buy-orders/",
        )

    logger.info(
        "Batch notification sent for %d buy orders across %d configs",
        len(order_ids),
        len(orders_by_config),
    )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    rate_limit="500/m",
    time_limit=600,
    soft_time_limit=580,
)
def check_completed_material_exchange_contracts():
    """
    Check if corp contracts for approved sell orders have been completed.
    Update order status and notify users when payment is verified.
    """
    config = MaterialExchangeConfig.objects.filter(is_active=True).first()
    if not config:
        return

    approved_orders = MaterialExchangeSellOrder.objects.filter(
        config=config,
        status=MaterialExchangeSellOrder.Status.VALIDATED,
    )

    if not approved_orders.exists():
        return

    try:
        contracts = shared_client.fetch_corporation_contracts(
            corporation_id=config.corporation_id,
            character_id=_get_character_for_scope(
                config.corporation_id,
                "esi-contracts.read_corporation_contracts.v1",
            ),
        )
    except (ESITokenError, ESIRateLimitError, ESIForbiddenError, ESIClientError) as exc:
        logger.error("Failed to check contract status: %s", exc)
        return

    for order in approved_orders:
        # Extract contract ID from notes if present
        contract_id = _extract_contract_id(order.notes)
        if not contract_id:
            continue

        contract = next(
            (c for c in contracts if c["contract_id"] == contract_id),
            None,
        )
        if not contract:
            continue

        # Handle contract status
        contract_status = contract.get("status", "")

        # Contract completed successfully
        if contract_status in ["finished", "finished_issuer", "finished_contractor"]:
            order.status = MaterialExchangeSellOrder.Status.COMPLETED
            order.payment_verified_at = timezone.now()
            order.save(
                update_fields=[
                    "status",
                    "payment_verified_at",
                    "updated_at",
                ]
            )

            notify_user(
                order.seller,
                _("Sell Order Completed"),
                _(
                    f"Your sell order {order.order_reference} has been completed!\n"
                    f"Total payment: {order.total_price:,.2f} ISK\n\n"
                    f"The corporation has accepted the contract. Check your wallet for payment confirmation."
                ),
                level="success",
            )

            logger.info(
                "Sell order %s completed: contract %s accepted (status: %s)",
                order.id,
                contract_id,
                contract_status,
            )

        # Contract cancelled, rejected, failed, expired or deleted
        elif contract_status in [
            "cancelled",
            "rejected",
            "failed",
            "expired",
            "deleted",
        ]:
            order.status = MaterialExchangeSellOrder.Status.CANCELLED
            order.notes = f"Contract {contract_id} was {contract_status} by EVE system"
            order.save(
                update_fields=[
                    "status",
                    "notes",
                    "updated_at",
                ]
            )

            notify_user(
                order.seller,
                _("Sell Order Cancelled"),
                _(
                    f"Your sell order {order.order_reference} was cancelled.\n\n"
                    f"Reason: Contract #{contract_id} status is '{contract_status}'.\n"
                    f"This means the contract was not accepted or expired.\n\n"
                    f"You can create a new sell order if you still want to sell these items."
                ),
                level="warning",
            )

            logger.warning(
                "Sell order %s cancelled: contract %s status is %s",
                order.id,
                contract_id,
                contract_status,
            )

        # Contract reversed (rare case - completed then reversed)
        elif contract_status == "reversed":
            order.status = MaterialExchangeSellOrder.Status.CANCELLED
            order.notes = f"Contract {contract_id} was reversed after completion"
            order.save(
                update_fields=[
                    "status",
                    "notes",
                    "updated_at",
                ]
            )

            notify_user(
                order.seller,
                _("Sell Order Reversed"),
                _(
                    f"Your sell order {order.order_reference} was reversed.\n\n"
                    f"Contract #{contract_id} was reversed by CCP/GM.\n"
                    f"This is very rare and usually means a game bug or GM intervention.\n\n"
                    f"Please contact corporation leadership for clarification."
                ),
                level="danger",
            )

            logger.error(
                "Sell order %s reversed: contract %s was reversed",
                order.id,
                contract_id,
            )

    # Process validated buy orders (corp -> member)
    validated_buy_orders = MaterialExchangeBuyOrder.objects.filter(
        config=config,
        status=MaterialExchangeBuyOrder.Status.VALIDATED,
    )

    if not validated_buy_orders.exists():
        return

    for order in validated_buy_orders:
        contract_id = order.esi_contract_id or _extract_contract_id(order.notes)
        if not contract_id:
            continue

        contract = next(
            (c for c in contracts if c["contract_id"] == contract_id),
            None,
        )
        if not contract:
            continue

        # Handle contract status
        contract_status = contract.get("status", "")

        # Contract completed successfully
        if contract_status in ["finished", "finished_issuer", "finished_contractor"]:
            order.status = MaterialExchangeBuyOrder.Status.COMPLETED
            order.delivered_at = contract.get("date_completed") or timezone.now()
            order.save(
                update_fields=[
                    "status",
                    "delivered_at",
                    "updated_at",
                ]
            )

            notify_user(
                order.buyer,
                _("Buy Order Completed"),
                _(
                    f"Your buy order {order.order_reference} has been completed!\n"
                    f"Total paid: {order.total_price:,.2f} ISK\n\n"
                    f"The contract was accepted. Enjoy your items!"
                ),
                level="success",
            )

            logger.info(
                "Buy order %s completed: contract %s accepted (status: %s)",
                order.id,
                contract_id,
                contract_status,
            )

        # Contract cancelled, rejected, failed, expired or deleted
        elif contract_status in [
            "cancelled",
            "rejected",
            "failed",
            "expired",
            "deleted",
        ]:
            order.status = MaterialExchangeBuyOrder.Status.CANCELLED
            order.notes = f"Contract {contract_id} was {contract_status} by EVE system"
            order.save(
                update_fields=[
                    "status",
                    "notes",
                    "updated_at",
                ]
            )

            notify_user(
                order.buyer,
                _("Buy Order Cancelled"),
                _(
                    f"Your buy order {order.order_reference} was cancelled.\n\n"
                    f"Reason: Contract #{contract_id} status is '{contract_status}'.\n"
                    f"This means the contract expired or was cancelled.\n\n"
                    f"Please contact corporation leadership if you still need these items."
                ),
                level="warning",
            )

            logger.warning(
                "Buy order %s cancelled: contract %s status is %s",
                order.id,
                contract_id,
                contract_status,
            )

        # Contract reversed (rare case - completed then reversed)
        elif contract_status == "reversed":
            order.status = MaterialExchangeBuyOrder.Status.CANCELLED
            order.notes = f"Contract {contract_id} was reversed after completion"
            order.save(
                update_fields=[
                    "status",
                    "notes",
                    "updated_at",
                ]
            )

            notify_user(
                order.buyer,
                _("Buy Order Reversed"),
                _(
                    f"Your buy order {order.order_reference} was reversed.\n\n"
                    f"Contract #{contract_id} was reversed by CCP/GM.\n"
                    f"This is very rare and usually means a game bug or GM intervention.\n\n"
                    f"Please contact corporation leadership for clarification."
                ),
                level="danger",
            )

            logger.error(
                "Buy order %s reversed: contract %s was reversed",
                order.id,
                contract_id,
            )


def _extract_contract_id(notes: str) -> int | None:
    """Extract contract ID from order notes (format: "Contract validated: 12345")"""
    if not notes:
        return None
    try:
        parts = notes.split(":")
        if len(parts) >= 2:
            return int(parts[-1].strip())
    except (IndexError, ValueError):
        pass
    return None


def _get_character_for_scope(corporation_id: int, scope: str) -> int:
    """
    Find a character with the required scope in the corporation.
    Used for authenticated ESI calls.

    Raises:
        ESITokenError: If no character with the scope is found
    """
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter
    from esi.models import Token

    try:
        # Step 1: Get character IDs from the corporation
        character_ids = EveCharacter.objects.filter(
            corporation_id=corporation_id
        ).values_list("character_id", flat=True)

        if not character_ids:
            raise ESITokenError(
                f"No characters found for corporation {corporation_id}. "
                f"At least one corporation member must login to grant ESI scopes."
            )

        # Step 2: Get all tokens for these characters
        # Note: AllianceAuth's Token model does not have a 'character' FK.
        # Avoid select_related("character") to prevent FieldError.
        tokens = Token.objects.filter(character_id__in=character_ids)

        if not tokens.exists():
            raise ESITokenError(
                f"No tokens found for corporation {corporation_id}. "
                f"At least one corporation member must login to grant ESI scopes."
            )

        # Try to find a token with the required scope
        # Token.scopes is a ManyToMany field (Scope model)
        for token in tokens:
            try:
                token_scope_names = list(token.scopes.values_list("name", flat=True))
                if scope in token_scope_names:
                    logger.debug(
                        f"Found token for {scope} via character {token.character_id}"
                    )
                    return token.character_id
            except Exception:
                continue

        # No token with required scope found
        # Build a readable list of available scopes and character names
        try:
            # Alliance Auth
            from allianceauth.eveonline.models import EveCharacter

            name_map = {
                ec.character_id: (ec.character_name or str(ec.character_id))
                for ec in EveCharacter.objects.filter(character_id__in=character_ids)
            }
        except Exception:
            name_map = {}

        available_scopes_list = []
        for token in tokens:
            try:
                scopes_str = ", ".join(token.scopes.values_list("name", flat=True))
            except Exception:
                scopes_str = "unknown"
            char_name = name_map.get(token.character_id, f"char {token.character_id}")
            available_scopes_list.append(f"{char_name}: {scopes_str}")

        raise ESITokenError(
            f"No character in corporation {corporation_id} has scope '{scope}'. "
            f"Available characters and scopes:\n" + "\n".join(available_scopes_list)
        )

    except ESITokenError:
        raise
    except Exception as exc:
        logger.error(
            f"Error checking tokens for corporation {corporation_id}: {exc}",
            exc_info=True,
        )
        raise ESITokenError(
            f"Error checking tokens for corporation {corporation_id}: {exc}"
        )


def _get_user_character_ids(user: User) -> list[int]:
    """Get all character IDs for a user."""
    try:
        # Alliance Auth
        from esi.models import Token

        return list(
            Token.objects.filter(user=user)
            .values_list("character_id", flat=True)
            .distinct()
        )
    except Exception:
        return []


def _get_admins_for_config(config: MaterialExchangeConfig) -> list[User]:
    """
    Get users to notify about material exchange orders.
    Includes: staff (admin panel access), superusers, and users with explicit permission.
    """
    # Django
    from django.contrib.auth.models import Permission

    # Start with staff members and superusers (all have admin panel access)
    admins = list(User.objects.filter(is_staff=True, is_active=True).distinct())

    # Add users with explicit can_manage_material_exchange permission (via groups or user)
    try:
        perm = Permission.objects.get(
            codename="can_manage_material_exchange",
            content_type__app_label="indy_hub",
        )
        perm_users = list(
            User.objects.filter(
                Q(groups__permissions=perm) | Q(user_permissions=perm),
                is_active=True,
            ).distinct()
        )
        admins.extend(perm_users)
    except Permission.DoesNotExist:
        pass

    # Remove duplicates while preserving order
    seen = set()
    unique_admins = []
    for user in admins:
        if user.id not in seen:
            seen.add(user.id)
            unique_admins.append(user)

    return unique_admins


def _get_corp_name(corporation_id: int) -> str:
    """Get corporation name, fallback to ID if not available."""
    try:
        # Alliance Auth
        from allianceauth.eveonline.models import EveCharacter

        char = EveCharacter.objects.filter(corporation_id=corporation_id).first()
        if char:
            return char.corporation_name
    except Exception:
        pass
    return f"Corp {corporation_id}"
