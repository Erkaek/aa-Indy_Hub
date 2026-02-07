"""Material Exchange Configuration views."""

# Standard Library
from decimal import Decimal, InvalidOperation

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi.views import sso_redirect

# AA Example App
from indy_hub.services.providers import esi_provider

from ..decorators import indy_hub_permission_required, tokens_required
from ..models import CharacterRoles, MaterialExchangeConfig, MaterialExchangeSettings
from ..services.asset_cache import (
    get_corp_assets_cached,
    get_corp_divisions_cached,
    resolve_structure_names,
)

esi = esi_provider
logger = get_extension_logger(__name__)


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
    from allianceauth.eveonline.models import EveCharacter
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
        try:
            stored = EveCharacter.objects.get_character_by_id(int(char_id))
            if stored is None:
                stored = EveCharacter.objects.create_character(int(char_id))
            if stored and getattr(stored, "corporation_id", None) is not None:
                return int(stored.corporation_id) == int(corp_id)
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
@tokens_required(scopes="esi-characters.read_corporation_roles.v1")
def material_exchange_config(request, tokens):
    """
    Material Exchange configuration page.
    Allows admins to configure corp, structure, and pricing.
    """
    config = MaterialExchangeConfig.objects.first()

    # Get available corporations from user's ESI tokens
    available_corps = _get_user_corporations(request.user)

    # Do NOT load structures on initial page load - wait for AJAX after corp selection
    available_structures = []
    hangar_divisions = {}
    division_scope_missing = False
    assets_scope_missing = False
    current_corp_ticker = ""
    current_hangar_name = ""

    if config and getattr(config, "corporation_id", None):
        try:
            hangar_divisions, division_scope_missing = _get_corp_hangar_divisions(
                request.user, config.corporation_id
            )
            current_hangar_name = hangar_divisions.get(
                int(config.hangar_division),
                f"Hangar Division {config.hangar_division}",
            )
        except Exception:
            current_hangar_name = f"Hangar Division {config.hangar_division}"

        for corp in available_corps:
            if corp.get("id") == config.corporation_id:
                current_corp_ticker = corp.get("ticker", "")
                break

    if request.method == "POST":
        return _handle_config_save(request, config)

    market_group_choices: list[dict[str, str | int]] = []
    try:
        market_group_choices = _get_industry_market_group_choices(depth_from_root=2)
    except Exception as exc:
        logger.warning("Failed to build market group choices: %s", exc)

    allowed_choice_ids = set(_get_industry_market_group_choice_ids(depth_from_root=2))
    selected_market_groups_buy = (
        [
            gid
            for gid in (list(getattr(config, "allowed_market_groups_buy", []) or []))
            if not allowed_choice_ids or int(gid) in allowed_choice_ids
        ]
        if config
        else []
    )
    selected_market_groups_sell = (
        [
            gid
            for gid in (list(getattr(config, "allowed_market_groups_sell", []) or []))
            if not allowed_choice_ids or int(gid) in allowed_choice_ids
        ]
        if config
        else []
    )

    market_group_search_index = {}
    try:
        market_group_search_index = _get_industry_market_group_search_index(
            depth_from_root=2
        )
    except Exception as exc:
        logger.warning("Failed to build market group search index: %s", exc)

    context = {
        "config": config,
        "available_corps": available_corps,
        "available_structures": available_structures,
        "assets_scope_missing": assets_scope_missing,
        "current_corp_ticker": current_corp_ticker,
        "current_hangar_name": current_hangar_name,
        "hangar_divisions": (
            hangar_divisions
            if (hangar_divisions or division_scope_missing)
            else {i: f"Hangar Division {i}" for i in range(1, 8)}
        ),
        "division_scope_missing": division_scope_missing,
        "market_group_choices": market_group_choices,
        "selected_market_groups_buy": selected_market_groups_buy,
        "selected_market_groups_sell": selected_market_groups_sell,
        "market_group_search_index": market_group_search_index,
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
    context["material_exchange_enabled"] = (
        MaterialExchangeSettings.get_solo().is_enabled
    )

    return render(request, "indy_hub/material_exchange/config.html", context)


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_toggle_active(request):
    """Toggle Material Exchange availability from settings page."""

    if request.method != "POST":
        return redirect("indy_hub:settings_hub")

    next_url = request.POST.get("next") or reverse("indy_hub:settings_hub")
    settings_obj = MaterialExchangeSettings.get_solo()

    desired_active = request.POST.get("is_active") == "on"
    if settings_obj.is_enabled == desired_active:
        messages.info(
            request,
            _("No change: Material Exchange is already {state}.").format(
                state=_("enabled") if settings_obj.is_enabled else _("disabled")
            ),
        )
        return redirect(next_url)

    settings_obj.is_enabled = desired_active
    settings_obj.save(update_fields=["is_enabled", "updated_at"])
    try:
        config = MaterialExchangeConfig.objects.first()
        if config and config.is_active != desired_active:
            config.is_active = desired_active
            config.save(update_fields=["is_active", "updated_at"])
    except Exception:
        pass
    try:
        # Third Party
        from django_celery_beat.models import PeriodicTask

        PeriodicTask.objects.filter(name="indy-hub-material-exchange-cycle").update(
            enabled=desired_active
        )
    except Exception:
        # Beat not installed or table missing; ignore
        pass
    if desired_active:
        messages.success(request, _("Material Exchange enabled."))
    else:
        messages.success(request, _("Material Exchange disabled."))

    return redirect(next_url)


@login_required
@indy_hub_permission_required("can_manage_material_hub")
@tokens_required(
    scopes="esi-assets.read_corporation_assets.v1 esi-corporations.read_divisions.v1"
)
def material_exchange_get_structures(request, tokens, corp_id):
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


def _find_director_character(user, corp_id):
    """Find a character with DIRECTOR role in the given corporation.

    Returns the character_id or None if not found.
    """
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter
    from esi.models import Token

    # AA Example App
    from indy_hub.services.esi_client import shared_client

    logger.warning(
        "Looking for DIRECTOR character in corp %s for user %s", corp_id, user.username
    )

    # Get ALL character tokens for the user first
    try:
        all_tokens = Token.objects.filter(user=user).require_valid()
        all_tokens_list = list(all_tokens)
        logger.warning(
            "Found %s valid tokens for user %s: %s",
            len(all_tokens_list),
            user.username,
            [t.character_id for t in all_tokens_list],
        )
    except Exception as exc:
        logger.warning("Failed to get tokens for user %s: %s", user.username, exc)
        return None

    # Try tokens with the role-checking scope
    try:
        scoped_tokens = (
            Token.objects.filter(user=user)
            .require_scopes(["esi-characters.read_corporation_roles.v1"])
            .require_valid()
        )
        scoped_tokens_list = list(scoped_tokens)
        logger.warning(
            "Found %s tokens with role-checking scope for user %s: %s",
            len(scoped_tokens_list),
            user.username,
            [t.character_id for t in scoped_tokens_list],
        )
    except Exception as exc:
        logger.warning(
            "Failed to filter tokens by scope for user %s: %s", user.username, exc
        )
        scoped_tokens_list = []

    def _coerce_list(value: object) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item]
        return []

    def _load_roles(character_id: int) -> list[str]:
        snapshot = CharacterRoles.objects.filter(character_id=character_id).first()
        if snapshot:
            return [str(role).upper() for role in (snapshot.roles or []) if role]

        try:
            payload = shared_client.fetch_character_corporation_roles(character_id)
        except Exception as exc:
            logger.warning(
                "Failed to fetch roles for character %s: %s",
                character_id,
                exc,
            )
            return []

        if not isinstance(payload, dict):
            logger.warning(
                "Unexpected roles payload for character %s: %s",
                character_id,
                type(payload),
            )
            return []

        role_payload = {
            "roles": _coerce_list(payload.get("roles")),
            "roles_at_hq": _coerce_list(payload.get("roles_at_hq")),
            "roles_at_base": _coerce_list(payload.get("roles_at_base")),
            "roles_at_other": _coerce_list(payload.get("roles_at_other")),
        }
        CharacterRoles.objects.update_or_create(
            character_id=character_id,
            defaults={
                "owner_user": user,
                "corporation_id": int(corp_id) if corp_id else None,
                **role_payload,
            },
        )
        return [str(role).upper() for role in role_payload["roles"] if role]

    # Check scoped tokens first
    for token in scoped_tokens_list:
        try:
            character_id = token.character_id
            logger.warning(
                "Checking character %s from scoped token",
                character_id,
            )

            # Get the character from the database
            try:
                char = EveCharacter.objects.get(character_id=character_id)
                char_corp_id = int(char.corporation_id) if char.corporation_id else None
                logger.warning(
                    "Character %s is in corp %s (looking for %s)",
                    character_id,
                    char_corp_id,
                    corp_id,
                )
                if char_corp_id != int(corp_id):
                    logger.warning(
                        "Character %s is in corp %s, not %s - SKIPPING",
                        character_id,
                        char_corp_id,
                        corp_id,
                    )
                    continue
            except EveCharacter.DoesNotExist:
                logger.warning(
                    "Character %s not found in database",
                    character_id,
                )
                continue

            logger.warning(
                "Checking DIRECTOR role for character %s in corp %s",
                character_id,
                corp_id,
            )

            corp_roles = _load_roles(character_id)
            logger.warning("Character %s roles: %s", character_id, corp_roles)

            if "DIRECTOR" in corp_roles:
                logger.warning(
                    "Found DIRECTOR character %s for corporation %s",
                    character_id,
                    corp_id,
                )
                return character_id
            else:
                logger.warning(
                    "Character %s does NOT have Director role (has: %s)",
                    character_id,
                    corp_roles,
                )
        except Exception as exc:
            logger.warning(
                "Failed to check director role for character %s: %s",
                getattr(token, "character_id", "?"),
                exc,
            )
            continue

    # If no scoped tokens worked, try ALL tokens (they might have the scope but not filtered correctly)
    logger.warning(
        "No DIRECTOR found in scoped tokens, trying all tokens for user %s",
        user.username,
    )

    all_tokens = Token.objects.filter(user=user).require_valid()
    for token in all_tokens:
        try:
            character_id = token.character_id
            logger.warning(
                "Checking character %s from all tokens",
                character_id,
            )

            # Get the character from the database
            try:
                char = EveCharacter.objects.get(character_id=character_id)
                char_corp_id = int(char.corporation_id) if char.corporation_id else None
                if char_corp_id != int(corp_id):
                    continue
            except EveCharacter.DoesNotExist:
                continue

            logger.warning(
                "Checking DIRECTOR role for character %s (second pass)",
                character_id,
            )

            corp_roles = _load_roles(character_id)
            logger.warning(
                "Character %s roles (second pass): %s", character_id, corp_roles
            )

            if "DIRECTOR" in corp_roles:
                logger.warning(
                    "Found DIRECTOR character %s for corporation %s (second pass)",
                    character_id,
                    corp_id,
                )
                return character_id
            else:
                logger.warning(
                    "Character %s does NOT have Director role in second pass (has: %s)",
                    character_id,
                    corp_roles,
                )
        except Exception as exc:
            logger.warning(
                "Unexpected error checking character %s: %s",
                getattr(token, "character_id", "?"),
                exc,
            )
            continue

    logger.warning(
        "No DIRECTOR character found for user %s in corporation %s",
        user.username,
        corp_id,
    )
    return None


