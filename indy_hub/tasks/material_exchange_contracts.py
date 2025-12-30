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
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# AA Example App
# Local
from indy_hub.models import (
    ESIContract,
    ESIContractItem,
    MaterialExchangeBuyOrder,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
)
from indy_hub.notifications import notify_multi, notify_user
from indy_hub.services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESITokenError,
    shared_client,
)

logger = logging.getLogger(__name__)

# Cache for structure names to avoid repeated ESI lookups
_structure_name_cache: dict[int, str] = {}


def _get_structure_name(location_id: int, esi_client) -> str | None:
    """
    Get the name of a structure from ESI, with caching.

    Returns the structure name or None if lookup fails.
    Uses cache to avoid repeated ESI calls for the same structure.
    """
    if location_id in _structure_name_cache:
        return _structure_name_cache[location_id]

    if not esi_client:
        return None

    try:
        structure_info = esi_client.get_structure_info(location_id)
        structure_name = structure_info.get("name")
        if structure_name:
            _structure_name_cache[location_id] = structure_name
            return structure_name
    except Exception as exc:
        logger.debug(
            "Failed to fetch structure name for location %s: %s",
            location_id,
            exc,
        )

    return None


@shared_task
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


@shared_task
def run_material_exchange_cycle():
    """
    End-to-end cycle: sync contracts, validate pending sell orders,
    then check completion of approved orders.
    Intended to be scheduled in Celery Beat to simplify orchestration.
    """
    # Step 1: sync cached contracts
    sync_esi_contracts()

    # Step 2: validate pending sell orders using cached contracts
    validate_material_exchange_sell_orders()

    # Step 3: check completion/payment for approved orders
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
    except (ESIClientError, ESIForbiddenError) as exc:
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


@shared_task
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
        status=MaterialExchangeSellOrder.Status.PENDING,
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
        order.status = MaterialExchangeSellOrder.Status.APPROVED
        order.notes = (
            f"Contract validated: {matching_contract.contract_id} @ "
            f"{matching_contract.price:,.2f} ISK"
        )
        if ref_missing:
            order.notes += f" (title missing {order_ref})"
        order.save(update_fields=["status", "notes", "updated_at"])

        admins = _get_admins_for_config(config)
        notify_multi(
            admins,
            _("Sell Order Approved"),
            _(
                f"{order.seller.username} wants to sell:\n{items_list}\n\n"
                f"Total: {order.total_price:,.2f} ISK\n"
                f"Contract #{matching_contract.contract_id} at {matching_contract.price:,.2f} ISK verified from database."
                + (
                    f"\nContract title missing reference {order_ref}."
                    if ref_missing
                    else ""
                )
            ),
            level="success",
            link=f"/indy_hub/material-exchange/sell-orders/{order.id}/",
        )

        logger.info(
            "Sell order %s approved: contract %s verified",
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


def _matches_sell_order_criteria_db(
    contract, order, config, seller_character_ids, esi_client=None
):
    """
    Check if a database contract matches sell order basic criteria.

    Location matching:
    - First attempts to match by structure name via ESI if available
    - Falls back to ID matching if name lookup fails or ESI unavailable
    """
    # Issuer must be the seller
    if contract.issuer_id not in seller_character_ids:
        return False

    # Assignee must be the corporation (recipient of the contract)
    if contract.assignee_id != config.corporation_id:
        return False

    # Check location: try name matching first (for same structure with multiple IDs)
    location_matches = False

    # Try structure name matching if ESI client available and config has structure_name
    if esi_client and config.structure_name:
        for location_id in [contract.start_location_id, contract.end_location_id]:
            if not location_id:
                continue

            structure_name = _get_structure_name(location_id, esi_client)
            if (
                structure_name
                and structure_name.strip() == config.structure_name.strip()
            ):
                location_matches = True
                logger.debug(
                    "Contract location matched by name: '%s' (ID %s)",
                    structure_name,
                    location_id,
                )
                break

    # Fall back to ID matching if name matching didn't find a match
    if not location_matches:
        if (
            contract.start_location_id == config.structure_id
            or contract.end_location_id == config.structure_id
        ):
            location_matches = True

    if not location_matches:
        return False

    return True


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


@shared_task
def handle_material_exchange_buy_order_created(order_id):
    """
    Send immediate notification to admins when a buy order is created.

    Buy orders don't require contract validation - they're approved by admins
    who then deliver the items via contract or direct trade.
    """
    try:
        order = MaterialExchangeBuyOrder.objects.get(id=order_id)
    except MaterialExchangeBuyOrder.DoesNotExist:
        logger.warning("Buy order %s not found", order_id)
        return

    config = order.config
    admins = _get_admins_for_config(config)

    notify_multi(
        admins,
        _("New Buy Order"),
        _(
            f"{order.buyer.username} wants to buy {order.quantity}x {order.type_name} for {order.total_price:,.2f} ISK.\n"
            f"Stock available at creation: {order.stock_available_at_creation}x\n"
            f"Review and approve to proceed with delivery."
        ),
        level="info",
        link=f"/indy_hub/material-exchange/buy-orders/{order.id}/",
    )

    logger.info("Buy order %s notification sent to admins", order_id)


@shared_task
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
        status=MaterialExchangeSellOrder.Status.APPROVED,
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
    except (ESITokenError, ESIClientError) as exc:
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

        # Check if contract is completed
        if contract.get("status") == "completed":
            order.status = MaterialExchangeSellOrder.Status.PAID
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
                    f"Your sell order for {order.quantity}x {order.type_name} has been verified as completed.\n"
                    f"Payment of {order.total_price:,.2f} ISK will be processed."
                ),
                level="success",
            )

            logger.info(
                "Sell order %s marked as paid: contract %s completed",
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
    """Get users with can_manage_material_exchange permission."""
    # Django
    from django.contrib.auth.models import Permission

    try:
        perm = Permission.objects.get(
            codename="can_manage_material_exchange",
            content_type__app_label="indy_hub",
        )
        return list(User.objects.filter(groups__permissions=perm).distinct())
    except Permission.DoesNotExist:
        # Fallback: get superusers
        return list(User.objects.filter(is_superuser=True))


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
