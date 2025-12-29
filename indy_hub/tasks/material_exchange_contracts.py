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
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# AA Example App
# Local
from indy_hub.models import (
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


@shared_task
def validate_material_exchange_sell_orders():
    """
    Check ESI for corporation contracts matching pending sell orders.

    Workflow:
    1. Find all pending sell orders
    2. Fetch corp contracts via ESI
    3. Match contracts to orders by:
        - Contract type = item_exchange
        - Contract issuer = member
        - Contract acceptor = corporation
        - Items match (type_id, quantity)
    4. Update order status & notify users
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

    # Get a character with corp contracts scope
    try:
        contracts = shared_client.fetch_corporation_contracts(
            corporation_id=config.corporation_id,
            character_id=_get_character_for_scope(
                config.corporation_id,
                "esi-contracts.read_corporation_contracts.v1",
            ),
        )
    except ESITokenError as exc:
        logger.error(
            "Failed to validate contracts - missing ESI scope: %s",
            exc,
            exc_info=False,
        )
        # Notify admins about the missing scope
        admins = _get_admins_for_config(config)
        for admin in admins:
            notify_user(
                admin,
                _("⚠️ Material Exchange - Missing ESI Scope"),
                _(
                    "Contract validation cannot run because no corporation member "
                    "has granted the 'read_corporation_contracts' ESI scope.\n\n"
                    f"Error: {exc}\n\n"
                    "Please have a corporation administrator login and grant this scope."
                ),
                level="warning",
            )
        return
    except (ESIClientError, ESIForbiddenError) as exc:
        logger.error(
            "Failed to fetch corp contracts for validation: %s",
            exc,
            exc_info=True,
        )
        return

    # Process each pending order
    for order in pending_orders:
        try:
            _validate_sell_order(config, order, contracts)
        except Exception as exc:
            logger.error(
                "Error validating sell order %s: %s",
                order.id,
                exc,
                exc_info=True,
            )


def _validate_sell_order(config, order, contracts):
    """
    Validate a single sell order against ESI contracts.

    Contract matching criteria:
    - type = item_exchange
    - issuer_id = seller's main character
    - acceptor_id = config.corporation_id
    - start_location_id or end_location_id = structure_id
    """
    order_ref = f"INDY-{order.id}"

    # Find seller's main character (assumed to be their user)
    seller_character_ids = _get_user_character_ids(order.seller)
    if not seller_character_ids:
        logger.warning(
            "Sell order %s: seller %s has no character",
            order.id,
            order.seller,
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

    # Search for matching contract
    matching_contract = None
    last_price_issue: str | None = None
    ref_missing = False
    for contract in contracts:
        if not _matches_sell_order_criteria(
            contract,
            order,
            config,
            seller_character_ids,
        ):
            continue

        # Found potential match - verify items
        try:
            contract_items = shared_client.fetch_corporation_contract_items(
                corporation_id=config.corporation_id,
                contract_id=contract["contract_id"],
                character_id=_get_character_for_scope(
                    config.corporation_id,
                    "esi-contracts.read_corporation_contracts.v1",
                ),
            )
        except (ESITokenError, ESIClientError) as exc:
            logger.warning(
                "Failed to fetch items for contract %s: %s",
                contract["contract_id"],
                exc,
            )
            continue

        # Check if items match
        if _contract_items_match_order(contract_items, order):
            price_ok, price_details = _contract_price_matches(contract, order)
            if not price_ok:
                last_price_issue = (
                    f"Contract {contract['contract_id']}: {price_details}"
                )
                continue

            ref_missing = order_ref.lower() not in (contract.get("title") or "").lower()
            matching_contract = contract
            break

    # Format order items for notification
    items_list = "\n".join(
        f"- {item.type_name}: {item.quantity}x @ {item.unit_price:,.2f} ISK each"
        for item in order.items.all()
    )

    if matching_contract:
        # Contract found and items verified
        order.status = MaterialExchangeSellOrder.Status.APPROVED
        order.notes = (
            f"Contract validated: {matching_contract['contract_id']} @ "
            f"{Decimal(str(matching_contract.get('price', 0) or 0)).quantize(Decimal('0.01')):,.2f} ISK"
        )
        if ref_missing:
            order.notes += f" (title missing {order_ref})"
        order.save(update_fields=["status", "notes", "updated_at"])

        # Notify admins
        admins = _get_admins_for_config(config)
        notify_multi(
            admins,
            _("Sell Order Approved"),
            _(
                f"{order.seller.username} wants to sell:\n{items_list}\n\n"
                f"Total: {order.total_price:,.2f} ISK\n"
                f"Contract #{matching_contract['contract_id']} at {matching_contract.get('price', 0):,.2f} ISK verified via ESI."
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
            matching_contract["contract_id"],
        )
    else:
        # No matching contract found
        order.status = MaterialExchangeSellOrder.Status.REJECTED
        order.notes = "No valid contract found. Please submit an item exchange contract matching your sell order."
        order.save(update_fields=["status", "notes", "updated_at"])

        notify_user(
            order.seller,
            _("Sell Order Contract Mismatch"),
            _(
                f"We could not verify your sell order:\n{items_list}\n\n"
                f"Please create an item exchange contract with the following details:\n"
                f"- Recipient: {_get_corp_name(config.corporation_id)}\n"
                f"- Price: {order.total_price:,.2f} ISK (contract price)\n"
                f"- Title: include {order_ref}\n"
                f"- Items: {', '.join(item.type_name for item in order.items.all())}\n"
                f"- Location: {config.structure_name or f'Structure {config.structure_id}'}"
                + (f"\n\nLast checked: {last_price_issue}" if last_price_issue else "")
            ),
            level="warning",
        )

        logger.info("Sell order %s rejected: no matching contract", order.id)


def _matches_sell_order_criteria(contract, order, config, seller_character_ids):
    """Check if a contract matches sell order basic criteria."""
    # Only item exchange contracts
    if contract.get("type") != "item_exchange":
        return False

    # Issuer must be the seller
    if contract.get("issuer_id") not in seller_character_ids:
        return False

    # Acceptor must be the corporation
    if contract.get("acceptor_id") != config.corporation_id:
        return False

    # Must be at or relate to the structure
    start_loc = contract.get("start_location_id")
    end_loc = contract.get("end_location_id")
    if start_loc != config.structure_id and end_loc != config.structure_id:
        return False

    return True


def _contract_items_match_order(contract_items, order):
    """Check if contract items exactly match the sell order items."""
    # Only validate included items (not requested)
    included = [item for item in contract_items if item.get("is_included", False)]

    order_items = list(order.items.all())

    if len(included) != len(order_items):
        # Number of contract items must match order items
        return False

    # Check each order item has a matching contract item
    for order_item in order_items:
        found = False
        for contract_item in included:
            if (
                contract_item.get("type_id") == order_item.type_id
                and contract_item.get("quantity") == order_item.quantity
            ):
                found = True
                break
        if not found:
            return False

    return True


def _contract_price_matches(contract: dict, order) -> tuple[bool, str]:
    """Validate contract price against order total."""
    try:
        contract_price = Decimal(str(contract.get("price") or 0)).quantize(
            Decimal("0.01")
        )
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
        tokens = Token.objects.filter(character_id__in=character_ids).select_related(
            "character"
        )

        if not tokens.exists():
            raise ESITokenError(
                f"No tokens found for corporation {corporation_id}. "
                f"At least one corporation member must login to grant ESI scopes."
            )

        # Try to find a token with the required scope
        for token in tokens:
            if token.has_scopes([scope]):
                logger.debug(
                    f"Found token for {scope} via character {token.character_id}"
                )
                return token.character_id

        # No token with required scope found
        available_scopes_list = [
            f"{token.character.name} (char {token.character_id}): {token.get_scopes()}"
            for token in tokens
        ]
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
