"""Material Exchange views for Indy Hub."""

# Standard Library
import hashlib
from decimal import ROUND_CEILING, Decimal

# Django
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

# Alliance Auth
from allianceauth.authentication.models import UserProfile
from allianceauth.services.hooks import get_extension_logger

from ..decorators import indy_hub_permission_required, tokens_required
from ..models import (
    CachedCharacterAsset,
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
    MaterialExchangeSettings,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
)
from ..services.asset_cache import get_corp_divisions_cached, get_user_assets_cached
from ..tasks.material_exchange import (
    ESI_DOWN_COOLDOWN_SECONDS,
    ME_STOCK_SYNC_CACHE_VERSION,
    ME_USER_ASSETS_CACHE_VERSION,
    me_buy_stock_esi_cooldown_key,
    me_sell_assets_esi_cooldown_key,
    me_stock_sync_cache_version_key,
    me_user_assets_cache_version_key,
    refresh_material_exchange_buy_stock,
    refresh_material_exchange_sell_user_assets,
    sync_material_exchange_prices,
    sync_material_exchange_stock,
)
from ..utils.eve import get_type_name
from ..utils.material_exchange_pricing import compute_buy_price_from_member
from .navigation import build_nav_context

logger = get_extension_logger(__name__)
User = get_user_model()

_PRODUCTION_IDS_CACHE: set[int] | None = None
_INDUSTRY_MARKET_GROUP_IDS_CACHE: set[int] | None = None


def _resolve_main_character_name(user) -> str:
    """Return user's main character name when available, fallback to username."""
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


