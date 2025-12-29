"""Material Exchange views for Indy Hub."""

# Standard Library
import logging
from decimal import Decimal

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

# Local
from ..decorators import indy_hub_permission_required
from ..models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
)
from ..tasks.material_exchange import (
    sync_material_exchange_prices,
    sync_material_exchange_stock,
)
from ..utils.eve import get_type_name

logger = logging.getLogger(__name__)

_PRODUCTION_IDS_CACHE: set[int] | None = None


def _load_production_ids() -> set[int]:
    """Return the cached set of production item type IDs from EveUniverse."""

    global _PRODUCTION_IDS_CACHE

    # Return cached value if already loaded
    if _PRODUCTION_IDS_CACHE is not None:
        return _PRODUCTION_IDS_CACHE

    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveIndustryActivityMaterial

        # Get all unique material_eve_type_id values
        material_ids = (
            EveIndustryActivityMaterial.objects.values_list(
                "material_eve_type_id", flat=True
            )
            .distinct()
            .order_by()
        )
        _PRODUCTION_IDS_CACHE = set(material_ids)
        logger.info(
            f"Loaded {len(_PRODUCTION_IDS_CACHE)} production IDs from EveUniverse"
        )
        return _PRODUCTION_IDS_CACHE
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load production IDs from EveUniverse: %s", exc)
        _PRODUCTION_IDS_CACHE = set()
        return set()


def _get_available_market_groups() -> list[dict]:
    """
    Return list of market groups available for production materials.

    - Filters to EveIndustryActivityMaterial -> EveType -> EveMarketGroup
    - Uses parent market group name when parent exists
    - Groups identical names and merges their IDs
    - Returns list of dicts: {"name": str, "ids": list[int]}
    """
    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import (
            EveIndustryActivityMaterial,
            EveMarketGroup,
        )

        # Get all material type IDs used in production
        material_type_ids = list(
            EveIndustryActivityMaterial.objects.values_list(
                "material_eve_type_id", flat=True
            )
            .distinct()
            .order_by()
        )

        logger.info(f"Found {len(material_type_ids)} production material type IDs")

        # Limit to child groups of the desired parent market groups (e.g. Materials + others)
        TARGET_PARENT_IDS = [533, 1031, 1034, 2395]
        raw_groups = (
            EveMarketGroup.objects.filter(
                eve_types__id__in=material_type_ids,
                parent_market_group_id__in=TARGET_PARENT_IDS,
            )
            .select_related("parent_market_group")
            .exclude(name="")  # Exclude empty names
            .distinct()
            .values(
                "id",
                "name",
                "parent_market_group_id",
                "parent_market_group__name",
            )
        )

        grouped: dict[str, set[int]] = {}
        for mg in raw_groups:
            parent_name = (mg.get("parent_market_group__name") or "").strip()
            base_name = (mg.get("name") or "").strip()
            display_name = parent_name or base_name
            if not display_name:
                continue
            grouped.setdefault(display_name, set()).add(mg["id"])

        result = [
            {"name": name, "ids": sorted(ids)} for name, ids in sorted(grouped.items())
        ]
        logger.info(
            "Returning %s market group display rows from %s raw groups",
            len(result),
            len(raw_groups),
        )
        return result
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load market groups from EveUniverse: %s", exc)
        return []


def _get_group_map(type_ids: list[int]) -> dict[int, str]:
    """Return mapping type_id -> group name using EveUniverse if available."""

    if not type_ids:
        return {}

    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveType

        eve_types = EveType.objects.filter(id__in=type_ids).select_related("eve_group")
        return {
            et.id: (et.eve_group.name if et.eve_group else "Other") for et in eve_types
        }
    except Exception:
        return {}