@login_required
@indy_hub_permission_required("can_manage_material_hub")
@tokens_required(
    scopes="esi-characters.read_corporation_roles.v1 esi-assets.read_corporation_assets.v1"
)
def material_exchange_refresh_corp_assets(request, tokens):
    """
    AJAX endpoint to refresh corporation assets and structures.
    Triggers background task to fetch latest ESI data.
    """
    # Standard Library
    import json

    # Django
    from django.http import JsonResponse

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "Method not allowed"}, status=405
        )

    try:
        data = json.loads(request.body)
        corp_id = int(data.get("corporation_id"))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse(
            {"success": False, "error": "Invalid corporation_id"}, status=400
        )

    try:
        # Find a DIRECTOR character for this corporation
        director_char_id = _find_director_character(request.user, corp_id)
        if not director_char_id:
            return JsonResponse(
                {
                    "success": False,
                    "error": "No character with DIRECTOR role found in this corporation",
                },
                status=400,
            )

        # Trigger task to refresh corp assets using the director character
        # AA Example App
        from indy_hub.tasks.material_exchange import refresh_corp_assets_cached

        task = refresh_corp_assets_cached.delay(corp_id, director_char_id)

        return JsonResponse(
            {
                "success": True,
                "task_id": task.id,
                "message": "Asset refresh task started. Structures will be updated shortly.",
            }
        )
    except Exception as exc:
        logger.exception(
            "Failed to trigger asset refresh for corp %s: %s", corp_id, exc
        )
        return JsonResponse(
            {"success": False, "error": f"Failed to refresh assets: {str(exc)}"},
            status=500,
        )


