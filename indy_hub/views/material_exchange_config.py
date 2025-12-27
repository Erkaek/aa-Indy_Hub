"""Material Exchange Configuration views."""

# Standard Library
import logging
from decimal import Decimal

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from esi.clients import EsiClientProvider
from esi.views import sso_redirect

from ..decorators import indy_hub_permission_required
from ..models import MaterialExchangeConfig

esi = EsiClientProvider()
logger = logging.getLogger(__name__)


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
def material_exchange_request_divisions_token(request):
    """Request ESI token with divisions scope, then redirect back to config."""
    return sso_redirect(
        request,
        scopes="esi-corporations.read_divisions.v1",
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
@indy_hub_permission_required("can_manage_material_exchange")
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
    }

    return render(request, "indy_hub/material_exchange/config.html", context)


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
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

    try:
        # Get all user tokens
        tokens = Token.objects.filter(user=user)

        for token in tokens:
            # Get character info
            try:
                char_info = esi.client.Character.get_characters_character_id(
                    character_id=token.character_id
                ).results()

                corp_id = char_info.get("corporation_id")
                if corp_id and corp_id not in seen_corps:
                    # Get corp name
                    corp_info = esi.client.Corporation.get_corporations_corporation_id(
                        corporation_id=corp_id
                    ).results()

                    corporations.append(
                        {
                            "id": corp_id,
                            "name": corp_info.get("name", f"Corp {corp_id}"),
                            "ticker": corp_info.get("ticker", ""),
                        }
                    )
                    seen_corps.add(corp_id)

            except Exception:
                # Skip tokens with errors
                continue

    except Exception as e:
        messages.warning(None, f"Error fetching corporations from ESI: {e}")

    return corporations