def _fetch_fuzzwork_prices(type_ids: list[int]) -> dict[int, dict[str, Decimal]]:
    """Batch fetch Jita buy/sell prices from Fuzzwork for given type IDs."""

    if not type_ids:
        return {}

    try:
        # Third Party
        import requests

        jita_station_id = 60003760  # Jita 4-4
        unique_ids = list({int(t) for t in type_ids if t})
        type_ids_str = ",".join(map(str, unique_ids))
        url = f"https://market.fuzzwork.co.uk/aggregates/?station={jita_station_id}&types={type_ids_str}"

        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"material_exchange: failed to fetch fuzzwork prices: {exc}")
        return {}

    prices: dict[int, dict[str, Decimal]] = {}
    for tid in unique_ids:
        info = data.get(str(tid), {})
        buy_price = Decimal(str(info.get("buy", {}).get("max", 0) or 0))
        sell_price = Decimal(str(info.get("sell", {}).get("min", 0) or 0))
        prices[tid] = {"buy": buy_price, "sell": sell_price}

    return prices


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_index(request):
    """
    Material Exchange hub landing page.
    Shows overview, recent transactions, and quick stats.
    """
    try:
        config = MaterialExchangeConfig.objects.filter(is_active=True).first()
    except MaterialExchangeConfig.DoesNotExist:
        config = None

    if not config:
        return render(
            request,
            "indy_hub/material_exchange/not_configured.html",
            {"nav_context": _build_nav_context(request.user)},
        )

    # Stats (based on the user's visible sell items)
    stock_count = 0
    total_stock_value = 0

    try:
        # Django
        from django.db import connection

        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership

        # Alliance Auth (External Libs)
        from eveuniverse.models import EveType

        character_ids = list(
            CharacterOwnership.objects.filter(user=request.user).values_list(
                "character_id", flat=True
            )
        )

        if character_ids:
            placeholders = ",".join(["%s"] * len(character_ids))
            base_id = int(config.structure_id)
            low = base_id * 10
            high = base_id * 10 + 9

            personal_loc_ids: list[int] = []
            with connection.cursor() as cursor:
                try:
                    cursor.execute(
                        """
SELECT DISTINCT e.location_id
FROM corptools_evelocation e
WHERE e.location_id > 0
AND e.system_id = (
    SELECT system_id FROM corptools_evelocation
    WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
    LIMIT 1
)
AND (
    e.location_name = (
        SELECT SUBSTRING_INDEX(location_name, '>', 1)
        FROM corptools_evelocation
        WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
        LIMIT 1
    )
    OR e.location_name LIKE CONCAT(
        (
            SELECT SUBSTRING_INDEX(location_name, '>', 1)
            FROM corptools_evelocation
            WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
            LIMIT 1
        ), %s
    )
)
""",
                        [base_id, base_id, base_id, "%"],
                    )
                    personal_loc_ids = [
                        int(r[0]) for r in cursor.fetchall() if r and r[0]
                    ]
                except Exception:
                    personal_loc_ids = []

                extra_clause = ""
                params = list(character_ids)
                # First bind base/range placeholders, then optional personal location IDs
                base_params = [base_id, low, high]
                if personal_loc_ids:
                    plh = ",".join(["%s"] * len(personal_loc_ids))
                    extra_clause = f" OR location_id IN ({plh})"
                    params.extend(base_params + personal_loc_ids)
                else:
                    params.extend(base_params)

                query = (
                    f"""
SELECT type_id, SUM(quantity) as total_qty
FROM corptools_characterasset
WHERE character_id IN ({placeholders})
AND ((ABS(location_id) = %s OR (ABS(location_id) BETWEEN %s AND %s))"""
                    + extra_clause
                    + """
)
GROUP BY type_id
"""
                )

                cursor.execute(query, params)
                user_assets = {row[0]: row[1] for row in cursor.fetchall()}

            # NOTE: Removed production filter to align with Sell page (allow all items)
            logger.info(
                f"INDEX DEBUG: Found {len(user_assets)} unique items in assets before production filter (filter disabled)"
            )
            logger.info(
                f"INDEX DEBUG: {len(user_assets)} items after production filter (filter disabled)"
            )

            allowed_type_ids = set(
                EveType.objects.filter(
                    eve_market_group__parent_market_group_id__in=[
                        533,
                        1031,
                        1034,
                        2395,
                    ]
                ).values_list("id", flat=True)
            )
            user_assets = {
                tid: qty for tid, qty in user_assets.items() if tid in allowed_type_ids
            }
            logger.info(
                f"INDEX DEBUG: {len(user_assets)} items after market group filter"
            )

            if user_assets:
                logger.info(
                    f"INDEX DEBUG: user_assets keys: {list(user_assets.keys())}"
                )
                price_data = _fetch_fuzzwork_prices(list(user_assets.keys()))
                logger.info(
                    f"INDEX DEBUG: Got prices for {len(price_data)} items from Fuzzwork"
                )
                logger.info(f"INDEX DEBUG: price_data keys: {list(price_data.keys())}")
                visible_items = 0
                total_value = Decimal(0)

                for type_id, user_qty in user_assets.items():
                    fuzz_prices = price_data.get(type_id, {})
                    jita_buy = fuzz_prices.get("buy") or Decimal(0)
                    jita_sell = fuzz_prices.get("sell") or Decimal(0)
                    base = jita_sell if config.sell_markup_base == "sell" else jita_buy
                    logger.info(
                        f"INDEX DEBUG: type_id={type_id} qty={user_qty} jita_buy={jita_buy} jita_sell={jita_sell} base={base} sell_markup_base={config.sell_markup_base}"
                    )
                    if base <= 0:
                        logger.info(
                            f"INDEX DEBUG: SKIPPING type_id {type_id} - no valid price (base={base})"
                        )
                        continue
                    unit_price = base * (
                        1 + (config.sell_markup_percent / Decimal(100))
                    )
                    item_value = unit_price * user_qty
                    logger.info(
                        f"INDEX DEBUG: COUNTING type_id {type_id} qty={user_qty} unit_price={unit_price} item_value={item_value}"
                    )
                    total_value += item_value
                    visible_items += 1

                logger.info(
                    f"INDEX DEBUG: Final visible items count: {visible_items}, total_value: {total_value}"
                )
                stock_count = visible_items
                total_stock_value = total_value
    except Exception:
        # Fall back silently if user assets cannot be loaded
        pass

    pending_sell_orders = config.sell_orders.filter(status="pending").count()
    pending_buy_orders = config.buy_orders.filter(status="pending").count()

    # User's recent orders
    user_sell_orders = request.user.material_sell_orders.filter(config=config).order_by(
        "-created_at"
    )[:5]
    user_buy_orders = request.user.material_buy_orders.filter(config=config).order_by(
        "-created_at"
    )[:5]

    # Recent transactions (last 10)
    recent_transactions = config.transactions.select_related("user").order_by(
        "-completed_at"
    )[:10]

    # Admin section data (if user has permission)
    can_admin = request.user.has_perm("indy_hub.can_manage_material_exchange")
    admin_sell_orders = None
    admin_buy_orders = None
    status_filter = None

    if can_admin:
        status_filter = request.GET.get("status", "pending")
        admin_sell_orders = config.sell_orders.all().order_by("-created_at")
        admin_buy_orders = config.buy_orders.all().order_by("-created_at")
        if status_filter:
            admin_sell_orders = admin_sell_orders.filter(status=status_filter)
            admin_buy_orders = admin_buy_orders.filter(status=status_filter)

    context = {
        "config": config,
        "stock_count": stock_count,
        "total_stock_value": total_stock_value,
        "pending_sell_orders": pending_sell_orders,
        "pending_buy_orders": pending_buy_orders,
        "user_sell_orders": user_sell_orders,
        "user_buy_orders": user_buy_orders,
        "recent_transactions": recent_transactions,
        "can_admin": can_admin,
        "admin_sell_orders": admin_sell_orders,
        "admin_buy_orders": admin_buy_orders,
        "status_filter": status_filter,
        "nav_context": _build_nav_context(request.user),
    }

    return render(request, "indy_hub/material_exchange/index.html", context)