@login_required
@indy_hub_permission_required("can_manage_material_hub")
def material_exchange_check_refresh_status(request, task_id):
    """
    AJAX endpoint to check the status of a refresh task.
    Returns the task status: pending, success, or failure, plus progress info.

    Can also accept corp_id query parameter to check actual database updates
    instead of relying on Celery backend state tracking.
    """
    # Standard Library
    from datetime import timedelta

    # Third Party
    from celery.result import AsyncResult

    # Django
    from django.http import JsonResponse
    from django.utils import timezone

    # AA Example App
    from indy_hub.models import CachedStructureName

    try:
        # Get optional corp_id from query params for database-based status check
        corp_id = request.GET.get("corp_id")

        task_result = AsyncResult(task_id)

        # Try to get the task state from Celery
        try:
            state = task_result.state
        except AttributeError:
            # DisabledBackend or other backend that doesn't support task tracking
            state = None

        # Get progress info from task metadata
        progress_info = {}
        # Check state safely, handling DisabledBackend which may not support state access
        if state and state in ["PROGRESS", "SUCCESS"]:
            try:
                progress_data = task_result.info
                if isinstance(progress_data, dict) and "current" in progress_data:
                    progress_info = {
                        "current": progress_data.get("current", 0),
                        "total": progress_data.get("total", 0),
                        "percent": (
                            int(
                                (
                                    progress_data.get("current", 0)
                                    / progress_data.get("total", 1)
                                )
                                * 100
                            )
                            if progress_data.get("total", 0) > 0
                            else 0
                        ),
                        "status": progress_data.get("status", ""),
                    }
            except Exception as exc:
                logger.debug("Failed to extract progress info: %s", exc)

        # If we have a corp_id, verify by checking database updates
        if corp_id and state != "SUCCESS":
            try:
                # Check if any structures were cached in the last 30 seconds
                # This indicates the task has completed or is completing
                recent_structures = CachedStructureName.objects.filter(
                    last_resolved__gte=timezone.now() - timedelta(seconds=30)
                ).exists()

                if recent_structures:
                    logger.info(
                        "Task %s appears complete (found recent structure caches)",
                        task_id,
                    )
                    return JsonResponse(
                        {
                            "status": "success",
                            "progress": {
                                "percent": 100,
                                "status": "Complete!",
                            },
                        }
                    )
            except Exception as exc:
                logger.debug("Failed to check structure cache status: %s", exc)

        # Use Celery state if available
        if state == "PENDING":
            return JsonResponse(
                {
                    "status": "pending",
                    "progress": progress_info
                    or {"percent": 0, "status": "Initializing..."},
                }
            )
        elif state == "SUCCESS":
            return JsonResponse(
                {
                    "status": "success",
                    "progress": {"percent": 100, "status": "Complete!"},
                }
            )
        elif state == "FAILURE":
            return JsonResponse(
                {
                    "status": "failure",
                    "error": str(task_result.info),
                    "progress": progress_info,
                },
                status=400,
            )
        elif state == "PROGRESS":
            return JsonResponse(
                {
                    "status": "pending",
                    "progress": progress_info
                    or {"percent": 0, "status": "Processing..."},
                }
            )
        elif state is None:
            # Backend doesn't support state tracking and no db verification
            # Wait a bit longer before declaring success (give task time to run)
            return JsonResponse(
                {
                    "status": "pending",
                    "progress": {
                        "percent": 50,
                        "status": "Processing (no backend tracking)...",
                    },
                }
            )
        else:
            # RETRY, STARTED, etc.
            return JsonResponse(
                {
                    "status": "pending",
                    "progress": progress_info
                    or {"percent": 0, "status": "In progress..."},
                }
            )
    except Exception as exc:
        logger.exception("Failed to check refresh status for task %s: %s", task_id, exc)
        return JsonResponse(
            {
                "status": "failure",
                "error": f"Failed to check status: {str(exc)}",
                "progress": {"percent": 0},
            },
            status=500,
        )