def _build_timeline_breadcrumb_for_order(
    order, order_kind: str, perspective: str = "user"
):
    """Build compact timeline breadcrumb for order list cards."""
    breadcrumb = []

    if order_kind == "sell":
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
            }
        )
        breadcrumb.append(
            {
                "status": _("Auth Validation"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-check-circle",
            }
        )
        breadcrumb.append(
            {
                "status": _("Corporation Acceptance"),
                "completed": order.status == "completed",
                "icon": "fa-flag-checkered",
            }
        )
    else:
        final_acceptance_label = (
            _("User Accept") if perspective == "admin" else _("You Accept")
        )
        breadcrumb.append(
            {
                "status": _("Order Created"),
                "completed": order.status
                in ["draft", "awaiting_validation", "validated", "completed"],
                "icon": "fa-pen",
            }
        )
        breadcrumb.append(
            {
                "status": _("Awaiting Corp Contract"),
                "completed": order.status
                in ["awaiting_validation", "validated", "completed"],
                "icon": "fa-file",
            }
        )
        breadcrumb.append(
            {
                "status": _("Auth Validation"),
                "completed": order.status in ["validated", "completed"],
                "icon": "fa-check-circle",
            }
        )
        breadcrumb.append(
            {
                "status": final_acceptance_label,
                "completed": order.status == "completed",
                "icon": "fa-hand-pointer",
            }
        )

    return breadcrumb


def _annotate_timeline_positions(timeline):
    total_steps = len(timeline)
    if total_steps <= 1:
        if total_steps == 1:
            timeline[0]["position_percent"] = 0
        return timeline

    last_index = total_steps - 1
    for index, step in enumerate(timeline):
        step["position_percent"] = round((index / last_index) * 100, 2)
    return timeline


def _attach_order_progress_data(order, order_kind: str, perspective: str = "user"):
    order.order_kind = order_kind
    order.timeline_breadcrumb = _build_timeline_breadcrumb_for_order(
        order, order_kind, perspective
    )
    order.timeline_breadcrumb = _annotate_timeline_positions(order.timeline_breadcrumb)
    order.progress_width = _calc_progress_width(order.timeline_breadcrumb)
    order.progress_total_steps = len(order.timeline_breadcrumb)
    order.progress_completed_steps = sum(
        1 for step in order.timeline_breadcrumb if step.get("completed")
    )
    order.progress_active_start = 0
    order.progress_active_width = 0

    current_step_index = 0
    for idx, step in enumerate(order.timeline_breadcrumb):
        if step.get("completed"):
            current_step_index = idx

    if order.timeline_breadcrumb:
        order.progress_current_label = order.timeline_breadcrumb[current_step_index][
            "status"
        ]
        current_step_position = order.timeline_breadcrumb[current_step_index].get(
            "position_percent", 0
        )
        if current_step_index < order.progress_total_steps - 1:
            next_step_position = order.timeline_breadcrumb[current_step_index + 1].get(
                "position_percent", current_step_position
            )
            order.progress_active_start = current_step_position
            order.progress_active_width = max(
                0, round(next_step_position - current_step_position, 2)
            )
    else:
        order.progress_current_label = ""

    return order


def _calc_progress_width(breadcrumb) -> int:
    if not breadcrumb:
        return 0
    total = len(breadcrumb)
    done = sum(1 for step in breadcrumb if step.get("completed"))
    if total <= 1:
        return 100 if done else 0
    ratio = max(0, min(done - 1, total - 1)) / (total - 1)
    return int(ratio * 100)


def _minutes_until_refresh(last_update, *, window_seconds: int = 3600) -> int | None:
    if not last_update:
        return None
    try:
        remaining = window_seconds - (timezone.now() - last_update).total_seconds()
    except Exception:
        return None
    if remaining <= 0:
        return 0
    return int((remaining + 59) // 60)


def _get_material_exchange_settings() -> MaterialExchangeSettings | None:
    try:
        return MaterialExchangeSettings.get_solo()
    except Exception:
        return None


def _is_material_exchange_enabled() -> bool:
    settings_obj = _get_material_exchange_settings()
    if settings_obj is None:
        return True
    return bool(settings_obj.is_enabled)


def _get_material_exchange_config() -> MaterialExchangeConfig | None:
    return MaterialExchangeConfig.objects.first()


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
            return set()

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
    """Return active admins for Material Exchange (explicit permission holders only)."""

    try:
        perm = Permission.objects.get(
            codename="can_manage_material_hub", content_type__app_label="indy_hub"
        )
        perm_users = User.objects.filter(
            Q(groups__permissions=perm) | Q(user_permissions=perm), is_active=True
        ).distinct()
        return list(perm_users)
    except Permission.DoesNotExist:
        return []


def _fetch_user_assets_for_structure(
    user, structure_id: int, *, allow_refresh: bool = True
) -> tuple[dict[int, int], bool]:
    """Return aggregated asset quantities for the user's characters at a structure using cache."""

    aggregated, _by_character, scope_missing = _fetch_user_assets_for_structure_data(
        user, structure_id, allow_refresh=allow_refresh
    )
    return aggregated, scope_missing


def _fetch_user_assets_for_structure_data(
    user, structure_id: int, *, allow_refresh: bool = True
) -> tuple[dict[int, int], dict[int, dict[int, int]], bool]:
    """Return aggregated and per-character asset quantities at a structure using cache."""

    assets, scope_missing = get_user_assets_cached(user, allow_refresh=allow_refresh)

    aggregated: dict[int, int] = {}
    by_character: dict[int, dict[int, int]] = {}
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

        try:
            character_id = int(asset.get("character_id") or 0)
        except (TypeError, ValueError):
            character_id = 0
        if character_id > 0:
            char_assets = by_character.setdefault(character_id, {})
            char_assets[type_id] = char_assets.get(type_id, 0) + quantity

    return aggregated, by_character, scope_missing


def _resolve_user_character_names_map(user) -> dict[int, str]:
    """Return owned character names keyed by character ID."""

    names: dict[int, str] = {}
    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership

        ownerships = CharacterOwnership.objects.select_related("character").filter(
            user=user
        )
        for ownership in ownerships:
            character = getattr(ownership, "character", None)
            if not character:
                continue
            character_id = getattr(character, "character_id", None)
            if not character_id:
                continue
            character_name = (getattr(character, "character_name", "") or "").strip()
            names[int(character_id)] = character_name or str(character_id)
    except Exception:
        return names

    return names


def _me_sell_assets_progress_key(user_id: int) -> str:
    return f"indy_hub:material_exchange:sell_assets_refresh:{int(user_id)}"


def _ensure_sell_assets_refresh_started(user) -> dict:
    """Start (if needed) an async refresh of user assets and return the current progress state."""

    progress_key = _me_sell_assets_progress_key(user.id)
    ttl_seconds = 10 * 60
    state = cache.get(progress_key) or {}

    cooldown_until = cache.get(me_sell_assets_esi_cooldown_key(int(user.id)))
    if cooldown_until:
        try:
            retry_seconds = max(
                0, int(float(cooldown_until) - timezone.now().timestamp())
            )
        except (TypeError, ValueError):
            retry_seconds = int(ESI_DOWN_COOLDOWN_SECONDS)
        retry_minutes = int((retry_seconds + 59) // 60)
        state = {
            "running": False,
            "finished": True,
            "error": "esi_down",
            "retry_after_minutes": retry_minutes,
        }
        cache.set(progress_key, state, ttl_seconds)
        return state
    if state.get("running"):
        try:
            started_at = float(state.get("started_at") or 0)
            last_progress_at = float(state.get("last_progress_at") or started_at or 0)
            elapsed = timezone.now().timestamp() - last_progress_at
        except (TypeError, ValueError):
            elapsed = 0
        if not state.get("started_at") and not state.get("last_progress_at"):
            elapsed = 999999
        if elapsed <= 180:
            return state
        state.update({"running": False, "finished": True, "error": "timeout"})
        cache.set(progress_key, state, ttl_seconds)

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
            .require_valid()
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

    started_at = timezone.now().timestamp()
    state = {
        "running": True,
        "finished": False,
        "error": None,
        "total": total,
        "done": 0,
        "failed": 0,
        "started_at": started_at,
        "last_progress_at": started_at,
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

    if not _is_material_exchange_enabled():
        return JsonResponse({"running": False, "finished": True, "error": "disabled"})

    progress_key = _me_sell_assets_progress_key(request.user.id)
    state = cache.get(progress_key) or {
        "running": False,
        "finished": False,
        "error": None,
        "total": 0,
        "done": 0,
        "failed": 0,
    }
    if state.get("running"):
        try:
            started_at = float(state.get("started_at") or 0)
            last_progress_at = float(state.get("last_progress_at") or started_at or 0)
            elapsed = timezone.now().timestamp() - last_progress_at
        except (TypeError, ValueError):
            elapsed = 0
        if not state.get("started_at") and not state.get("last_progress_at"):
            elapsed = 999999
        if elapsed > 180:
            state.update({"running": False, "finished": True, "error": "timeout"})
            cache.set(progress_key, state, 10 * 60)
    response = dict(state)
    try:
        last_update = (
            CachedCharacterAsset.objects.filter(user=request.user)
            .order_by("-synced_at")
            .values_list("synced_at", flat=True)
            .first()
        )
    except Exception:
        last_update = None

    if last_update:
        try:
            last_update_utc = timezone.localtime(last_update, timezone.utc)
        except Exception:
            last_update_utc = last_update
        response["last_update"] = last_update_utc.isoformat()

    return JsonResponse(response)


def _ensure_buy_stock_refresh_started(config) -> dict:
    """Start (if needed) an async refresh of buy stock and return the current progress state."""

    progress_key = (
        f"indy_hub:material_exchange:buy_stock_refresh:{int(config.corporation_id)}"
    )
    ttl_seconds = 10 * 60
    state = cache.get(progress_key) or {}

    cooldown_until = cache.get(
        me_buy_stock_esi_cooldown_key(int(config.corporation_id))
    )
    if cooldown_until:
        try:
            retry_seconds = max(
                0, int(float(cooldown_until) - timezone.now().timestamp())
            )
        except (TypeError, ValueError):
            retry_seconds = int(ESI_DOWN_COOLDOWN_SECONDS)
        retry_minutes = int((retry_seconds + 59) // 60)
        state = {
            "running": False,
            "finished": True,
            "error": "esi_down",
            "retry_after_minutes": retry_minutes,
        }
        cache.set(progress_key, state, ttl_seconds)
        return state

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
    if not _is_material_exchange_enabled():
        return JsonResponse({"running": False, "finished": True, "error": "disabled"})

    config = _get_material_exchange_config()
    if not config:
        return JsonResponse(
            {"running": False, "finished": True, "error": "not_configured"}
        )
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
    # Local
    from ..services.fuzzwork import FuzzworkError, fetch_fuzzwork_prices

    if not type_ids:
        return {}

    try:
        return fetch_fuzzwork_prices(type_ids, timeout=15)
    except FuzzworkError as exc:  # pragma: no cover - defensive
        logger.warning("material_exchange: failed to fetch fuzzwork prices: %s", exc)
        return {}


@login_required
@indy_hub_permission_required("can_access_indy_hub")
def material_exchange_index(request):
    """
    Material Exchange hub landing page.
    Shows overview, recent transactions, and quick stats.
    """
    config = _get_material_exchange_config()
    enabled = _is_material_exchange_enabled()

    if not enabled or not config:
        context = {
            "nav_context": _build_nav_context(request.user),
            "material_exchange_disabled": not enabled,
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
        return render(
            request,
            "indy_hub/material_exchange/not_configured.html",
            context,
        )

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
        if allowed_type_ids is not None:
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

    # User's active orders
    closed_statuses = ["completed", "rejected", "cancelled"]
    user_sell_orders = (
        request.user.material_sell_orders.filter(config=config)
        .exclude(status__in=closed_statuses)
        .prefetch_related("items")
        .order_by("-created_at")[:5]
    )
    user_buy_orders = (
        request.user.material_buy_orders.filter(config=config)
        .exclude(status__in=closed_statuses)
        .prefetch_related("items")
        .order_by("-created_at")[:5]
    )

    recent_orders = []
    for order in user_sell_orders:
        recent_orders.append(_attach_order_progress_data(order, "sell"))
    for order in user_buy_orders:
        recent_orders.append(_attach_order_progress_data(order, "buy"))
    recent_orders.sort(key=lambda order: order.created_at, reverse=True)
    recent_orders = recent_orders[:10]

    # Admin section data (if user has permission)
    can_admin = request.user.has_perm("indy_hub.can_manage_material_hub")
    explicit_manage_material_hub_perm = False
    try:
        manage_perm = Permission.objects.get(
            codename="can_manage_material_hub", content_type__app_label="indy_hub"
        )
        explicit_manage_material_hub_perm = (
            User.objects.filter(
                id=request.user.id,
                is_active=True,
            )
            .filter(
                Q(groups__permissions=manage_perm) | Q(user_permissions=manage_perm)
            )
            .exists()
        )
    except Permission.DoesNotExist:
        explicit_manage_material_hub_perm = False

    superuser_without_material_hub_manage = bool(
        request.user.is_superuser and not explicit_manage_material_hub_perm
    )
    admin_sell_orders = None
    admin_buy_orders = None
    status_filter = None

    if can_admin:
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

        admin_sell_orders = list(admin_sell_orders)
        for order in admin_sell_orders:
            order.seller_display_name = _resolve_main_character_name(order.seller)
            _attach_order_progress_data(order, "sell", perspective="admin")

        admin_buy_orders = list(admin_buy_orders)
        for order in admin_buy_orders:
            order.buyer_display_name = _resolve_main_character_name(order.buyer)
            _attach_order_progress_data(order, "buy", perspective="admin")

    context = {
        "config": config,
        "stock_count": stock_count,
        "total_stock_value": total_stock_value,
        "pending_sell_orders": pending_sell_orders,
        "pending_buy_orders": pending_buy_orders,
        "recent_orders": recent_orders,
        "can_admin": can_admin,
        "superuser_without_material_hub_manage": superuser_without_material_hub_manage,
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

    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    config = _get_material_exchange_config()
    if not config:
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")
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
@tokens_required(scopes="esi-assets.read_assets.v1")
def material_exchange_sell(request, tokens):
    """
    Sell materials TO the hub.
    Member chooses materials + quantities, creates ONE order with multiple items.
    """
    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    config = _get_material_exchange_config()
    if not config:
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")
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

    if sell_assets_progress.get("error") == "esi_down" and not sell_assets_progress.get(
        "retry_after_minutes"
    ):
        cooldown_until = cache.get(
            me_sell_assets_esi_cooldown_key(int(request.user.id))
        )
        if cooldown_until:
            try:
                retry_seconds = max(
                    0, int(float(cooldown_until) - timezone.now().timestamp())
                )
            except (TypeError, ValueError):
                retry_seconds = int(ESI_DOWN_COOLDOWN_SECONDS)
            sell_assets_progress["retry_after_minutes"] = int(
                (retry_seconds + 59) // 60
            )

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

        # Apply market group filter strictly (empty config means no allowed items)
        try:
            allowed_type_ids = _get_allowed_type_ids_for_config(config, "sell")
            if allowed_type_ids is not None:
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

        # Parse submitted quantities from the form. Do not iterate over `user_assets` here:
        # doing so can silently drop items if assets changed or a type was filtered out.
        submitted_quantities: dict[int, int] = {}
        for key, value in request.POST.items():
            if not key.startswith("qty_"):
                continue
            type_id_str = key[4:]
            if not type_id_str.isdigit():
                continue
            qty_raw = (value or "").strip()
            if not qty_raw:
                continue
            try:
                qty = int(qty_raw)
            except Exception:
                continue
            if qty <= 0:
                continue
            submitted_quantities[int(type_id_str)] = qty

        if not submitted_quantities:
            messages.error(
                request,
                _("Please enter a quantity greater than 0 for at least one item."),
            )
            return redirect("indy_hub:material_exchange_sell")

        items_to_create: list[dict] = []
        errors: list[str] = []
        total_payout = Decimal("0")

        price_data = _fetch_fuzzwork_prices(list(submitted_quantities.keys()))

        for type_id, qty in submitted_quantities.items():
            user_qty = user_assets.get(type_id)
            if user_qty is None:
                type_name = get_type_name(type_id)
                errors.append(
                    _(
                        f"{type_name} is no longer available at {config.structure_name}. Please refresh the page and try again."
                    )
                )
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
            if jita_buy <= 0 and jita_sell <= 0:
                type_name = get_type_name(type_id)
                errors.append(_(f"{type_name} has no valid market price."))
                continue

            unit_price = compute_buy_price_from_member(
                config=config,
                jita_buy=jita_buy,
                jita_sell=jita_sell,
            )
            if unit_price <= 0:
                type_name = get_type_name(type_id)
                errors.append(_(f"{type_name} has no valid market price."))
                continue
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

        if errors:
            for err in errors:
                messages.error(request, err)

            # Prevent creating a partial order with an unexpected (lower) total.
            return redirect("indy_hub:material_exchange_sell")

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

            rounded_total_payout = total_payout.quantize(
                Decimal("1"), rounding=ROUND_CEILING
            )
            order.rounded_total_price = rounded_total_payout
            order.save(update_fields=["rounded_total_price", "updated_at"])

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
    user_assets, user_assets_by_character, scope_missing = (
        _fetch_user_assets_for_structure_data(
            request.user,
            config.structure_id,
            allow_refresh=allow_refresh,
        )
    )
    if sell_assets_progress.get("error") == "no_assets_fetched" and (
        has_cached_assets or user_assets
    ):
        sell_assets_progress = dict(sell_assets_progress)
        sell_assets_progress["error"] = None
        cache.set(
            progress_key,
            sell_assets_progress,
            10 * 60,
        )
    if user_assets:
        pre_filter_count = len(user_assets)
        logger.info(
            f"SELL DEBUG: Found {len(user_assets)} unique items in assets before production filter (filter disabled)"
        )

        # Apply market group filter strictly (same as POST + Index)
        try:
            allowed_type_ids = _get_allowed_type_ids_for_config(config, "sell")
            if allowed_type_ids is not None:
                user_assets = {
                    tid: qty
                    for tid, qty in user_assets.items()
                    if tid in allowed_type_ids
                }
                user_assets_by_character = {
                    character_id: {
                        tid: qty
                        for tid, qty in char_assets.items()
                        if tid in allowed_type_ids
                    }
                    for character_id, char_assets in user_assets_by_character.items()
                }
                logger.info(
                    f"SELL DEBUG: {len(user_assets)} items after market group filter"
                )
        except Exception as exc:
            logger.warning("Failed to apply market group filter (GET): %s", exc)

        price_data = _fetch_fuzzwork_prices(list(user_assets.keys()))
        logger.info(f"SELL DEBUG: Got prices for {len(price_data)} items from Fuzzwork")

        def _is_sellable_type(type_id: int) -> bool:
            fuzz_prices = price_data.get(type_id, {})
            jita_buy = fuzz_prices.get("buy") or Decimal(0)
            jita_sell = fuzz_prices.get("sell") or Decimal(0)
            if jita_buy <= 0 and jita_sell <= 0:
                return False
            buy_price = compute_buy_price_from_member(
                config=config,
                jita_buy=jita_buy,
                jita_sell=jita_sell,
            )
            return buy_price > 0

        character_names_map = _resolve_user_character_names_map(request.user)
        sell_page_base_url = reverse("indy_hub:material_exchange_sell")
        character_tabs = []

        sorted_characters = sorted(
            user_assets_by_character.keys(),
            key=lambda character_id: character_names_map.get(
                character_id, str(character_id)
            ).lower(),
        )
        for character_id in sorted_characters:
            character_assets = user_assets_by_character.get(character_id, {})
            tab_count = sum(
                1 for type_id in character_assets if _is_sellable_type(type_id)
            )
            if tab_count <= 0:
                continue
            character_tabs.append(
                {
                    "id": str(character_id),
                    "name": character_names_map.get(
                        character_id, _("Character %(id)s") % {"id": character_id}
                    ),
                    "item_count": tab_count,
                    "url": f"{sell_page_base_url}?character={character_id}",
                }
            )

        selected_character_param = (request.GET.get("character") or "").strip()
        selected_character_id: int | None = None
        if selected_character_param:
            try:
                selected_character_id = int(selected_character_param)
            except (TypeError, ValueError):
                selected_character_id = None

        available_character_ids = {
            int(tab["id"]) for tab in character_tabs if str(tab.get("id", "")).isdigit()
        }

        if selected_character_id in available_character_ids:
            active_character_tab = str(selected_character_id)
        elif character_tabs:
            active_character_tab = str(character_tabs[0]["id"])
            selected_character_id = int(active_character_tab)
        else:
            active_character_tab = ""
            selected_character_id = None

        if selected_character_id and selected_character_id in user_assets_by_character:
            assets_for_display = user_assets_by_character[selected_character_id]
        else:
            assets_for_display = {}

        for type_id, user_qty in assets_for_display.items():
            fuzz_prices = price_data.get(type_id, {})
            jita_buy = fuzz_prices.get("buy") or Decimal(0)
            jita_sell = fuzz_prices.get("sell") or Decimal(0)
            if jita_buy <= 0 and jita_sell <= 0:
                logger.debug(
                    f"SELL DEBUG: Skipping type_id {type_id} - no valid price (buy={jita_buy}, sell={jita_sell})"
                )
                continue

            buy_price = compute_buy_price_from_member(
                config=config,
                jita_buy=jita_buy,
                jita_sell=jita_sell,
            )
            if buy_price <= 0:
                continue
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

    # Show loading spinner only while the refresh task is running.
    assets_refreshing = bool(sell_assets_progress.get("running"))

    # Get corporation name
    corporation_name = _get_corp_name_for_hub(config.corporation_id)

    context = {
        "config": config,
        "materials": materials_with_qty,
        "character_tabs": character_tabs if user_assets else [],
        "active_character_tab": active_character_tab if user_assets else "",
        "corporation_name": corporation_name,
        "assets_refreshing": assets_refreshing,
        "sell_assets_progress": sell_assets_progress,
        "sell_last_update": sell_last_update,
        "sell_next_refresh_minutes": _minutes_until_refresh(sell_last_update),
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
@tokens_required(scopes="esi-assets.read_corporation_assets.v1")
def material_exchange_buy(request, tokens):
    """
    Buy materials FROM the hub.
    Member chooses materials + quantities, creates ONE order with multiple items.
    """
    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    config = _get_material_exchange_config()
    if not config:
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")
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

        # Apply market group filter strictly (empty config means no allowed items)
        try:
            allowed_type_ids = _get_allowed_type_ids_for_config(config, "buy")
            if allowed_type_ids is not None:
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

        # Parse submitted quantities from the form. Do not iterate over `stock_items` here:
        # doing so can silently drop items if stock changed (quantity=0) or an item is no
        # longer visible due to filters.
        submitted_quantities: dict[int, int] = {}
        for key, value in request.POST.items():
            if not key.startswith("qty_"):
                continue
            type_id_str = key[4:]
            if not type_id_str.isdigit():
                continue
            qty_raw = (value or "").strip()
            if not qty_raw:
                continue
            try:
                qty = int(qty_raw)
            except Exception:
                continue
            if qty <= 0:
                continue
            submitted_quantities[int(type_id_str)] = qty

        if not submitted_quantities:
            messages.error(
                request,
                _("Please enter a quantity greater than 0 for at least one item."),
            )
            return redirect("indy_hub:material_exchange_buy")

        stock_by_type_id = {item.type_id: item for item in stock_items}

        items_to_create = []
        errors = []
        total_cost = Decimal("0")

        for type_id, qty in submitted_quantities.items():
            stock_item = stock_by_type_id.get(type_id)
            if stock_item is None:
                type_name = get_type_name(type_id)
                errors.append(
                    _(
                        f"{type_name} is no longer available in stock. Please refresh the page and try again."
                    )
                )
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

            # Prevent creating a partial order with an unexpected (lower) total.
            return redirect("indy_hub:material_exchange_buy")

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

            rounded_total_cost = total_cost.quantize(
                Decimal("1"), rounding=ROUND_CEILING
            )
            order.rounded_total_price = rounded_total_cost
            order.save(update_fields=["rounded_total_price", "updated_at"])

            # Admin notifications are handled by the post_save signal + async task

            messages.success(
                request,
                _(
                    f"Created buy order #{order.id} with {len(items_to_create)} item(s). Total cost: {rounded_total_cost:,.0f} ISK. Awaiting admin approval."
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

    if buy_stock_progress.get("error") == "esi_down" and not buy_stock_progress.get(
        "retry_after_minutes"
    ):
        cooldown_until = cache.get(
            me_buy_stock_esi_cooldown_key(int(config.corporation_id))
        )
        if cooldown_until:
            try:
                retry_seconds = max(
                    0, int(float(cooldown_until) - timezone.now().timestamp())
                )
            except (TypeError, ValueError):
                retry_seconds = int(ESI_DOWN_COOLDOWN_SECONDS)
            buy_stock_progress["retry_after_minutes"] = int((retry_seconds + 59) // 60)

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

    # Apply market group filter strictly (empty config means no allowed items)
    try:
        allowed_type_ids = _get_allowed_type_ids_for_config(config, "buy")
        if allowed_type_ids is not None:
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
        "buy_next_refresh_minutes": _minutes_until_refresh(buy_last_update),
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
@tokens_required(scopes="esi-assets.read_corporation_assets.v1")
def material_exchange_sync_stock(request, tokens):
    """
    Force an immediate sync of stock from ESI corp assets.
    Updates MaterialExchangeStock and redirects back.
    """
    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    if not _get_material_exchange_config():
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")

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
    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    if not _get_material_exchange_config():
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")

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

    _reject_buy_order(order)

    messages.warning(request, _(f"Buy order #{order.id} rejected and buyer notified."))
    return redirect("indy_hub:material_exchange_index")


def _reject_buy_order(order: MaterialExchangeBuyOrder) -> None:
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
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_transactions(request):
    """
    Transaction history and finance reporting.
    Shows all completed transactions with filters and monthly aggregates.
    """
    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    config = _get_material_exchange_config()
    if not config:
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")

    # Filters
    transaction_type = request.GET.get("type", "")  # 'sell', 'buy', or ''
    user_filter = request.GET.get("user", "")

    transactions_qs = config.transactions.select_related(
        "user", "sell_order", "buy_order"
    ).prefetch_related("sell_order__items", "buy_order__items")

    if transaction_type:
        transactions_qs = transactions_qs.filter(transaction_type=transaction_type)
    if user_filter:
        transactions_qs = transactions_qs.filter(user__username__icontains=user_filter)

    transactions_qs = transactions_qs.order_by("-completed_at")

    # Pagination
    paginator = Paginator(transactions_qs, 50)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    transactions = list(page_obj.object_list)

    for tx in transactions:
        if tx.sell_order_id:
            order = tx.sell_order
            tx.has_linked_order = True
            tx.order_reference = order.order_reference or f"SELL-{tx.sell_order_id}"
            tx.order_items = list(order.items.all())
        elif tx.buy_order_id:
            order = tx.buy_order
            tx.has_linked_order = True
            tx.order_reference = order.order_reference or f"BUY-{tx.buy_order_id}"
            tx.order_items = list(order.items.all())
        else:
            tx.has_linked_order = False
            tx.order_reference = ""
            tx.order_items = []

        if not tx.order_items:
            tx.order_items = [tx]

        tx.order_item_count = len(tx.order_items)
        tx.order_total_price = sum(
            (item.total_price for item in tx.order_items),
            Decimal("0"),
        )

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
        "transactions": transactions,
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
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_stats_history(request):
    """Monthly statistics history for Material Exchange transactions."""
    if not _is_material_exchange_enabled():
        messages.warning(request, _("Material Exchange is disabled."))
        return redirect("indy_hub:material_exchange_index")

    config = _get_material_exchange_config()
    if not config:
        messages.warning(request, _("Material Exchange is not configured."))
        return redirect("indy_hub:material_exchange_index")

    period_options = [
        ("1m", _("This month")),
        ("3m", _("Last 3 months")),
        ("6m", _("Last 6 months")),
        ("12m", _("Last 12 months")),
        ("24m", _("Last 24 months")),
        ("all", _("All time")),
    ]
    period_months_map = {
        "1m": 1,
        "3m": 3,
        "6m": 6,
        "12m": 12,
        "24m": 24,
    }
    selected_period = request.GET.get("period", "all")
    if selected_period not in {key for key, _ in period_options}:
        selected_period = "all"

    filtered_transactions = config.transactions.all()
    period_start = None
    if selected_period in period_months_map:
        months = period_months_map[selected_period]
        month_anchor = timezone.now().replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        month_index = (month_anchor.year * 12 + month_anchor.month - 1) - (months - 1)
        start_year = month_index // 12
        start_month = (month_index % 12) + 1
        period_start = month_anchor.replace(year=start_year, month=start_month)
        filtered_transactions = filtered_transactions.filter(
            completed_at__gte=period_start
        )

    monthly_rows = (
        filtered_transactions.annotate(month=TruncMonth("completed_at"))
        .values("month")
        .annotate(
            total_sell_volume=Sum(
                "total_price", filter=Q(transaction_type="sell"), default=0
            ),
            total_buy_volume=Sum(
                "total_price", filter=Q(transaction_type="buy"), default=0
            ),
            sell_orders=Count("id", filter=Q(transaction_type="sell")),
            buy_orders=Count("id", filter=Q(transaction_type="buy")),
        )
        .order_by("month")
    )

    chart_labels = []
    buy_volumes = []
    sell_volumes = []
    transaction_counts = []

    total_buy_volume = Decimal("0")
    total_sell_volume = Decimal("0")
    total_transactions = 0

    for row in monthly_rows:
        month = row.get("month")
        if not month:
            continue
        buy_volume = row.get("total_buy_volume") or Decimal("0")
        sell_volume = row.get("total_sell_volume") or Decimal("0")
        buy_count = row.get("buy_orders") or 0
        sell_count = row.get("sell_orders") or 0

        chart_labels.append(month.strftime("%Y-%m"))
        buy_volumes.append(float(buy_volume))
        sell_volumes.append(float(sell_volume))
        transaction_counts.append(buy_count + sell_count)

        total_buy_volume += buy_volume
        total_sell_volume += sell_volume
        total_transactions += buy_count + sell_count

    user_stats = (
        filtered_transactions.values("user__username")
        .annotate(
            buy_volume=Sum("total_price", filter=Q(transaction_type="buy"), default=0),
            sell_volume=Sum(
                "total_price", filter=Q(transaction_type="sell"), default=0
            ),
            buy_orders=Count("id", filter=Q(transaction_type="buy")),
            sell_orders=Count("id", filter=Q(transaction_type="sell")),
        )
        .order_by("user__username")
    )

    user_rows = []
    for row in user_stats:
        buy_volume = row.get("buy_volume") or Decimal("0")
        sell_volume = row.get("sell_volume") or Decimal("0")
        buy_orders = row.get("buy_orders") or 0
        sell_orders = row.get("sell_orders") or 0

        user_rows.append(
            {
                "username": row.get("user__username") or "-",
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "buy_orders": buy_orders,
                "sell_orders": sell_orders,
                "total_orders": buy_orders + sell_orders,
                "net_flow": buy_volume - sell_volume,
            }
        )

    top_user_stats = sorted(
        user_rows,
        key=lambda item: item["buy_volume"] + item["sell_volume"],
        reverse=True,
    )[:10]

    context = {
        "config": config,
        "chart_labels": chart_labels,
        "buy_volumes": buy_volumes,
        "sell_volumes": sell_volumes,
        "transaction_counts": transaction_counts,
        "months_count": len(chart_labels),
        "total_buy_volume": total_buy_volume,
        "total_sell_volume": total_sell_volume,
        "total_transactions": total_transactions,
        "top_user_stats": top_user_stats,
        "period_options": period_options,
        "selected_period": selected_period,
        "period_start": period_start,
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

    return render(request, "indy_hub/material_exchange/stats_history.html", context)


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