@login_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_sell(request):
    """
    Sell materials TO the hub.
    Member chooses materials + quantities, creates ONE order with multiple items.
    """
    config = get_object_or_404(MaterialExchangeConfig, is_active=True)
    materials_with_qty: list[dict] = []

    if request.method == "POST":
        # Django
        from django.db import connection

        cursor = connection.cursor()

        try:
            # Alliance Auth
            from allianceauth.authentication.models import CharacterOwnership

            character_ids = list(
                CharacterOwnership.objects.filter(user=request.user).values_list(
                    "character_id", flat=True
                )
            )
        except Exception:
            character_ids = []

        if not character_ids:
            messages.error(
                request,
                _(
                    "Aucun personnage associé trouvé. Liez vos personnages dans Alliance Auth."
                ),
            )
            return redirect("indy_hub:material_exchange_sell")

        placeholders = ",".join(["%s"] * len(character_ids))
        base_id = int(config.structure_id)
        low = base_id * 10
        high = base_id * 10 + 9

        personal_loc_ids: list[int] = []
        try:
            cursor.execute(
                """
SELECT DISTINCT e.location_id
FROM corptools_evelocation e
WHERE e.location_id > 0
AND e.system_id = (
SELECT system_id FROM corptools_evelocation
WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
LIMIT 1
)
AND (
e.location_name = (
SELECT SUBSTRING_INDEX(location_name, '>', 1)
FROM corptools_evelocation
WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
LIMIT 1
)
OR e.location_name LIKE CONCAT(
(
SELECT SUBSTRING_INDEX(location_name, '>', 1)
FROM corptools_evelocation
WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
LIMIT 1
), %s
)
)
                """,
                [base_id, base_id, base_id, "%"],
            )
            personal_loc_ids = [int(r[0]) for r in cursor.fetchall() if r and r[0]]
        except Exception:
            personal_loc_ids = []

        extra_clause = ""
        params = list(character_ids)
        # First bind base/range placeholders, then optional personal location IDs
        base_params = [base_id, low, high]
        if personal_loc_ids:
            plh = ",".join(["%s"] * len(personal_loc_ids))
            extra_clause = f" OR location_id IN ({plh})"
            params.extend(base_params + personal_loc_ids)
        else:
            params.extend(base_params)

        query = (
            f"""
SELECT type_id, SUM(quantity) as total_qty
FROM corptools_characterasset
WHERE character_id IN ({placeholders})
AND ((ABS(location_id) = %s OR (ABS(location_id) BETWEEN %s AND %s))"""
            + extra_clause
            + """
)
GROUP BY type_id
"""
        )

        cursor.execute(query, params)
        user_assets = {row[0]: row[1] for row in cursor.fetchall()}

        # Apply market group filter if configured
        # Always apply parent market group filter (Materials hierarchy)
        try:
            # Alliance Auth (External Libs)
            from eveuniverse.models import EveType

            allowed_type_ids = set(
                EveType.objects.filter(
                    eve_market_group__parent_market_group_id__in=[
                        533,
                        1031,
                        1034,
                        2395,
                    ]
                ).values_list("id", flat=True)
            )
            user_assets = {
                tid: qty for tid, qty in user_assets.items() if tid in allowed_type_ids
            }
        except Exception as exc:
            logger.warning("Failed to apply market group filter: %s", exc)

        if not user_assets:
            messages.error(request, _("You have no items to sell at this location."))
            return redirect("indy_hub:material_exchange_sell")

        items_to_create: list[dict] = []
        errors: list[str] = []
        total_payout = 0

        price_data = _fetch_fuzzwork_prices(list(user_assets.keys()))

        for type_id, user_qty in user_assets.items():
            qty_raw = request.POST.get(f"qty_{type_id}")
            if not qty_raw:
                continue
            try:
                qty = int(qty_raw)
                if qty <= 0:
                    continue
            except Exception:
                errors.append(_(f"Invalid quantity for type {type_id}"))
                continue

            if qty > user_qty:
                type_name = get_type_name(type_id)
                errors.append(
                    _(
                        f"Insufficient {type_name} in {config.structure_name}. You have: {user_qty:,}, requested: {qty:,}"
                    )
                )
                continue

            fuzz_prices = price_data.get(type_id, {})
            jita_buy = fuzz_prices.get("buy") or Decimal(0)
            jita_sell = fuzz_prices.get("sell") or Decimal(0)
            base = jita_sell if config.sell_markup_base == "sell" else jita_buy
            if base <= 0:
                type_name = get_type_name(type_id)
                errors.append(_(f"{type_name} has no valid market price."))
                continue

            unit_price = base * (1 + (config.sell_markup_percent / Decimal(100)))
            total_price = unit_price * qty
            total_payout += total_price

            type_name = get_type_name(type_id)
            items_to_create.append(
                {
                    "type_id": type_id,
                    "type_name": type_name,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "total_price": total_price,
                }
            )

        if not items_to_create and not errors:
            messages.error(
                request,
                _("Please enter a quantity greater than 0 for at least one item."),
            )
            return redirect("indy_hub:material_exchange_sell")

        if errors:
            for err in errors:
                messages.error(request, err)

        if items_to_create:
            order = MaterialExchangeSellOrder.objects.create(
                config=config,
                seller=request.user,
                status="pending",
            )
            for item_data in items_to_create:
                MaterialExchangeSellOrderItem.objects.create(order=order, **item_data)

            # Send PM notification with order reference and instructions
            # Alliance Auth
            from allianceauth.authentication.models import CharacterOwnership

            from ..notifications import notify_user

            # Get corporation name
            corp_name = _get_corp_name_for_hub(config.corporation_id)

            # Build items list for notification
            items_list = "\n".join(
                f"• {item.type_name}: {item.quantity:,}x @ {item.unit_price:,.2f} ISK"
                for item in order.items.all()
            )

            notify_user(
                request.user,
                _("✅ Sell Order Created"),
                _(
                    f"Your sell order has been created!\n\n"
                    f"📋 Order Reference: **{order.order_reference}**\n"
                    f"💰 Total Payout: {total_payout:,.2f} ISK\n"
                    f"📦 Items ({len(items_to_create)}):\n{items_list}\n\n"
                    f"**Next Steps:**\n"
                    f"1. Create an Item Exchange contract in-game\n"
                    f"2. Set 'Assigné à': {corp_name}\n"
                    f"3. Add all items listed above\n"
                    f"4. **IMPORTANT: Include '{order.order_reference}' in the contract title/description**\n"
                    f"5. Set location: {config.structure_name} (ID: {config.structure_id})\n"
                    f"6. Set price: {total_payout:,.2f} ISK\n"
                    f"7. Set duration: 4 weeks\n\n"
                    f"The system will automatically verify your contract within 5 minutes.\n"
                    f"View your order status: /indy-hub/material-exchange/my-orders/{order.id}/"
                ),
                level="success",
            )

            messages.success(
                request,
                _(
                    f"Sell order created! Order reference: {order.order_reference}. "
                    f"Check your notifications for contract instructions."
                ),
            )

            # Redirect to order detail page instead of index
            return redirect("indy_hub:sell_order_detail", order_id=order.id)

        return redirect("indy_hub:material_exchange_sell")

    # GET branch
    # Django
    from django.db import connection

    cursor = connection.cursor()

    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership

        character_ids = list(
            CharacterOwnership.objects.filter(user=request.user).values_list(
                "character_id", flat=True
            )
        )
    except Exception:
        character_ids = []

    if character_ids:
        placeholders = ",".join(["%s"] * len(character_ids))
        base_id = int(config.structure_id)
        low = base_id * 10
        high = base_id * 10 + 9

        personal_loc_ids: list[int] = []
        try:
            cursor.execute(
                """
SELECT DISTINCT e.location_id
FROM corptools_evelocation e
WHERE e.location_id > 0
AND e.system_id = (
SELECT system_id FROM corptools_evelocation
WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
LIMIT 1
)
AND (
e.location_name = (
SELECT SUBSTRING_INDEX(location_name, '>', 1)
FROM corptools_evelocation
WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
LIMIT 1
)
OR e.location_name LIKE CONCAT(
(
SELECT SUBSTRING_INDEX(location_name, '>', 1)
FROM corptools_evelocation
WHERE location_id < 0 AND ABS(location_id) DIV 10 = %s
LIMIT 1
), %s
)
)
                """,
                [base_id, base_id, base_id, "%"],
            )
            personal_loc_ids = [int(r[0]) for r in cursor.fetchall() if r and r[0]]
        except Exception:
            personal_loc_ids = []

        extra_clause = ""
        if personal_loc_ids:
            plh = ",".join(["%s"] * len(personal_loc_ids))
            extra_clause = f" OR location_id IN ({plh})"

        query = f"""
    SELECT type_id, SUM(quantity) as total_qty
    FROM corptools_characterasset
    WHERE character_id IN ({placeholders})
    AND (
    (ABS(location_id) = %s OR (ABS(location_id) BETWEEN %s AND %s)){extra_clause}
    )
    GROUP BY type_id
    """

        params = list(character_ids) + [base_id, low, high] + (personal_loc_ids or [])
        cursor.execute(query, params)

        user_assets = {row[0]: row[1] for row in cursor.fetchall()}
        logger.info(
            f"SELL DEBUG: Found {len(user_assets)} unique items in assets before production filter (filter disabled)"
        )

        # Apply market group filter (same as POST + Index) to keep views consistent
        try:
            # Alliance Auth (External Libs)
            from eveuniverse.models import EveType

            allowed_type_ids = set(
                EveType.objects.filter(
                    eve_market_group__parent_market_group_id__in=[
                        533,
                        1031,
                        1034,
                        2395,
                    ]
                ).values_list("id", flat=True)
            )
            user_assets = {
                tid: qty for tid, qty in user_assets.items() if tid in allowed_type_ids
            }
            logger.info(
                f"SELL DEBUG: {len(user_assets)} items after market group filter"
            )
        except Exception as exc:
            logger.warning("Failed to apply market group filter (GET): %s", exc)

        price_data = _fetch_fuzzwork_prices(list(user_assets.keys()))
        logger.info(f"SELL DEBUG: Got prices for {len(price_data)} items from Fuzzwork")

        for type_id, user_qty in user_assets.items():
            fuzz_prices = price_data.get(type_id, {})
            jita_buy = fuzz_prices.get("buy") or Decimal(0)
            jita_sell = fuzz_prices.get("sell") or Decimal(0)
            base = jita_sell if config.sell_markup_base == "sell" else jita_buy
            if base <= 0:
                logger.debug(
                    f"SELL DEBUG: Skipping type_id {type_id} - no valid price (buy={jita_buy}, sell={jita_sell}, base={base})"
                )
                continue

            buy_price = base * (1 + (config.sell_markup_percent / Decimal(100)))
            type_name = get_type_name(type_id)
            materials_with_qty.append(
                {
                    "type_id": type_id,
                    "type_name": type_name,
                    "buy_price_from_member": buy_price,
                    "user_quantity": user_qty,
                }
            )

        logger.info(
            f"SELL DEBUG: Final materials_with_qty count: {len(materials_with_qty)}"
        )
        materials_with_qty.sort(key=lambda x: x["type_name"])

    # Get corporation name
    corporation_name = _get_corp_name_for_hub(config.corporation_id)

    context = {
        "config": config,
        "materials": materials_with_qty,
        "corporation_name": corporation_name,
        "nav_context": _build_nav_context(request.user),
    }

    return render(request, "indy_hub/material_exchange/sell.html", context)


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_buy(request):
    """
    Buy materials FROM the hub.
    Member chooses materials + quantities, creates ONE order with multiple items.
    """
    config = get_object_or_404(MaterialExchangeConfig, is_active=True)

    if request.method == "POST":
        # Get available stock
        stock_items = list(
            config.stock_items.filter(quantity__gt=0, jita_buy_price__gt=0)
        )

        # Apply market group filter if configured
        # Always apply parent market group filter (Materials hierarchy)
        try:
            # Alliance Auth (External Libs)
            from eveuniverse.models import EveType

            allowed_type_ids = set(
                EveType.objects.filter(
                    eve_market_group__parent_market_group_id__in=[
                        533,
                        1031,
                        1034,
                        2395,
                    ]
                ).values_list("id", flat=True)
            )
            stock_items = [
                item for item in stock_items if item.type_id in allowed_type_ids
            ]
        except Exception as exc:
            logger.warning("Failed to apply market group filter: %s", exc)

        group_map = _get_group_map([item.type_id for item in stock_items])
        stock_items.sort(
            key=lambda i: (
                group_map.get(i.type_id, "Other").lower(),
                (i.type_name or "").lower(),
            )
        )
        if not stock_items:
            messages.error(request, _("No stock available."))
            return redirect("indy_hub:material_exchange_buy")

        items_to_create = []
        errors = []
        total_cost = 0

        for stock_item in stock_items:
            type_id = stock_item.type_id
            qty_raw = request.POST.get(f"qty_{type_id}")
            if not qty_raw:
                continue
            try:
                qty = int(qty_raw)
                if qty <= 0:
                    continue
            except Exception:
                errors.append(_(f"Invalid quantity for {stock_item.type_name}"))
                continue

            if stock_item.quantity < qty:
                errors.append(
                    _(
                        f"Insufficient stock for {stock_item.type_name}. Available: {stock_item.quantity:,}, requested: {qty:,}"
                    )
                )
                continue

            unit_price = stock_item.sell_price_to_member
            total_price = unit_price * qty
            total_cost += total_price

            items_to_create.append(
                {
                    "type_id": type_id,
                    "type_name": stock_item.type_name,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "total_price": total_price,
                    "stock_available_at_creation": stock_item.quantity,
                }
            )

        if not items_to_create and not errors:
            messages.error(
                request,
                _("Please enter a quantity greater than 0 for at least one item."),
            )
            return redirect("indy_hub:material_exchange_buy")

        if errors:
            for err in errors:
                messages.error(request, err)

        if items_to_create:
            # Create ONE order with ALL items
            order = MaterialExchangeBuyOrder.objects.create(
                config=config,
                buyer=request.user,
                status="pending",
            )

            # Create items for this order
            for item_data in items_to_create:
                MaterialExchangeBuyOrderItem.objects.create(order=order, **item_data)

            messages.success(
                request,
                _(
                    f"Created buy order #{order.id} with {len(items_to_create)} item(s). Total cost: {total_cost:,.2f} ISK. Awaiting admin approval."
                ),
            )
            return redirect("indy_hub:material_exchange_index")

        return redirect("indy_hub:material_exchange_buy")

    # GET: ensure stock is current if config was just changed or never synced
    try:
        if not config.last_stock_sync or (
            config.updated_at and config.last_stock_sync < config.updated_at
        ):
            sync_material_exchange_stock()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Stock auto-sync failed: %s", exc)

    # GET: ensure prices are populated if stock exists without prices
    base_stock_qs = config.stock_items.filter(quantity__gt=0)
    if (
        base_stock_qs.exists()
        and not base_stock_qs.filter(jita_buy_price__gt=0).exists()
    ):
        try:
            sync_material_exchange_prices()
            config.refresh_from_db()
        except Exception as exc:  # pragma: no cover - defensive
            messages.warning(
                request,
                _(f"Price sync failed automatically: {exc}"),
            )

    # Show available stock (quantity > 0 and price available)
    stock_items = list(config.stock_items.filter(quantity__gt=0, jita_buy_price__gt=0))

    # Apply market group filter if configured
    # Always apply parent market group filter (Materials hierarchy)
    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveType

        allowed_type_ids = set(
            EveType.objects.filter(
                eve_market_group__parent_market_group_id__in=[
                    533,
                    1031,
                    1034,
                    2395,
                ]
            ).values_list("id", flat=True)
        )
        stock_items = [item for item in stock_items if item.type_id in allowed_type_ids]
    except Exception as exc:
        logger.warning("Failed to apply market group filter: %s", exc)

    group_map = _get_group_map([item.type_id for item in stock_items])
    stock_items.sort(
        key=lambda i: (
            group_map.get(i.type_id, "Other").lower(),
            (i.type_name or "").lower(),
        )
    )

    context = {
        "config": config,
        "stock": stock_items,
        "nav_context": _build_nav_context(request.user),
    }

    return render(request, "indy_hub/material_exchange/buy.html", context)


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
@require_http_methods(["POST"])
def material_exchange_sync_stock(request):
    """
    Force an immediate sync of stock from corptools cache.
    Updates MaterialExchangeStock and redirects back.
    """
    try:
        sync_material_exchange_stock()
        config = MaterialExchangeConfig.objects.first()
        messages.success(
            request,
            _(
                f"Stock synced successfully. Last sync: {config.last_stock_sync.strftime('%Y-%m-%d %H:%M:%S') if config.last_stock_sync else 'just now'}"
            ),
        )
    except Exception as e:
        messages.error(request, _(f"Stock sync failed: {str(e)}"))

    # Redirect back to buy page or referrer
    referrer = request.headers.get("referer", "")
    if "material-exchange/buy" in referrer:
        return redirect("indy_hub:material_exchange_buy")
    elif "material-exchange/sell" in referrer:
        return redirect("indy_hub:material_exchange_sell")
    else:
        return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
