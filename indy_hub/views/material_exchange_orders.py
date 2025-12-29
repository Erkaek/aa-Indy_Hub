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
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

# Local
from ..models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
)
from ..notifications import notify_user

logger = logging.getLogger(__name__)


@login_required
def my_orders(request):
    """
    Display all orders (sell + buy) for the current user.
    Shows order reference, status, items count, total price, timestamps.
    """
    # Get all sell orders for user
    sell_orders = MaterialExchangeSellOrder.objects.filter(
        seller=request.user
    ).prefetch_related("items").order_by("-created_at")

    # Get all buy orders for user
    buy_orders = MaterialExchangeBuyOrder.objects.filter(
        buyer=request.user
    ).prefetch_related("items").order_by("-created_at")

    # Combine and sort by created_at
    all_orders = []
    
    for order in sell_orders:
        all_orders.append({
            "type": "sell",
            "order": order,
            "reference": order.order_reference,
            "status": order.get_status_display(),
            "status_class": _get_status_class(order.status),
            "items_count": order.items.count(),
            "total_price": order.total_price,
            "created_at": order.created_at,
            "id": order.id,
        })
    
    for order in buy_orders:
        all_orders.append({
            "type": "buy",
            "order": order,
            "reference": order.order_reference,
            "status": order.get_status_display(),
            "status_class": _get_status_class(order.status),
            "items_count": order.items.count(),
            "total_price": order.total_price,
            "created_at": order.created_at,
            "id": order.id,
        })
    
    # Sort by created_at descending
    all_orders.sort(key=lambda x: x["created_at"], reverse=True)
    
    # Paginate
    paginator = Paginator(all_orders, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    context = {
        "page_obj": page_obj,
        "total_sell": sell_orders.count(),
        "total_buy": buy_orders.count(),
    }
    
    return render(request, "indy_hub/material_exchange/my_orders.html", context)


@login_required
def sell_order_detail(request, order_id):
    """
    Display detailed view of a specific sell order.
    Shows order reference prominently, items, status timeline, contract info.
    """
    order = get_object_or_404(
        MaterialExchangeSellOrder.objects.prefetch_related("items"),
        id=order_id,
        seller=request.user,  # Ensure user owns this order
    )
    
    config = order.config
    
    # Get all items with their details
    items = order.items.all()
    
    # Status timeline
    timeline = _build_status_timeline(order, "sell")
    
    context = {
        "order": order,
        "config": config,
        "items": items,
        "timeline": timeline,
        "can_cancel": order.status in ["pending", "approved"],
    }
    
    return render(request, "indy_hub/material_exchange/sell_order_detail.html", context)


@login_required
def buy_order_detail(request, order_id):
    """
    Display detailed view of a specific buy order.
    Shows order reference prominently, items, status timeline, delivery info.
    """
    order = get_object_or_404(
        MaterialExchangeBuyOrder.objects.prefetch_related("items"),
        id=order_id,
        buyer=request.user,  # Ensure user owns this order
    )
    
    config = order.config
    
    # Get all items with their details
    items = order.items.all()
    
    # Status timeline
    timeline = _build_status_timeline(order, "buy")
    
    context = {
        "order": order,
        "config": config,
        "items": items,
        "timeline": timeline,
        "can_cancel": order.status in ["pending", "approved"],
    }
    
    return render(request, "indy_hub/material_exchange/buy_order_detail.html", context)


def _get_status_class(status):
    """Return Bootstrap color class for status badge."""
    status_classes = {
        "pending": "warning",
        "approved": "info",
        "paid": "primary",
        "delivered": "success",
        "completed": "success",
        "rejected": "danger",
        "cancelled": "secondary",
    }
    return status_classes.get(status, "secondary")


def _build_status_timeline(order, order_type):
    """
    Build a timeline of status changes for an order.
    Returns list of dicts with: status, timestamp, user, completed.
    """
    timeline = []
    
    if order_type == "sell":
        # Sell order timeline
        timeline.append({
            "status": "Créée",
            "timestamp": order.created_at,
            "user": order.seller.username,
            "completed": True,
            "icon": "fa-plus-circle",
            "color": "success",
        })
        
        if order.approved_at:
            timeline.append({
                "status": "Approuvée",
                "timestamp": order.approved_at,
                "user": order.approved_by.username if order.approved_by else "System",
                "completed": True,
                "icon": "fa-check-circle",
                "color": "info",
            })
        else:
            timeline.append({
                "status": "En attente d'approbation",
                "timestamp": None,
                "user": None,
                "completed": False,
                "icon": "fa-hourglass-half",
                "color": "warning",
            })
        
        if order.payment_verified_at:
            timeline.append({
                "status": "Paiement vérifié",
                "timestamp": order.payment_verified_at,
                "user": order.payment_verified_by.username if order.payment_verified_by else "System",
                "completed": True,
                "icon": "fa-dollar-sign",
                "color": "primary",
            })
        elif order.status in ["paid", "completed"]:
            timeline.append({
                "status": "En attente de vérification paiement",
                "timestamp": None,
                "user": None,
                "completed": False,
                "icon": "fa-hourglass-half",
                "color": "warning",
            })
        
        if order.status == "completed":
            timeline.append({
                "status": "Terminée",
                "timestamp": order.updated_at,
                "user": None,
                "completed": True,
                "icon": "fa-flag-checkered",
                "color": "success",
            })
        
        if order.status == "rejected":
            timeline.append({
                "status": "Rejetée",
                "timestamp": order.updated_at,
                "user": order.approved_by.username if order.approved_by else "System",
                "completed": True,
                "icon": "fa-times-circle",
                "color": "danger",
            })
    
    else:  # buy order
        timeline.append({
            "status": "Créée",
            "timestamp": order.created_at,
            "user": order.buyer.username,
            "completed": True,
            "icon": "fa-plus-circle",
            "color": "success",
        })
        
        if order.approved_at:
            timeline.append({
                "status": "Approuvée",
                "timestamp": order.approved_at,
                "user": order.approved_by.username if order.approved_by else "System",
                "completed": True,
                "icon": "fa-check-circle",
                "color": "info",
            })
        else:
            timeline.append({
                "status": "En attente d'approbation",
                "timestamp": None,
                "user": None,
                "completed": False,
                "icon": "fa-hourglass-half",
                "color": "warning",
            })
        
        if order.delivered_at:
            timeline.append({
                "status": "Livrée",
                "timestamp": order.delivered_at,
                "user": order.delivered_by.username if order.delivered_by else "System",
                "completed": True,
                "icon": "fa-truck",
                "color": "primary",
            })
        elif order.status in ["delivered", "completed"]:
            timeline.append({
                "status": "En attente de livraison",
                "timestamp": None,
                "user": None,
                "completed": False,
                "icon": "fa-hourglass-half",
                "color": "warning",
            })
        
        if order.status == "completed":
            timeline.append({
                "status": "Terminée",
                "timestamp": order.updated_at,
                "user": None,
                "completed": True,
                "icon": "fa-flag-checkered",
                "color": "success",
            })
        
        if order.status == "rejected":
            timeline.append({
                "status": "Rejetée",
                "timestamp": order.updated_at,
                "user": order.approved_by.username if order.approved_by else "System",
                "completed": True,
                "icon": "fa-times-circle",
                "color": "danger",
            })
    
    return timeline
