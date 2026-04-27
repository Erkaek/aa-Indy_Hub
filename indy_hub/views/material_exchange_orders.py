"""
Material Exchange - User Order Management Views.
Handles user-facing order tracking, details, and history.
"""

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

# Alliance Auth
from allianceauth.authentication.models import UserProfile
from allianceauth.services.hooks import get_extension_logger

from ..decorators import indy_hub_permission_required
from ..models import (
    MaterialExchangeBuyOrder,
    MaterialExchangeSellOrder,
    NotificationWebhookMessage,
)
from ..notifications import delete_discord_webhook_message
from ..utils.analytics import emit_view_analytics_event
from ..utils.eve import get_corporation_name
from ..utils.material_exchange_contract_check import (
    build_expected_items,
    collapse_whitespace,
    normalize_text,
    parse_contract_export,
    parse_contract_items,
    parse_isk_amount,
    summarize_counter,
)

# Local
from .navigation import build_nav_context

logger = get_extension_logger(__name__)


def _get_material_exchange_config_locations(config) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    try:
        for location in config.accepted_locations.all().order_by("sort_order", "id"):
            rows.append(
                {
                    "structure_id": int(location.structure_id),
                    "structure_name": str(location.structure_name or ""),
                    "hangar_division": int(location.hangar_division),
                }
            )
    except Exception:
        rows = []

    if rows:
        return rows

    structure_id = getattr(config, "structure_id", None)
    hangar_division = getattr(config, "hangar_division", None)
    if not structure_id or not hangar_division:
        return []

    return [
        {
            "structure_id": int(structure_id),
            "structure_name": str(getattr(config, "structure_name", "") or ""),
            "hangar_division": int(hangar_division),
        }
    ]


def _get_material_exchange_location_names(config) -> list[str]:
    names: list[str] = []
    for location in _get_material_exchange_config_locations(config):
        structure_id = int(location.get("structure_id") or 0)
        structure_name = str(location.get("structure_name") or "").strip()
        candidate = structure_name or f"Structure {structure_id}"
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _get_material_exchange_location_summary(config) -> str:
    return ", ".join(_get_material_exchange_location_names(config))


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def my_orders(request):
    """
    Display all orders (sell + buy) for the current user.
    Shows order reference, status, items count, total price, timestamps.
    """
    emit_view_analytics_event(
        view_name="material_exchange_orders.my_orders", request=request
    )
    logger.debug("Material exchange orders list accessed (user_id=%s)", request.user.id)
    # Optimize: Annotate items_count to avoid N+1 queries
    # Get all sell orders for user with annotated count
    sell_orders = (
        MaterialExchangeSellOrder.objects.filter(seller=request.user)
        .select_related("config")
        .annotate(items_count=Count("items"))
        .order_by("-created_at")
    )

    # Get all buy orders for user with annotated count
    buy_orders = (
        MaterialExchangeBuyOrder.objects.filter(buyer=request.user)
        .select_related("config")
        .annotate(items_count=Count("items"))
        .order_by("-created_at")
    )

    # Combine and sort by created_at
    all_orders = []

    for order in sell_orders:
        timeline = _build_timeline_breadcrumb(order, "sell")
        is_closed = order.status in {"completed", "rejected", "cancelled"}
        corporation_name = get_corporation_name(
            getattr(order.config, "corporation_id", None)
        )
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
                "contract_check_enabled": not is_closed,
                "contract_check_recipient": corporation_name,
                "contract_check_location": _get_material_exchange_location_summary(
                    order.config
                ),
                "contract_check_amount": str(order.total_price),
                "contract_check_amount_label": _("I will receive"),
            }
        )

    for order in buy_orders:
        timeline = _build_timeline_breadcrumb(order, "buy")
        is_closed = order.status in {"completed", "rejected", "cancelled"}
        buyer_main_character = _resolve_main_character_name(order.buyer)
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
                "contract_check_enabled": not is_closed,
                "contract_check_recipient": buyer_main_character,
                "contract_check_location": _get_material_exchange_location_summary(
                    order.config
                ),
                "contract_check_amount": str(order.total_price),
                "contract_check_amount_label": _("I will pay"),
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

    logger.debug(
        "Material exchange orders summary (user_id=%s, sell=%s, buy=%s)",
        request.user.id,
        orders_stats["sell_count"],
        buy_stats["buy_count"],
    )

    context.update(build_nav_context(request.user, active_tab="material_hub"))

    return render(request, "indy_hub/material_exchange/my_orders.html", context)


