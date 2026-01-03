"""Material Exchange Configuration views."""

# Standard Library
import logging
from decimal import Decimal

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from esi.clients import EsiClientProvider
from esi.views import sso_redirect

from ..decorators import indy_hub_permission_required
from ..models import MaterialExchangeConfig
from ..services.asset_cache import (
    get_corp_assets_cached,
    get_corp_divisions_cached,
    resolve_structure_names,
)

esi = EsiClientProvider()
logger = logging.getLogger(__name__)


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_request_divisions_token(request):
    """Request ESI token with divisions scope, then redirect back to config."""
    return sso_redirect(
        request,
        scopes="esi-corporations.read_divisions.v1",
        return_to="indy_hub:material_exchange_config",
    )


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_request_all_scopes(request):
    """
    Request all Material Exchange required ESI scopes at once.

    Required scopes:
    - esi-assets.read_corporation_assets.v1 (for structures)
    - esi-corporations.read_divisions.v1 (for hangar divisions)
    - esi-contracts.read_corporation_contracts.v1 (for contract validation)
    - esi-universe.read_structures.v1 (for structure names)
    """
    scopes = " ".join(
        [
            "esi-assets.read_corporation_assets.v1",
            "esi-corporations.read_divisions.v1",
            "esi-contracts.read_corporation_contracts.v1",
            "esi-universe.read_structures.v1",
        ]
    )
    return sso_redirect(
        request,
        scopes=scopes,
        return_to="indy_hub:material_exchange_config",
    )


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_request_contracts_scope(request):
    """Request ESI token with contracts scope, then redirect back to config."""
    return sso_redirect(
        request,
        scopes="esi-contracts.read_corporation_contracts.v1",
        return_to="indy_hub:material_exchange_config",
    )