def _get_user_corporations(user):
    """
    Get list of corporations the user has ESI access to.
    Returns list of dicts with corp_id and corp_name.
    """
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
    from esi.models import Token

    corporations = []
    seen_corps = set()

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
            char_obj = EveCharacter.objects.get_character_by_id(int(char_id))
            if char_obj is None:
                char_obj = EveCharacter.objects.create_character(int(char_id))
        except Exception as exc:
            logger.debug("Skip char %s (character lookup failed: %s)", char_id, exc)
            continue

        corp_id = getattr(char_obj, "corporation_id", None)
        if not corp_id or corp_id in seen_corps:
            continue

        try:
            corp_obj = EveCorporationInfo.objects.filter(corporation_id=corp_id).first()
            if corp_obj is None:
                corp_obj = EveCorporationInfo.objects.create_corporation(int(corp_id))
        except Exception as exc:
            logger.debug("Skip corp %s (lookup failed: %s)", corp_id, exc)
            continue

        corporations.append(
            {
                "id": corp_id,
                "name": getattr(corp_obj, "corporation_name", f"Corp {corp_id}"),
                "ticker": getattr(corp_obj, "corporation_ticker", ""),
            }
        )
        seen_corps.add(corp_id)

    return corporations


def _get_corp_structures(user, corp_id):
    """Get list of player structures using lazy queryset and resolve names for user's DIRECTOR characters."""

    cache_key = f"indy_hub:material_exchange:corp_structures:v2:{int(corp_id)}"
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            cached_structures, cached_scope_missing = cached
            cached_structures = list(cached_structures or [])
            cached_structures.sort(key=lambda row: str(row.get("name", "")).lower())
            return cached_structures, cached_scope_missing
        except Exception:
            return cached

    # Get structure IDs from corp assets
    assets_qs, assets_scope_missing = get_corp_assets_cached(
        int(corp_id),
        allow_refresh=True,
        as_queryset=True,
    )

    # Get unique structure IDs from corp assets.
    # - OfficeFolder assets carry the structure_id directly in location_id.
    # - CorpSAG* assets store the office folder item_id in location_id, so we
    #   must map that to the OfficeFolder's location_id (structure_id).
    resolvable_structure_flags = {
        "OfficeFolder",
        "StructureFuel",
        "MoonMaterialBay",
        "QuantumCoreRoom",
        "ServiceSlot0",
        "CorpDeliveries",
    }

    office_folder_map = {
        int(item_id): int(location_id)
        for item_id, location_id in assets_qs.filter(location_flag="OfficeFolder")
        .exclude(item_id__isnull=True)
        .values_list("item_id", "location_id")
        .distinct()
    }

    loc_ids_set: set[int] = set()
    structure_flags: dict[int, set[str]] = {}

    for loc_id in assets_qs.filter(
        location_flag__in=resolvable_structure_flags
    ).values_list("location_id", flat=True):
        if loc_id:
            loc_ids_set.add(int(loc_id))

    for office_folder_item_id, flag in assets_qs.filter(
        location_flag__startswith="CorpSAG"
    ).values_list("location_id", "location_flag"):
        if not office_folder_item_id:
            continue
        structure_id = office_folder_map.get(int(office_folder_item_id))
        if structure_id:
            loc_ids_set.add(int(structure_id))
            structure_flags.setdefault(int(structure_id), set()).add(str(flag))

    loc_ids = list(loc_ids_set)

    if not loc_ids:
        result = (
            [
                {
                    "id": 0,
                    "name": _("⚠ No corporation assets available (ESI scope missing)"),
                }
            ],
            assets_scope_missing,
        )
        cache.set(cache_key, result, 300)
        return result

    # Resolve structure names using user's DIRECTOR characters
    # This will use /universe/structures/{structure_id} for each structure
    structure_names = resolve_structure_names(
        sorted(loc_ids), character_id=None, corporation_id=int(corp_id), user=user
    )

    structures: list[dict] = []
    for loc_id in sorted(loc_ids):
        # Always include all asset-backed locations, even if name resolution failed.
        structures.append(
            {
                "id": loc_id,
                "name": structure_names.get(loc_id) or f"Structure {loc_id}",
                "flags": sorted(structure_flags.get(int(loc_id), set())),
            }
        )

    structures.sort(key=lambda row: str(row.get("name", "")).lower())

    result = (structures, assets_scope_missing)
    cache.set(cache_key, result, 300)
    return result


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