def _render_order_not_found(request, *, order_id, order_type: str):
    """Render a friendly 404 page when a Material Exchange order is unavailable.

    Shown in two cases, both indistinguishable from the user's perspective and
    intentionally surfaced as the same neutral "no longer available" page so we
    do not leak information about other users' orders:

    * The order does not exist (e.g. completed, cancelled, or deleted) —
        typical when following a stale link from a Discord notification.
    * The order exists but the current user is not allowed to view it (i.e.
        they are not the seller/buyer and lack ``indy_hub.can_manage_material_hub``).

    Honors a safe ``next`` query parameter so the user can continue back to
    where they came from.
    """
    next_url = request.GET.get("next") or ""
    if next_url and not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    context = {
        "order_id": order_id,
        "order_type": order_type,
        "next_url": next_url,
    }
    context.update(build_nav_context(request.user, active_tab="material_hub"))
    return render(
        request,
        "indy_hub/material_exchange/order_not_found.html",
        context,
        status=404,
    )


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def sell_order_detail(request, order_id):
    """
    Display detailed view of a specific sell order.
    Shows order reference prominently, items, status timeline, contract info.
    """
    emit_view_analytics_event(
        view_name="material_exchange_orders.sell_order_detail", request=request
    )
    queryset = MaterialExchangeSellOrder.objects.prefetch_related("items")

    # Admins can inspect any order; regular users limited to their own
    try:
        if request.user.has_perm("indy_hub.can_manage_material_hub"):
            order = get_object_or_404(queryset, id=order_id)
        else:
            order = get_object_or_404(queryset, id=order_id, seller=request.user)
    except Http404:
        logger.warning(
            "Sell order not found or unauthorized (order_id=%s, user_id=%s)",
            order_id,
            request.user.id,
        )
        return _render_order_not_found(request, order_id=order_id, order_type="sell")

    logger.debug(
        "Sell order detail accessed (order_id=%s, user_id=%s)",
        order_id,
        request.user.id,
    )

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
        "accepted_location_summary": _get_material_exchange_location_summary(config),
        "items": items,
        "timeline": timeline,
        "timeline_breadcrumb": timeline_breadcrumb,
        "can_cancel": order.status not in ["completed", "rejected", "cancelled"],
    }

    context.update(build_nav_context(request.user, active_tab="material_hub"))

    return render(request, "indy_hub/material_exchange/sell_order_detail.html", context)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def buy_order_detail(request, order_id):
    """
    Display detailed view of a specific buy order.
    Shows order reference prominently, items, status timeline, delivery info.
    """
    emit_view_analytics_event(
        view_name="material_exchange_orders.buy_order_detail", request=request
    )
    queryset = MaterialExchangeBuyOrder.objects.prefetch_related("items")

    # Admins can inspect any order; regular users limited to their own
    try:
        if request.user.has_perm("indy_hub.can_manage_material_hub"):
            order = get_object_or_404(queryset, id=order_id)
        else:
            order = get_object_or_404(queryset, id=order_id, buyer=request.user)
    except Http404:
        logger.warning(
            "Buy order not found or unauthorized (order_id=%s, user_id=%s)",
            order_id,
            request.user.id,
        )
        return _render_order_not_found(request, order_id=order_id, order_type="buy")

    logger.debug(
        "Buy order detail accessed (order_id=%s, user_id=%s)",
        order_id,
        request.user.id,
    )

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
        "accepted_location_summary": _get_material_exchange_location_summary(config),
        "items": items,
        "timeline": timeline,
        "timeline_breadcrumb": timeline_breadcrumb,
        "buyer_main_character": buyer_main_character,
        "can_cancel": order.status not in ["completed", "rejected", "cancelled"],
    }

    context.update(build_nav_context(request.user, active_tab="material_hub"))

    return render(request, "indy_hub/material_exchange/buy_order_detail.html", context)