def _get_token_for_corp(user, corp_id, scope, require_corporation_token: bool = False):
    """Return a valid token for the given corp that has the scope.

    If require_corporation_token is True, only return corporation-type tokens
    that belong to the selected corporation. Otherwise, prefer those and
    fall back to a character token that belongs to the corp.
    """
    # Alliance Auth
    from esi.models import Token

    # Important: require_scopes expects an iterable of scopes
    tokens = Token.objects.filter(user=user).require_scopes([scope]).require_valid()
    tokens = list(tokens)
    if not tokens:
        logger.debug(
            f"_get_token_for_corp: user={user.username}, corp_id={corp_id}, scope={scope} -> no valid tokens with scope"
        )
    else:
        logger.debug(
            f"_get_token_for_corp: user={user.username}, corp_id={corp_id}, "
            f"scope={scope}, require_corp={require_corporation_token}, "
            f"found {len(tokens)} valid tokens with scope"
        )

    # Cache character corp lookups to avoid extra ESI calls
    char_corp_cache: dict[int, int] = {}

    def _character_matches(token) -> bool:
        char_id = getattr(token, "character_id", None)
        if not char_id:
            return False
        # Prefer cached character relation if available to avoid ESI calls
        try:
            char_obj = getattr(token, "character", None)
            if char_obj and getattr(char_obj, "corporation_id", None) is not None:
                return int(char_obj.corporation_id) == int(corp_id)
        except Exception:
            pass
        if char_id in char_corp_cache:
            return char_corp_cache[char_id] == int(corp_id)
        try:
            char_info = esi.client.Character.get_characters_character_id(
                character_id=char_id
            ).results()
            char_corp_cache[char_id] = int(char_info.get("corporation_id", 0))
            return char_corp_cache[char_id] == int(corp_id)
        except Exception:
            return False

    # Prefer corporation tokens that belong to the selected corp
    for token in tokens:
        if getattr(token, "token_type", "") != Token.TOKEN_TYPE_CORPORATION:
            continue
        corp_attr = getattr(token, "corporation_id", None)
        logger.debug(
            f"  Checking corp token id={token.id}: corp_attr={corp_attr}, "
            f"type={getattr(token, 'token_type', '')}, char_id={token.character_id}"
        )
        if corp_attr is not None and int(corp_attr) == int(corp_id):
            logger.info(
                f"Found matching corp token id={token.id} for corp_id={corp_id}"
            )
            return token
        # For corp tokens missing corp_attr, accept if backing character belongs to corp
        if corp_attr is None and _character_matches(token):
            return token

    # If a corporation token is required, still try character tokens as fallback
    # (character tokens from the corp can still access corp endpoints if the character has roles)
    for token in tokens:
        if _character_matches(token):
            logger.info(
                f"Using character token id={token.id} (char_id={token.character_id}) for corp_id={corp_id}"
            )
            return token

    # No suitable token for this corporation
    logger.warning(
        f"No token found (corp or character): user={user.username}, corp_id={corp_id}, "
        f"scope={scope}, checked {len(tokens)} tokens"
    )
    return None


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_config(request):
    """
    Material Exchange configuration page.
    Allows admins to configure corp, structure, and pricing.
    """
    config = MaterialExchangeConfig.objects.first()

    # Get available corporations from user's ESI tokens
    available_corps = _get_user_corporations(request.user)

    # Get structures if corp is selected
    available_structures = []
    hangar_divisions = {}
    division_scope_missing = False
    assets_scope_missing = False
    if config and config.corporation_id:
        available_structures, assets_scope_missing = _get_corp_structures(
            request.user, config.corporation_id
        )
        hangar_divisions, division_scope_missing = _get_corp_hangar_divisions(
            request.user, config.corporation_id
        )

    # Removed market group selection UI: filtering is now hardcoded to parent market group 533

    if request.method == "POST":
        return _handle_config_save(request, config)

    context = {
        "config": config,
        "available_corps": available_corps,
        "available_structures": available_structures,
        "assets_scope_missing": assets_scope_missing,
        "hangar_divisions": (
            hangar_divisions
            if (hangar_divisions or division_scope_missing)
            else {i: f"Hangar Division {i}" for i in range(1, 8)}
        ),
        "division_scope_missing": division_scope_missing,
        # Market groups selection removed
    }

    from .navigation import build_nav_context

    context.update(
        build_nav_context(
            request.user,
            active_tab="material_hub",
            can_manage_corp=request.user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            ),
        )
    )
    context["back_to_overview_url"] = reverse("indy_hub:index")
    context["material_exchange_enabled"] = MaterialExchangeConfig.objects.filter(
        is_active=True
    ).exists()

    return render(request, "indy_hub/material_exchange/config.html", context)


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_toggle_active(request):
    """Toggle Material Exchange availability from settings page."""

    if request.method != "POST":
        return redirect("indy_hub:settings_hub")

    next_url = request.POST.get("next") or reverse("indy_hub:settings_hub")
    config = MaterialExchangeConfig.objects.first()
    if not config:
        messages.error(
            request,
            _("Configure the Material Exchange before enabling or disabling it."),
        )
        return redirect(next_url)

    desired_active = request.POST.get("is_active") == "on"
    if config.is_active == desired_active:
        messages.info(
            request,
            _("No change: Material Exchange is already {state}.").format(
                state=_("enabled") if config.is_active else _("disabled")
            ),
        )
        return redirect(next_url)

    config.is_active = desired_active
    config.save(update_fields=["is_active", "updated_at"])
    if desired_active:
        messages.success(request, _("Material Exchange enabled."))
    else:
        messages.success(request, _("Material Exchange disabled."))

    return redirect(next_url)


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_get_structures(request, corp_id):
    """
    AJAX endpoint to get structures for a given corporation.
    Returns JSON list of structures.
    """
    # Django
    from django.http import JsonResponse

    structures, assets_scope_missing = _get_corp_structures(request.user, corp_id)
    hangar_divisions, division_scope_missing = _get_corp_hangar_divisions(
        request.user, corp_id
    )

    return JsonResponse(
        {
            "structures": [
                {"id": s["id"], "name": s["name"], "flags": s.get("flags", [])}
                for s in structures
            ],
            "hangar_divisions": hangar_divisions,
            "division_scope_missing": division_scope_missing,
            "assets_scope_missing": assets_scope_missing,
        }
    )


def _get_user_corporations(user):
    """
    Get list of corporations the user has ESI access to.
    Returns list of dicts with corp_id and corp_name.
    """
    # Alliance Auth
    from esi.models import Token

    corporations = []
    seen_corps = set()

    # Only hit ESI once per unique character and cache corp lookups briefly.
    cache_ttl = 10 * 60  # 10 minutes
    character_ids = set()
    try:
        tokens = Token.objects.filter(user=user)
        for token in tokens:
            if token.character_id:
                character_ids.add(int(token.character_id))
    except Exception:
        logger.warning("Failed to list tokens for user %s", user.username)
        return corporations

    for char_id in character_ids:
        try:
            char_info = esi.client.Character.get_characters_character_id(
                character_id=char_id
            ).results()
        except Exception as exc:
            logger.debug("Skip char %s (character lookup failed: %s)", char_id, exc)
            continue

        corp_id = char_info.get("corporation_id")
        if not corp_id or corp_id in seen_corps:
            continue

        cache_key = f"indy_hub:corp_info:{corp_id}"
        corp_info = cache.get(cache_key)
        if not corp_info:
            try:
                corp_info = esi.client.Corporation.get_corporations_corporation_id(
                    corporation_id=corp_id
                ).results()
                cache.set(cache_key, corp_info, cache_ttl)
            except Exception as exc:
                logger.debug("Skip corp %s (lookup failed: %s)", corp_id, exc)
                continue

        corporations.append(
            {
                "id": corp_id,
                "name": corp_info.get("name", f"Corp {corp_id}"),
                "ticker": corp_info.get("ticker", ""),
            }
        )
        seen_corps.add(corp_id)

    return corporations


