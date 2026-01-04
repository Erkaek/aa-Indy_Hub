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

# Local
from ..models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeSellOrder,
)

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
                "id": order.id,
                "timeline_breadcrumb": _build_timeline_breadcrumb(order, "sell"),
            }
        )

    for order in buy_orders:
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
                "id": order.id,
                "timeline_breadcrumb": _build_timeline_breadcrumb(order, "buy"),
            }
        )

    # Sort by created_at descending
    all_orders.sort(key=lambda x: x["created_at"], reverse=True)

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

    # Get all items with their details
    items = order.items.all()

    # Status timeline + breadcrumb
    timeline = _build_status_timeline(order, "sell")
    timeline_breadcrumb = _build_timeline_breadcrumb(order, "sell")

    context = {
        "order": order,
        "config": config,
        "items": items,
        "timeline": timeline,
        "timeline_breadcrumb": timeline_breadcrumb,
        "can_cancel": order.status not in ["completed", "rejected", "cancelled"],
    }

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

    context = {
        "order": order,
        "config": config,
        "items": items,
        "timeline": timeline,
        "timeline_breadcrumb": timeline_breadcrumb,
        "can_cancel": order.status not in ["completed", "rejected", "cancelled"],
    }

    return render(request, "indy_hub/material_exchange/buy_order_detail.html", context)


def _get_status_class(status):
    """Return Bootstrap color class for status badge."""
    status_classes = {
        "draft": "secondary",
        "awaiting_validation": "warning",
        "validated": "info",
        "accepted": "primary",
        "completed": "success",
        "rejected": "danger",
        "cancelled": "secondary",
    }
    return status_classes.get(status, "secondary")


def _build_timeline_breadcrumb(order, order_type):
    """
    Build a simplified timeline breadcrumb for list views.
    Returns list of dicts with just: status, completed, icon, color.
    Used for the breadcrumb on my_orders page.
    """
    breadcrumb = []

    if order_type == "sell":
        # Sell order breadcrumb: Draft -> Validation -> Validated -> Completed
        breadcrumb.append(
            {
                "status": _("Draft"),
                "completed": True,
                "icon": "fa-file",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Validation"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-hourglass-half",
                "color": "warning",
            }
        )

        breadcrumb.append(
            {
                "status": _("Validated"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-check-circle",
                "color": "info",
            }
        )

        breadcrumb.append(
            {
                "status": _("Completed"),
                "completed": order.status == "completed",
                "icon": "fa-flag-checkered",
                "color": "success",
            }
        )

    else:  # buy order
        # Buy order breadcrumb: Draft -> Validation -> Validated -> Completed
        breadcrumb.append(
            {
                "status": _("Draft"),
                "completed": True,
                "icon": "fa-file",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Validation"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-hourglass-half",
                "color": "warning",
            }
        )

        breadcrumb.append(
            {
                "status": _("Validated"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-check-circle",
                "color": "info",
            }
        )

        breadcrumb.append(
            {
                "status": _("Completed"),
                "completed": order.status == "completed",
                "icon": "fa-flag-checkered",
                "color": "success",
            }
        )

    return breadcrumb


def _build_status_timeline(order, order_type):
    """
    Build a timeline of status changes for an order.
    Returns list of dicts with: status, timestamp, user, completed.
    """
    timeline = []

    if order_type == "sell":
        # Sell order timeline
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

        if order.approved_at:
            timeline.append(
                {
                    "status": _("Approved"),
                    "timestamp": order.approved_at,
                    "user": (
                        order.approved_by.username if order.approved_by else "System"
                    ),
                    "completed": True,
                    "icon": "fa-check-circle",
                    "color": "info",
                }
            )
        else:
            timeline.append(
                {
                    "status": _("Awaiting Approval"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
                    "color": "warning",
                }
            )

        if order.contract_validated_at:
            timeline.append(
                {
                    "status": _("Contract Validated"),
                    "timestamp": order.contract_validated_at,
                    "user": "System",
                    "completed": True,
                    "icon": "fa-file-contract",
                    "color": "info",
                }
            )
        elif order.status in ["awaiting_validation", "validated", "completed"]:
            timeline.append(
                {
                    "status": _("Awaiting Contract Validation"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
                    "color": "warning",
                }
            )

        if order.payment_verified_at:
            timeline.append(
                {
                    "status": _("Payment Verified"),
                    "timestamp": order.payment_verified_at,
                    "user": (
                        order.payment_verified_by.username
                        if order.payment_verified_by
                        else "System"
                    ),
                    "completed": True,
                    "icon": "fa-dollar-sign",
                    "color": "primary",
                }
            )
        elif order.status == "completed":
            timeline.append(
                {
                    "status": _("Awaiting Payment Verification"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
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

        # For buy orders, the corp needs to create the contract first
        # Status: draft = waiting for corp contract
        if order.status == "draft":
            timeline.append(
                {
                    "status": _("Awaiting Contract Creation"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
                    "color": "warning",
                }
            )

        # Status: awaiting_validation = contract created, waiting for auth validation
        if order.status in ["awaiting_validation", "validated", "completed"]:
            timeline.append(
                {
                    "status": _("Contract Created by Corporation"),
                    "timestamp": None,  # We don't track when contract was created
                    "user": None,
                    "completed": True,
                    "icon": "fa-file-contract",
                    "color": "info",
                }
            )

        if order.contract_validated_at:
            timeline.append(
                {
                    "status": _("Contract Validated"),
                    "timestamp": order.contract_validated_at,
                    "user": "System",
                    "completed": True,
                    "icon": "fa-check-circle",
                    "color": "info",
                }
            )
        elif order.status in ["awaiting_validation"]:
            timeline.append(
                {
                    "status": _("Awaiting Contract Validation"),
                    "timestamp": None,
                    "user": None,
                    "completed": False,
                    "icon": "fa-hourglass-half",
                    "color": "warning",
                }
            )

        # Status: validated = waiting for user to accept
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