def _build_contract_check_payload(
    *,
    order,
    order_type: str,
    raw_text: str,
    recipient_name: str,
    location_name: str,
    accepted_location_names: list[str] | None = None,
):
    fields = parse_contract_export(raw_text)
    expected_items, expected_item_labels = build_expected_items(order.items.all())
    actual_items, actual_item_labels = parse_contract_items(
        fields.get("Items For Sale", "")
    )
    expected_items_summary = summarize_counter(expected_items, expected_item_labels)
    actual_items_summary = summarize_counter(actual_items, expected_item_labels)

    if order_type == "sell":
        amount_label = "I will receive"
    else:
        amount_label = "I will pay"

    expected_amount = int(order.total_price)
    actual_amount = parse_isk_amount(fields.get(amount_label, ""))

    actual_contract_type = fields.get("Contract Type", "")
    actual_description = fields.get("Description", "")
    actual_availability = fields.get("Availability", "")
    actual_location = fields.get("Location", "")
    expected_reference = order.order_reference or f"INDY-{order.id}"
    expected_amount_display = f"{expected_amount:,.0f} ISK"
    actual_amount_display = (
        f"{actual_amount:,.0f} ISK" if actual_amount is not None else ""
    )

    checks = []

    contract_type_ok = normalize_text(actual_contract_type) == normalize_text(
        "Item Exchange"
    )
    checks.append(
        {
            "key": "contract_type",
            "label": _("Contract type"),
            "passed": contract_type_ok,
            "expected": "Item Exchange",
            "actual": actual_contract_type,
            "reminder": _("Use Item Exchange on the contract creation screen."),
            "copy_value": "Item Exchange",
            "copy_label": _("Copy contract type"),
            "message": (
                _("Contract type is correct.")
                if contract_type_ok
                else _("Contract type must be Item Exchange.")
            ),
        }
    )

    description_ok = normalize_text(actual_description) == normalize_text(
        expected_reference
    )
    checks.append(
        {
            "key": "description",
            "label": _("Description"),
            "passed": description_ok,
            "expected": expected_reference,
            "actual": actual_description,
            "reminder": _(
                "The Description field must exactly match the order reference."
            ),
            "copy_value": expected_reference,
            "copy_label": _("Copy description"),
            "message": (
                _("Description matches the order reference.")
                if description_ok
                else _("Description must exactly match the order reference.")
            ),
        }
    )

    normalized_recipient = normalize_text(recipient_name)
    availability_ok = bool(
        normalized_recipient
    ) and normalized_recipient in normalize_text(actual_availability)
    checks.append(
        {
            "key": "availability",
            "label": _("Availability"),
            "passed": availability_ok,
            "expected": recipient_name,
            "actual": actual_availability,
            "reminder": _(
                "Availability must target the recipient shown for this order."
            ),
            "copy_value": recipient_name,
            "copy_label": _("Copy recipient"),
            "message": (
                _("Availability points to the expected recipient.")
                if availability_ok
                else _("Availability must target the expected recipient.")
            ),
        }
    )

    normalized_location = normalize_text(location_name)
    actual_location_normalized = normalize_text(actual_location)
    accepted_location_names = accepted_location_names or []
    normalized_locations = [
        normalize_text(name) for name in accepted_location_names if normalize_text(name)
    ]
    if normalized_location and normalized_location not in normalized_locations:
        normalized_locations.append(normalized_location)
    location_ok = bool(normalized_locations) and any(
        actual_location_normalized == candidate
        or candidate in actual_location_normalized
        or actual_location_normalized in candidate
        for candidate in normalized_locations
    )
    checks.append(
        {
            "key": "location",
            "label": _("Location"),
            "passed": location_ok,
            "expected": location_name,
            "actual": actual_location,
            "reminder": _(
                "Location must match one of the configured accepted locations for this order."
            ),
            "copy_value": location_name,
            "copy_label": _("Copy location"),
            "message": (
                _("Location matches one of the configured accepted locations.")
                if location_ok
                else _("Location must match one of the configured accepted locations.")
            ),
        }
    )

    amount_ok = actual_amount == expected_amount
    checks.append(
        {
            "key": "amount",
            "label": amount_label,
            "passed": amount_ok,
            "expected": expected_amount_display,
            "actual": actual_amount_display,
            "reminder": _("The ISK amount must match the order total exactly."),
            "copy_value": str(expected_amount),
            "copy_label": _("Copy amount"),
            "message": (
                _("Amount matches the order total.")
                if amount_ok
                else _("Amount does not match the order total.")
            ),
        }
    )

    expected_only = expected_items - actual_items
    actual_only = actual_items - expected_items
    item_detail_sections = []
    if expected_only:
        item_detail_sections.append(
            {
                "key": "missing",
                "label": _("Missing from pasted contract"),
                "items": summarize_counter(expected_only, expected_item_labels),
            }
        )
    if actual_only:
        item_detail_sections.append(
            {
                "key": "surplus",
                "label": _("Surplus in pasted contract"),
                "items": summarize_counter(actual_only, actual_item_labels),
            }
        )

    expected_items_copy = "\n".join(expected_items_summary)
    items_ok = expected_items == actual_items and bool(expected_items)
    checks.append(
        {
            "key": "items",
            "label": _("Item"),
            "passed": items_ok,
            "expected": expected_items_summary,
            "actual": actual_items_summary,
            "reminder": _(
                "Items For Sale must contain exactly the same items and quantities as the order."
            ),
            "detail_sections": item_detail_sections,
            "copy_value": expected_items_copy,
            "copy_label": _("Copy expected items"),
            "message": (
                _("Items match the order exactly.")
                if items_ok
                else _("Items do not match the order.")
            ),
        }
    )

    ok = all(check["passed"] for check in checks)
    return {
        "ok": ok,
        "summary": (
            _("Contract looks valid.")
            if ok
            else _(
                "Contract has mismatches that should be fixed before in-game validation."
            )
        ),
        "checks": checks,
        "expected": {
            "reference": expected_reference,
            "recipient": recipient_name,
            "location": location_name,
            "contract_type": "Item Exchange",
            "amount_label": amount_label,
            "amount": expected_amount,
            "amount_display": expected_amount_display,
            "items": expected_items_summary,
        },
        "parsed": {
            "contract_type": actual_contract_type,
            "description": actual_description,
            "availability": actual_availability,
            "location": actual_location,
        },
    }