@require_http_methods(["POST"])
def material_exchange_sync_prices(request):
    """
    Force an immediate sync of Jita prices for current stock items.
    Updates MaterialExchangeStock jita_buy_price/jita_sell_price and redirects back.
    """
    try:
        sync_material_exchange_prices()
        config = MaterialExchangeConfig.objects.first()
        messages.success(
            request,
            _(
                f"Prices synced successfully. Last sync: {config.last_price_sync.strftime('%Y-%m-%d %H:%M:%S') if getattr(config, 'last_price_sync', None) else 'just now'}"
            ),
        )
    except Exception as e:
        messages.error(request, _(f"Price sync failed: {str(e)}"))

    # Redirect back to buy page or referrer
    referrer = request.headers.get("referer", "")
    if "material-exchange/buy" in referrer:
        return redirect("indy_hub:material_exchange_buy")
    elif "material-exchange/sell" in referrer:
        return redirect("indy_hub:material_exchange_sell")
    else:
        return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
def material_exchange_admin(request):
    """
    [DEPRECATED] Admin dashboard for managing orders.
    Functionality moved to material_exchange_index() with can_admin context.

    This view is kept for backwards compatibility but is no longer used.
    Admins see the admin panel directly on the main Material Exchange page.
    """
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
@login_required
@require_http_methods(["POST"])
def material_exchange_approve_sell(request, order_id):
    """Approve a sell order (member → hub)."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="pending")
    order.status = "approved"
    order.approved_by = request.user
    order.approved_at = timezone.now()
    order.save()

    messages.success(
        request,
        _(f"Sell order #{order.id} approved. Awaiting payment verification."),
    )
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_reject_sell(request, order_id):
    """Reject a sell order."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="pending")
    order.status = "rejected"
    order.save()

    messages.warning(request, _(f"Sell order #{order.id} rejected."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_verify_payment_sell(request, order_id):
    """Mark sell order payment as verified (via ESI wallet check or manual)."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="approved")
    journal_ref = request.POST.get("journal_ref", "").strip()

    order.status = "paid"
    order.payment_verified_by = request.user
    order.payment_verified_at = timezone.now()
    if journal_ref:
        order.payment_journal_ref = journal_ref
    order.save()

    messages.success(request, _(f"Payment for sell order #{order.id} verified."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_complete_sell(request, order_id):
    """Mark sell order as completed and create transaction logs for each item."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="paid")

    with transaction.atomic():
        order.status = "completed"
        order.save()

        # Create transaction log for each item and update stock
        for item in order.items.all():
            # Create transaction log
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

            # Update stock (add quantity)
            stock_item, _created = MaterialExchangeStock.objects.get_or_create(
                config=order.config,
                type_id=item.type_id,
                defaults={"type_name": item.type_name},
            )
            stock_item.quantity += item.quantity
            stock_item.save()

    messages.success(
        request, _(f"Sell order #{order.id} completed and transaction logged.")
    )
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_approve_buy(request, order_id):
    """Approve a buy order (hub → member)."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="pending")

    # Re-check stock for all items
    errors = []
    for item in order.items.all():
        try:
            stock_item = order.config.stock_items.get(type_id=item.type_id)
            if stock_item.quantity < item.quantity:
                errors.append(
                    _(
                        f"{item.type_name}: insufficient stock. Available: {stock_item.quantity}, required: {item.quantity}"
                    )
                )
        except MaterialExchangeStock.DoesNotExist:
            errors.append(_(f"{item.type_name}: not in stock."))

    if errors:
        messages.error(request, _("Cannot approve: ") + "; ".join(errors))
        return redirect("indy_hub:material_exchange_index")

    order.status = "approved"
    order.approved_by = request.user
    order.approved_at = timezone.now()
    order.save()

    messages.success(
        request, _(f"Buy order #{order.id} approved. Awaiting delivery confirmation.")
    )
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_reject_buy(request, order_id):
    """Reject a buy order."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="pending")
    order.status = "rejected"
    order.save()

    messages.warning(request, _(f"Buy order #{order.id} rejected."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_mark_delivered_buy(request, order_id):
    """Mark buy order as delivered."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="approved")
    delivery_method = request.POST.get("delivery_method", "contract")

    order.status = "delivered"
    order.delivered_by = request.user
    order.delivered_at = timezone.now()
    order.delivery_method = delivery_method
    order.save()

    messages.success(request, _(f"Buy order #{order.id} marked as delivered."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_complete_buy(request, order_id):
    """Mark buy order as completed and create transaction logs for each item."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="delivered")

    with transaction.atomic():
        order.status = "completed"
        order.save()

        # Create transaction log for each item and update stock
        for item in order.items.all():
            # Create transaction log
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

            # Update stock (subtract quantity)
            try:
                stock_item = order.config.stock_items.get(type_id=item.type_id)
                stock_item.quantity -= item.quantity
                if stock_item.quantity < 0:
                    stock_item.quantity = 0
                stock_item.save()
            except MaterialExchangeStock.DoesNotExist:
                pass

    messages.success(
        request, _(f"Buy order #{order.id} completed and transaction logged.")
    )
    return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_transactions(request):
    """
    Transaction history and finance reporting.
    Shows all completed transactions with filters and monthly aggregates.
    """
    config = get_object_or_404(MaterialExchangeConfig, is_active=True)

    # Filters
    transaction_type = request.GET.get("type", "")  # 'sell', 'buy', or ''
    user_filter = request.GET.get("user", "")

    transactions_qs = config.transactions.select_related("user")

    if transaction_type:
        transactions_qs = transactions_qs.filter(transaction_type=transaction_type)
    if user_filter:
        transactions_qs = transactions_qs.filter(user__username__icontains=user_filter)

    transactions_qs = transactions_qs.order_by("-completed_at")

    # Pagination
    paginator = Paginator(transactions_qs, 50)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Aggregates for current month
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_stats = config.transactions.filter(completed_at__gte=month_start).aggregate(
        total_sell_volume=Sum(
            "total_price", filter=Q(transaction_type="sell"), default=0
        ),
        total_buy_volume=Sum(
            "total_price", filter=Q(transaction_type="buy"), default=0
        ),
        sell_count=Count("id", filter=Q(transaction_type="sell")),
        buy_count=Count("id", filter=Q(transaction_type="buy")),
    )

    context = {
        "config": config,
        "page_obj": page_obj,
        "transaction_type": transaction_type,
        "user_filter": user_filter,
        "month_stats": month_stats,
        "nav_context": _build_nav_context(request.user),
    }

    return render(request, "indy_hub/material_exchange/transactions.html", context)


@login_required
@require_http_methods(["POST"])
def material_exchange_assign_contract(request, order_id):
    """Assign ESI contract ID to a sell or buy order."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order_type = request.POST.get("order_type")  # 'sell' or 'buy'
    contract_id = request.POST.get("contract_id", "").strip()

    if not contract_id or not contract_id.isdigit():
        messages.error(request, _("Invalid contract ID. Must be a number."))
        return redirect("indy_hub:material_exchange_index")

    contract_id_int = int(contract_id)

    try:
        if order_type == "sell":
            order = get_object_or_404(MaterialExchangeSellOrder, id=order_id)
            # Assign contract ID to all items in this order
            order.items.update(
                esi_contract_id=contract_id_int,
                esi_validation_checked_at=None,  # Reset to trigger re-validation
            )
            messages.success(
                request,
                _(
                    f"Contract ID {contract_id_int} assigned to sell order #{order.id}. Validation will run automatically."
                ),
            )
        elif order_type == "buy":
            order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id)
            order.items.update(
                esi_contract_id=contract_id_int,
                esi_validation_checked_at=None,
            )
            messages.success(
                request,
                _(
                    f"Contract ID {contract_id_int} assigned to buy order #{order.id}. Validation will run automatically."
                ),
            )
        else:
            messages.error(request, _("Invalid order type."))

    except Exception as exc:
        logger.error(f"Error assigning contract ID: {exc}", exc_info=True)
        messages.error(request, _(f"Error assigning contract ID: {exc}"))

    return redirect("indy_hub:material_exchange_index")


def _build_nav_context(user):
    """Helper to build navigation context for Material Exchange."""
    return {
        "can_manage": user.has_perm("indy_hub.can_manage_material_exchange"),
    }


def _get_corp_name_for_hub(corporation_id: int) -> str:
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
