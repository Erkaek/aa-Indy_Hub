"""Material Exchange views for Indy Hub."""

# Standard Library
import hashlib
import logging
from decimal import Decimal

# Django
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission
from django.core.cache import cache
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
    CachedCharacterAsset,
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
)
from ..notifications import notify_multi
from ..services.asset_cache import get_corp_divisions_cached, get_user_assets_cached
from ..tasks.material_exchange import (
    ME_STOCK_SYNC_CACHE_VERSION,
    ME_USER_ASSETS_CACHE_VERSION,
    me_stock_sync_cache_version_key,
    me_user_assets_cache_version_key,
    refresh_material_exchange_buy_stock,
    refresh_material_exchange_sell_user_assets,
    sync_material_exchange_prices,
    sync_material_exchange_stock,
)
from ..utils.eve import get_type_name
from .navigation import build_nav_context

logger = logging.getLogger(__name__)
User = get_user_model()

_PRODUCTION_IDS_CACHE: set[int] | None = None
_INDUSTRY_MARKET_GROUP_IDS_CACHE: set[int] | None = None


def _get_industry_market_group_ids() -> set[int]:
    """Return market group IDs used by EVE industry materials (cached)."""

    global _INDUSTRY_MARKET_GROUP_IDS_CACHE
    if _INDUSTRY_MARKET_GROUP_IDS_CACHE is not None:
        return _INDUSTRY_MARKET_GROUP_IDS_CACHE

    cache_key = "indy_hub:material_exchange:industry_market_group_ids:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            _INDUSTRY_MARKET_GROUP_IDS_CACHE = {int(x) for x in cached}
            return _INDUSTRY_MARKET_GROUP_IDS_CACHE
        except Exception:
            _INDUSTRY_MARKET_GROUP_IDS_CACHE = set()
            return _INDUSTRY_MARKET_GROUP_IDS_CACHE

    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveIndustryActivityMaterial

        ids = set(
            EveIndustryActivityMaterial.objects.exclude(
                material_eve_type__eve_market_group_id__isnull=True
            )
            .values_list("material_eve_type__eve_market_group_id", flat=True)
            .distinct()
        )
    except Exception as exc:
        logger.warning("Failed to load industry market group IDs: %s", exc)
        ids = set()

    cache.set(cache_key, list(ids), 3600)
    _INDUSTRY_MARKET_GROUP_IDS_CACHE = ids
    return ids


def _get_market_group_children_map() -> dict[int | None, set[int]]:
    """Return a mapping of parent_id -> child_ids (cached)."""

    cache_key = "indy_hub:material_exchange:market_group_children_map:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            return {int(k) if k != "None" else None: set(v) for k, v in cached.items()}
        except Exception:
            pass

    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveMarketGroup

        children_map: dict[int | None, set[int]] = {}
        for group_id, parent_id in EveMarketGroup.objects.values_list(
            "id", "parent_market_group_id"
        ):
            children_map.setdefault(parent_id, set()).add(group_id)
    except Exception as exc:
        logger.warning("Failed to load market group tree: %s", exc)
        return {}

    cache.set(
        cache_key,
        {"None" if k is None else str(k): list(v) for k, v in children_map.items()},
        3600,
    )
    return children_map


def _expand_market_group_ids(group_ids: set[int]) -> set[int]:
    """Expand market group IDs to include all descendants."""

    if not group_ids:
        return set()

    children_map = _get_market_group_children_map()
    expanded = set(group_ids)
    stack = list(group_ids)
    while stack:
        current = stack.pop()
        for child_id in children_map.get(current, set()):
            if child_id in expanded:
                continue
            expanded.add(child_id)
            stack.append(child_id)
    return expanded