def _get_sell_order_for_request(request, order_id):
    queryset = MaterialExchangeSellOrder.objects.prefetch_related(
        "items"
    ).select_related("config", "seller")
    if request.user.has_perm("indy_hub.can_manage_material_hub"):
        return get_object_or_404(queryset, id=order_id)
    return get_object_or_404(queryset, id=order_id, seller=request.user)


def _get_buy_order_for_request(request, order_id):
    queryset = MaterialExchangeBuyOrder.objects.prefetch_related(
        "items"
    ).select_related("config", "buyer")
    if request.user.has_perm("indy_hub.can_manage_material_hub"):
        return get_object_or_404(queryset, id=order_id)
    return get_object_or_404(queryset, id=order_id, buyer=request.user)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_POST
def sell_order_check_contract(request, order_id):
    order = _get_sell_order_for_request(request, order_id)
    raw_text = collapse_whitespace(request.POST.get("contract_text", ""))
    if not raw_text:
        return JsonResponse(
            {
                "ok": False,
                "summary": _("Please paste the in-game contract export first."),
            },
            status=400,
        )

    corporation_name = get_corporation_name(
        getattr(order.config, "corporation_id", None)
    )
    accepted_location_names = _get_material_exchange_location_names(order.config)
    payload = _build_contract_check_payload(
        order=order,
        order_type="sell",
        raw_text=request.POST.get("contract_text", ""),
        recipient_name=corporation_name,
        location_name=_get_material_exchange_location_summary(order.config),
        accepted_location_names=accepted_location_names,
    )
    return JsonResponse(payload)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_POST
def buy_order_check_contract(request, order_id):
    order = _get_buy_order_for_request(request, order_id)
    raw_text = collapse_whitespace(request.POST.get("contract_text", ""))
    if not raw_text:
        return JsonResponse(
            {
                "ok": False,
                "summary": _("Please paste the in-game contract export first."),
            },
            status=400,
        )

    accepted_location_names = _get_material_exchange_location_names(order.config)
    payload = _build_contract_check_payload(
        order=order,
        order_type="buy",
        raw_text=request.POST.get("contract_text", ""),
        recipient_name=_resolve_main_character_name(order.buyer),
        location_name=_get_material_exchange_location_summary(order.config),
        accepted_location_names=accepted_location_names,
    )
    return JsonResponse(payload)


def _get_status_class(status):
    """Return Bootstrap color class for status badge."""
    status_classes = {
        "draft": "secondary",
        "awaiting_validation": "warning",
        "anomaly": "warning",
        "anomaly_rejected": "warning",
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
                in [
                    "draft",
                    "awaiting_validation",
                    "anomaly",
                    "anomaly_rejected",
                    "validated",
                    "completed",
                ],
                "icon": "fa-pen",
                "color": "secondary",
            }
        )

        breadcrumb.append(
            {
                "status": _("Awaiting Contract"),
                "completed": order.status
                in [
                    "awaiting_validation",
                    "anomaly",
                    "anomaly_rejected",
                    "validated",
                    "completed",
                ],
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

        if order.status in ["anomaly", "anomaly_rejected"]:
            timeline.append(
                {
                    "status": (
                        _("Anomaly - Waiting User/Admin Action")
                        if order.status == "anomaly"
                        else _("Anomaly - Contract Refused In-Game (Redo Required)")
                    ),
                    "timestamp": order.updated_at,
                    "user": (
                        order.approved_by.username if order.approved_by else "System"
                    ),
                    "completed": True,
                    "icon": "fa-exclamation-triangle",
                    "color": "danger",
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


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def sell_order_delete(request, order_id):
    """
    Delete a sell order.
    Only owner can delete, only if not completed/rejected/cancelled.
    """
    emit_view_analytics_event(
        view_name="material_exchange_orders.sell_order_delete", request=request
    )
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


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def buy_order_delete(request, order_id):
    """
    Delete a buy order.
    Only owner can delete, only if not completed/rejected/cancelled.
    """
    emit_view_analytics_event(
        view_name="material_exchange_orders.buy_order_delete", request=request
    )
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