def _get_industry_market_group_ids() -> set[int]:
    """Return market group IDs used by industry materials (cached)."""

    cache_key = "indy_hub:material_exchange:industry_market_group_ids:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            return {int(x) for x in cached}
        except Exception:
            return set()

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
    return ids


def _build_market_group_index() -> dict[int, dict[str, str | int | None]]:
    """Return a dict of market group metadata keyed by id."""

    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveMarketGroup

        return {
            g["id"]: {
                "id": g["id"],
                "name": g["name"],
                "parent_market_group_id": g["parent_market_group_id"],
            }
            for g in EveMarketGroup.objects.values(
                "id", "name", "parent_market_group_id"
            )
        }
    except Exception as exc:
        logger.warning("Failed to load market group choices: %s", exc)
        return {}


def _get_market_group_path_ids(
    group_id: int, all_groups: dict[int, dict[str, str | int | None]]
) -> list[int]:
    """Return path of IDs from root to the group (inclusive)."""

    path: list[int] = []
    seen: set[int] = set()
    current_id = group_id
    while current_id and current_id in all_groups and current_id not in seen:
        seen.add(current_id)
        path.append(current_id)
        current_id = all_groups[current_id]["parent_market_group_id"]
    return list(reversed(path))


def _get_industry_market_group_choice_ids(depth_from_root: int = 2) -> set[int]:
    """Return grouped market group IDs at the given depth for industry items."""

    cache_key = (
        "indy_hub:material_exchange:industry_market_group_choice_ids:v2:"
        f"depth:{depth_from_root}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            return {int(x) for x in cached}
        except Exception:
            return set()

    used_ids = _get_industry_market_group_ids()
    if not used_ids:
        return set()

    all_groups = _build_market_group_index()
    if not all_groups:
        return set()

    grouped_ids: set[int] = set()
    for group_id in used_ids:
        path_ids = _get_market_group_path_ids(int(group_id), all_groups)
        if not path_ids:
            continue
        if len(path_ids) <= depth_from_root:
            grouped_ids.add(path_ids[-1])
        else:
            grouped_ids.add(path_ids[depth_from_root])

    cache.set(cache_key, list(grouped_ids), 3600)
    return grouped_ids


def _get_industry_market_group_choices(
    depth_from_root: int = 2,
) -> list[dict[str, str | int]]:
    """Return sorted market group choices (id + label) for industry items."""

    cache_key = (
        "indy_hub:material_exchange:industry_market_group_choices:v2:"
        f"depth:{depth_from_root}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    grouped_ids = _get_industry_market_group_choice_ids(depth_from_root)
    if not grouped_ids:
        return []

    all_groups = _build_market_group_index()
    if not all_groups:
        return []

    choices = [
        {"id": int(group_id), "label": all_groups[int(group_id)]["name"]}
        for group_id in grouped_ids
        if int(group_id) in all_groups
    ]
    choices.sort(key=lambda x: (str(x["label"]).lower()))

    cache.set(cache_key, choices, 3600)
    return choices


def _get_industry_market_group_search_index(
    depth_from_root: int = 2,
) -> dict[int, dict[str, object]]:
    """Return market group labels and item names for search."""

    cache_key = (
        "indy_hub:material_exchange:industry_market_group_search_index:v1:"
        f"depth:{depth_from_root}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            return {int(k): v for k, v in cached.items()}
        except Exception:
            return {}

    grouped_ids = _get_industry_market_group_choice_ids(depth_from_root)
    if not grouped_ids:
        return {}

    all_groups = _build_market_group_index()
    if not all_groups:
        return {}

    try:
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveIndustryActivityMaterial

        rows = (
            EveIndustryActivityMaterial.objects.exclude(
                material_eve_type__eve_market_group_id__isnull=True
            )
            .values_list(
                "material_eve_type__eve_market_group_id",
                "material_eve_type__name",
            )
            .distinct()
        )
    except Exception as exc:
        logger.warning("Failed to load industry item names: %s", exc)
        return {}

    index: dict[int, dict[str, object]] = {
        int(group_id): {
            "label": str(all_groups[int(group_id)]["name"]),
            "items": set(),
        }
        for group_id in grouped_ids
        if int(group_id) in all_groups
    }

    for market_group_id, type_name in rows:
        if not market_group_id:
            continue
        path_ids = _get_market_group_path_ids(int(market_group_id), all_groups)
        if not path_ids:
            continue
        if len(path_ids) <= depth_from_root:
            grouped_id = path_ids[-1]
        else:
            grouped_id = path_ids[depth_from_root]
        if grouped_id in index and type_name:
            index[grouped_id]["items"].add(str(type_name))

    for group_id, payload in index.items():
        items = sorted(payload["items"], key=lambda x: x.lower())
        payload["items"] = items

    cache.set(
        cache_key,
        {str(k): v for k, v in index.items()},
        3600,
    )
    return index


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
    allowed_market_groups_buy_raw = request.POST.getlist("allowed_market_groups_buy")
    allowed_market_groups_sell_raw = request.POST.getlist("allowed_market_groups_sell")

    raw_enforce_bounds = request.POST.get("enforce_jita_price_bounds")
    if raw_enforce_bounds is None and existing_config is not None:
        enforce_jita_price_bounds = existing_config.enforce_jita_price_bounds
    else:
        enforce_jita_price_bounds = raw_enforce_bounds == "on"

    raw_is_active = request.POST.get("is_active")
    if raw_is_active is None and existing_config is not None:
        is_active = existing_config.is_active
    else:
        is_active = raw_is_active == "on"

    def _parse_decimal(raw_value: str, fallback: str) -> Decimal:
        normalized = (raw_value or "").strip().replace(",", ".")
        if not normalized:
            normalized = fallback
        return Decimal(normalized)

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
        sell_markup_percent = _parse_decimal(sell_markup_percent, "0")
        buy_markup_percent = _parse_decimal(buy_markup_percent, "5")

        allowed_ids = _get_industry_market_group_choice_ids(depth_from_root=2)

        def _parse_group_ids(raw_list: list[str]) -> list[int]:
            parsed: set[int] = set()
            for raw in raw_list:
                try:
                    group_id = int(raw)
                except (TypeError, ValueError):
                    continue
                if allowed_ids and group_id not in allowed_ids:
                    continue
                parsed.add(group_id)
            return sorted(parsed)

        allowed_market_groups_buy = _parse_group_ids(allowed_market_groups_buy_raw)
        allowed_market_groups_sell = _parse_group_ids(allowed_market_groups_sell_raw)

        if not (1 <= hangar_division <= 7):
            raise ValueError("Hangar division must be between 1 and 7")

    except (ValueError, TypeError, InvalidOperation) as e:
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
                    [int(structure_id)],
                    character_id_for_names,
                    int(corporation_id),
                    user=request.user,
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
            existing_config.enforce_jita_price_bounds = enforce_jita_price_bounds
            existing_config.allowed_market_groups_buy = allowed_market_groups_buy
            existing_config.allowed_market_groups_sell = allowed_market_groups_sell
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
                enforce_jita_price_bounds=enforce_jita_price_bounds,
                allowed_market_groups_buy=allowed_market_groups_buy,
                allowed_market_groups_sell=allowed_market_groups_sell,
                is_active=is_active,
            )
            messages.success(
                request, _("Material Exchange configuration created successfully.")
            )

    return redirect("indy_hub:material_exchange_index")


@login_required
@indy_hub_permission_required("can_manage_material_hub")
@tokens_required(scopes="esi-characters.read_corporation_roles.v1")
def material_exchange_debug_tokens(request, corp_id, tokens):
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
