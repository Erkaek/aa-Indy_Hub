"""
Material Exchange - User Order Management Views.
Handles user-facing order tracking, details, and history.
"""

# Standard Library
import logging

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

# Alliance Auth
from allianceauth.authentication.models import UserProfile

from ..models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeSellOrder,
    NotificationWebhookMessage,
)
from ..notifications import delete_discord_webhook_message
from ..utils.eve import get_corporation_name

# Local
from .navigation import build_nav_context

logger = logging.getLogger(__name__)


@login_required
def my_orders(request):
    """
    Display all orders (sell + buy) for the current user.
    Shows order reference, status, items count, total price, timestamps.
    """
    # Optimize: Annotate items_count to avoid N+1 queries
    # Get all sell orders for user with annotated count
    sell_orders = (
        MaterialExchangeSellOrder.objects.filter(seller=request.user)
        .annotate(items_count=Count("items"))
        .order_by("-created_at")
    )

    # Get all buy orders for user with annotated count
    buy_orders = (
        MaterialExchangeBuyOrder.objects.filter(buyer=request.user)
        .annotate(items_count=Count("items"))
        .order_by("-created_at")
    )

    # Combine and sort by created_at
    all_orders = []

    for order in sell_orders:
        timeline = _build_timeline_breadcrumb(order, "sell")
        is_closed = order.status in {"completed", "rejected", "cancelled"}
        all_orders.append(
            {
                "type": "sell",
                "order": order,
                "reference": order.order_reference,
                "status": order.get_status_display(),
                "status_class": _get_status_class(order.status),
                "items_count": order.items_count,  # Use annotated value
                "total_price": order.total_price,
                "created_at": order.created_at,
                "is_closed": is_closed,
                "id": order.id,
                "timeline_breadcrumb": timeline,
                "progress_width": _calc_progress_width(timeline),
            }
        )

    for order in buy_orders:
        timeline = _build_timeline_breadcrumb(order, "buy")
        is_closed = order.status in {"completed", "rejected", "cancelled"}
        all_orders.append(
            {
                "type": "buy",
                "order": order,
                "reference": order.order_reference,
                "status": order.get_status_display(),
                "status_class": _get_status_class(order.status),
                "items_count": order.items_count,  # Use annotated value
                "total_price": order.total_price,
                "created_at": order.created_at,
                "is_closed": is_closed,
                "id": order.id,
                "timeline_breadcrumb": timeline,
                "progress_width": _calc_progress_width(timeline),
            }
        )

    # Sort: in-progress orders first, then closed orders; each group newest-first.
    all_orders.sort(key=lambda x: x["created_at"], reverse=True)
    all_orders.sort(key=lambda x: x["is_closed"])

    # Paginate
    paginator = Paginator(all_orders, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Optimize: Use aggregate instead of separate count() calls
    orders_stats = MaterialExchangeSellOrder.objects.filter(
        Q(seller=request.user) | Q(pk__in=[])
    ).aggregate(
        sell_count=Count("id", filter=Q(seller=request.user)),
    )
    buy_stats = MaterialExchangeBuyOrder.objects.filter(buyer=request.user).aggregate(
        buy_count=Count("id")
    )

    context = {
        "page_obj": page_obj,
        "total_sell": orders_stats["sell_count"],
        "total_buy": buy_stats["buy_count"],
    }

    context.update(build_nav_context(request.user, active_tab="material_hub"))

    return render(request, "indy_hub/material_exchange/my_orders.html", context)


@login_required
def sell_order_detail(request, order_id):
    """
    Display detailed view of a specific sell order.
    Shows order reference prominently, items, status timeline, contract info.
    """
    queryset = MaterialExchangeSellOrder.objects.prefetch_related("items")

    # Admins can inspect any order; regular users limited to their own
    if request.user.has_perm("indy_hub.can_manage_material_hub"):
        order = get_object_or_404(queryset, id=order_id)
    else:
        order = get_object_or_404(queryset, id=order_id, seller=request.user)

    config = order.config

    corporation_name = get_corporation_name(getattr(config, "corporation_id", None))

    # Get all items with their details
    items = order.items.all()

    # Status timeline + breadcrumb
    timeline = _build_status_timeline(order, "sell")
    timeline_breadcrumb = _build_timeline_breadcrumb(order, "sell")

    context = {
        "order": order,
        "config": config,
        "corporation_name": corporation_name,
        "items": items,
        "timeline": timeline,
        "timeline_breadcrumb": timeline_breadcrumb,
        "can_cancel": order.status not in ["completed", "rejected", "cancelled"],
    }

    context.update(build_nav_context(request.user, active_tab="material_hub"))

    return render(request, "indy_hub/material_exchange/sell_order_detail.html", context)


@login_required
def buy_order_detail(request, order_id):
    """
    Display detailed view of a specific buy order.
    Shows order reference prominently, items, status timeline, delivery info.
    """
    queryset = MaterialExchangeBuyOrder.objects.prefetch_related("items")

    # Admins can inspect any order; regular users limited to their own
    if request.user.has_perm("indy_hub.can_manage_material_hub"):
        order = get_object_or_404(queryset, id=order_id)
    else:
        order = get_object_or_404(queryset, id=order_id, buyer=request.user)

    config = order.config

    # Get all items with their details
    items = order.items.all()

    # Status timeline + breadcrumb
    timeline = _build_status_timeline(order, "buy")
    timeline_breadcrumb = _build_timeline_breadcrumb(order, "buy")

    buyer_main_character = _resolve_main_character_name(order.buyer)

    context = {
        "order": order,
        "config": config,
        "items": items,
        "timeline": timeline,
        "timeline_breadcrumb": timeline_breadcrumb,
        "buyer_main_character": buyer_main_character,
        "can_cancel": order.status not in ["completed", "rejected", "cancelled"],
    }

    context.update(build_nav_context(request.user, active_tab="material_hub"))

    return render(request, "indy_hub/material_exchange/buy_order_detail.html", context)


def _get_status_class(status):
    """Return Bootstrap color class for status badge."""
    status_classes = {
        "draft": "secondary",
        "awaiting_validation": "warning",
        "validated": "info",
        "completed": "success",
        "rejected": "danger",
        "cancelled": "secondary",
    }
    return status_classes.get(status, "secondary")


def _resolve_main_character_name(user) -> str:
    """Return a user's main character name if available, fallback to username."""
    if not user:
        return ""

    try:
        profile = UserProfile.objects.select_related("main_character").get(user=user)
        main_character = getattr(profile, "main_character", None)
        if main_character and getattr(main_character, "character_name", None):
            return str(main_character.character_name)
    except UserProfile.DoesNotExist:
        pass
    except Exception:
        pass

    return str(getattr(user, "username", ""))


def _build_timeline_breadcrumb(order, order_type):
    """
    Build a simplified timeline breadcrumb for list views.
    Returns list of dicts with just: status, completed, icon, color.
    Used for the breadcrumb on my_orders page.
    """
    breadcrumb = []

    if order_type == "sell":
        # Sell steps: Order Created -> Awaiting Contract -> Auth Validated -> Corp Accepted
        breadcrumb.append(
            {
                "status": _("Order Created"),
                "completed": order.status
                in ["draft", "awaiting_validation", "validated", "completed"],
                "icon": "fa-pen",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Awaiting Contract"),
                "completed": order.status
                in ["awaiting_validation", "validated", "completed"],
                "icon": "fa-file",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Auth Validation"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-check-circle",
                "color": "info",
            }
        )

        breadcrumb.append(
            {
                "status": _("Corporation Acceptance"),
                "completed": order.status == "completed",
                "icon": "fa-flag-checkered",
                "color": "success",
            }
        )

    else:  # buy order
        # Buy steps: Order Created -> Awaiting Corp Contract -> Auth Validated -> You Accept
        breadcrumb.append(
            {
                "status": _("Order Created"),
                "completed": order.status
                in ["draft", "awaiting_validation", "validated", "completed"],
                "icon": "fa-pen",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Awaiting Corp Contract"),
                "completed": order.status
                in ["awaiting_validation", "validated", "completed"],
                "icon": "fa-file",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Auth Validation"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-check-circle",
                "color": "info",
            }
        )

        breadcrumb.append(
            {
                "status": _("You Accept"),
                "completed": order.status == "completed",
                "icon": "fa-hand-pointer",
                "color": "success",
            }
        )

    return breadcrumb


def _calc_progress_width(breadcrumb):
    """Return percentage width for completed steps (0-100)."""
    if not breadcrumb:
        return 0

    total = len(breadcrumb)
    done = sum(1 for step in breadcrumb if step.get("completed"))

    if total <= 1:
        return 100 if done else 0

    # Map completed steps to a segment-based percent so first step starts at 0
    ratio = max(0, min(done - 1, total - 1)) / (total - 1)
    return int(ratio * 100)


def _build_status_timeline(order, order_type):
    """
    Build a timeline of status changes for an order.
    Returns list of dicts with: status, timestamp, user, completed.
    """
    timeline = []

    if order_type == "sell":
        timeline.append(
            {
                "status": _("Created"),
                "timestamp": order.created_at,
                "user": order.seller.username,
                "completed": True,
                "icon": "fa-plus-circle",
                "color": "success",
            }
        )

        if order.status == "draft":
            timeline.append(
                {
                    "status": _("Awaiting Your Contract"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-file-contract",
                    "color": "warning",
                }
            )

        if order.contract_validated_at:
            timeline.append(
                {
                    "status": _("Contract Validated by Auth"),
                    "timestamp": order.contract_validated_at,
                    "user": "System",
                    "completed": True,
                    "icon": "fa-check-circle",
                    "color": "info",
                }
            )
        elif order.status in ["awaiting_validation", "validated", "completed"]:
            timeline.append(
                {
                    "status": _("Awaiting Auth Validation"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
                    "color": "warning",
                }
            )

        if order.status == "validated":
            timeline.append(
                {
                    "status": _("Awaiting Corporation Acceptance"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-building",
                    "color": "warning",
                }
            )

        if order.payment_verified_at or order.status == "completed":
            timeline.append(
                {
                    "status": _("Completed"),
                    "timestamp": order.updated_at,
                    "user": None,
                    "completed": True,
                    "icon": "fa-flag-checkered",
                    "color": "success",
                }
            )

        if order.status == "rejected":
            timeline.append(
                {
                    "status": _("Rejected"),
                    "timestamp": order.updated_at,
                    "user": (
                        order.approved_by.username if order.approved_by else "System"
                    ),
                    "completed": True,
                    "icon": "fa-times-circle",
                    "color": "danger",
                }
            )
    else:  # buy order
        timeline.append(
            {
                "status": _("Created"),
                "timestamp": order.created_at,
                "user": order.buyer.username,
                "completed": True,
                "icon": "fa-plus-circle",
                "color": "success",
            }
        )

        if order.status == "draft":
            timeline.append(
                {
                    "status": _("Awaiting Corporation Contract"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-building",
                    "color": "warning",
                }
            )

        if order.status in ["awaiting_validation", "validated", "completed"]:
            timeline.append(
                {
                    "status": _("Contract Created"),
                    "timestamp": None,
                    "user": None,
                    "completed": True,
                    "icon": "fa-file-contract",
                    "color": "info",
                }
            )

        if order.contract_validated_at:
            timeline.append(
                {
                    "status": _("Contract Validated by Auth"),
                    "timestamp": order.contract_validated_at,
                    "user": "System",
                    "completed": True,
                    "icon": "fa-check-circle",
                    "color": "info",
                }
            )
        elif order.status == "awaiting_validation":
            timeline.append(
                {
                    "status": _("Awaiting Auth Validation"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
                    "color": "warning",
                }
            )

        if order.status == "validated":
            timeline.append(
                {
                    "status": _("Awaiting Your Acceptance"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hand-pointer",
                    "color": "warning",
                }
            )

        if order.status == "completed":
            timeline.append(
                {
                    "status": _("Completed"),
                    "timestamp": order.updated_at,
                    "user": None,
                    "completed": True,
                    "icon": "fa-flag-checkered",
                    "color": "success",
                }
            )

        if order.status == "rejected":
            timeline.append(
                {
                    "status": _("Rejected"),
                    "timestamp": order.updated_at,
                    "user": (
                        order.approved_by.username if order.approved_by else "System"
                    ),
                    "completed": True,
                    "icon": "fa-times-circle",
                    "color": "danger",
                }
            )

    return timeline


@login_required
def sell_order_delete(request, order_id):
    """
    Delete a sell order.
    Only owner can delete, only if not completed/rejected/cancelled.
    """
    order = get_object_or_404(
        MaterialExchangeSellOrder,
        id=order_id,
        seller=request.user,
    )

    # Can only delete non-terminal orders
    if order.status in ["completed", "rejected", "cancelled"]:
        messages.error(
            request,
            _("Cannot delete completed or rejected orders."),
        )
        return redirect("indy_hub:sell_order_detail", order_id=order_id)

    if request.method == "POST":
        order_ref = order.order_reference
        order.delete()
        messages.success(
            request,
            _("Sell order %(ref)s has been deleted.") % {"ref": order_ref},
        )
        return redirect("indy_hub:my_orders")

    # GET request - show confirmation
    context = {
        "order": order,
        "order_type": "sell",
    }
    return render(
        request,
        "indy_hub/material_exchange/order_delete_confirm.html",
        context,
    )


@login_required
def buy_order_delete(request, order_id):
    """
    Delete a buy order.
    Only owner can delete, only if not completed/rejected/cancelled.
    """
    order = get_object_or_404(
        MaterialExchangeBuyOrder,
        id=order_id,
        buyer=request.user,
    )

    # Can only delete non-terminal orders
    if order.status in ["completed", "rejected", "cancelled"]:
        messages.error(
            request,
            _("Cannot delete completed or rejected orders."),
        )
        return redirect("indy_hub:buy_order_detail", order_id=order_id)

    if request.method == "POST":
        order_ref = order.order_reference
        webhook_messages = NotificationWebhookMessage.objects.filter(buy_order=order)
        for webhook_message in webhook_messages:
            delete_discord_webhook_message(
                webhook_message.webhook_url,
                webhook_message.message_id,
            )
        webhook_messages.delete()
        order.delete()
        messages.success(
            request,
            _("Buy order %(ref)s has been deleted.") % {"ref": order_ref},
        )
        return redirect("indy_hub:my_orders")

    # GET request - show confirmation
    context = {
        "order": order,
        "order_type": "buy",
    }
    return render(
        request,
        "indy_hub/material_exchange/order_delete_confirm.html",
        context,
    )