def _get_corp_structures(user, corp_id):
    """
    Get list of structures where the corporation has assets.
    This includes structures owned by the corp AND structures where corp has assets.
    """
    from ..utils.eve import is_station_id, resolve_location_name

    try:
        # Third Party
        from corptools.models import CorpAsset
    except Exception:
        CorpAsset = None

    structures = []
    assets_scope_missing = False
    location_ids = set()
    location_flags_by_id: dict[int, set[str]] = {}

    # Only use a token that actually belongs to the corporation and has structure scope for name lookups
    token_for_names = _get_token_for_corp(
        user,
        corp_id,
        "esi-universe.read_structures.v1",
        require_corporation_token=False,
    )
    logger.info(
        f"material_exchange_config: names token corp_id={corp_id}, token_id={(getattr(token_for_names, 'id', None))}, token_type={(getattr(token_for_names, 'token_type', None))}"
    )

    # Use cached corp assets from corptools when available to avoid ESI rate limits
    if CorpAsset:
        try:
            qs = (
                CorpAsset.objects.filter(corporation_id=corp_id)
                .exclude(location_type__iexact="item")
                .values_list("location_id", "location_flag")
            )
            for loc_id, loc_flag in qs:
                try:
                    int_id = int(loc_id)
                except (TypeError, ValueError):
                    continue
                location_ids.add(int_id)
                if loc_flag:
                    location_flags_by_id.setdefault(int_id, set()).add(str(loc_flag))
        except Exception:
            pass

    try:
        # Fallback to live ESI if nothing is cached locally
        if not location_ids:
            required_scope = "esi-assets.read_corporation_assets.v1"

            # Get all tokens with the required scope
            # Alliance Auth
            from esi.models import Token

            potential_tokens = list(
                Token.objects.filter(user=user)
                .require_scopes([required_scope])
                .require_valid()
            )

            if not potential_tokens:
                assets_scope_missing = True
                logger.info(
                    f"material_exchange_config: missing corp assets token for corp_id={corp_id} (scope={required_scope})"
                )
                return (
                    [
                        {
                            "id": 0,
                            "name": _(
                                "⚠ Missing valid corporation token with scope: esi-assets.read_corporation_assets.v1"
                            ),
                        }
                    ],
                    assets_scope_missing,
                )

            # Filter tokens to only those from the target corporation using cached data
            # Alliance Auth
            from allianceauth.eveonline.models import EveCharacter

            # Get cached characters in bulk to avoid repeated queries
            character_ids = [t.character_id for t in potential_tokens]
            cached_chars = {
                char.character_id: char
                for char in EveCharacter.objects.filter(character_id__in=character_ids)
            }

            corp_tokens = []
            for token in potential_tokens:
                char_id = token.character_id
                # Check if in cache first
                if char_id in cached_chars:
                    if cached_chars[char_id].corporation_id == int(corp_id):
                        corp_tokens.append(token)
                # If not in cache, skip it (don't fall back to slow ESI call)
                # The EveCharacter data should be up-to-date from Alliance Auth

            # Try each corp token until one works (has the required corp roles)
            assets_data = None
            last_error = None
            for token in corp_tokens:
                try:
                    # Try to fetch assets with this token
                    assets_data = (
                        esi.client.Assets.get_corporations_corporation_id_assets(
                            corporation_id=corp_id, token=token.valid_access_token()
                        ).results()
                    )
                    logger.info(
                        f"material_exchange_config: fetched corp assets via ESI for corp_id={corp_id}, "
                        f"token_id={token.id}, character_id={token.character_id}"
                    )
                    break  # Success! Use this token
                except Exception as e:
                    # Third Party
                    from bravado.exception import HTTPError

                    status_code = getattr(
                        getattr(e, "response", None), "status_code", None
                    )
                    if isinstance(e, HTTPError) and status_code == 403:
                        # This character doesn't have the required roles, try next token
                        logger.debug(
                            f"Token {token.id} (char_id={token.character_id}) lacks corp roles for assets, trying next..."
                        )
                        last_error = e
                        continue
                    else:
                        # Other error, save and try next
                        last_error = e
                        continue

            if assets_data is None:
                # All tokens failed
                assets_scope_missing = True
                error_name = _(
                    "⚠ None of your characters have the required corporation roles for assets. "
                    "Please authorize with a character that has Director or Junior Accountant role."
                )
                if last_error:
                    logger.warning(
                        f"material_exchange_config: All tokens failed for corp_id={corp_id}, last_error={last_error}"
                    )
                return (
                    [
                        {
                            "id": 0,
                            "name": error_name,
                        }
                    ],
                    assets_scope_missing,
                )

            for asset in assets_data:
                location_id = asset.get("location_id")
                if not location_id:
                    continue
                if asset.get("item_id") == location_id:
                    continue  # nested container
                try:
                    int_id = int(location_id)
                except (TypeError, ValueError):
                    continue
                location_ids.add(int_id)
                loc_flag = asset.get("location_flag")
                if loc_flag:
                    location_flags_by_id.setdefault(int_id, set()).add(str(loc_flag))

        logger.info(
            f"material_exchange_config: collected {len(location_ids)} location_ids for corp_id={corp_id}"
        )

        if not location_ids:
            return (
                [
                    {
                        "id": 0,
                        "name": _("⚠ No corporation assets found"),
                    }
                ],
                assets_scope_missing,
            )

        for location_id in location_ids:
            if not token_for_names and not is_station_id(location_id):
                # Avoid public structure lookups when we lack structure scope
                location_name = f"Structure {location_id}"
            else:
                location_name = resolve_location_name(
                    location_id,
                    character_id=(
                        token_for_names.character_id if token_for_names else None
                    ),
                    owner_user_id=user.pk,
                    allow_public=is_station_id(location_id),
                )

            structures.append(
                {
                    "id": location_id,
                    "name": location_name or f"Structure {location_id}",
                    "flags": sorted(location_flags_by_id.get(location_id, set())),
                }
            )

        structures.sort(key=lambda x: x["name"])

    except Exception as e:
        return (
            [
                {
                    "id": 0,
                    "name": f"⚠ Error: {str(e)}",
                }
            ],
            assets_scope_missing,
        )

    return structures, assets_scope_missing


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
def material_exchange_request_assets_token(request):
    """Request ESI token with corp assets scope, then redirect back to config."""
    return sso_redirect(
        request,
        scopes="esi-assets.read_corporation_assets.v1",
        return_to="indy_hub:material_exchange_config",
    )


