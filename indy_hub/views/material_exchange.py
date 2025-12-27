"""Material Exchange views for Indy Hub."""

# Standard Library
from decimal import Decimal

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
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
    sync_material_exchange_stock,
    sync_material_exchange_prices,
)
from ..utils.eve import get_type_name


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

    # Stats
    stock_count = config.stock_items.count()
    total_stock_value = (
        config.stock_items.aggregate(
            total=Sum("jita_buy_price")
        )["total"]
        or 0
    )

    pending_sell_orders = config.sell_orders.filter(status="pending").count()
    pending_buy_orders = config.buy_orders.filter(status="pending").count()

    # User's recent orders
    user_sell_orders = request.user.material_sell_orders.filter(
        config=config
    ).order_by("-created_at")[:5]
    user_buy_orders = request.user.material_buy_orders.filter(
        config=config
    ).order_by("-created_at")[:5]

    # Recent transactions (last 10)
    recent_transactions = config.transactions.select_related("user").order_by(
        "-completed_at"
    )[:10]

    context = {
        "config": config,
        "stock_count": stock_count,
        "total_stock_value": total_stock_value,
        "pending_sell_orders": pending_sell_orders,
        "pending_buy_orders": pending_buy_orders,
        "user_sell_orders": user_sell_orders,
        "user_buy_orders": user_buy_orders,
        "recent_transactions": recent_transactions,
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

    if request.method == "POST":
        # Get user's character assets at the config location
        from django.db import connection
        cursor = connection.cursor()
        
        # Get user's main character ID
        character_id = None
        if hasattr(request.user, 'profile') and hasattr(request.user.profile, 'main_character'):
            character_id = request.user.profile.main_character.character_id if request.user.profile.main_character else None
        
        if not character_id:
            messages.error(request, _("No main character found. Please set your main character."))
            return redirect("indy_hub:material_exchange_sell")
        
        # Get user's assets at config location
        cursor.execute("""
            SELECT type_id, SUM(quantity) as total_qty
            FROM corptools_characterasset
            WHERE character_id = %s AND location_id = %s
            GROUP BY type_id
        """, [character_id, config.structure_id])
        
        user_assets = {row[0]: row[1] for row in cursor.fetchall()}
        
        if not user_assets:
            messages.error(request, _("You have no items to sell at this location."))
            return redirect("indy_hub:material_exchange_sell")

        items_to_create = []
        errors = []
        total_payout = 0

        # Process each item user wants to sell
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

            # Check user has enough
            if qty > user_qty:
                type_name = get_type_name(type_id)
                errors.append(
                    _(f"Insufficient {type_name} in {config.structure_name}. You have: {user_qty:,}, requested: {qty:,}")
                )
                continue

            # Get pricing from hub for this item
            stock_item = MaterialExchangeStock.objects.filter(
                config=config,
                type_id=type_id
            ).first()
            
            if not stock_item or not stock_item.jita_buy_price:
                type_name = get_type_name(type_id)
                errors.append(_(f"{type_name} is not accepted by the hub for selling."))
                continue

            type_name = stock_item.type_name or get_type_name(type_id)
            unit_price = stock_item.buy_price_from_member
            total_price = unit_price * qty
            total_payout += total_price

            items_to_create.append({
                'type_id': type_id,
                'type_name': type_name,
                'quantity': qty,
                'unit_price': unit_price,
                'total_price': total_price,
            })

        if not items_to_create and not errors:
            messages.error(request, _("Please enter a quantity greater than 0 for at least one item."))
            return redirect("indy_hub:material_exchange_sell")

        if errors:
            for err in errors:
                messages.error(request, err)

        if items_to_create:
            # Create ONE order with ALL items
            order = MaterialExchangeSellOrder.objects.create(
                config=config,
                seller=request.user,
                status="pending",
            )
            
            # Create items for this order
            for item_data in items_to_create:
                MaterialExchangeSellOrderItem.objects.create(
                    order=order,
                    **item_data
                )
            
            messages.success(
                request,
                _(f"Created sell order #{order.id} with {len(items_to_create)} item(s). Total payout: {total_payout:,.2f} ISK. Awaiting admin approval."),
            )
            return redirect("indy_hub:material_exchange_index")

        return redirect("indy_hub:material_exchange_sell")

    # GET: Get user's assets at this location and match with hub pricing
    from django.db import connection
    cursor = connection.cursor()
    
    # Get user's main character ID
    character_id = None
    if hasattr(request.user, 'profile') and hasattr(request.user.profile, 'main_character'):
        character_id = request.user.profile.main_character.character_id if request.user.profile.main_character else None
    
    materials_with_qty = []
    
    if character_id:
        # Get ALL user's assets at this location (not just ones in the hub)
        cursor.execute("""
            SELECT type_id, SUM(quantity) as total_qty
            FROM corptools_characterasset
            WHERE character_id = %s AND location_id = %s
            GROUP BY type_id
        """, [character_id, config.structure_id])
        
        user_assets = {row[0]: row[1] for row in cursor.fetchall()}
        
        # For each user asset, try to get pricing from the hub
        # If not in hub, still show it (user can sell to corp even if not pre-priced)
        for type_id, user_qty in user_assets.items():
            # Try to get pricing from MaterialExchangeStock
            try:
                stock_item = MaterialExchangeStock.objects.filter(
                    config=config,
                    type_id=type_id
                ).first()
                
                if stock_item and stock_item.jita_buy_price:
                    # Get type name from stock item or fetch it
                    type_name = stock_item.type_name or get_type_name(type_id)
                    buy_price = stock_item.buy_price_from_member
                else:
                    # Item not in hub inventory, but user still might want to sell it
                    # Fetch type name and skip (no price available)
                    type_name = get_type_name(type_id)
                    continue  # Skip items without pricing
                
                materials_with_qty.append({
                    'type_id': type_id,
                    'type_name': type_name,
                    'buy_price_from_member': buy_price,
                    'user_quantity': user_qty,
                })
            except Exception:
                # Skip items we can't get pricing for
                continue
        
        # Sort by type name
        materials_with_qty.sort(key=lambda x: x['type_name'])

    context = {
        "config": config,
        "materials": materials_with_qty,
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

            items_to_create.append({
                'type_id': type_id,
                'type_name': stock_item.type_name,
                'quantity': qty,
                'unit_price': unit_price,
                'total_price': total_price,
                'stock_available_at_creation': stock_item.quantity,
            })

        if not items_to_create and not errors:
            messages.error(request, _("Please enter a quantity greater than 0 for at least one item."))
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
                MaterialExchangeBuyOrderItem.objects.create(
                    order=order,
                    **item_data
                )
            
            messages.success(
                request,
                _(f"Created buy order #{order.id} with {len(items_to_create)} item(s). Total cost: {total_cost:,.2f} ISK. Awaiting admin approval."),
            )
            return redirect("indy_hub:material_exchange_index")

        return redirect("indy_hub:material_exchange_buy")

    # GET: ensure stock is current if config was just changed or never synced
    try:
        if not config.last_stock_sync or (config.updated_at and config.last_stock_sync < config.updated_at):
            sync_material_exchange_stock()
            config.refresh_from_db()
    except Exception:
        # Non-blocking: continue rendering even if auto sync fails
        pass

    # GET: ensure prices are populated if stock exists without prices
    base_stock_qs = config.stock_items.filter(quantity__gt=0)
    if base_stock_qs.exists() and not base_stock_qs.filter(jita_buy_price__gt=0).exists():
        try:
            sync_material_exchange_prices()
            config.refresh_from_db()
        except Exception as exc:  # pragma: no cover - defensive
            messages.warning(
                request,
                _(f"Price sync failed automatically: {exc}"),
            )

    # Show available stock (quantity > 0 and price available)
    stock = config.stock_items.filter(
        quantity__gt=0, jita_buy_price__gt=0
    ).order_by("type_name")

    context = {
        "config": config,
        "stock": stock,
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
            _(f"Stock synced successfully. Last sync: {config.last_stock_sync.strftime('%Y-%m-%d %H:%M:%S') if config.last_stock_sync else 'just now'}"),
        )
    except Exception as e:
        messages.error(request, _(f"Stock sync failed: {str(e)}"))
    
    # Redirect back to buy page or referrer
    referrer = request.META.get('HTTP_REFERER', '')
    if 'material-exchange/buy' in referrer:
        return redirect("indy_hub:material_exchange_buy")
    elif 'material-exchange/sell' in referrer:
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
            _(f"Prices synced successfully. Last sync: {config.last_price_sync.strftime('%Y-%m-%d %H:%M:%S') if getattr(config, 'last_price_sync', None) else 'just now'}"),
        )
    except Exception as e:
        messages.error(request, _(f"Price sync failed: {str(e)}"))
    
    # Redirect back to buy page or referrer
    referrer = request.META.get('HTTP_REFERER', '')
    if 'material-exchange/buy' in referrer:
        return redirect("indy_hub:material_exchange_buy")
    elif 'material-exchange/sell' in referrer:
        return redirect("indy_hub:material_exchange_sell")
    else:
        return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
def material_exchange_admin(request):
    """
    Admin dashboard for managing orders.
    Approve/reject sell and buy orders, verify payments, mark delivered.
    """
    config = get_object_or_404(MaterialExchangeConfig, is_active=True)

    # Filters
    order_type = request.GET.get("type", "")  # '' = show all, 'sell' or 'buy'
    status_filter = request.GET.get("status", "pending")

    # Get sell orders with status filter
    sell_orders = config.sell_orders.all().order_by("-created_at")
    if status_filter:
        sell_orders = sell_orders.filter(status=status_filter)
    
    # Get buy orders with status filter
    buy_orders = config.buy_orders.all().order_by("-created_at")
    if status_filter:
        buy_orders = buy_orders.filter(status=status_filter)

    context = {
        "config": config,
        "order_type": order_type,
        "status_filter": status_filter,
        "sell_orders": sell_orders,
        "buy_orders": buy_orders,
        "nav_context": _build_nav_context(request.user),
    }

    return render(request, "indy_hub/material_exchange/admin.html", context)


@login_required
@require_http_methods(["POST"])
@login_required
@require_http_methods(["POST"])
def material_exchange_approve_sell(request, order_id):
    """Approve a sell order (member → hub)."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="pending")
    order.status = "approved"
    order.approved_by = request.user
    order.approved_at = timezone.now()
    order.save()

    messages.success(
        request,
        _(f"Sell order #{order.id} approved. Awaiting payment verification."),
    )
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_reject_sell(request, order_id):
    """Reject a sell order."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="pending")
    order.status = "rejected"
    order.save()

    messages.warning(request, _(f"Sell order #{order.id} rejected."))
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_verify_payment_sell(request, order_id):
    """Mark sell order payment as verified (via ESI wallet check or manual)."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

    order = get_object_or_404(MaterialExchangeSellOrder, id=order_id, status="approved")
    journal_ref = request.POST.get("journal_ref", "").strip()

    order.status = "paid"
    order.payment_verified_by = request.user
    order.payment_verified_at = timezone.now()
    if journal_ref:
        order.payment_journal_ref = journal_ref
    order.save()

    messages.success(request, _(f"Payment for sell order #{order.id} verified."))
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_complete_sell(request, order_id):
    """Mark sell order as completed and create transaction logs for each item."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

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
            stock_item, _ = MaterialExchangeStock.objects.get_or_create(
                config=order.config,
                type_id=item.type_id,
                defaults={"type_name": item.type_name},
            )
            stock_item.quantity += item.quantity
            stock_item.save()

    messages.success(
        request, _(f"Sell order #{order.id} completed and transaction logged.")
    )
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_approve_buy(request, order_id):
    """Approve a buy order (hub → member)."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

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
        return redirect("indy_hub:material_exchange_admin")

    order.status = "approved"
    order.approved_by = request.user
    order.approved_at = timezone.now()
    order.save()

    messages.success(
        request, _(f"Buy order #{order.id} approved. Awaiting delivery confirmation.")
    )
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_reject_buy(request, order_id):
    """Reject a buy order."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="pending")
    order.status = "rejected"
    order.save()

    messages.warning(request, _(f"Buy order #{order.id} rejected."))
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_mark_delivered_buy(request, order_id):
    """Mark buy order as delivered."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="approved")
    delivery_method = request.POST.get("delivery_method", "contract")

    order.status = "delivered"
    order.delivered_by = request.user
    order.delivered_at = timezone.now()
    order.delivery_method = delivery_method
    order.save()

    messages.success(request, _(f"Buy order #{order.id} marked as delivered."))
    return redirect("indy_hub:material_exchange_admin")


@login_required
@require_http_methods(["POST"])
def material_exchange_complete_buy(request, order_id):
    """Mark buy order as completed and create transaction logs for each item."""
    if not request.user.has_perm("indy_hub.can_manage_material_exchange"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_admin")

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
    return redirect("indy_hub:material_exchange_admin")


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

    month_stats = config.transactions.filter(
        completed_at__gte=month_start
    ).aggregate(
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


def _build_nav_context(user):
    """Helper to build navigation context for Material Exchange."""
    return {
        "can_manage": user.has_perm("indy_hub.can_manage_material_exchange"),
    }