def _get_corp_structures(user, corp_id):
    """Get list of player structures for a corporation using cached corp assets."""

    structures: list[dict] = []
    corp_assets, assets_scope_missing = get_corp_assets_cached(int(corp_id))

    # Need a character with universe.read_structures to resolve names
    token_for_names = _get_token_for_corp(
        user, corp_id, "esi-universe.read_structures.v1"
    )

    # Fallback: use any corp member token with the scope if the current user lacks it
    if not token_for_names:
        try:
            # Alliance Auth
            from esi.models import Token

            token_for_names = (
                Token.objects.filter(character__corporation_id=corp_id)
                .require_scopes(["esi-universe.read_structures.v1"])
                .require_valid()
                .order_by("-created")
                .first()
            )
        except Exception:
            token_for_names = None

    character_id_for_names = (
        getattr(token_for_names, "character_id", None) if token_for_names else None
    )

    # Prefer structure ids from OfficeFolder entries (Upwell offices)
    loc_ids: set[int] = set()
    for asset in corp_assets:
        if str(asset.get("location_flag") or "") != "OfficeFolder":
            continue
        try:
            loc_id = int(asset.get("location_id"))
        except (TypeError, ValueError):
            continue
        if loc_id:
            loc_ids.add(loc_id)

    # Fallback: stations and older locations may not have OfficeFolder entries
    if not loc_ids:
        for asset in corp_assets:
            flag = str(asset.get("location_flag", "") or "")
            if not flag.startswith("CorpSAG"):
                continue
            try:
                loc_id = int(asset.get("location_id"))
            except (TypeError, ValueError):
                continue
            if loc_id:
                loc_ids.add(loc_id)

    structure_names = resolve_structure_names(
        sorted(loc_ids), character_id_for_names, int(corp_id)
    )
    for loc_id in sorted(loc_ids):
        structures.append(
            {
                "id": loc_id,
                "name": structure_names.get(loc_id) or f"Structure {loc_id}",
                "flags": [],
            }
        )

    if structures:
        return structures, assets_scope_missing

    return (
        [
            {
                "id": 0,
                "name": _("⚠ No corporation assets available (ESI scope missing)"),
            }
        ],
        assets_scope_missing,
    )


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_request_assets_token(request):
    """Request ESI token with corp assets scope, then redirect back to config."""
    return sso_redirect(
        request,
        scopes="esi-assets.read_corporation_assets.v1",
        return_to="indy_hub:material_exchange_config",
    )


def _get_corp_hangar_divisions(user, corp_id):
    """Get hangar division names from cached ESI data."""

    default_divisions = {
        1: _("Hangar Division 1"),
        2: _("Hangar Division 2"),
        3: _("Hangar Division 3"),
        4: _("Hangar Division 4"),
        5: _("Hangar Division 5"),
        6: _("Hangar Division 6"),
        7: _("Hangar Division 7"),
    }

    divisions, scope_missing = get_corp_divisions_cached(int(corp_id))
    if divisions:
        default_divisions.update(divisions)
    return default_divisions, scope_missing