def _get_allowed_type_ids_for_config(
    config: MaterialExchangeConfig, mode: str
) -> set[int] | None:
    """Resolve allowed EveType IDs for the given mode (sell/buy)."""

    if mode not in {"sell", "buy"}:
        return None

    try:
        raw_group_ids = (
            config.allowed_market_groups_sell
            if mode == "sell"
            else config.allowed_market_groups_buy
        )
        group_ids = {int(x) for x in (raw_group_ids or [])}
        if not group_ids:
            group_ids = _get_industry_market_group_ids()

        if not group_ids:
            return None

        expanded_group_ids = _expand_market_group_ids(group_ids)
        groups_key = ",".join(map(str, sorted(expanded_group_ids)))
        groups_hash = hashlib.md5(
            groups_key.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        cache_key = (
            "indy_hub:material_exchange:allowed_type_ids:v1:" f"{mode}:{groups_hash}"
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return {int(x) for x in cached}

        # Alliance Auth (External Libs)
        from eveuniverse.models import EveType

        allowed_type_ids = set(
            EveType.objects.filter(
                eve_market_group_id__in=expanded_group_ids
            ).values_list("id", flat=True)
        )
        cache.set(cache_key, list(allowed_type_ids), 3600)
        return allowed_type_ids
    except Exception as exc:
        logger.warning("Failed to resolve market group filter (%s): %s", mode, exc)
        return None


def _get_material_exchange_admins() -> list[User]:
    """Return active admins for Material Exchange (staff + permission holders)."""

    admins = list(User.objects.filter(is_staff=True, is_active=True))

    try:
        perm = Permission.objects.get(
            codename="can_manage_material_hub", content_type__app_label="indy_hub"
        )
        perm_users = User.objects.filter(
            Q(groups__permissions=perm) | Q(user_permissions=perm), is_active=True
        ).distinct()
        admins.extend(list(perm_users))
    except Permission.DoesNotExist:
        pass

    seen: set[int] = set()
    unique_admins: list[User] = []
    for admin in admins:
        if admin.id in seen:
            continue
        seen.add(admin.id)
        unique_admins.append(admin)

    return unique_admins


def _fetch_user_assets_for_structure(
    user, structure_id: int, *, allow_refresh: bool = True
) -> tuple[dict[int, int], bool]:
    """Return aggregated asset quantities for the user's characters at a structure using cache."""

    assets, scope_missing = get_user_assets_cached(user, allow_refresh=allow_refresh)

    aggregated: dict[int, int] = {}
    for asset in assets:
        try:
            if int(asset.get("location_id", 0)) != int(structure_id):
                continue
        except (TypeError, ValueError):
            continue

        try:
            type_id = int(asset.get("type_id"))
        except (TypeError, ValueError):
            continue

        qty_raw = asset.get("quantity", 1)
        try:
            quantity = int(qty_raw or 0)
        except (TypeError, ValueError):
            quantity = 1

        if quantity <= 0:
            quantity = 1 if asset.get("is_singleton") else 0

        aggregated[type_id] = aggregated.get(type_id, 0) + quantity

    return aggregated, scope_missing


def _me_sell_assets_progress_key(user_id: int) -> str:
    return f"indy_hub:material_exchange:sell_assets_refresh:{int(user_id)}"


def _ensure_sell_assets_refresh_started(user) -> dict:
    """Start (if needed) an async refresh of user assets and return the current progress state."""

    progress_key = _me_sell_assets_progress_key(user.id)
    ttl_seconds = 10 * 60
    state = cache.get(progress_key) or {}
    if state.get("running"):
        return state

    # Always refresh on page open unless explicitly suppressed.
    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership
        from esi.models import Token

        total = int(
            CharacterOwnership.objects.filter(user=user)
            .values_list("character__character_id", flat=True)
            .distinct()
            .count()
        )

        has_assets_token = (
            Token.objects.filter(user=user)
            .require_scopes(["esi-assets.read_assets.v1"])
            .exists()
        )
    except Exception:
        total = 0
        has_assets_token = False

    if total > 0 and not has_assets_token:
        state = {
            "running": False,
            "finished": True,
            "error": "missing_assets_scope",
            "total": total,
            "done": 0,
            "failed": 0,
        }
        cache.set(progress_key, state, ttl_seconds)
        return state

    state = {
        "running": True,
        "finished": False,
        "error": None,
        "total": total,
        "done": 0,
        "failed": 0,
    }
    cache.set(progress_key, state, ttl_seconds)

    try:
        task_result = refresh_material_exchange_sell_user_assets.delay(int(user.id))
        logger.info(
            "Started asset refresh task for user %s (task_id=%s)",
            user.id,
            task_result.id,
        )
    except Exception as exc:
        # Fallback: mark as finished; UI will stop polling.
        logger.error(
            "Failed to start asset refresh task for user %s: %s",
            user.id,
            exc,
            exc_info=True,
        )
        state.update({"running": False, "finished": True, "error": "task_start_failed"})
        cache.set(progress_key, state, ttl_seconds)

    return state


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_sell_assets_refresh_status(request):
    """Return JSON progress for sell-page user asset refresh."""

    progress_key = _me_sell_assets_progress_key(request.user.id)
    state = cache.get(progress_key) or {
        "running": False,
        "finished": False,
        "error": None,
        "total": 0,
        "done": 0,
        "failed": 0,
    }
    return JsonResponse(state)


def _ensure_buy_stock_refresh_started(config) -> dict:
    """Start (if needed) an async refresh of buy stock and return the current progress state."""

    progress_key = (
        f"indy_hub:material_exchange:buy_stock_refresh:{int(config.corporation_id)}"
    )
    ttl_seconds = 10 * 60
    state = cache.get(progress_key) or {}

    if state.get("running"):
        return state

    state = {
        "running": True,
        "finished": False,
        "error": None,
    }
    cache.set(progress_key, state, ttl_seconds)

    try:
        task_result = refresh_material_exchange_buy_stock.delay(
            int(config.corporation_id)
        )
        logger.info(
            "Started buy stock refresh task for corporation %s (task_id=%s)",
            config.corporation_id,
            task_result.id,
        )
    except Exception as exc:
        logger.error(
            "Failed to start buy stock refresh task for corporation %s: %s",
            config.corporation_id,
            exc,
            exc_info=True,
        )
        state.update({"running": False, "finished": True, "error": "task_start_failed"})
        cache.set(progress_key, state, ttl_seconds)

    return state


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_buy_stock_refresh_status(request):
    """Return JSON progress for buy-page stock refresh."""

    config = get_object_or_404(MaterialExchangeConfig, is_active=True)
    progress_key = (
        f"indy_hub:material_exchange:buy_stock_refresh:{int(config.corporation_id)}"
    )
    state = cache.get(progress_key) or {
        "running": False,
        "finished": False,
        "error": None,
    }
    return JsonResponse(state)


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
        context = {"nav_context": _build_nav_context(request.user)}
        context.update(
            build_nav_context(
                request.user,
                active_tab="material_hub",
                can_manage_corp=request.user.has_perm(
                    "indy_hub.can_manage_corp_bp_requests"
                ),
            )
        )
        return render(
            request,
            "indy_hub/material_exchange/not_configured.html",
            context,
        )

    # Post-deploy self-heal: if the user already has cached assets but they were
    # produced by an older normalization version, trigger a one-time background refresh.
    try:
        has_cached_assets = CachedCharacterAsset.objects.filter(
            user=request.user
        ).exists()
        if has_cached_assets:
            current_version = int(
                cache.get(me_user_assets_cache_version_key(int(request.user.id))) or 0
            )
            if current_version < int(ME_USER_ASSETS_CACHE_VERSION):
                _ensure_sell_assets_refresh_started(request.user)
    except Exception:
        pass

    # Stats (based on the user's visible sell items)
    stock_count = 0
    total_stock_value = 0

    try:
        # Avoid blocking ESI calls on index page; use cached data only
        user_assets, scope_missing = _fetch_user_assets_for_structure(
            request.user, int(config.structure_id), allow_refresh=False
        )

        if scope_missing:
            messages.info(
                request,
                _(
                    "Refreshing via ESI. Make sure you have granted the assets scope to at least one character."
                ),
            )

        allowed_type_ids = _get_allowed_type_ids_for_config(config, "sell")
        if allowed_type_ids:
            user_assets = {
                tid: qty for tid, qty in user_assets.items() if tid in allowed_type_ids
            }

        if user_assets:
            price_data = _fetch_fuzzwork_prices(list(user_assets.keys()))
            visible_items = 0
            total_value = Decimal(0)

            for type_id, user_qty in user_assets.items():
                fuzz_prices = price_data.get(type_id, {})
                jita_buy = fuzz_prices.get("buy") or Decimal(0)
                jita_sell = fuzz_prices.get("sell") or Decimal(0)
                base = jita_sell if config.sell_markup_base == "sell" else jita_buy
                if base <= 0:
                    continue
                unit_price = base * (1 + (config.sell_markup_percent / Decimal(100)))
                item_value = unit_price * user_qty
                total_value += item_value
                visible_items += 1

            stock_count = visible_items
            total_stock_value = total_value
    except Exception:
        # Fall back silently if user assets cannot be loaded
        pass

    pending_sell_orders = config.sell_orders.filter(
        status=MaterialExchangeSellOrder.Status.DRAFT
    ).count()
    pending_buy_orders = config.buy_orders.filter(status="draft").count()

    # User's recent orders
    user_sell_orders = (
        request.user.material_sell_orders.filter(config=config)
        .prefetch_related("items")
        .order_by("-created_at")[:5]
    )
    user_buy_orders = (
        request.user.material_buy_orders.filter(config=config)
        .prefetch_related("items")
        .order_by("-created_at")[:5]
    )

    # Recent transactions (last 10)
    recent_transactions = config.transactions.select_related(
        "user", "sell_order", "buy_order"
    ).order_by("-completed_at")[:10]

    # Admin section data (if user has permission)
    can_admin = request.user.has_perm("indy_hub.can_manage_material_hub")
    admin_sell_orders = None
    admin_buy_orders = None
    status_filter = None

    if can_admin:
        closed_statuses = ["completed", "rejected", "cancelled"]
        status_filter = request.GET.get("status") or None
        # Admin panel: show only active/in-flight orders; closed ones move to history view
        admin_sell_orders = (
            config.sell_orders.exclude(status__in=closed_statuses)
            .select_related("seller")
            .prefetch_related("items")
            .order_by("-created_at")
        )
        admin_buy_orders = (
            config.buy_orders.exclude(status__in=closed_statuses)
            .select_related("buyer")
            .prefetch_related("items")
            .order_by("-created_at")
        )
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

    context.update(
        build_nav_context(
            request.user,
            active_tab="material_hub",
            can_manage_corp=request.user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            ),
        )
    )

    return render(request, "indy_hub/material_exchange/index.html", context)


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_history(request):
    """Admin-only history page showing closed (completed/rejected/cancelled) orders."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("You are not allowed to view this page."))
        return redirect("indy_hub:material_exchange_index")

    config = get_object_or_404(MaterialExchangeConfig, is_active=True)
    closed_statuses = ["completed", "rejected", "cancelled"]

    sell_history = (
        config.sell_orders.filter(status__in=closed_statuses)
        .select_related("seller")
        .order_by("-created_at")
    )
    buy_history = (
        config.buy_orders.filter(status__in=closed_statuses)
        .select_related("buyer")
        .order_by("-created_at")
    )

    context = {
        "config": config,
        "sell_history": sell_history,
        "buy_history": buy_history,
        "nav_context": _build_nav_context(request.user),
    }

    context.update(
        build_nav_context(
            request.user,
            active_tab="material_hub",
            can_manage_corp=request.user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            ),
        )
    )

    return render(request, "indy_hub/material_exchange/history.html", context)


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_sell(request):
    """
    Sell materials TO the hub.
    Member chooses materials + quantities, creates ONE order with multiple items.
    """
    config = get_object_or_404(MaterialExchangeConfig, is_active=True)
    materials_with_qty: list[dict] = []
    assets_refreshing = False

    sell_last_update = (
        CachedCharacterAsset.objects.filter(user=request.user)
        .order_by("-synced_at")
        .values_list("synced_at", flat=True)
        .first()
    )

    user_assets_version_refresh = False
    try:
        if sell_last_update:
            current_version = int(
                cache.get(me_user_assets_cache_version_key(int(request.user.id))) or 0
            )
            user_assets_version_refresh = current_version < int(
                ME_USER_ASSETS_CACHE_VERSION
            )
    except Exception:
        user_assets_version_refresh = False

    try:
        user_assets_stale = (
            not sell_last_update
            or (timezone.now() - sell_last_update).total_seconds() > 3600
        )
    except Exception:
        user_assets_stale = True

    # Start async refresh of the user's assets on page open (GET only).
    progress_key = _me_sell_assets_progress_key(request.user.id)
    sell_assets_progress = cache.get(progress_key) or {}
    if request.method == "GET" and (user_assets_stale or user_assets_version_refresh):
        # The refreshed=1 guard prevents loops, but version migrations should override it.
        if request.GET.get("refreshed") != "1" or user_assets_version_refresh:
            sell_assets_progress = _ensure_sell_assets_refresh_started(request.user)
    assets_refreshing = bool(sell_assets_progress.get("running"))

    if request.method == "POST":
        user_assets, scope_missing = _fetch_user_assets_for_structure(
            request.user, config.structure_id
        )
        if scope_missing:
            # Avoid transient flash messaging for missing scopes; the page already
            # renders a persistent on-page warning based on `sell_assets_progress`.
            _ensure_sell_assets_refresh_started(request.user)
            return redirect("indy_hub:material_exchange_sell")

        if not user_assets:
            messages.error(
                request,
                _("No items available to sell at this location."),
            )
            return redirect("indy_hub:material_exchange_sell")

        pre_filter_count = len(user_assets)

        # Apply market group filter if configured
        try:
            allowed_type_ids = _get_allowed_type_ids_for_config(config, "sell")
            if allowed_type_ids:
                user_assets = {
                    tid: qty
                    for tid, qty in user_assets.items()
                    if tid in allowed_type_ids
                }
        except Exception as exc:
            logger.warning("Failed to apply market group filter: %s", exc)

        if not user_assets:
            if pre_filter_count > 0:
                messages.error(
                    request,
                    _("No accepted items available to sell at this location."),
                )
            else:
                messages.error(
                    request, _("You have no items to sell at this location.")
                )
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
            # Get order reference from client (generated in JavaScript)
            client_order_ref = request.POST.get("order_reference", "").strip()

            order = MaterialExchangeSellOrder.objects.create(
                config=config,
                seller=request.user,
                status=MaterialExchangeSellOrder.Status.DRAFT,
                order_reference=client_order_ref if client_order_ref else None,
            )
            for item_data in items_to_create:
                MaterialExchangeSellOrderItem.objects.create(order=order, **item_data)

            messages.success(
                request,
                _(
                    f"Sell order created. Order reference: {order.order_reference}. "
                    f"Open your order page to follow the contract steps."
                ),
            )

            # Redirect to order detail page instead of index
            return redirect("indy_hub:sell_order_detail", order_id=order.id)

        return redirect("indy_hub:material_exchange_sell")

    # GET branch: trigger stock sync only if stale (> 1h) or never synced
    message_shown = False
    try:
        last_sync = config.last_stock_sync
        needs_refresh = (
            not last_sync or (timezone.now() - last_sync).total_seconds() > 3600
        )
    except Exception:
        needs_refresh = True

    stock_version_refresh = False
    try:
        # Only trigger the version refresh if there is already synced data.
        if config.last_stock_sync:
            current_version = int(
                cache.get(me_stock_sync_cache_version_key(int(config.corporation_id)))
                or 0
            )
            stock_version_refresh = current_version < int(ME_STOCK_SYNC_CACHE_VERSION)
    except Exception:
        stock_version_refresh = False

    if needs_refresh or stock_version_refresh:
        messages.info(
            request,
            _(
                "Refreshing via ESI. Make sure you have granted the assets scope to at least one character."
            ),
        )
        message_shown = True
        try:
            logger.info(
                "Starting stock sync for sell page (last_sync=%s)",
                config.last_stock_sync,
            )
            sync_material_exchange_stock()
            config.refresh_from_db()
            logger.info(
                "Stock sync completed successfully (last_sync=%s)",
                config.last_stock_sync,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Stock auto-sync failed (sell page): %s", exc, exc_info=True)

    # Avoid blocking GET requests: if a background refresh is running, don't do a synchronous refresh.
    # If we're on ?refreshed=1 and nothing is cached yet, allow a one-time sync refresh so the list
    # can still render even if the background job didn't populate anything.
    has_cached_assets = CachedCharacterAsset.objects.filter(user=request.user).exists()

    current_user_assets_version = 0
    try:
        current_user_assets_version = int(
            cache.get(me_user_assets_cache_version_key(int(request.user.id))) or 0
        )
    except Exception:
        current_user_assets_version = 0
    needs_user_assets_version_refresh = has_cached_assets and (
        current_user_assets_version < int(ME_USER_ASSETS_CACHE_VERSION)
    )

    allow_refresh = (
        not bool(sell_assets_progress.get("running"))
        or sell_assets_progress.get("error") == "task_start_failed"
    ) and (
        request.GET.get("refreshed") != "1"
        or not has_cached_assets
        or needs_user_assets_version_refresh
    )
    user_assets, scope_missing = _fetch_user_assets_for_structure(
        request.user,
        config.structure_id,
        allow_refresh=allow_refresh,
    )
    if user_assets:
        pre_filter_count = len(user_assets)
        logger.info(
            f"SELL DEBUG: Found {len(user_assets)} unique items in assets before production filter (filter disabled)"
        )

        # Apply market group filter (same as POST + Index) to keep views consistent
        try:
            allowed_type_ids = _get_allowed_type_ids_for_config(config, "sell")
            if allowed_type_ids:
                user_assets = {
                    tid: qty
                    for tid, qty in user_assets.items()
                    if tid in allowed_type_ids
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

        if pre_filter_count > 0 and not materials_with_qty and not message_shown:
            messages.info(
                request,
                _("No accepted items available to sell at this location."),
            )
    else:
        if scope_missing and not message_shown:
            messages.info(
                request,
                _(
                    "Refreshing via ESI. Make sure you have granted the assets scope to at least one character."
                ),
            )
        elif not message_shown:
            messages.info(
                request,
                _("No items available to sell at this location."),
            )

    # Show loading spinner if either Celery task is running OR stock sync just happened
    # (stock sync is bloquant and completes before template render, so this is safe)
    assets_refreshing = assets_refreshing or needs_refresh

    # Get corporation name
    corporation_name = _get_corp_name_for_hub(config.corporation_id)

    context = {
        "config": config,
        "materials": materials_with_qty,
        "corporation_name": corporation_name,
        "assets_refreshing": assets_refreshing,
        "sell_assets_progress": sell_assets_progress,
        "sell_last_update": sell_last_update,
        "nav_context": _build_nav_context(request.user),
    }

    context.update(
        build_nav_context(
            request.user,
            active_tab="material_hub",
            can_manage_corp=request.user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            ),
        )
    )

    return render(request, "indy_hub/material_exchange/sell.html", context)


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_buy(request):
    """
    Buy materials FROM the hub.
    Member chooses materials + quantities, creates ONE order with multiple items.
    """
    config = get_object_or_404(MaterialExchangeConfig, is_active=True)
    stock_refreshing = False

    corp_assets_scope_missing = False
    try:
        # Alliance Auth
        from esi.models import Token

        corp_assets_scope_missing = not (
            Token.objects.filter(character__corporation_id=int(config.corporation_id))
            .require_scopes(["esi-assets.read_corporation_assets.v1"])
            .require_valid()
            .exists()
        )
    except Exception:
        corp_assets_scope_missing = False

    if request.method == "POST":
        # Get available stock
        stock_items = list(
            config.stock_items.filter(quantity__gt=0, jita_buy_price__gt=0)
        )

        pre_filter_stock_count = len(stock_items)

        # Apply market group filter if configured
        try:
            allowed_type_ids = _get_allowed_type_ids_for_config(config, "buy")
            if allowed_type_ids:
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
            if pre_filter_stock_count > 0:
                messages.error(
                    request,
                    _(
                        "No stock available in the allowed Market Groups based on the current configuration."
                    ),
                )
            else:
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
            # Get order reference from client (generated in JavaScript)
            client_order_ref = request.POST.get("order_reference", "").strip()

            # Create ONE order with ALL items
            order = MaterialExchangeBuyOrder.objects.create(
                config=config,
                buyer=request.user,
                status="draft",
                order_reference=client_order_ref if client_order_ref else None,
            )

            # Create items for this order
            for item_data in items_to_create:
                MaterialExchangeBuyOrderItem.objects.create(order=order, **item_data)

            admins = _get_material_exchange_admins()
            if admins:
                preview_lines = []
                for item in order.items.all()[:5]:
                    preview_lines.append(
                        f"- {item.type_name}: {item.quantity:,} @ {item.unit_price:,.2f} ISK"
                    )
                if order.items.count() > 5:
                    preview_lines.append("…")

                notify_multi(
                    admins,
                    _("New Buy Order"),
                    _(
                        f"{request.user.username} created buy order {order.order_reference}.\n"
                        f"Items: {order.items.count()}  |  Total qty: {order.total_quantity:,}\n"
                        f"Total: {total_cost:,.2f} ISK\n\n"
                        f"Action: create/validate the contract."
                        + (
                            "\n\nPreview:\n" + "\n".join(preview_lines)
                            if preview_lines
                            else ""
                        )
                    ),
                    level="info",
                    link=f"/indy_hub/material-exchange/my-orders/buy/{order.id}/",
                )

            messages.success(
                request,
                _(
                    f"Created buy order #{order.id} with {len(items_to_create)} item(s). Total cost: {total_cost:,.2f} ISK. Awaiting admin approval."
                ),
            )
            return redirect("indy_hub:material_exchange_index")

        return redirect("indy_hub:material_exchange_buy")

    # Auto-refresh stock only if stale (> 1h) or never synced; otherwise keep cache.
    # Post-deploy self-heal: if we changed stock derivation logic, trigger a one-time refresh.
    try:
        last_sync = config.last_stock_sync
        # Django
        from django.utils import timezone

        needs_refresh = (
            not last_sync or (timezone.now() - last_sync).total_seconds() > 3600
        )
    except Exception:
        needs_refresh = True

    stock_version_refresh = False
    try:
        if config.last_stock_sync:
            current_version = int(
                cache.get(me_stock_sync_cache_version_key(int(config.corporation_id)))
                or 0
            )
            stock_version_refresh = current_version < int(ME_STOCK_SYNC_CACHE_VERSION)
    except Exception:
        stock_version_refresh = False

    stock_refreshing = False
    buy_stock_progress = (
        cache.get(
            f"indy_hub:material_exchange:buy_stock_refresh:{int(config.corporation_id)}"
        )
        or {}
    )

    if request.method == "GET" and (needs_refresh or stock_version_refresh):
        # The refreshed=1 guard prevents loops, but version migrations should override it.
        if request.GET.get("refreshed") != "1" or stock_version_refresh:
            buy_stock_progress = _ensure_buy_stock_refresh_started(config)
    stock_refreshing = bool(buy_stock_progress.get("running"))

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
            messages.warning(request, f"Price sync failed automatically: {exc}")

    # Show available stock (quantity > 0 and price available)
    stock_items = list(config.stock_items.filter(quantity__gt=0, jita_buy_price__gt=0))
    pre_filter_stock_count = len(stock_items)

    # Apply market group filter if configured
    try:
        allowed_type_ids = _get_allowed_type_ids_for_config(config, "buy")
        if allowed_type_ids:
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

    if pre_filter_stock_count > 0 and not stock_items:
        messages.info(
            request,
            _(
                "Stock exists, but none of it matches the allowed Market Groups based on the current configuration."
            ),
        )

    buy_last_update = None
    try:
        candidates = [config.last_stock_sync, config.last_price_sync]
        candidates = [dt for dt in candidates if dt]
        buy_last_update = max(candidates) if candidates else None
    except Exception:
        buy_last_update = None

    try:
        div_map, _div_scope_missing = get_corp_divisions_cached(
            int(config.corporation_id), allow_refresh=False
        )
        hangar_division_label = (
            div_map.get(int(config.hangar_division)) if div_map else None
        )
    except Exception:
        hangar_division_label = None

    hangar_division_label = (
        hangar_division_label or ""
    ).strip() or f"Hangar Division {int(config.hangar_division)}"

    context = {
        "config": config,
        "stock": stock_items,
        "stock_refreshing": stock_refreshing,
        "buy_stock_progress": buy_stock_progress,
        "corp_assets_scope_missing": corp_assets_scope_missing,
        "hangar_division_label": hangar_division_label,
        "buy_last_update": buy_last_update,
        "nav_context": _build_nav_context(request.user),
    }

    context.update(
        build_nav_context(
            request.user,
            active_tab="material_hub",
            can_manage_corp=request.user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            ),
        )
    )

    return render(request, "indy_hub/material_exchange/buy.html", context)


@login_required
@indy_hub_permission_required("can_manage_material_hub")
@require_http_methods(["POST"])
def material_exchange_sync_stock(request):
    """
    Force an immediate sync of stock from ESI corp assets.
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
@indy_hub_permission_required("can_manage_material_hub")
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
@require_http_methods(["POST"])
@login_required
@require_http_methods(["POST"])
def material_exchange_approve_sell(request, order_id):
    """Approve a sell order (member → hub)."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(
        MaterialExchangeSellOrder,
        id=order_id,
        status=MaterialExchangeSellOrder.Status.DRAFT,
    )

    order.status = MaterialExchangeSellOrder.Status.AWAITING_VALIDATION
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
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(
        MaterialExchangeSellOrder,
        id=order_id,
        status__in=[
            MaterialExchangeSellOrder.Status.DRAFT,
            MaterialExchangeSellOrder.Status.AWAITING_VALIDATION,
            MaterialExchangeSellOrder.Status.VALIDATED,
        ],
    )
    order.status = MaterialExchangeSellOrder.Status.REJECTED
    order.save()

    messages.warning(request, _(f"Sell order #{order.id} rejected."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_verify_payment_sell(request, order_id):
    """Mark sell order as completed (contract accepted in-game)."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(
        MaterialExchangeSellOrder, id=order_id, status="validated"
    )

    order.status = "completed"
    order.payment_verified_by = request.user
    order.payment_verified_at = timezone.now()
    order.save()

    messages.success(request, _(f"Sell order #{order.id} completed."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_complete_sell(request, order_id):
    """Mark sell order as fully completed and create transaction logs for each item."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(
        MaterialExchangeSellOrder, id=order_id, status="completed"
    )

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
    """Approve a buy order (hub → member) - Creates contract permission."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="draft")

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

    order.status = "awaiting_validation"
    order.approved_by = request.user
    order.approved_at = timezone.now()
    order.save()

    messages.success(
        request,
        _(f"Buy order #{order.id} approved. Corporation will now create a contract."),
    )
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_reject_buy(request, order_id):
    """Reject a buy order."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(MaterialExchangeBuyOrder, id=order_id, status="draft")

    from ..notifications import notify_user

    notify_user(
        order.buyer,
        _("❌ Buy Order Rejected"),
        _(
            f"Your buy order #{order.id} has been rejected.\n\n"
            f"Reason: Admin decision.\n\n"
            f"Contact the admins in Auth if you need details or want to retry."
        ),
        level="error",
        link=f"/indy_hub/material-exchange/my-orders/buy/{order.id}/",
    )

    order.status = "rejected"
    order.save()

    messages.warning(request, _(f"Buy order #{order.id} rejected and buyer notified."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_mark_delivered_buy(request, order_id):
    """Mark buy order as delivered."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(
        MaterialExchangeBuyOrder,
        id=order_id,
        status=MaterialExchangeBuyOrder.Status.VALIDATED,
    )
    delivery_method = request.POST.get("delivery_method", "contract")

    _complete_buy_order(
        order, delivered_by=request.user, delivery_method=delivery_method
    )

    messages.success(request, _(f"Buy order #{order.id} marked as delivered."))
    return redirect("indy_hub:material_exchange_index")


@login_required
@require_http_methods(["POST"])
def material_exchange_complete_buy(request, order_id):
    """Mark buy order as completed and create transaction logs for each item."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
        messages.error(request, _("Permission denied."))
        return redirect("indy_hub:material_exchange_index")

    order = get_object_or_404(
        MaterialExchangeBuyOrder,
        id=order_id,
        status=MaterialExchangeBuyOrder.Status.VALIDATED,
    )

    _complete_buy_order(order)

    messages.success(
        request, _(f"Buy order #{order.id} completed and transaction logged.")
    )
    return redirect("indy_hub:material_exchange_index")


def _complete_buy_order(order, *, delivered_by=None, delivery_method=None):
    """Helper to finalize a buy order (auth-side manual completion)."""
    with transaction.atomic():
        if delivered_by:
            order.delivered_by = delivered_by
            order.delivered_at = timezone.now()
            order.delivery_method = delivery_method

        order.status = MaterialExchangeBuyOrder.Status.COMPLETED
        order.save()

        # Create transaction log for each item and update stock
        for item in order.items.all():
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

            try:
                stock_item = order.config.stock_items.get(type_id=item.type_id)
                stock_item.quantity = max(stock_item.quantity - item.quantity, 0)
                stock_item.save()
            except MaterialExchangeStock.DoesNotExist:
                continue


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
        "transactions": page_obj.object_list,
        "is_paginated": page_obj.has_other_pages(),
        "transaction_type": transaction_type,
        "user_filter": user_filter,
        "month_stats": month_stats,
        "nav_context": _build_nav_context(request.user),
    }

    context.update(
        build_nav_context(
            request.user,
            active_tab="material_hub",
            can_manage_corp=request.user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            ),
        )
    )

    return render(request, "indy_hub/material_exchange/transactions.html", context)


@login_required
@require_http_methods(["POST"])
def material_exchange_assign_contract(request, order_id):
    """Assign ESI contract ID to a sell or buy order."""
    if not request.user.has_perm("indy_hub.can_manage_material_hub"):
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
        "can_manage": user.has_perm("indy_hub.can_manage_material_hub"),
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