def _get_corp_hangar_divisions(user, corp_id):
    """Get hangar division names and whether scope was missing."""
    # Standard Library
    import logging

    logger = logging.getLogger(__name__)

    # Default names if ESI fails or no custom names set
    default_divisions = {
        1: _("Hangar Division 1"),
        2: _("Hangar Division 2"),
        3: _("Hangar Division 3"),
        4: _("Hangar Division 4"),
        5: _("Hangar Division 5"),
        6: _("Hangar Division 6"),
        7: _("Hangar Division 7"),
    }

    scope_missing = False

    try:
        # ESI divisions endpoint requires corp divisions scope
        required_scope = "esi-corporations.read_divisions.v1"

        # Get all tokens with the required scope
        # Alliance Auth
        from esi.models import Token

        potential_tokens = list(
            Token.objects.filter(user=user)
            .require_scopes([required_scope])
            .require_valid()
        )

        if not potential_tokens:
            scope_missing = True
            logger.info(
                f"material_exchange_config: missing corp divisions token for corp_id={corp_id} (scope={required_scope})"
            )
            return {}, scope_missing

        # Filter tokens to only those from the target corporation using cached data
        # Alliance Auth
        from allianceauth.eveonline.models import EveCharacter

        # Get cached characters in bulk to avoid repeated queries
        character_ids = [t.character_id for t in potential_tokens]
        cached_chars = {
            char.character_id: char
            for char in EveCharacter.objects.filter(character_id__in=character_ids)
        }

        corp_tokens = []
        for token in potential_tokens:
            char_id = token.character_id
            # Check if in cache first
            if char_id in cached_chars:
                if cached_chars[char_id].corporation_id == int(corp_id):
                    corp_tokens.append(token)
            # If not in cache, skip it (don't fall back to slow ESI call)
            # The EveCharacter data should be up-to-date from Alliance Auth

        # Try each corp token until one works (has the required corp roles)
        divisions_data = None
        for token in corp_tokens:
            try:
                # Get corp divisions
                divisions_data = (
                    esi.client.Corporation.get_corporations_corporation_id_divisions(
                        corporation_id=corp_id, token=token.valid_access_token()
                    ).results()
                )
                logger.info(
                    f"material_exchange_config: fetched corp divisions via ESI for corp_id={corp_id}, "
                    f"token_id={token.id}, character_id={token.character_id}"
                )

                # Parse hangar division names
                hangar_divisions = divisions_data.get("hangar", [])
                for division_info in hangar_divisions:
                    division_num = division_info.get("division")
                    division_name = division_info.get("name")
                    if division_num and division_name:
                        default_divisions[division_num] = division_name

                break  # Success! Stop trying other tokens

            except Exception as e:
                # Third Party
                from bravado.exception import HTTPError

                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if isinstance(e, HTTPError) and status_code == 403:
                    # This character doesn't have the required roles, try next token
                    logger.debug(
                        f"Token {token.id} (char_id={token.character_id}) lacks corp roles for divisions, trying next..."
                    )
                    continue
                else:
                    logger.warning(
                        f"Could not fetch corp division names with token {token.id}: {e}"
                    )
                    continue

    except Exception as e:
        logger.warning(f"Error getting corp hangar divisions: {e}")

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
    is_active = request.POST.get("is_active") == "on"

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

        if not (1 <= hangar_division <= 7):
            raise ValueError("Hangar division must be between 1 and 7")

    except (ValueError, TypeError) as e:
        messages.error(request, _("Invalid configuration values: {}").format(e))
        return redirect("indy_hub:material_exchange_config")

    # Save or update config
    with transaction.atomic():
        if existing_config:
            existing_config.corporation_id = corporation_id
            existing_config.structure_id = structure_id
            existing_config.structure_name = structure_name
            existing_config.hangar_division = hangar_division
            existing_config.sell_markup_percent = sell_markup_percent
            existing_config.sell_markup_base = sell_markup_base
            existing_config.buy_markup_percent = buy_markup_percent
            existing_config.buy_markup_base = buy_markup_base
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
                is_active=is_active,
            )
            messages.success(
                request, _("Material Exchange configuration created successfully.")
            )

    return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_manage_material_exchange")
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