def _handle_config_save(request, existing_config):
    """Handle POST request to save Material Exchange configuration."""

    corporation_id = request.POST.get("corporation_id")
    structure_id = request.POST.get("structure_id")
    structure_name = request.POST.get("structure_name", "")
    hangar_division = request.POST.get("hangar_division")
    sell_markup_percent = request.POST.get("sell_markup_percent", "0")
    sell_markup_base = request.POST.get("sell_markup_base", "buy")
    buy_markup_percent = request.POST.get("buy_markup_percent", "5")
    buy_markup_base = request.POST.get("buy_markup_base", "buy")
    # Market group selections removed; filtering is hardcoded
    raw_is_active = request.POST.get("is_active")
    if raw_is_active is None and existing_config is not None:
        is_active = existing_config.is_active
    else:
        is_active = raw_is_active == "on"

    # Validation
    try:
        if not corporation_id:
            raise ValueError("Corporation ID is required")
        if not structure_id:
            raise ValueError("Structure ID is required")
        if not hangar_division:
            raise ValueError(
                "Hangar division is required. Please ensure the divisions scope token is added and a division is selected."
            )

        corporation_id = int(corporation_id)
        structure_id = int(structure_id)
        hangar_division = int(hangar_division)
        sell_markup_percent = Decimal(sell_markup_percent)
        buy_markup_percent = Decimal(buy_markup_percent)
        # No market group parsing required

        if not (1 <= hangar_division <= 7):
            raise ValueError("Hangar division must be between 1 and 7")

    except (ValueError, TypeError) as e:
        messages.error(request, _("Invalid configuration values: {}").format(e))
        return redirect("indy_hub:material_exchange_config")

    # Save or update config
    with transaction.atomic():
        # Best-effort: resolve name server-side to avoid persisting placeholders.
        if corporation_id and structure_id:
            try:
                token_for_names = _get_token_for_corp(
                    request.user, corporation_id, "esi-universe.read_structures.v1"
                )
                character_id_for_names = (
                    getattr(token_for_names, "character_id", None)
                    if token_for_names
                    else None
                )
                resolved = resolve_structure_names(
                    [int(structure_id)], character_id_for_names, int(corporation_id)
                ).get(int(structure_id))
                if resolved and not str(resolved).startswith("Structure "):
                    structure_name = resolved
            except Exception:
                pass

        if existing_config:
            existing_config.corporation_id = corporation_id
            existing_config.structure_id = structure_id
            existing_config.structure_name = structure_name
            existing_config.hangar_division = hangar_division
            existing_config.sell_markup_percent = sell_markup_percent
            existing_config.sell_markup_base = sell_markup_base
            existing_config.buy_markup_percent = buy_markup_percent
            existing_config.buy_markup_base = buy_markup_base
            # Market group filters removed
            existing_config.is_active = is_active
            existing_config.save()
            messages.success(
                request, _("Material Exchange configuration updated successfully.")
            )
        else:
            MaterialExchangeConfig.objects.create(
                corporation_id=corporation_id,
                structure_id=structure_id,
                structure_name=structure_name,
                hangar_division=hangar_division,
                sell_markup_percent=sell_markup_percent,
                sell_markup_base=sell_markup_base,
                buy_markup_percent=buy_markup_percent,
                buy_markup_base=buy_markup_base,
                # Market group filters removed
                is_active=is_active,
            )
            messages.success(
                request, _("Material Exchange configuration created successfully.")
            )

    return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_debug_tokens(request, corp_id):
    """Debug endpoint: list user's tokens and scopes relevant to a corporation.

    Query params:
    - scope: optional scope name to filter tokens (e.g., "esi-assets.read_corporation_assets.v1")
    """
    # Django
    from django.http import JsonResponse

    # Alliance Auth
    from esi.models import Token

    scope = request.GET.get("scope")
    qs = Token.objects.filter(user=request.user)
    if scope:
        qs = qs.require_scopes([scope])
    qs = qs.require_valid()

    results = []

    # Reuse character corp check
    def _character_matches(token) -> bool:
        char_id = getattr(token, "character_id", None)
        if not char_id:
            return False
        try:
            char_obj = getattr(token, "character", None)
            if char_obj and getattr(char_obj, "corporation_id", None) is not None:
                return int(char_obj.corporation_id) == int(corp_id)
        except Exception:
            pass
        try:
            char_info = esi.client.Character.get_characters_character_id(
                character_id=char_id
            ).results()
            return int(char_info.get("corporation_id", 0)) == int(corp_id)
        except Exception:
            return False

    for t in qs:
        try:
            scope_names = list(t.scopes.values_list("name", flat=True))
        except Exception:
            scope_names = []
        results.append(
            {
                "id": t.id,
                "type": getattr(t, "token_type", ""),
                "corporation_id": getattr(t, "corporation_id", None),
                "character_id": getattr(t, "character_id", None),
                "belongs_to_corp": (
                    (
                        getattr(t, "corporation_id", None) is not None
                        and int(getattr(t, "corporation_id")) == int(corp_id)
                    )
                    or _character_matches(t)
                ),
                "scopes": scope_names,
            }
        )

    return JsonResponse(
        {"corp_id": int(corp_id), "scope_filter": scope or None, "tokens": results}
    )
