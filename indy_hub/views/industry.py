"""Industry-related views for Indy Hub."""

# Standard Library
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal
from math import ceil
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

# Django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Case, Count, Max, Prefetch, Q, Sum, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, mark_safe
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.forms.industry_structures import (
    IndustryStructureBulkImportForm,
    IndustryStructureBulkTaxUpdateForm,
    IndustryStructureRegistryForm,
    IndustryStructureRigFormSet,
    IndustryStructureTaxProfileDuplicateForm,
)
from indy_hub.models import CharacterSettings, CorporationSharingSetting

from ..decorators import indy_hub_access_required, indy_hub_permission_required
from ..models import (
    Blueprint,
    BlueprintCopyChat,
    BlueprintCopyMessage,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    IndustryActivityMixin,
    IndustryJob,
    IndustrySkillSnapshot,
    IndustryStructure,
    IndustryStructureRig,
    IndustrySystemCostIndex,
    NotificationWebhookMessage,
    ProductionProject,
    ProductionProjectItem,
    UserFavoriteStructure,
)
from ..notifications import (
    build_site_url,
    delete_discord_webhook_message,
    edit_discord_webhook_message,
    notify_user,
    send_discord_webhook_with_message_id,
)
from ..services.asset_cache import build_user_asset_inventory_snapshot
from ..services.corporation_blueprint_visibility import (
    get_viewable_corporation_ids,
    get_viewable_corporation_job_ids,
)
from ..services.craft_times import (
    compute_effective_cycle_seconds,
    compute_max_runs_before_launch_window,
    get_max_copy_runs_per_request,
)
from ..services.esi_client import ESIUnmodifiedError, shared_client
from ..services.industry_blueprint_eligibility import (
    EligibleOwnerDetails,
    UserIdentity,
)
from ..services.industry_blueprint_eligibility import (
    build_eligible_owner_ids_map as _build_eligible_owner_ids_map_service,
)
from ..services.industry_blueprint_eligibility import (
    eligible_owner_details_for_request as _eligible_owner_details_for_request_service,
)
from ..services.industry_blueprint_eligibility import (
    resolve_user_identity as _resolve_user_identity_service,
)
from ..services.industry_blueprint_notifications import (
    build_blueprint_copy_request_notification_content as _build_blueprint_copy_request_notification_content_service,
)
from ..services.industry_blueprint_notifications import (
    notify_blueprint_copy_request_providers as _notify_blueprint_copy_request_providers_service,
)
from ..services.industry_blueprint_notifications import (
    strike_discord_webhook_messages_for_request as _strike_discord_webhook_messages_for_request_service,
)
from ..services.industry_chat_helpers import (
    build_bp_chat_history_payload,
)
from ..services.industry_chat_helpers import chat_has_unread as _chat_has_unread_service
from ..services.industry_chat_helpers import (
    chat_preview_messages as _chat_preview_messages_service,
)
from ..services.industry_chat_helpers import (
    close_request_chats as _close_request_chats_service,
)
from ..services.industry_chat_helpers import (
    ensure_offer_chat as _ensure_offer_chat_service,
)
from ..services.industry_chat_helpers import (
    execute_bp_chat_decision,
)
from ..services.industry_chat_helpers import (
    resolve_chat_viewer_role as _resolve_chat_viewer_role_service,
)
from ..services.industry_copy_estimated_values import (
    build_copy_duration_payload as _build_copy_duration_payload_service,
)
from ..services.industry_copy_estimated_values import (
    build_copy_estimated_item_values as _build_copy_estimated_item_values_service,
)
from ..services.industry_copy_estimated_values import (
    copy_estimate_cost_rate as _copy_estimate_cost_rate_service,
)
from ..services.industry_copy_estimated_values import (
    copy_estimate_decimal as _copy_estimate_decimal_service,
)
from ..services.industry_copy_estimated_values import (
    fetch_item_base_prices as _fetch_item_base_prices_service,
)
from ..services.industry_copy_estimated_values import (
    serialize_copy_request_price_estimate as _serialize_copy_request_price_estimate_service,
)
from ..services.industry_offer_actions import (
    OfferFlowDeps,
    finalize_offer_with_deps,
    mark_buyer_accept_with_deps,
    mark_seller_accept_with_deps,
    process_offer_action_with_deps,
)
from ..services.industry_offer_actions import (
    record_offer_proposal as _record_offer_proposal_service,
)
from ..services.industry_offer_helpers import (
    classify_bp_chat_message as _classify_bp_chat_message_service,
)
from ..services.industry_offer_helpers import (
    format_duration_compact as _format_duration_compact_service,
)
from ..services.industry_offer_helpers import (
    format_isk_amount as _format_isk_amount_service,
)
from ..services.industry_offer_helpers import (
    format_percent_compact as _format_percent_compact_service,
)
from ..services.industry_offer_helpers import (
    normalize_offer_amount as _normalize_offer_amount_service,
)
from ..services.industry_skills import (
    SKILLS_SCOPE,
    build_craft_character_advisor,
    build_skill_snapshot_defaults,
)
from ..services.industry_skills import (
    build_slot_overview_summary as _build_slot_overview_summary_service,
)
from ..services.industry_skills import (
    build_user_character_skill_contexts,
    skill_snapshot_stale,
)
from ..services.industry_structure_import import (
    import_indy_structure_paste,
    resolve_structure_scan_loadout,
)
from ..services.industry_structure_sync import get_available_structure_sync_targets
from ..services.industry_structures import (
    COPYING_JOB_COST_BASE_PERCENT,
    build_structure_activity_previews,
    build_structure_rig_advisor_rows,
    calculate_installation_cost,
    get_enabled_activity_ids_from_flags,
    get_industry_rig_catalog,
    get_structure_type_catalog,
    get_structure_type_options,
    resolve_solar_system_reference,
    sde_item_types_loaded,
    search_solar_system_options,
)
from ..services.market_prices import MarketPriceError, fetch_adjusted_prices
from ..services.material_exchange_assets import (
    CRAFT_PROJECT_STOCK_CACHE_MAX_AGE_MINUTES,
)
from ..services.material_exchange_assets import (
    craft_project_stock_refresh_progress_key as _craft_project_stock_refresh_progress_key_service,
)
from ..services.material_exchange_assets import (
    ensure_craft_project_stock_refresh_started as _ensure_craft_project_stock_refresh_started_service,
)
from ..services.material_exchange_assets import (
    get_latest_character_asset_sync as _get_latest_character_asset_sync,
)
from ..services.material_exchange_assets import (
    get_sell_assets_refresh_progress,
)
from ..services.production_projects import (
    LEGACY_SINGLE_BLUEPRINT_PROJECT_NOTE,
    PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY,
    build_project_workspace_payload,
    build_temporary_project_payload,
    build_temporary_project_workspace_state,
    create_temporary_project_workspace,
    get_cached_project_workspace_payload,
    get_temporary_project_workspace,
    normalize_production_project_ref,
    parse_project_final_output_quantity_overrides,
    parse_project_me_te_overrides,
    set_temporary_project_workspace,
    strip_project_workspace_cache,
)
from ..services.project_progress import normalize_project_progress
from ..tasks.industry import (
    BLUEPRINT_SCOPE,
    CORP_BLUEPRINT_SCOPE_SET,
    CORP_JOBS_SCOPE_SET,
    JOBS_SCOPE,
    MANUAL_REFRESH_KIND_BLUEPRINTS,
    MANUAL_REFRESH_KIND_JOBS,
    STRUCTURE_SCOPE,
    request_manual_refresh,
)
from ..utils.analytics import emit_view_analytics_event
from ..utils.discord_actions import (
    _DEFAULT_TOKEN_MAX_AGE,
    BadSignature,
    SignatureExpired,
    build_action_link,
    decode_action_token,
)
from ..utils.eve import (
    PLACEHOLDER_PREFIX,
    get_blueprint_product_type_id,
    get_character_name,
    get_corporation_name,
    get_corporation_ticker,
    get_type_name,
    is_reaction_blueprint,
)
from .navigation import build_nav_context

# ESI skills scope + industry slot calculations
SKILL_CACHE_TTL = timedelta(hours=1)
INDUSTRY_JOBS_PER_PAGE_DEFAULT = 50
INDUSTRY_JOBS_PER_PAGE_MAX_CHARACTER = 100
INDUSTRY_JOBS_PER_PAGE_MAX_CORPORATION = 200
MANUFACTURING_ACTIVITY_IDS = {1}
RESEARCH_ACTIVITY_IDS = {3, 4, 5, 8}
REACTION_ACTIVITY_IDS = {9, 11}

_SKILLS_OPERATION_UNAVAILABLE = False


def _is_eve_sde_installed() -> bool:
    installed_apps = getattr(settings, "INSTALLED_APPS", ())
    return any(
        app == "eve_sde" or str(app).startswith("eve_sde.") for app in installed_apps
    )


if _is_eve_sde_installed():  # pragma: no branch
    try:  # pragma: no cover - eve_sde optional in tests
        # Alliance Auth (External Libs)
        from eve_sde.models import ItemType as EveType
    except ImportError:  # pragma: no cover - fallback when eve_sde absent
        EveType = None
else:  # pragma: no cover - eve_sde not installed
    EveType = None

logger = get_extension_logger(__name__)


def _craft_project_stock_refresh_progress_key(user_id: int) -> str:
    return _craft_project_stock_refresh_progress_key_service(user_id)


def _ensure_craft_project_stock_refresh_started(user) -> dict:
    return _ensure_craft_project_stock_refresh_started_service(user)


def _get_craft_project_stock_refresh_progress(user) -> dict:
    latest_update = _get_latest_character_asset_sync(user)
    try:
        stock_is_stale = (
            not latest_update
            or (timezone.now() - latest_update).total_seconds()
            > CRAFT_PROJECT_STOCK_CACHE_MAX_AGE_MINUTES * 60
        )
    except Exception:
        stock_is_stale = True

    progress_key = _craft_project_stock_refresh_progress_key(int(user.id))
    cached_progress = cache.get(progress_key)
    progress = get_sell_assets_refresh_progress(int(user.id)) if cached_progress else {}
    if stock_is_stale:
        progress = _ensure_craft_project_stock_refresh_started(user)
    return progress


@indy_hub_access_required
@login_required
@require_http_methods(["GET"])
def craft_project_stock_refresh_status(request):
    emit_view_analytics_event(
        view_name="industry.craft_project_stock_refresh_status", request=request
    )

    state = get_sell_assets_refresh_progress(int(request.user.id))

    latest_update = _get_latest_character_asset_sync(request.user)
    response = dict(state)
    response["synced_at"] = latest_update.isoformat() if latest_update else ""
    initial_synced_at = str(request.GET.get("since") or "").strip()
    response["changed"] = bool(
        latest_update and latest_update.isoformat() != initial_synced_at
    )
    return JsonResponse(response)


def _fetch_character_skill_levels(
    character_id: int,
    *,
    force_refresh: bool = False,
) -> dict[int, dict[str, int]]:
    global _SKILLS_OPERATION_UNAVAILABLE
    if _SKILLS_OPERATION_UNAVAILABLE:
        raise ESIUnmodifiedError("ESI skills operation unavailable")

    try:
        payload = shared_client.fetch_character_skills(
            int(character_id),
            force_refresh=force_refresh,
        )
    except Exception as exc:
        exc_text = str(exc)
        if "GetCharactersCharacterIdSkills" in exc_text and "not found" in exc_text:
            _SKILLS_OPERATION_UNAVAILABLE = True
            raise ESIUnmodifiedError("ESI skills operation unavailable") from exc
        raise

    skills = payload.get("skills", []) if payload else []
    levels: dict[int, dict[str, int]] = {}
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_id = skill.get("skill_id")
        if not skill_id:
            continue
        active_level = int(skill.get("active_skill_level") or 0)
        trained_level = int(skill.get("trained_skill_level") or 0)
        levels[int(skill_id)] = {"active": active_level, "trained": trained_level}
    return levels


def _update_skill_snapshot(
    user: User,
    character_id: int,
    levels: dict[int, dict[str, int]],
) -> IndustrySkillSnapshot:
    return IndustrySkillSnapshot.objects.update_or_create(
        owner_user=user,
        character_id=character_id,
        defaults=build_skill_snapshot_defaults(levels),
    )[0]


def _skill_snapshot_stale(snapshot: IndustrySkillSnapshot | None) -> bool:
    return skill_snapshot_stale(snapshot, SKILL_CACHE_TTL)


def _build_slot_overview_rows(
    user: User,
    *,
    allow_live_fetch: bool = False,
    force_refresh: bool = False,
) -> list[dict[str, object]]:
    return build_user_character_skill_contexts(
        user,
        fetch_character_skill_levels=_fetch_character_skill_levels,
        update_skill_snapshot=_update_skill_snapshot,
        skill_cache_ttl=SKILL_CACHE_TTL,
        allow_live_fetch=allow_live_fetch,
        force_refresh=force_refresh,
    )


def _build_slot_overview_summary(
    rows: list[dict[str, object]],
) -> dict[str, dict[str, int] | int]:
    return _build_slot_overview_summary_service(rows)


def _has_required_scopes(user, scopes: list[str]) -> bool:
    try:
        # Alliance Auth
        from esi.models import Token

        return (
            Token.objects.filter(user=user)
            .require_scopes(scopes)
            .require_valid()
            .exists()
        )
    except Exception:
        return False


def _parse_bounded_int(
    raw_value,
    *,
    default: int,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    try:
        parsed_value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        parsed_value = default

    if parsed_value < minimum:
        parsed_value = minimum
    if maximum is not None and parsed_value > maximum:
        parsed_value = maximum
    return parsed_value


def _resolve_user_identity(user: User | None) -> UserIdentity:
    return _resolve_user_identity_service(user)


def _eligible_owner_details_for_request(
    req: BlueprintCopyRequest,
) -> EligibleOwnerDetails:
    return _eligible_owner_details_for_request_service(req)


class _MissingMaxProductionLimit:
    pass


_SENTINEL = _MissingMaxProductionLimit()


def _fetch_blueprint_activity_times(
    blueprint_type_ids: list[int] | set[int] | tuple[int, ...],
) -> dict[int, dict[int, int]]:
    numeric_blueprint_type_ids = sorted(
        {
            int(blueprint_type_id)
            for blueprint_type_id in blueprint_type_ids
            if blueprint_type_id
        }
    )
    if not numeric_blueprint_type_ids:
        return {}

    placeholders = ", ".join(["%s"] * len(numeric_blueprint_type_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT ba.blueprint_item_type_id, ba.activity, ba.time
            FROM eve_sde_blueprintactivity ba
            WHERE ba.blueprint_item_type_id IN ({placeholders})
                AND ba.activity IN ('manufacturing', 'copying')
            """,
            numeric_blueprint_type_ids,
        )
        rows = cursor.fetchall()

    activity_name_to_id = {
        "manufacturing": IndustryActivityMixin.ACTIVITY_MANUFACTURING,
        "copying": IndustryActivityMixin.ACTIVITY_COPYING,
    }

    activity_times: dict[int, dict[int, int]] = defaultdict(dict)
    for blueprint_type_id, activity_name, time_seconds in rows:
        activity_id = activity_name_to_id.get(str(activity_name or ""))
        if not activity_id:
            continue
        activity_times[int(blueprint_type_id)][int(activity_id)] = max(
            0,
            int(time_seconds or 0),
        )
    return activity_times


def _fetch_blueprint_max_production_limits(
    blueprint_type_ids: list[int] | set[int] | tuple[int, ...],
) -> dict[int, int]:
    """Batched lookup of native ``max_production_limit`` per blueprint type."""

    numeric_blueprint_type_ids = sorted(
        {
            int(blueprint_type_id)
            for blueprint_type_id in blueprint_type_ids
            if blueprint_type_id
        }
    )
    if not numeric_blueprint_type_ids:
        return {}

    table_name = "eve_sde_blueprintactivity"
    table_names = set(connection.introspection.table_names())
    if table_name not in table_names:
        return {}

    with connection.cursor() as cursor:
        table_description = connection.introspection.get_table_description(
            cursor,
            table_name,
        )
    available_columns = {column.name for column in table_description}
    if "max_production_limit" not in available_columns:
        return {}
    if "blueprint_item_type_id" in available_columns:
        blueprint_key_column = "blueprint_item_type_id"
    elif "eve_type_id" in available_columns:
        blueprint_key_column = "eve_type_id"
    else:
        return {}

    placeholders = ", ".join(["%s"] * len(numeric_blueprint_type_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {blueprint_key_column}, MIN(max_production_limit)
            FROM {table_name}
            WHERE {blueprint_key_column} IN ({placeholders})
            AND max_production_limit IS NOT NULL
            AND max_production_limit > 0
            GROUP BY {blueprint_key_column}
            """,
            numeric_blueprint_type_ids,
        )
        return {int(type_id): int(limit) for type_id, limit in cursor.fetchall()}


def _build_copy_request_preview(
    *,
    requester: User,
    type_id: int,
    material_efficiency: int,
    time_efficiency: int,
    type_name: str,
    activity_times: dict[int, dict[int, int]],
    eligible_owner_ids: set[int] | None = None,
    max_production_limit: int | None | _MissingMaxProductionLimit = _SENTINEL,
) -> dict[str, object]:
    if eligible_owner_ids is None:
        request_probe = BlueprintCopyRequest(
            requested_by=requester,
            requested_by_id=requester.id,
            type_id=type_id,
            material_efficiency=material_efficiency,
            time_efficiency=time_efficiency,
            runs_requested=1,
            copies_requested=1,
        )
        eligible_owner_ids = _eligible_owner_details_for_request(
            request_probe
        ).owner_ids

    copy_base_time_seconds = (
        activity_times.get(int(type_id), {}).get(IndustryActivityMixin.ACTIVITY_COPYING)
        or 0
    )
    if max_production_limit is _SENTINEL:
        max_runs_per_copy = get_max_copy_runs_per_request(
            blueprint_type_id=type_id,
            time_efficiency=time_efficiency,
        )
    else:
        manufacturing_base_time = (
            activity_times.get(int(type_id), {}).get(
                IndustryActivityMixin.ACTIVITY_MANUFACTURING
            )
            or 0
        )
        candidate_limits: list[int] = []
        if max_production_limit is not None and int(max_production_limit) > 0:
            candidate_limits.append(int(max_production_limit))
        if manufacturing_base_time > 0:
            effective_cycle_seconds = compute_effective_cycle_seconds(
                base_time_seconds=manufacturing_base_time,
                time_efficiency=time_efficiency,
            )
            launch_window_limit = compute_max_runs_before_launch_window(
                effective_cycle_seconds
            )
            if launch_window_limit > 0:
                candidate_limits.append(int(launch_window_limit))
        max_runs_per_copy = min(candidate_limits) if candidate_limits else None

    return {
        "type_id": int(type_id),
        "type_name": type_name,
        "material_efficiency": int(material_efficiency),
        "time_efficiency": int(time_efficiency),
        "copy_base_time_seconds": int(copy_base_time_seconds or 0),
        "per_run_copy_seconds": int(copy_base_time_seconds or 0),
        "max_runs_per_copy": int(max_runs_per_copy or 0) if max_runs_per_copy else None,
        "alerted_owner_count": int(len(eligible_owner_ids)),
        "alerted_owner_ids": sorted(int(owner_id) for owner_id in eligible_owner_ids),
    }


def _build_eligible_owner_ids_map(
    requester: User,
    blueprint_keys: Iterable[tuple[int, int, int]],
) -> dict[tuple[int, int, int], set[int]]:
    return _build_eligible_owner_ids_map_service(requester, blueprint_keys)


def _build_blueprint_copy_request_notification_content(
    req: BlueprintCopyRequest,
) -> tuple[str, str, str]:
    return _build_blueprint_copy_request_notification_content_service(req)


def _strike_discord_webhook_messages_for_request(
    request,
    req: BlueprintCopyRequest,
    *,
    actor: User,
) -> None:
    _strike_discord_webhook_messages_for_request_service(
        req,
        edit_webhook_fn=edit_discord_webhook_message,
    )


def _notify_blueprint_copy_request_providers(
    request,
    req: BlueprintCopyRequest,
    *,
    notification_title: str | None = None,
    notification_body: str | None = None,
) -> None:
    _notify_blueprint_copy_request_providers_service(
        request,
        req,
        notification_title=notification_title,
        notification_body=notification_body,
        notify_user_fn=notify_user,
        send_webhook_fn=send_discord_webhook_with_message_id,
        build_action_link_fn=build_action_link,
    )


def _eligible_owner_ids_for_request(req: BlueprintCopyRequest) -> set[int]:
    """Return user IDs that can fulfil the request based on owned originals."""

    details = _eligible_owner_details_for_request(req)
    return set(details.owner_ids)


def _user_can_fulfill_request(req: BlueprintCopyRequest, user: User) -> bool:
    """Check whether a user is allowed to act as provider for a request."""

    if not user or req.requested_by_id == user.id:
        return False

    if _eligible_owner_ids_for_request(req).__contains__(user.id):
        return True

    # Allow if an existing offer from this user is already recorded (legacy cases)
    return req.offers.filter(owner=user).exists()


def _offer_rejects_scope(
    offer: BlueprintCopyOffer | None,
    scope: str,
) -> bool:
    """Return whether a rejected offer blocks the requested fulfilment scope."""

    if not offer or offer.status != "rejected":
        return False

    normalized_scope = (offer.source_scope or "").strip().lower()
    if normalized_scope in {"personal", "corporation"}:
        return normalized_scope == scope

    # Legacy rejections without a scope are treated as global declines.
    return True


def _finalize_request_if_all_rejected(req: BlueprintCopyRequest) -> bool:
    """Notify requester and delete request if all eligible providers rejected."""

    details = _eligible_owner_details_for_request(req)
    eligible_owner_ids = details.owner_ids
    offers_by_owner = {
        offer.owner_id: offer
        for offer in req.offers.filter(owner_id__in=eligible_owner_ids)
    }

    if eligible_owner_ids:
        outstanding: list[int | tuple[str, int]] = []

        for owner_id in details.character_owner_ids:
            if not _offer_rejects_scope(offers_by_owner.get(owner_id), "personal"):
                outstanding.append(owner_id)

        all_corp_member_ids: set[int] = set()
        for corp_id, members in details.corporate_members_by_corp.items():
            all_corp_member_ids.update(members)
            if any(
                not _offer_rejects_scope(offers_by_owner.get(member_id), "corporation")
                for member_id in members
            ):
                outstanding.append(("corporation", corp_id))

        remaining_owner_ids = (
            eligible_owner_ids - details.character_owner_ids - all_corp_member_ids
        )
        for owner_id in remaining_owner_ids:
            offer = offers_by_owner.get(owner_id)
            if not offer or offer.status != "rejected":
                outstanding.append(owner_id)

        if outstanding:
            return False

    my_requests_url = build_site_url(reverse("indy_hub:bp_copy_my_requests"))

    notify_user(
        req.requested_by,
        _("Blueprint Copy Request Unavailable"),
        _(
            "All available builders declined your request for %(type)s (ME%(me)d, TE%(te)d)."
        )
        % {
            "type": get_type_name(req.type_id),
            "me": req.material_efficiency,
            "te": req.time_efficiency,
        },
        "warning",
        link=my_requests_url,
        link_label=_("Review your requests"),
    )
    _close_request_chats(req, BlueprintCopyChat.CloseReason.REQUEST_WITHDRAWN)
    req.delete()
    return True


def _ensure_offer_chat(offer: BlueprintCopyOffer) -> BlueprintCopyChat:
    return _ensure_offer_chat_service(offer)


def _chat_has_unread(chat: BlueprintCopyChat, role: str) -> bool:
    return _chat_has_unread_service(chat, role)


def _chat_preview_messages(chat: BlueprintCopyChat, *, limit: int = 3) -> list[dict]:
    return _chat_preview_messages_service(chat, limit=limit)


def _resolve_chat_viewer_role(
    chat: BlueprintCopyChat,
    user: User,
    *,
    base_role: str | None,
    override: str | None = None,
) -> str | None:
    return _resolve_chat_viewer_role_service(
        chat,
        user,
        base_role=base_role,
        override=override,
    )


def _close_offer_chat_if_exists(offer: BlueprintCopyOffer, reason: str) -> None:
    try:
        chat = offer.chat
    except BlueprintCopyChat.DoesNotExist:
        return
    chat.close(reason=reason)


def _close_request_chats(
    req: BlueprintCopyRequest,
    reason: str,
    *,
    exclude_offer_id: int | None = None,
) -> None:
    _close_request_chats_service(
        req,
        reason,
        exclude_offer_id=exclude_offer_id,
    )


def _build_offer_chat_payload(
    offer: BlueprintCopyOffer,
    *,
    viewer_role: str,
    reopen: bool = False,
) -> dict[str, Any] | None:
    try:
        chat = offer.chat
    except BlueprintCopyChat.DoesNotExist:
        chat = _ensure_offer_chat(offer)
    else:
        if reopen and not chat.is_open:
            chat.reopen()

    if not chat or not chat.is_open:
        return None

    payload = {
        "id": chat.id,
        "fetch_url": reverse("indy_hub:bp_chat_history", args=[chat.id]),
        "send_url": reverse("indy_hub:bp_chat_send", args=[chat.id]),
        "has_unread": _chat_has_unread(chat, viewer_role),
        "last_message_at": chat.last_message_at,
        "last_message_display": (
            timezone.localtime(chat.last_message_at).strftime("%Y-%m-%d %H:%M")
            if chat.last_message_at
            else ""
        ),
        "preview": _chat_preview_messages(chat),
    }
    if offer.status == "conditional":
        payload["decision_url"] = reverse("indy_hub:bp_chat_decide", args=[chat.id])
    return payload


def _normalize_offer_amount(raw_amount: Any) -> Decimal | None:
    return _normalize_offer_amount_service(raw_amount)


def _format_isk_amount(amount: Decimal | None) -> str:
    return _format_isk_amount_service(amount)


def _format_percent_compact(value: Decimal | int | float | str | None) -> str:
    return _format_percent_compact_service(value)


def _format_duration_compact(total_seconds: int | float | Decimal | None) -> str:
    return _format_duration_compact_service(total_seconds)


def _build_copy_duration_payload(
    *,
    base_time_seconds: int | float | Decimal | None,
    runs_requested: int,
    copies_requested: int,
    structure_time_bonus_percent: Decimal | int | float | str | None = None,
    character_time_bonus_percent: Decimal | int | float | str | None = None,
) -> dict[str, Any] | None:
    return _build_copy_duration_payload_service(
        base_time_seconds=base_time_seconds,
        runs_requested=runs_requested,
        copies_requested=copies_requested,
        structure_time_bonus_percent=structure_time_bonus_percent,
        character_time_bonus_percent=character_time_bonus_percent,
    )


def _classify_bp_chat_message(message: BlueprintCopyMessage) -> str:
    return _classify_bp_chat_message_service(message)


def _offer_flow_deps() -> OfferFlowDeps:
    return OfferFlowDeps(
        ensure_offer_chat_fn=_ensure_offer_chat,
        close_request_chats_fn=_close_request_chats,
        strike_webhooks_fn=_strike_discord_webhook_messages_for_request,
        format_isk_amount_fn=_format_isk_amount,
        notify_user_fn=notify_user,
        get_type_name_fn=get_type_name,
        build_site_url_fn=build_site_url,
        record_offer_proposal_fn=_record_offer_proposal,
        close_offer_chat_if_exists_fn=_close_offer_chat_if_exists,
        finalize_all_rejected_fn=_finalize_request_if_all_rejected,
        messages_api=messages,
    )


def _record_offer_proposal(
    offer: BlueprintCopyOffer,
    *,
    proposer_role: str,
    amount: Decimal,
    sender: User,
    note: str = "",
) -> BlueprintCopyChat:
    return _record_offer_proposal_service(
        offer,
        proposer_role=proposer_role,
        amount=amount,
        sender=sender,
        note=note,
        ensure_offer_chat_fn=_ensure_offer_chat,
        format_isk_amount_fn=_format_isk_amount,
    )


def _finalize_conditional_offer(offer: BlueprintCopyOffer) -> None:
    finalize_offer_with_deps(offer, deps=_offer_flow_deps())


def _mark_offer_buyer_accept(offer: BlueprintCopyOffer) -> bool:
    return mark_buyer_accept_with_deps(offer, deps=_offer_flow_deps())


def _mark_offer_seller_accept(offer: BlueprintCopyOffer) -> bool:
    return mark_seller_accept_with_deps(offer, deps=_offer_flow_deps())


# --- Blueprint and job views ---
@indy_hub_access_required
@login_required
def personnal_bp_list(request, scope="character"):
    emit_view_analytics_event(view_name="industry.personnal_bp_list", request=request)
    # Copy of the old blueprints_list code
    owner_options = []
    scope_param = request.GET.get("scope")
    scope = (scope_param or scope or "character").lower()
    if scope not in {"character", "corporation"}:
        scope = "character"

    is_corporation_scope = scope == "corporation"
    has_corporate_perm = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    accessible_corporation_ids: list[int] = []
    if is_corporation_scope:
        accessible_corporation_ids = sorted(get_viewable_corporation_ids(request.user))

    requires_own_tokens = not is_corporation_scope or has_corporate_perm
    required_scopes = (
        list(CORP_BLUEPRINT_SCOPE_SET)
        if is_corporation_scope
        else [BLUEPRINT_SCOPE, STRUCTURE_SCOPE]
    )
    if requires_own_tokens and not _has_required_scopes(request.user, required_scopes):
        messages.warning(
            request,
            _("ESI: missing blueprint scope/tokens. Add a character in the ESI tab."),
        )
    try:
        # Check if we need to sync data
        force_update = request.GET.get("refresh") == "1"
        if force_update:
            logger.info(
                f"User {request.user.username} requested blueprint refresh; enqueuing Celery task"
            )
            if is_corporation_scope and not has_corporate_perm:
                logger.info(
                    "Ignoring manual corporate blueprint refresh for %s due to missing permission",
                    request.user.username,
                )
            else:
                scheduled, remaining, reason = request_manual_refresh(
                    MANUAL_REFRESH_KIND_BLUEPRINTS,
                    request.user.id,
                    priority=5,
                    scope=scope,
                    check_active=not is_corporation_scope,
                )
                if scheduled:
                    messages.success(
                        request,
                        _(
                            "Blueprint refresh scheduled. Updated data will appear shortly."
                        ),
                    )
                elif reason == "in_progress":
                    messages.info(
                        request,
                        _(
                            "A blueprint refresh is already running. Updated data will appear shortly."
                        ),
                    )
                elif remaining is None:
                    messages.warning(
                        request,
                        _("Blueprint refresh skipped: user considered inactive."),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "A blueprint refresh was already requested recently. Please try again in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
    except Exception as e:
        logger.error(f"Error handling blueprint refresh: {e}")
        messages.error(request, f"Error handling blueprint refresh: {e}")

    if is_corporation_scope and not accessible_corporation_ids:
        messages.error(
            request,
            _("You do not have permission to view corporation blueprints."),
        )
        return redirect(reverse("indy_hub:personnal_bp_list"))

    search = request.GET.get("search", "")
    efficiency_filter = request.GET.get("efficiency", "")
    type_filter = request.GET.get("type", "")
    owner_filter = request.GET.get("owner")
    if owner_filter is None:
        owner_filter = request.GET.get("character", "")
    owner_filter = owner_filter.strip() if isinstance(owner_filter, str) else ""
    activity_id = request.GET.get("activity_id", "")
    sort_order = request.GET.get("order", "asc")
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 25))

    if activity_id == "1":
        filter_names = ["manufacturing"]
    elif activity_id == "9,11":
        filter_names = ["reaction"]
    else:
        filter_names = ["manufacturing", "reaction"]

    try:
        activity_placeholders = ",".join(["%s"] * len(filter_names))
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT activity.blueprint_item_type_id
                FROM eve_sde_blueprintactivityproduct p
                JOIN eve_sde_blueprintactivity activity ON activity.id = p.blueprint_activity_id
                JOIN eve_sde_itemtype t ON t.id = activity.blueprint_item_type_id
                WHERE activity.activity IN ({activity_placeholders})
                                AND COALESCE(t.published, 0) = 1
                """,
                filter_names,
            )
            allowed_type_ids = [row[0] for row in cursor.fetchall()]

        owner_kind_filter = (
            Blueprint.OwnerKind.CORPORATION
            if is_corporation_scope
            else Blueprint.OwnerKind.CHARACTER
        )
        if is_corporation_scope:
            base_blueprints_qs = Blueprint.objects.filter(
                owner_kind=owner_kind_filter,
                corporation_id__in=accessible_corporation_ids,
            )
        else:
            base_blueprints_qs = Blueprint.objects.filter(
                owner_user=request.user,
                owner_kind=owner_kind_filter,
            )

        if is_corporation_scope:
            owner_pairs = (
                base_blueprints_qs.exclude(corporation_id__isnull=True)
                .values_list("corporation_id", "corporation_name")
                .distinct()
            )
            owner_options = []
            for corp_id, corp_name in owner_pairs:
                if not corp_id:
                    continue
                display_name = (
                    corp_name or get_corporation_name(corp_id) or str(corp_id)
                )
                owner_options.append((corp_id, display_name))
        else:
            owner_ids = (
                base_blueprints_qs.exclude(character_id__isnull=True)
                .values_list("character_id", flat=True)
                .distinct()
            )
            owner_options = []
            for cid in owner_ids:
                if not cid:
                    continue
                display_name = get_character_name(cid) or str(cid)
                owner_options.append((cid, display_name))

        blueprints_qs = base_blueprints_qs.filter(type_id__in=allowed_type_ids)
        if search:
            blueprints_qs = blueprints_qs.filter(
                Q(type_name__icontains=search) | Q(type_id__icontains=search)
            )
        if efficiency_filter == "perfect":
            blueprints_qs = blueprints_qs.filter(
                material_efficiency__gte=10, time_efficiency__gte=20
            )
        elif efficiency_filter == "researched":
            blueprints_qs = blueprints_qs.filter(
                Q(material_efficiency__gt=0) | Q(time_efficiency__gt=0)
            )
        elif efficiency_filter == "unresearched":
            blueprints_qs = blueprints_qs.filter(
                material_efficiency=0, time_efficiency=0
            )
        if type_filter == "original":
            blueprints_qs = blueprints_qs.filter(
                bp_type__in=[Blueprint.BPType.ORIGINAL, Blueprint.BPType.REACTION]
            )
        elif type_filter == "copy":
            blueprints_qs = blueprints_qs.filter(bp_type=Blueprint.BPType.COPY)
        if owner_filter:
            try:
                owner_id = int(owner_filter)
                if is_corporation_scope:
                    blueprints_qs = blueprints_qs.filter(corporation_id=owner_id)
                else:
                    blueprints_qs = blueprints_qs.filter(character_id=owner_id)
            except (TypeError, ValueError):
                logger.warning(
                    "[BLUEPRINTS FILTER] Invalid owner filter: %s", owner_filter
                )
        blueprints_qs = blueprints_qs.order_by("type_name")

        quantity_normalized_expr = Case(
            When(quantity__in=[-1, -2], then=1),
            When(quantity__isnull=True, then=0),
            When(quantity__lt=0, then=0),
            default="quantity",
        )

        grouped_rows = list(
            blueprints_qs.values(
                "type_id",
                "material_efficiency",
                "time_efficiency",
                "bp_type",
            )
            .annotate(
                type_name=Max("type_name"),
                character_id=Max("character_id"),
                corporation_id=Max("corporation_id"),
                character_name=Max("character_name"),
                location_id=Max("location_id"),
                location_flag=Max("location_flag"),
                location_name=Max("location_name"),
                quantity_value=Sum(quantity_normalized_expr),
                total_runs=Sum(
                    Case(
                        When(
                            bp_type=Blueprint.BPType.COPY,
                            then=Case(
                                When(runs__isnull=True, then=0),
                                When(runs__lt=0, then=0),
                                default="runs",
                            )
                            * Case(
                                When(quantity__in=[-1, -2], then=1),
                                When(quantity__isnull=True, then=0),
                                When(quantity__lt=0, then=0),
                                default="quantity",
                            ),
                        ),
                        default=0,
                    )
                ),
            )
            .order_by("type_name", "material_efficiency", "time_efficiency", "bp_type")
        )

        bp_items = []
        total_original_quantity = 0
        total_copy_quantity = 0

        for row in grouped_rows:
            quantity_value = int(row.get("quantity_value") or 0)
            is_copy = row["bp_type"] == Blueprint.BPType.COPY
            is_reaction = row["bp_type"] == Blueprint.BPType.REACTION
            total_runs = int(row.get("total_runs") or 0)

            if is_copy:
                orig_quantity = 0
                copy_quantity = quantity_value
                total_copy_quantity += quantity_value
            else:
                orig_quantity = quantity_value
                copy_quantity = 0
                total_original_quantity += quantity_value

            bp_items.append(
                SimpleNamespace(
                    type_id=row["type_id"],
                    type_name=row.get("type_name") or str(row["type_id"]),
                    material_efficiency=row["material_efficiency"],
                    time_efficiency=row["time_efficiency"],
                    bp_type=row["bp_type"],
                    is_copy=is_copy,
                    is_reaction=is_reaction,
                    is_original=not is_copy and not is_reaction,
                    orig_quantity=orig_quantity,
                    copy_quantity=copy_quantity,
                    total_quantity=orig_quantity + copy_quantity,
                    total_runs=total_runs,
                    runs=total_runs,
                    character_id=row.get("character_id"),
                    corporation_id=row.get("corporation_id"),
                    character_name=row.get("character_name") or "",
                    location_id=row.get("location_id"),
                    location_flag=row.get("location_flag") or "",
                    location_name=row.get("location_name") or "",
                )
            )

        paginator = Paginator(bp_items, per_page)
        blueprints_page = paginator.get_page(page)
        page_bp_items = list(blueprints_page.object_list)

        location_ids = {bp.location_id for bp in page_bp_items if bp.location_id}

        def _populate_location_map(ids: set[int], location_map: dict[int, str]) -> None:
            if not ids:
                return

            # AA Example App
            from indy_hub.models import CachedStructureName

            for structure_id, name in CachedStructureName.objects.filter(
                structure_id__in=ids
            ).values_list("structure_id", "name"):
                if (
                    structure_id
                    and name
                    and not str(name).startswith(PLACEHOLDER_PREFIX)
                ):
                    location_map[int(structure_id)] = str(name)

        location_map: dict[int, str] = {}
        _populate_location_map(location_ids, location_map)

        container_root_map: dict[int, int] = {}
        if not is_corporation_scope and location_ids:
            unresolved_ids = location_ids - set(location_map.keys())
            if unresolved_ids:
                # AA Example App
                from indy_hub.models import CachedCharacterAsset

                container_pairs = (
                    CachedCharacterAsset.objects.filter(
                        user=request.user,
                        item_id__in=unresolved_ids,
                    )
                    .exclude(location_id__isnull=True)
                    .values_list("item_id", "location_id")
                )
                for item_id, root_location_id in container_pairs:
                    if not item_id or not root_location_id:
                        continue
                    container_root_map[int(item_id)] = int(root_location_id)

                root_ids = set(container_root_map.values()) - set(location_map.keys())
                if root_ids:
                    _populate_location_map(root_ids, location_map)

        for bp in page_bp_items:
            effective_location_id = container_root_map.get(
                bp.location_id, bp.location_id
            )
            resolved_name = location_map.get(effective_location_id)

            if resolved_name:
                bp.location_name = resolved_name

            location_path = resolved_name
            if not location_path and effective_location_id != bp.location_id:
                location_path = str(effective_location_id)
            bp.location_path = location_path or bp.location_flag

        owner_map = {owner_id: name for owner_id, name in owner_options}
        owner_field = "corporation_id" if is_corporation_scope else "character_id"
        owner_icon = (
            "fas fa-building" if is_corporation_scope else "fas fa-user-astronaut"
        )

        for bp in page_bp_items:
            owner_id_value = getattr(bp, owner_field)
            owner_display = owner_map.get(owner_id_value, owner_id_value)
            setattr(bp, "owner_display", owner_display)
            setattr(bp, "owner_id", owner_id_value)
            if is_corporation_scope:
                bp.character_name = owner_display
        total_blueprints = total_original_quantity + total_copy_quantity
        originals_count = total_original_quantity
        copies_count = total_copy_quantity

        activity_labels = {
            1: "Manufacturing",
            3: "TE Research",
            4: "ME Research",
            5: "Copying",
            8: "Invention",
            9: "Reactions",
            11: "Reactions",
        }
        activity_options = [
            ("", "All Activities"),
            ("1", activity_labels[1]),
            ("9,11", activity_labels[9]),
        ]
        context = {
            "blueprints": blueprints_page,
            "statistics": {
                "total_count": total_blueprints,
                "original_count": originals_count,
                "copy_count": copies_count,
                "perfect_me_count": blueprints_qs.filter(
                    material_efficiency__gte=10
                ).count(),
                "perfect_te_count": blueprints_qs.filter(
                    time_efficiency__gte=20
                ).count(),
                "owner_count": len(owner_options),
            },
            "current_filters": {
                "search": search,
                "efficiency": efficiency_filter,
                "type": type_filter,
                "owner": owner_filter,
                "activity_id": activity_id,
                "sort": request.GET.get("sort", "type_name"),
                "order": sort_order,
                "per_page": per_page,
            },
            "per_page_options": [10, 25, 50, 100, 200],
            "activity_options": activity_options,
            "owner_options": owner_options,
            "owner_icon": owner_icon,
            "scope": scope,
            "is_corporation_scope": is_corporation_scope,
            "owner_label": _("Corporation") if is_corporation_scope else _("Character"),
            "scope_title": (
                _("Corporation Blueprints")
                if is_corporation_scope
                else _("My Blueprints")
            ),
            "scope_description": (
                _("Review blueprints imported from corporation hangars.")
                if is_corporation_scope
                else _("Manage your blueprint library and research progress")
            ),
            "scope_urls": {
                "character": reverse("indy_hub:personnal_bp_list"),
                "corporation": reverse("indy_hub:corporation_bp_list"),
            },
            "can_manage_corp_bp_requests": has_corporate_perm,
            "back_to_overview_url": reverse("indy_hub:index"),
        }
        context.update(
            build_nav_context(
                request.user,
                active_tab="blueprints",
                can_manage_corp=has_corporate_perm,
            )
        )

        return render(request, "indy_hub/blueprints/Personnal_BP_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying blueprints: {e}")
        messages.error(request, f"Error displaying blueprints: {e}")
        return redirect("indy_hub:index")


@indy_hub_access_required
@login_required
def all_bp_list(request):
    emit_view_analytics_event(view_name="industry.all_bp_list", request=request)
    search = request.GET.get("search", "").strip()
    activity_id = request.GET.get("activity_id", "")
    market_group_id = request.GET.get("market_group_id", "")
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.GET.get("per_page", 25))
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in {10, 25, 50, 100, 200}:
        per_page = 25
    page = max(page, 1)
    offset = (page - 1) * per_page

    where_clauses = ["COALESCE(t.published, 0) = 1"]
    params: list[Any] = []

    if activity_id == "1":
        where_clauses.append("a.activity = 'manufacturing'")
    elif activity_id == "reactions":
        where_clauses.append("a.activity = 'reaction'")
    else:
        where_clauses.append("a.activity IN ('manufacturing', 'reaction')")

    if search:
        where_clauses.append("(t.name LIKE %s OR t.id LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if market_group_id:
        where_clauses.append("t.group_id = %s")
        params.append(market_group_id)

    where_sql = " AND ".join(where_clauses)
    base_from_sql = (
        "FROM eve_sde_itemtype t "
        "JOIN eve_sde_blueprintactivity a ON t.id = a.blueprint_item_type_id"
    )
    count_sql = f"SELECT COUNT(DISTINCT t.id) {base_from_sql} WHERE {where_sql}"
    data_sql = (
        f"SELECT DISTINCT t.id, t.name {base_from_sql} "
        f"WHERE {where_sql} ORDER BY t.name ASC LIMIT %s OFFSET %s"
    )

    # Fixed activity options for the dropdown.
    raw_activity_options = [
        (IndustryActivityMixin.ACTIVITY_MANUFACTURING, "Manufacturing"),
        (IndustryActivityMixin.ACTIVITY_REACTIONS, "Reactions"),
        (IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY, "Reactions"),
    ]
    # Apply consistent activity labels
    activity_labels = {
        1: "Manufacturing",
        3: "TE Research",
        4: "ME Research",
        5: "Copying",
        8: "Invention",
        9: "Reactions",
        11: "Reactions",
    }
    # Build grouped activity options: All, Manufacturing, Reactions
    raw_ids = [opt[0] for opt in raw_activity_options]
    activity_options = [("", "All Activities")]
    # Manufacturing
    activity_options.append(("1", activity_labels[1]))
    # Reactions group
    if any(r in raw_ids for r in [9, 11]):
        activity_options.append(("reactions", activity_labels[9]))

    blueprints = []
    total_blueprints = 0
    market_group_options: list[tuple[int, str]] = []

    try:
        with connection.cursor() as cursor:
            cursor.execute(count_sql, params)
            total_blueprints = int(cursor.fetchone()[0] or 0)

            cursor.execute(data_sql, [*params, per_page, offset])
            blueprints = [
                {
                    "type_id": row[0],
                    "type_name": row[1],
                }
                for row in cursor.fetchall()
            ]

        paginator = Paginator(range(total_blueprints), per_page)
        blueprints_page = paginator.get_page(page)
        blueprints_page.object_list = blueprints

        with connection.cursor() as cursor:
            market_group_sql = (
                "SELECT DISTINCT t.group_id, g.name "
                "FROM eve_sde_itemtype t "
                "JOIN eve_sde_blueprintactivity a ON t.id = a.blueprint_item_type_id "
                "JOIN eve_sde_itemgroup g ON t.group_id = g.id "
                f"WHERE {where_sql} AND t.group_id IS NOT NULL "
                "ORDER BY g.name"
            )
            cursor.execute(market_group_sql, params)
            market_group_options = [(row[0], row[1]) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching blueprints: {e}")
        messages.error(request, f"Error fetching blueprints: {e}")
        paginator = Paginator([], per_page)
        blueprints_page = paginator.get_page(page)
        market_group_options = []

    context = {
        "blueprints": blueprints_page,
        "filters": {
            "search": search,
            "activity_id": activity_id,
            "market_group_id": market_group_id,
        },
        "activity_options": activity_options,
        "market_group_options": market_group_options,
        "per_page_options": [10, 25, 50, 100, 200],
        "back_to_overview_url": reverse("indy_hub:index"),
    }
    context.update(build_nav_context(request.user, active_tab="blueprints"))

    return render(request, "indy_hub/blueprints/All_BP_list.html", context)


@indy_hub_access_required
@login_required
def personnal_job_list(request, scope="character"):
    emit_view_analytics_event(view_name="industry.personnal_job_list", request=request)
    owner_options: list[tuple[int, str]] = []
    scope_param = request.GET.get("scope")
    scope = (scope_param or scope or "character").lower()
    if scope not in {"character", "corporation"}:
        scope = "character"

    is_corporation_scope = scope == "corporation"
    has_corporate_perm = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    viewable_corporation_job_ids = sorted(
        get_viewable_corporation_job_ids(request.user)
    )
    accessible_corporation_ids: list[int] = []
    if is_corporation_scope:
        accessible_corporation_ids = viewable_corporation_job_ids

    requires_own_tokens = not is_corporation_scope or has_corporate_perm
    required_scopes = (
        list(CORP_JOBS_SCOPE_SET)
        if is_corporation_scope
        else [JOBS_SCOPE, STRUCTURE_SCOPE]
    )
    if requires_own_tokens and not _has_required_scopes(request.user, required_scopes):
        messages.warning(
            request,
            _("ESI: missing job scope/tokens. Add a character in the ESI tab."),
        )
    try:
        force_update = request.GET.get("refresh") == "1"
        force_skill_refresh = request.GET.get("force_refresh") == "1" or force_update
        if force_update:
            logger.info(
                f"User {request.user.username} requested jobs refresh; enqueuing Celery task"
            )
            if is_corporation_scope and not has_corporate_perm:
                logger.info(
                    "Ignoring manual corporate jobs refresh for %s due to missing permission",
                    request.user.username,
                )
            else:
                scheduled, remaining, reason = request_manual_refresh(
                    MANUAL_REFRESH_KIND_JOBS,
                    request.user.id,
                    priority=5,
                    scope=scope,
                    check_active=not is_corporation_scope,
                )
                if scheduled:
                    messages.success(
                        request,
                        _(
                            "Industry jobs refresh scheduled. Updated data will appear shortly."
                        ),
                    )
                elif reason == "in_progress":
                    messages.info(
                        request,
                        _(
                            "An industry jobs refresh is already running. Updated data will appear shortly."
                        ),
                    )
                elif remaining is None:
                    messages.warning(
                        request,
                        _("Industry jobs refresh skipped: user considered inactive."),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "An industry jobs refresh was already requested recently. Please try again in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
    except Exception as e:
        logger.error(f"Error handling jobs refresh: {e}")
        messages.error(request, f"Error handling jobs refresh: {e}")

    if is_corporation_scope and not accessible_corporation_ids:
        messages.error(
            request,
            _("You do not have permission to view corporation industry jobs."),
        )
        return redirect(reverse("indy_hub:personnal_job_list"))

    owner_filter = request.GET.get("owner")
    if owner_filter is None:
        owner_filter = request.GET.get("character", "")
    owner_filter = owner_filter.strip() if isinstance(owner_filter, str) else ""

    search = request.GET.get("search", "")
    status_filter = request.GET.get("status", "")
    activity_filter = request.GET.get("activity", "")
    sort_by = request.GET.get("sort", "start_date")
    sort_order = request.GET.get("order", "desc")
    page = _parse_bounded_int(request.GET.get("page", 1), default=1)
    max_per_page = (
        INDUSTRY_JOBS_PER_PAGE_MAX_CORPORATION
        if is_corporation_scope
        else INDUSTRY_JOBS_PER_PAGE_MAX_CHARACTER
    )
    per_page = _parse_bounded_int(
        request.GET.get("per_page", INDUSTRY_JOBS_PER_PAGE_DEFAULT),
        default=INDUSTRY_JOBS_PER_PAGE_DEFAULT,
        maximum=max_per_page,
    )

    owner_kind_filter = (
        Blueprint.OwnerKind.CORPORATION
        if is_corporation_scope
        else Blueprint.OwnerKind.CHARACTER
    )

    if is_corporation_scope:
        base_jobs_qs = IndustryJob.objects.filter(
            owner_kind=owner_kind_filter,
            corporation_id__in=accessible_corporation_ids,
        )
    else:
        base_jobs_qs = IndustryJob.objects.filter(
            owner_user=request.user,
            owner_kind=owner_kind_filter,
        )
    if is_corporation_scope:
        owner_pairs = (
            base_jobs_qs.exclude(corporation_id__isnull=True)
            .values_list("corporation_id", "corporation_name")
            .distinct()
        )
        owner_options = []
        for corp_id, corp_name in owner_pairs:
            if not corp_id:
                continue
            display_name = corp_name or get_corporation_name(corp_id) or str(corp_id)
            owner_options.append((corp_id, display_name))
    else:
        owner_ids = (
            base_jobs_qs.exclude(character_id__isnull=True)
            .values_list("character_id", flat=True)
            .distinct()
        )
        owner_options = []
        for cid in owner_ids:
            if not cid:
                continue
            display_name = get_character_name(cid) or str(cid)
            owner_options.append((cid, display_name))

    per_page_options = [10, 25, 50, 100]
    if is_corporation_scope:
        per_page_options.append(200)

    jobs_qs = base_jobs_qs
    now = timezone.now()
    owner_map = {owner_id: name for owner_id, name in owner_options}
    owner_field = "corporation_id" if is_corporation_scope else "character_id"
    owner_icon = "fas fa-building" if is_corporation_scope else "fas fa-user-astronaut"
    owner_count = len(owner_options)
    try:
        if search:
            job_id_q = Q(job_id__icontains=search) if search.isdigit() else Q()
            owner_name_matches = [
                owner_id
                for owner_id, name in owner_map.items()
                if name and search.lower() in name.lower()
            ]
            owner_name_q = (
                Q(**{f"{owner_field}__in": owner_name_matches})
                if owner_name_matches
                else Q()
            )
            jobs_qs = jobs_qs.filter(
                Q(blueprint_type_name__icontains=search)
                | Q(product_type_name__icontains=search)
                | Q(activity_name__icontains=search)
                | job_id_q
                | owner_name_q
            )
        if status_filter:
            status_filter = status_filter.strip().lower()
            if status_filter == "active":
                jobs_qs = jobs_qs.filter(status="active", end_date__gt=now)
            elif status_filter == "completed":
                jobs_qs = jobs_qs.filter(end_date__lte=now)
        if activity_filter:
            try:
                activity_ids = {
                    int(part.strip())
                    for part in str(activity_filter).split(",")
                    if part.strip()
                }
                if activity_ids:
                    jobs_qs = jobs_qs.filter(activity_id__in=activity_ids)
            except (TypeError, ValueError):
                logger.warning(
                    "[JOBS FILTER] Invalid activity filter value: '%s'",
                    activity_filter,
                )
        if owner_filter:
            try:
                owner_filter_int = int(owner_filter.strip())
                jobs_qs = jobs_qs.filter(**{owner_field: owner_filter_int})
            except (ValueError, TypeError):
                logger.warning(
                    "[JOBS FILTER] Invalid owner filter value: '%s'", owner_filter
                )
        if sort_order == "desc":
            sort_by = f"-{sort_by}"
        jobs_qs = jobs_qs.order_by(sort_by)
        paginator = Paginator(jobs_qs, per_page)
        jobs_page = paginator.get_page(page)

        # Optimize: Consolidate 3 count() queries into 1 aggregate()
        job_stats = jobs_qs.aggregate(
            total=Count("id"),
            active=Count(Case(When(status="active", end_date__gt=now, then=1))),
            completed=Count(Case(When(end_date__lte=now, then=1))),
        )

        statistics = {
            "total": job_stats["total"],
            "active": job_stats["active"],
            "completed": job_stats["completed"],
        }
        # Only show computed statuses for filtering: 'active' and 'completed'
        statuses = ["active", "completed"]
        # Static mapping for activity filter with labels
        activity_labels = {
            1: "Manufacturing",
            3: "TE Research",
            4: "ME Research",
            5: "Copying",
            "waiting_on_you": {
                "label": _("Confirm agreement"),
                "badge": "bg-warning text-dark",
                "hint": _(
                    "The buyer already accepted your terms. Confirm in chat to lock in the agreement."
                ),
            },
            8: "Invention",
            9: "Reactions",
        }
        # Include only activities from base jobs (unfiltered) for filter options
        present_ids = base_jobs_qs.values_list("activity_id", flat=True).distinct()
        activities = [
            (str(aid), activity_labels.get(aid, str(aid))) for aid in present_ids
        ]
        # Removed update status tracking since unified settings don't track this
        jobs_on_page = list(jobs_page.object_list)
        blueprint_ids = [job.blueprint_id for job in jobs_on_page if job.blueprint_id]
        if is_corporation_scope:
            blueprint_map = {
                bp.item_id: bp
                for bp in Blueprint.objects.filter(
                    owner_kind=Blueprint.OwnerKind.CORPORATION,
                    corporation_id__in=accessible_corporation_ids,
                    item_id__in=blueprint_ids,
                )
            }
        else:
            blueprint_map = {
                bp.item_id: bp
                for bp in Blueprint.objects.filter(
                    owner_user=request.user,
                    owner_kind=owner_kind_filter,
                    item_id__in=blueprint_ids,
                )
            }

        activity_definitions = [
            {
                "key": "manufacturing",
                "activity_ids": {1},
                "title": _("Manufacturing"),
                "subtitle": _("Mass-produce items and hulls for your hangars."),
                "icon": "fas fa-industry",
                "chip": _("MANUFACTURING"),
                "badge_variant": "bg-warning text-white",
            },
            {
                "key": "research_te",
                "activity_ids": {3},
                "title": _("Time Efficiency Research"),
                "subtitle": _("Improve blueprint TE levels to reduce job durations."),
                "icon": "fas fa-stopwatch",
                "chip": "TE",
                "badge_variant": "bg-success text-white",
            },
            {
                "key": "research_me",
                "activity_ids": {4},
                "title": _("Material Efficiency Research"),
                "subtitle": _("Raise ME levels to save materials on future builds."),
                "icon": "fas fa-flask",
                "chip": "ME",
                "badge_variant": "bg-success text-white",
            },
            {
                "key": "copying",
                "activity_ids": {5},
                "title": _("Copying"),
                "subtitle": _(
                    "Generate blueprint copies ready for production or invention."
                ),
                "icon": "fas fa-copy",
                "chip": _("COPY"),
                "badge_variant": "bg-info text-white",
            },
            {
                "key": "invention",
                "activity_ids": {8},
                "title": _("Invention"),
                "subtitle": _(
                    "Transform tech I copies into advanced tech II blueprints."
                ),
                "icon": "fas fa-bolt",
                "chip": "INV",
                "badge_variant": "bg-dark text-white",
            },
            {
                "key": "reactions",
                "activity_ids": {9, 11},
                "title": _("Reactions"),
                "subtitle": _(
                    "Process raw materials through biochemical and polymer reactions."
                ),
                "icon": "fas fa-vials",
                "chip": _("REACTION"),
                "badge_variant": "bg-danger text-white",
            },
            {
                "key": "other",
                "activity_ids": set(),
                "title": _("Other Activities"),
                "subtitle": _(
                    "Specialised jobs that fall outside the main categories."
                ),
                "icon": "fas fa-tools",
                "chip": _("Other"),
                "badge_variant": "bg-secondary text-white",
            },
        ]

        activity_meta_by_key = {meta["key"]: meta for meta in activity_definitions}
        activity_key_by_id = {}
        for meta in activity_definitions:
            for aid in meta["activity_ids"]:
                activity_key_by_id[aid] = meta["key"]

        grouped_jobs = defaultdict(list)

        for job in jobs_on_page:
            activity_key = activity_key_by_id.get(job.activity_id, "other")
            activity_meta = activity_meta_by_key[activity_key]
            setattr(job, "activity_meta", activity_meta)
            owner_value = getattr(job, owner_field)
            owner_display = owner_map.get(owner_value, owner_value)
            setattr(job, "display_owner_name", owner_display)
            setattr(job, "display_character_name", owner_display)
            status_label = _("Completed") if job.is_completed else job.status.title()
            setattr(job, "status_label", status_label)
            setattr(job, "probability_percent", None)
            if job.probability is not None:
                try:
                    setattr(job, "probability_percent", round(job.probability * 100, 1))
                except TypeError:
                    setattr(job, "probability_percent", None)

            blueprint = blueprint_map.get(job.blueprint_id)
            research_details = None
            runs_count = job.runs or 0
            if job.activity_id in {3, 4}:
                if job.activity_id == 3:
                    current_value = blueprint.time_efficiency if blueprint else None
                    max_value = 20
                    attr_label = "TE"
                    per_run_gain = 2
                else:
                    current_value = blueprint.material_efficiency if blueprint else None
                    max_value = 10
                    attr_label = "ME"
                    per_run_gain = 1

                runs_count = max(runs_count, 0)
                completed_runs = job.successful_runs or 0
                if completed_runs < 0:
                    completed_runs = 0
                if runs_count:
                    completed_runs = min(completed_runs, runs_count)

                total_potential_gain = runs_count * per_run_gain

                base_value = None
                target_value = None
                effective_gain = total_potential_gain

                if current_value is not None:
                    inferred_start = current_value - (completed_runs * per_run_gain)
                    base_value = max(0, min(max_value, inferred_start))
                    projected_target = base_value + total_potential_gain
                    target_value = min(max_value, projected_target)
                    effective_gain = max(0, target_value - base_value)

                research_details = {
                    "attribute": attr_label,
                    "base": base_value,
                    "target": target_value,
                    "increments": runs_count,
                    "level_gain": effective_gain,
                    "max": max_value,
                }
            setattr(job, "research_details", research_details)

            copy_details = None
            if job.activity_id == 5:
                copy_details = {
                    "runs": job.runs,
                    "licensed_runs": job.licensed_runs,
                }
            setattr(job, "copy_details", copy_details)

            setattr(
                job,
                "output_name",
                job.product_type_name or job.product_type_id,
            )
            grouped_jobs[activity_key].append(job)

        job_groups = [
            {
                "key": meta["key"],
                "title": meta["title"],
                "subtitle": meta["subtitle"],
                "icon": meta["icon"],
                "chip": meta["chip"],
                "badge_variant": meta["badge_variant"],
                "jobs": grouped_jobs.get(meta["key"], []),
            }
            for meta in activity_definitions
            if grouped_jobs.get(meta["key"])
        ]

        slot_overview_rows = _build_slot_overview_rows(
            request.user,
            allow_live_fetch=force_skill_refresh,
            force_refresh=force_skill_refresh,
        )
        latest_skill_snapshot_at = (
            IndustrySkillSnapshot.objects.filter(owner_user=request.user)
            .aggregate(last_updated=Max("last_updated"))
            .get("last_updated")
        )
        latest_jobs_refresh_at = base_jobs_qs.aggregate(
            last_updated=Max("last_updated")
        ).get("last_updated")
        last_data_refresh_at = latest_skill_snapshot_at or latest_jobs_refresh_at
        if latest_skill_snapshot_at and latest_jobs_refresh_at:
            last_data_refresh_at = max(latest_skill_snapshot_at, latest_jobs_refresh_at)
        context = {
            "jobs": jobs_page,
            "statistics": statistics,
            "owner_count": owner_count,
            "statuses": statuses,
            "activities": activities,
            "current_filters": {
                "search": search,
                "status": status_filter,
                "activity": activity_filter,
                "owner": owner_filter,
                "sort": request.GET.get("sort", "start_date"),
                "order": sort_order,
                "per_page": per_page,
            },
            "per_page_options": per_page_options,
            "jobs_page": jobs_page,
            "job_groups": job_groups,
            "has_job_results": bool(job_groups),
            "owner_options": owner_options,
            "owner_icon": owner_icon,
            "scope": scope,
            "is_corporation_scope": is_corporation_scope,
            "owner_label": _("Corporation") if is_corporation_scope else _("Character"),
            "scope_title": (
                _("Corporation Jobs") if is_corporation_scope else _("Industry Jobs")
            ),
            "scope_description": (
                _("Monitor industry jobs running on behalf of your corporations.")
                if is_corporation_scope
                else _("Track your industry jobs and progress in real time")
            ),
            "scope_urls": {
                "character": reverse("indy_hub:personnal_job_list"),
                "corporation": reverse("indy_hub:corporation_job_list"),
            },
            "can_manage_corp_bp_requests": has_corporate_perm,
            "can_view_corporation_jobs": bool(viewable_corporation_job_ids),
            "slot_overview_rows": slot_overview_rows,
            "slot_overview_summary": _build_slot_overview_summary(slot_overview_rows),
            "skills_scope": SKILLS_SCOPE,
            "last_data_refresh_at": last_data_refresh_at,
        }
        context.update(
            build_nav_context(
                request.user,
                active_tab="industry",
                can_manage_corp=has_corporate_perm,
                can_view_corporation_jobs_flag=bool(viewable_corporation_job_ids),
            )
        )
        context["current_dashboard"] = (
            "corporation" if is_corporation_scope else "personal"
        )
        context["back_to_overview_url"] = reverse("indy_hub:index")
        # progress_percent and display_eta now available via model properties in template
        return render(request, "indy_hub/industry/Personnal_Job_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying industry jobs: {e}")
        messages.error(request, f"Error displaying industry jobs: {e}")
        return redirect("indy_hub:index")


def collect_blueprints_with_level(blueprint_configs):
    """Annotate each blueprint config with a "level" matching the deepest branch depth."""
    # Map type_id -> blueprint config for quick lookup
    config_map = {bc["type_id"]: bc for bc in blueprint_configs}

    def get_level(type_id):
        bc = config_map.get(type_id)
        if bc is None:
            return 0
        # Return the stored value when already computed
        if bc.get("level") is not None:
            return bc["level"]
        # Retrieve children (materials) or an empty list when none are defined
        children = (
            [m["type_id"] for m in bc.get("materials", [])] if "materials" in bc else []
        )
        # Compute the level recursively
        level = 1 + max((get_level(child_id) for child_id in children), default=0)
        bc["level"] = level
        return level

    # Compute the level for each blueprint
    for bc in blueprint_configs:
        get_level(bc["type_id"])
    return blueprint_configs


@indy_hub_access_required
@login_required
def craft_bp(request, type_id):
    blueprint_name = get_type_name(type_id) or str(type_id)
    try:
        num_runs = int(request.GET.get("runs", 1))
        if num_runs < 1:
            num_runs = 1
    except Exception:
        num_runs = 1

    try:
        me = int(request.GET.get("me", 0))
    except ValueError:
        me = 0
    try:
        te = int(request.GET.get("te", 0))
    except ValueError:
        te = 0
    me = max(0, min(me, 10))
    te = max(0, min(te, 20))

    active_tab = str(request.GET.get("active_tab") or "materials")
    selected_entries = [
        {
            "type_id": int(type_id),
            "type_name": blueprint_name,
            "quantity": max(1, num_runs),
            "category_key": "manual",
            "category_label": "Manual list",
            "category_order": 90,
            "source_line": blueprint_name,
            "is_craftable": True,
            "blueprint_type_id": int(type_id),
            "inclusion_mode": ProductionProjectItem.InclusionMode.PRODUCE,
        }
    ]
    workspace_state = build_temporary_project_workspace_state(
        source_kind=ProductionProject.SourceKind.MANUAL,
        source_name=blueprint_name,
    )
    workspace_state.update(
        {
            "blueprint_type_id": int(type_id),
            "blueprint_name": blueprint_name,
            "runs": num_runs,
            "active_tab": active_tab,
            "activeBlueprintTab": active_tab,
            "items": [],
            "blueprint_efficiencies": [
                {
                    "blueprint_type_id": int(type_id),
                    "material_efficiency": me,
                    "time_efficiency": te,
                }
            ],
            "custom_prices": [],
        }
    )
    temp_project_ref = create_temporary_project_workspace(
        user=request.user,
        name=blueprint_name,
        status=ProductionProject.Status.DRAFT,
        source_kind=ProductionProject.SourceKind.MANUAL,
        source_text=blueprint_name,
        source_name=blueprint_name,
        selected_entries=selected_entries,
        workspace_state=workspace_state,
        notes=LEGACY_SINGLE_BLUEPRINT_PROJECT_NOTE,
    )

    temp_project_url = reverse("indy_hub:craft_temp_project", args=[temp_project_ref])
    return redirect(temp_project_url)


@indy_hub_access_required
@login_required
def craft_project(request, project_ref):
    emit_view_analytics_event(view_name="industry.craft_project", request=request)

    try:
        normalized_project_ref = normalize_production_project_ref(project_ref)
    except ValueError:
        normalized_project_ref = ""

    project = get_object_or_404(
        ProductionProject.objects.prefetch_related("items"),
        project_ref=normalized_project_ref,
        user=request.user,
    )
    if "runs" in request.GET:
        try:
            runs_override = max(1, int(request.GET.get("runs") or 1))
        except (TypeError, ValueError):
            runs_override = 1
    else:
        runs_override = None
    refresh_from_current_sde = str(request.GET.get("refresh_sde") or "").lower() in {
        "1",
        "true",
        "yes",
    }

    workspace_state = strip_project_workspace_cache(project.workspace_state)
    active_tab = request.GET.get("active_tab") or workspace_state.get(
        "active_tab", "materials"
    )
    payload = None
    sde_has_changed = False
    favorite_ids = frozenset(
        UserFavoriteStructure.objects.filter(user=request.user).values_list(
            "structure_id", flat=True
        )
    )
    if runs_override is None and not refresh_from_current_sde:
        payload, sde_has_changed = get_cached_project_workspace_payload(project)
    if payload is None:
        payload = build_project_workspace_payload(
            project,
            skill_cache_ttl=SKILL_CACHE_TTL,
            me_te_overrides=parse_project_me_te_overrides(request.GET),
            final_output_quantity_overrides=parse_project_final_output_quantity_overrides(
                request.GET
            ),
            runs_override=runs_override,
            include_full_structure_options=False,
            favorite_structure_ids=favorite_ids,
        )
        sde_has_changed = False
    # Always patch so cached payloads reflect current favorite state
    _patch_payload_structure_planner_favorites(payload, favorite_ids)

    sde_refresh_url = ""
    if sde_has_changed:
        refresh_query = request.GET.copy()
        refresh_query["refresh_sde"] = "1"
        refresh_query["active_tab"] = active_tab
        sde_refresh_url = (
            f"{reverse('indy_hub:craft_project', args=[project.project_ref])}"
            f"?{refresh_query.urlencode()}"
        )

    render_workspace_state = dict(payload.get("workspace_state") or workspace_state)
    render_workspace_state["active_tab"] = active_tab
    stock_refresh_progress = _get_craft_project_stock_refresh_progress(request.user)

    payload.update(
        {
            "active_tab": active_tab,
            "workspace_state": render_workspace_state,
            "character_stock_snapshot": build_user_asset_inventory_snapshot(
                request.user,
                allow_refresh=False,
            ),
            "character_stock_refresh": stock_refresh_progress,
            "urls": {
                "save": reverse(
                    "indy_hub:save_production_project_workspace",
                    args=[project.project_ref],
                ),
                "load_list": reverse("indy_hub:production_simulations_list"),
                "load_config": None,
                "fuzzwork_price": reverse("indy_hub:fuzzwork_price"),
                "craft_bp_payload": reverse(
                    "indy_hub:production_project_payload",
                    args=[project.project_ref],
                ),
                "craft_stock_refresh_status": reverse(
                    "indy_hub:craft_project_stock_refresh_status"
                ),
                "structure_solar_system_search": reverse(
                    "indy_hub:industry_structure_solar_system_search"
                ),
                "craft_structure_jump_distances": reverse(
                    "indy_hub:craft_structure_jump_distances"
                ),
                "toggle_favorite_structure": reverse(
                    "indy_hub:toggle_favorite_structure"
                ),
            },
        }
    )

    final_outputs = payload.get("final_outputs") or []
    total_requested_quantity = sum(
        max(0, int(output.get("quantity") or 0)) for output in final_outputs
    )
    status_url = reverse(
        "indy_hub:set_production_project_status", args=[project.project_ref]
    )
    status_icon_map = {
        ProductionProject.Status.DRAFT: "fas fa-clock",
        ProductionProject.Status.SAVED: "fas fa-bookmark",
        ProductionProject.Status.ARCHIVED: "fas fa-box-archive",
    }
    current_status = project.status
    current_status_icon = status_icon_map.get(current_status, "fas fa-circle")
    current_status_label = escape(project.get_status_display())
    status_options_html = "".join(
        [
            (
                '<li><button type="button" class="dropdown-item project-status-option'
                f'{ " active" if value == current_status else "" }"'
                f' data-status="{escape(value)}">'
                f'<i class="{status_icon_map.get(value, "fas fa-circle")} me-2"></i>'
                f"{escape(str(label))}"
                "</button></li>"
            )
            for value, label in ProductionProject.Status.choices
        ]
    )
    craft_header_controls = mark_safe(
        "".join(
            [
                '<button id="saveSimulationBtn" class="btn btn-light btn-sm" type="button">',
                '<i class="fas fa-save me-1"></i>',
                escape(str(_("Save table"))),
                "</button>",
                '<div class="dropdown d-inline-block project-status-dropdown"'
                f' data-status-url="{escape(status_url)}"'
                f' data-current-status="{escape(current_status)}">',
                '<button type="button"'
                ' class="btn btn-sm btn-outline-light dropdown-toggle project-status-toggle"'
                ' data-bs-toggle="dropdown" aria-expanded="false"'
                f' title="{escape(str(_("Change project status")))}">',
                f'<i class="{current_status_icon} me-1 project-status-icon"></i>',
                f'<span class="project-status-label">{current_status_label}</span>',
                "</button>",
                '<ul class="dropdown-menu dropdown-menu-end">',
                status_options_html,
                "</ul>",
                "</div>",
                '<span class="badge bg-light text-dark border">',
                f"{escape(project.get_source_kind_display())}",
                "</span>",
            ]
        )
    )

    context = {
        "ui_version": "v2",
        "bp_type_id": payload.get("bp_type_id") or 0,
        "bp_name": project.name,
        "back_url": reverse("indy_hub:production_simulations_list"),
        "craft_header_controls": craft_header_controls,
        "deferred_shell": False,
        "active_tab": active_tab,
        "num_runs": payload.get("num_runs") or 1,
        "final_product_qty": payload.get("final_product_qty")
        or total_requested_quantity,
        "product_type_id": payload.get("product_type_id"),
        "me": payload.get("me") or 0,
        "te": payload.get("te") or 0,
        "materials": payload.get("materials") or [],
        "direct_materials": payload.get("direct_materials") or [],
        "materials_tree": payload.get("materials_tree") or [],
        "craft_cycles_summary": payload.get("craft_cycles_summary") or {},
        "blueprint_configs_grouped": payload.get("blueprint_configs_grouped") or [],
        "materials_by_group": payload.get("materials_by_group") or {},
        "production_time_map": payload.get("production_time_map") or {},
        "craft_character_advisor": payload.get("craft_character_advisor") or {},
        "structure_planner": payload.get("structure_planner") or {},
        "blueprint_payload": payload,
        "final_outputs": final_outputs,
        "is_project_workspace": True,
        "project": project,
        "sde_snapshot_has_changed": sde_has_changed,
        "sde_refresh_url": sde_refresh_url,
    }
    context.update(build_nav_context(request.user, active_tab="industry"))
    return render(request, "indy_hub/industry/Craft_BP_v2.html", context)


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def craft_temp_project(request, temp_project_ref):
    emit_view_analytics_event(view_name="industry.craft_temp_project", request=request)

    temp_state = get_temporary_project_workspace(request.user, temp_project_ref)
    if not temp_state:
        return redirect("indy_hub:production_simulations_list")

    if "runs" in request.GET:
        try:
            runs_override = max(1, int(request.GET.get("runs") or 1))
        except (TypeError, ValueError):
            runs_override = 1
    else:
        runs_override = None

    me_te_overrides = parse_project_me_te_overrides(request.GET)
    final_output_quantity_overrides = parse_project_final_output_quantity_overrides(
        request.GET
    )
    use_cached_payload = (
        runs_override is None
        and not me_te_overrides
        and not final_output_quantity_overrides
    )

    active_tab = request.GET.get("active_tab") or str(
        (temp_state.get("workspace_state") or {}).get("active_tab") or "materials"
    )
    payload = None
    favorite_ids = frozenset(
        UserFavoriteStructure.objects.filter(user=request.user).values_list(
            "structure_id", flat=True
        )
    )
    if use_cached_payload:
        cached_payload = (temp_state.get("workspace_state") or {}).get(
            PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY
        )
        if isinstance(cached_payload, dict):
            payload = dict(cached_payload)

    if payload is None:
        payload = build_temporary_project_payload(
            user=request.user,
            temp_project_ref=temp_project_ref,
            temp_state=temp_state,
            skill_cache_ttl=SKILL_CACHE_TTL,
            me_te_overrides=me_te_overrides,
            final_output_quantity_overrides=final_output_quantity_overrides,
            runs_override=runs_override,
            include_full_structure_options=False,
            favorite_structure_ids=favorite_ids,
        )
        if use_cached_payload:
            cached_state = dict(temp_state)
            cached_workspace_state = strip_project_workspace_cache(
                cached_state.get("workspace_state")
            )
            cached_workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY] = payload
            cached_state["workspace_state"] = cached_workspace_state
            set_temporary_project_workspace(temp_project_ref, cached_state)
    # Always patch so cached payloads reflect current favorite state
    _patch_payload_structure_planner_favorites(payload, favorite_ids)

    payload["temp_project_ref"] = str(temp_project_ref or "")
    payload["project_ref"] = str(payload.get("project_ref") or temp_project_ref or "")
    payload["is_temporary_project"] = True

    render_workspace_state = dict(payload.get("workspace_state") or {})
    render_workspace_state["active_tab"] = active_tab
    stock_refresh_progress = _get_craft_project_stock_refresh_progress(request.user)
    payload.update(
        {
            "active_tab": active_tab,
            "workspace_state": render_workspace_state,
            "character_stock_snapshot": build_user_asset_inventory_snapshot(
                request.user,
                allow_refresh=False,
            ),
            "character_stock_refresh": stock_refresh_progress,
            "urls": {
                "save": reverse(
                    "indy_hub:save_temporary_production_project_workspace",
                    args=[temp_project_ref],
                ),
                "load_list": reverse("indy_hub:production_simulations_list"),
                "load_config": None,
                "fuzzwork_price": reverse("indy_hub:fuzzwork_price"),
                "craft_bp_payload": reverse(
                    "indy_hub:temporary_production_project_payload",
                    args=[temp_project_ref],
                ),
                "craft_stock_refresh_status": reverse(
                    "indy_hub:craft_project_stock_refresh_status"
                ),
                "structure_solar_system_search": reverse(
                    "indy_hub:industry_structure_solar_system_search"
                ),
                "craft_structure_jump_distances": reverse(
                    "indy_hub:craft_structure_jump_distances"
                ),
                "toggle_favorite_structure": reverse(
                    "indy_hub:toggle_favorite_structure"
                ),
                "update_workspace_state": reverse(
                    "indy_hub:update_temporary_project_workspace_state",
                    args=[temp_project_ref],
                ),
            },
        }
    )

    final_outputs = payload.get("final_outputs") or []
    total_requested_quantity = sum(
        max(0, int(output.get("quantity") or 0)) for output in final_outputs
    )
    source_kind_value = str(
        temp_state.get("source_kind") or ProductionProject.SourceKind.MANUAL
    )
    try:
        source_kind_label = ProductionProject.SourceKind(source_kind_value).label
    except ValueError:
        source_kind_label = source_kind_value
    craft_header_controls = mark_safe(
        "".join(
            [
                '<button id="saveSimulationBtn" class="btn btn-light btn-sm" type="button">',
                '<i class="fas fa-save me-1"></i>',
                escape(str(_("Save table"))),
                "</button>",
                '<span class="badge bg-warning text-dark border">',
                escape(str(_("Temporary"))),
                "</span>",
                '<span class="badge bg-light text-dark border">',
                escape(str(source_kind_label)),
                "</span>",
            ]
        )
    )

    context = {
        "ui_version": "v2",
        "bp_type_id": payload.get("bp_type_id") or 0,
        "bp_name": str(
            temp_state.get("name")
            or temp_state.get("source_name")
            or _("Temporary craft table")
        ),
        "back_url": reverse("indy_hub:production_simulations_list"),
        "craft_header_controls": craft_header_controls,
        "deferred_shell": False,
        "active_tab": active_tab,
        "workspace_state": render_workspace_state,
        "num_runs": payload.get("num_runs") or 1,
        "final_product_qty": payload.get("final_product_qty")
        or total_requested_quantity,
        "product_type_id": payload.get("product_type_id"),
        "me": payload.get("me") or 0,
        "te": payload.get("te") or 0,
        "materials": payload.get("materials") or [],
        "direct_materials": payload.get("direct_materials") or [],
        "materials_tree": payload.get("materials_tree") or [],
        "craft_cycles_summary": payload.get("craft_cycles_summary") or {},
        "blueprint_configs_grouped": payload.get("blueprint_configs_grouped") or [],
        "materials_by_group": payload.get("materials_by_group") or {},
        "production_time_map": payload.get("production_time_map") or {},
        "craft_character_advisor": payload.get("craft_character_advisor") or {},
        "structure_planner": payload.get("structure_planner") or {},
        "blueprint_payload": payload,
        "final_outputs": payload.get("editable_final_outputs") or final_outputs,
        "is_project_workspace": True,
        "project": None,
        "sde_snapshot_has_changed": False,
        "sde_refresh_url": "",
    }
    context.update(build_nav_context(request.user, active_tab="industry"))
    return render(request, "indy_hub/industry/Craft_BP_v2.html", context)


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_copy_request_create(request):
    """Create a new blueprint copy request."""
    if request.method != "POST":
        messages.error(request, _("You can only create a request via POST."))
        return redirect("indy_hub:bp_copy_request_page")

    try:
        type_id = int(request.POST.get("type_id", 0))
        material_efficiency = int(request.POST.get("material_efficiency", 0))
        time_efficiency = int(request.POST.get("time_efficiency", 0))
        runs_requested = max(1, int(request.POST.get("runs_requested", 1)))
        copies_requested = max(1, int(request.POST.get("copies_requested", 1)))
    except (TypeError, ValueError):
        messages.error(request, _("Invalid values provided for the request."))
        return redirect("indy_hub:bp_copy_request_page")

    if type_id <= 0:
        messages.error(request, _("Invalid blueprint type."))
        return redirect("indy_hub:bp_copy_request_page")

    if is_reaction_blueprint(type_id):
        messages.error(
            request,
            _(
                "Reaction blueprints cannot be copied. Acquire the formula from the market instead."
            ),
        )
        referer = request.headers.get("referer", "")
        if referer and url_has_allowed_host_and_scheme(
            url=referer,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(referer)
        return redirect("indy_hub:bp_copy_request_page")

    max_runs_per_copy = get_max_copy_runs_per_request(
        blueprint_type_id=type_id,
        time_efficiency=time_efficiency,
    )
    if max_runs_per_copy is not None and runs_requested > max_runs_per_copy:
        messages.error(
            request,
            _("This blueprint request is limited to %(max_runs)s run(s) per copy.")
            % {
                "max_runs": max_runs_per_copy,
            },
        )
        referer = request.headers.get("referer", "")
        if referer and url_has_allowed_host_and_scheme(
            url=referer,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(referer)
        return redirect("indy_hub:bp_copy_request_page")

    # Check if user already has an active request for this exact blueprint
    existing_request = BlueprintCopyRequest.objects.filter(
        requested_by=request.user,
        type_id=type_id,
        material_efficiency=material_efficiency,
        time_efficiency=time_efficiency,
        fulfilled=False,
    ).first()

    if existing_request:
        messages.warning(
            request,
            _("You already have an active request for this blueprint."),
        )
        return redirect("indy_hub:bp_copy_my_requests")

    # Create the request
    new_request = BlueprintCopyRequest.objects.create(
        requested_by=request.user,
        type_id=type_id,
        material_efficiency=material_efficiency,
        time_efficiency=time_efficiency,
        runs_requested=runs_requested,
        copies_requested=copies_requested,
    )
    _notify_blueprint_copy_request_providers(request, new_request)

    messages.success(
        request,
        _("Blueprint copy request created successfully."),
    )
    return redirect("indy_hub:bp_copy_my_requests")


def _build_copy_request_blueprint_visibility_filter(user: User) -> Q | None:
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter

    viewer_corp_ids: set[int] = set()
    viewer_alliance_ids: set[int] = set()
    viewer_characters = EveCharacter.objects.filter(
        character_ownership__user=user
    ).values("corporation_id", "alliance_id")
    for char in viewer_characters:
        corp_id = char.get("corporation_id")
        if corp_id is not None:
            viewer_corp_ids.add(corp_id)
        alliance_id = char.get("alliance_id")
        if alliance_id is not None:
            viewer_alliance_ids.add(alliance_id)

    viewer_can_request_copies = user.has_perm("indy_hub.can_access_indy_hub")

    # Fetch copy sharing configuration for character-owned and corporation-owned originals
    character_settings = list(
        CharacterSettings.objects.filter(
            character_id=0,
            allow_copy_requests=True,
        ).exclude(copy_sharing_scope=CharacterSettings.SCOPE_NONE)
    )
    corporation_settings = list(
        CorporationSharingSetting.objects.filter(allow_copy_requests=True)
        .exclude(share_scope=CharacterSettings.SCOPE_NONE)
        .only("user_id", "corporation_id", "share_scope")
    )

    owner_user_ids = {
        setting.user_id for setting in character_settings + corporation_settings
    }

    owner_affiliations: dict[int, dict[str, set[int]]] = {}
    corp_alliance_map: dict[int, set[int]] = defaultdict(set)

    if owner_user_ids:
        owner_characters = EveCharacter.objects.filter(
            character_ownership__user_id__in=owner_user_ids
        ).values(
            "character_ownership__user_id",
            "corporation_id",
            "alliance_id",
        )
        for char in owner_characters:
            user_id = char["character_ownership__user_id"]
            corp_id = char.get("corporation_id")
            alliance_id = char.get("alliance_id")

            data = owner_affiliations.setdefault(
                user_id, {"corp_ids": set(), "alliance_ids": set()}
            )
            if corp_id is not None:
                data["corp_ids"].add(corp_id)
                if alliance_id:
                    corp_alliance_map[corp_id].add(alliance_id)
            if alliance_id:
                data["alliance_ids"].add(alliance_id)

    missing_corp_ids = {
        setting.corporation_id
        for setting in corporation_settings
        if setting.corporation_id and not corp_alliance_map.get(setting.corporation_id)
    }
    if missing_corp_ids:
        # Alliance Auth
        from allianceauth.eveonline.models import EveCorporationInfo

        corp_records = EveCorporationInfo.objects.filter(
            corporation_id__in=missing_corp_ids
        ).values("corporation_id", "alliance_id")
        for record in corp_records:
            corp_id = record.get("corporation_id")
            alliance_id = record.get("alliance_id")
            if corp_id and alliance_id:
                corp_alliance_map[corp_id].add(alliance_id)

    allowed_character_user_ids: set[int] = set()
    for setting in character_settings:
        affiliations = owner_affiliations.get(
            setting.user_id, {"corp_ids": set(), "alliance_ids": set()}
        )
        corp_ids = affiliations["corp_ids"]
        alliance_ids = affiliations["alliance_ids"]

        if setting.copy_sharing_scope == CharacterSettings.SCOPE_CORPORATION:
            if viewer_corp_ids & corp_ids:
                allowed_character_user_ids.add(setting.user_id)
        elif setting.copy_sharing_scope == CharacterSettings.SCOPE_ALLIANCE:
            if (viewer_corp_ids & corp_ids) or (viewer_alliance_ids & alliance_ids):
                allowed_character_user_ids.add(setting.user_id)
        elif setting.copy_sharing_scope == CharacterSettings.SCOPE_EVERYONE:
            if viewer_can_request_copies:
                allowed_character_user_ids.add(setting.user_id)

    allowed_corporate_pairs: set[tuple[int, int]] = set()
    for setting in corporation_settings:
        corp_id = setting.corporation_id
        if not corp_id:
            continue

        allowed = False
        if setting.share_scope == CharacterSettings.SCOPE_CORPORATION:
            allowed = corp_id in viewer_corp_ids
        elif setting.share_scope == CharacterSettings.SCOPE_ALLIANCE:
            alliance_ids = corp_alliance_map.get(corp_id, set())
            allowed = (corp_id in viewer_corp_ids) or bool(
                viewer_alliance_ids & alliance_ids
            )
        elif setting.share_scope == CharacterSettings.SCOPE_EVERYONE:
            allowed = viewer_can_request_copies

        if allowed:
            allowed_corporate_pairs.add((setting.user_id, corp_id))

    blueprint_filters: list[Q] = []
    if allowed_character_user_ids:
        blueprint_filters.append(
            Q(
                owner_user_id__in=allowed_character_user_ids,
                owner_kind=Blueprint.OwnerKind.CHARACTER,
            )
        )

    for user_id, corp_id in allowed_corporate_pairs:
        blueprint_filters.append(
            Q(
                owner_user_id=user_id,
                owner_kind=Blueprint.OwnerKind.CORPORATION,
                corporation_id=corp_id,
            )
        )

    combined_blueprint_filter: Q | None = None
    for condition in blueprint_filters:
        combined_blueprint_filter = (
            condition
            if combined_blueprint_filter is None
            else combined_blueprint_filter | condition
        )

    return combined_blueprint_filter


def _build_visible_copy_request_blueprint_entries(
    user: User,
    *,
    search: str = "",
    min_me: int | None = None,
    min_te: int | None = None,
) -> list[dict[str, Any]]:
    visibility_filter = _build_copy_request_blueprint_visibility_filter(user)
    if visibility_filter is None:
        qs = Blueprint.objects.none()
    else:
        qs = Blueprint.objects.filter(visibility_filter).filter(
            bp_type=Blueprint.BPType.ORIGINAL
        )
        if search:
            qs = qs.filter(type_name__icontains=search)
        if min_me is not None:
            qs = qs.filter(material_efficiency__gte=min_me)
        if min_te is not None:
            qs = qs.filter(time_efficiency__gte=min_te)
        qs = qs.values(
            "type_id",
            "type_name",
            "material_efficiency",
            "time_efficiency",
        ).distinct()
        qs = qs.order_by("type_name", "material_efficiency", "time_efficiency")

    bp_list = []
    for bp in qs:
        bp_list.append(
            {
                "type_id": bp["type_id"],
                "type_name": bp["type_name"] or str(bp["type_id"]),
                "icon_url": f"https://images.evetech.net/types/{bp['type_id']}/bp?size=32",
                "material_efficiency": bp["material_efficiency"],
                "time_efficiency": bp["time_efficiency"],
            }
        )
    return bp_list


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_copy_request_page(request):
    emit_view_analytics_event(
        view_name="industry.bp_copy_request_page", request=request
    )
    search = request.GET.get("search", "").strip()
    min_me = request.GET.get("min_me", "")
    min_te = request.GET.get("min_te", "")
    page = request.GET.get("page", 1)
    try:
        per_page = int(request.GET.get("per_page", 24))
    except (TypeError, ValueError):
        per_page = 24
    if per_page not in {12, 24, 48, 96}:
        per_page = 24

    min_me_val = int(min_me) if min_me.isdigit() else None
    min_te_val = int(min_te) if min_te.isdigit() else None
    bp_list = _build_visible_copy_request_blueprint_entries(
        request.user,
        search=search,
        min_me=min_me_val,
        min_te=min_te_val,
    )
    per_page_options = [12, 24, 48, 96]
    me_options = list(range(0, 11))
    te_options = list(range(0, 21, 2))  # 0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20
    paginator = Paginator(bp_list, per_page)
    page_obj = paginator.get_page(page)
    page_blueprint_type_ids = [entry["type_id"] for entry in page_obj.object_list]
    activity_times = _fetch_blueprint_activity_times(page_blueprint_type_ids)
    max_production_limits = _fetch_blueprint_max_production_limits(
        page_blueprint_type_ids
    )
    page_keys = [
        (
            int(entry["type_id"]),
            int(entry["material_efficiency"]),
            int(entry["time_efficiency"]),
        )
        for entry in page_obj.object_list
    ]
    eligible_owner_ids_map = _build_eligible_owner_ids_map(request.user, page_keys)
    for entry in page_obj.object_list:
        key = (
            int(entry["type_id"]),
            int(entry["material_efficiency"]),
            int(entry["time_efficiency"]),
        )
        entry["copy_request_preview"] = _build_copy_request_preview(
            requester=request.user,
            type_id=int(entry["type_id"]),
            material_efficiency=int(entry["material_efficiency"]),
            time_efficiency=int(entry["time_efficiency"]),
            type_name=str(entry["type_name"]),
            activity_times=activity_times,
            eligible_owner_ids=eligible_owner_ids_map.get(key, set()),
            max_production_limit=max_production_limits.get(int(entry["type_id"])),
        )
    page_range = paginator.get_elided_page_range(
        number=page_obj.number, on_each_side=5, on_ends=1
    )
    if request.method == "POST":
        return bp_copy_request_create(request)
    context = {
        "page_obj": page_obj,
        "search": search,
        "min_me": min_me,
        "min_te": min_te,
        "per_page": per_page,
        "per_page_options": per_page_options,
        "me_options": me_options,
        "te_options": te_options,
        "page_range": page_range,
        "requests": [],
    }
    context.update(build_nav_context(request.user, active_tab="blueprint_sharing"))

    return render(
        request, "indy_hub/blueprint_sharing/bp_copy_request_page.html", context
    )


def _serialize_copy_request_price_estimate(
    estimate: dict[str, Any],
) -> dict[str, Any]:
    return _serialize_copy_request_price_estimate_service(estimate)


@require_http_methods(["GET"])
@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_copy_request_estimate(request):
    try:
        type_id = int(request.GET.get("type_id") or 0)
        material_efficiency = int(request.GET.get("material_efficiency") or 0)
        time_efficiency = int(request.GET.get("time_efficiency") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid_blueprint"}, status=400)

    if type_id <= 0:
        return JsonResponse({"error": "invalid_blueprint"}, status=400)

    estimate_key = (type_id, material_efficiency, time_efficiency)
    visibility_filter = _build_copy_request_blueprint_visibility_filter(request.user)
    if visibility_filter is None:
        return JsonResponse({"error": "not_found"}, status=404)
    if (
        not Blueprint.objects.filter(visibility_filter)
        .filter(
            bp_type=Blueprint.BPType.ORIGINAL,
            type_id=type_id,
            material_efficiency=material_efficiency,
            time_efficiency=time_efficiency,
        )
        .exists()
    ):
        return JsonResponse({"error": "not_found"}, status=404)

    probe_request = BlueprintCopyRequest(
        requested_by=request.user,
        requested_by_id=request.user.id,
        type_id=type_id,
        material_efficiency=material_efficiency,
        time_efficiency=time_efficiency,
        runs_requested=1,
        copies_requested=1,
    )
    estimates = _build_copy_request_price_estimates(
        requester=request.user,
        requests=[probe_request],
    )
    estimate = estimates.get(estimate_key)
    if not estimate:
        visible_copying_structures = [
            structure
            for structure in _get_visible_industry_structures_queryset(request.user)
            if IndustryActivityMixin.ACTIVITY_COPYING
            in structure.get_enabled_activity_ids()
        ]
        if not visible_copying_structures:
            return JsonResponse(
                {
                    "available": False,
                    "reason": "no_visible_copy_structure",
                }
            )
        return JsonResponse({"available": False})

    return JsonResponse(
        {
            "available": True,
            "estimate": _serialize_copy_request_price_estimate(estimate),
        }
    )


def _fetch_item_base_prices(type_ids: list[int]) -> dict[int, Decimal]:
    return _fetch_item_base_prices_service(type_ids, connection=connection)


def _build_copy_estimated_item_values(
    requests: list[BlueprintCopyRequest],
) -> dict[int, dict[str, Any]]:
    return _build_copy_estimated_item_values_service(
        requests,
        connection=connection,
        fetch_adjusted_prices_fn=fetch_adjusted_prices,
        market_price_error_type=MarketPriceError,
    )


def _copy_estimate_decimal(value: Any) -> Decimal:
    return _copy_estimate_decimal_service(value)


def _copy_estimate_cost_rate(breakdown: Any) -> Decimal:
    return _copy_estimate_cost_rate_service(
        breakdown,
        copying_job_cost_base_percent=COPYING_JOB_COST_BASE_PERCENT,
    )


def _build_copy_request_price_estimates(
    *,
    requester: User,
    requests: list[BlueprintCopyRequest],
) -> dict[tuple[int, int, int], dict[str, Any]]:
    if not requests:
        return {}

    visible_copying_structures = [
        structure
        for structure in _get_visible_industry_structures_queryset(requester)
        .prefetch_related("rigs")
        .order_by("name", "id")
        if IndustryActivityMixin.ACTIVITY_COPYING
        in structure.get_enabled_activity_ids()
    ]
    if not visible_copying_structures:
        return {}

    visible_copying_solar_system_ids = {
        int(structure.solar_system_id)
        for structure in visible_copying_structures
        if structure.solar_system_id
    }
    if not visible_copying_solar_system_ids:
        return {}

    visible_copying_system_indices = {
        int(cost_index.solar_system_id): cost_index
        for cost_index in IndustrySystemCostIndex.objects.filter(
            solar_system_id__in=visible_copying_solar_system_ids,
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
        )
    }
    if not visible_copying_system_indices:
        return {}

    copying_cost_options: list[dict[str, Any]] = []
    for structure in visible_copying_structures:
        system_cost_index = visible_copying_system_indices.get(
            int(structure.solar_system_id or 0)
        )
        if system_cost_index is None:
            continue
        try:
            rate_breakdown = calculate_installation_cost(
                structure=structure,
                activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
                estimated_item_value=Decimal("1"),
                system_cost_index=system_cost_index,
            )
        except ValidationError:
            continue
        copying_cost_options.append(
            {
                "structure": structure,
                "system_cost_index": system_cost_index,
                "cost_rate": _copy_estimate_cost_rate(rate_breakdown),
            }
        )
    if not copying_cost_options:
        return {}
    selected_option = min(
        copying_cost_options,
        key=lambda option: option["cost_rate"],
    )

    estimated_item_values = _build_copy_estimated_item_values(requests)
    estimates: dict[tuple[int, int, int], dict[str, Any]] = {}

    for req in requests:
        blueprint_type_id = int(req.type_id or 0)
        if blueprint_type_id <= 0:
            continue

        value_meta = estimated_item_values.get(blueprint_type_id) or {}
        estimated_item_value = _copy_estimate_decimal(
            value_meta.get("estimated_item_value")
        )
        copies_requested = max(int(getattr(req, "copies_requested", 1) or 1), 1)
        if estimated_item_value <= 0 or copies_requested <= 0:
            continue

        total_estimated_item_value = estimated_item_value * Decimal(copies_requested)
        structure = selected_option["structure"]
        system_cost_index = selected_option["system_cost_index"]
        try:
            breakdown = calculate_installation_cost(
                structure=structure,
                activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
                estimated_item_value=total_estimated_item_value,
                system_cost_index=system_cost_index,
            )
        except ValidationError:
            continue

        amount = breakdown.total_installation_cost
        selected_estimate = {
            "available": True,
            "amount": amount,
            "amount_display": _format_isk_amount(amount),
            "estimated_item_value": breakdown.estimated_item_value,
            "estimated_item_unit_value": value_meta.get("unit_value") or Decimal("0"),
            "runs_requested": max(
                int(value_meta.get("runs_requested") or req.runs_requested or 1),
                1,
            ),
            "copies_requested": copies_requested,
            "job_cost_base_percent": COPYING_JOB_COST_BASE_PERCENT,
            "system_cost_index_percent": breakdown.system_cost_index_percent,
            "job_cost_bonus_percent": breakdown.total_job_cost_bonus_percent,
            "facility_tax_percent": breakdown.facility_tax_percent,
            "scc_surcharge_percent": breakdown.scc_surcharge_percent,
        }

        key = (
            blueprint_type_id,
            int(req.material_efficiency or 0),
            int(req.time_efficiency or 0),
        )
        estimates[key] = selected_estimate

    return estimates


def _normalize_structure_lookup_name(value: str | None) -> str:
    return str(value or "").strip().casefold()


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_copy_fulfill_requests(request):
    emit_view_analytics_event(
        view_name="industry.bp_copy_fulfill_requests", request=request
    )
    """List requests for blueprints the user owns and allows copy requests for."""
    from ..models import CharacterSettings

    active_filter = (request.GET.get("status") or "all").strip().lower()

    setting = CharacterSettings.objects.filter(
        user=request.user,
        character_id=0,  # Global settings only
        allow_copy_requests=True,
    ).first()
    include_self_requests = request.GET.get("include_self") in {
        "1",
        "true",
        "yes",
        "on",
    }
    can_manage_corporate = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")

    accessible_corporation_ids: set[int] = set()
    characters_by_corp: dict[int, set[int]] = defaultdict(set)

    if can_manage_corporate:
        memberships = CharacterOwnership.objects.filter(user=request.user).values(
            "character__character_id",
            "character__corporation_id",
        )
        for entry in memberships:
            corp_id = entry.get("character__corporation_id")
            char_id = entry.get("character__character_id")
            if corp_id:
                characters_by_corp[corp_id].add(char_id)

        if characters_by_corp:
            corp_settings_qs = CorporationSharingSetting.objects.filter(
                corporation_id__in=characters_by_corp.keys(),
                allow_copy_requests=True,
                share_scope__in=[
                    CharacterSettings.SCOPE_CORPORATION,
                    CharacterSettings.SCOPE_ALLIANCE,
                    CharacterSettings.SCOPE_EVERYONE,
                ],
            )

            for setting_obj in corp_settings_qs:
                corp_id = setting_obj.corporation_id
                if corp_id is None:
                    continue
                viewer_chars = characters_by_corp.get(corp_id, set())
                if not viewer_chars:
                    continue
                if setting_obj.restricts_characters and not any(
                    setting_obj.is_character_authorized(char_id)
                    for char_id in viewer_chars
                ):
                    continue
                accessible_corporation_ids.add(corp_id)
    auto_open_chat_id: str | None = None
    requested_chat = request.GET.get("open_chat")
    if requested_chat:
        try:
            requested_chat_id = int(requested_chat)
        except (TypeError, ValueError):
            requested_chat_id = None
        if requested_chat_id:
            exists = BlueprintCopyChat.objects.filter(
                id=requested_chat_id, seller=request.user
            ).exists()
            if exists:
                auto_open_chat_id = str(requested_chat_id)
    nav_context = build_nav_context(request.user, active_tab="blueprint_sharing")
    if not include_self_requests and not setting and not accessible_corporation_ids:
        context = {
            "requests": [],
            "has_requests": False,
            "active_filter": active_filter,
        }
        context.update(nav_context)
        if auto_open_chat_id:
            context["auto_open_chat_id"] = auto_open_chat_id
        context["include_self_requests"] = include_self_requests
        return render(
            request, "indy_hub/blueprint_sharing/bp_copy_fulfill_requests.html", context
        )

    accessible_blueprint_keys: set[tuple[int, int, int]] = set()

    if setting:
        personal_keys = Blueprint.objects.filter(
            owner_user=request.user,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            bp_type=Blueprint.BPType.ORIGINAL,
        ).values_list("type_id", "material_efficiency", "time_efficiency")
        accessible_blueprint_keys.update(
            {
                (int(type_id), int(me), int(te))
                for type_id, me, te in personal_keys
                if type_id is not None and me is not None and te is not None
            }
        )

    if accessible_corporation_ids:
        corporate_keys = Blueprint.objects.filter(
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            bp_type=Blueprint.BPType.ORIGINAL,
            corporation_id__in=accessible_corporation_ids,
        ).values_list("type_id", "material_efficiency", "time_efficiency")
        accessible_blueprint_keys.update(
            {
                (int(type_id), int(me), int(te))
                for type_id, me, te in corporate_keys
                if type_id is not None and me is not None and te is not None
            }
        )

    bp_index: dict[tuple[int, int, int], list[Blueprint]] = defaultdict(list)
    bp_item_map: dict[int, tuple[int, int, int]] = {}

    visible_copying_structures = [
        structure
        for structure in _get_visible_industry_structures_queryset(request.user)
        .prefetch_related("rigs")
        .order_by("name", "id")
        if IndustryActivityMixin.ACTIVITY_COPYING
        in structure.get_enabled_activity_ids()
    ]
    structure_by_location_id: dict[int, IndustryStructure] = {}
    structures_by_location_name: dict[str, list[IndustryStructure]] = defaultdict(list)
    visible_copying_system_indices = {
        int(cost_index.solar_system_id): cost_index
        for cost_index in IndustrySystemCostIndex.objects.filter(
            solar_system_id__in=[
                structure.solar_system_id
                for structure in visible_copying_structures
                if structure.solar_system_id
            ],
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
        )
    }
    for structure in visible_copying_structures:
        if structure.external_structure_id:
            structure_by_location_id[int(structure.external_structure_id)] = structure
        normalized_name = _normalize_structure_lookup_name(structure.name)
        if normalized_name:
            structures_by_location_name[normalized_name].append(structure)

    status_meta = {
        "awaiting_response": {
            "label": _("Awaiting response"),
            "badge": "bg-warning text-dark",
            "hint": _(
                "No offer sent yet. Accept, reject, or propose conditions to help your corpmate."
            ),
        },
        "waiting_on_buyer": {
            "label": _("Waiting on buyer"),
            "badge": "bg-info text-white",
            "hint": _("You've sent a conditional offer. Awaiting buyer confirmation."),
        },
        "waiting_on_you": {
            "label": _("Confirm agreement"),
            "badge": "bg-warning text-dark",
            "hint": _(
                "The buyer already accepted your terms. Confirm in chat to lock in the agreement."
            ),
        },
        "ready_to_deliver": {
            "label": _("Ready to deliver"),
            "badge": "bg-success text-white",
            "hint": _(
                "Buyer accepted your offer. Deliver the copies and mark the request as complete."
            ),
        },
        "offer_rejected": {
            "label": _("Offer rejected"),
            "badge": "bg-danger text-white",
            "hint": _(
                "Your previous offer was declined. Consider sending an updated proposal."
            ),
        },
        "self_request": {
            "label": _("Your tracked request"),
            "badge": "bg-secondary text-white",
            "hint": _("Simulation view: actions are disabled for your own requests."),
        },
    }

    metrics = {
        "total": 0,
        "awaiting_response": 0,
        "waiting_on_buyer": 0,
        "waiting_on_you": 0,
        "ready_to_deliver": 0,
        "offer_rejected": 0,
    }

    has_filters = bool(accessible_blueprint_keys)

    if not has_filters and not include_self_requests:
        context = {
            "requests": [],
            "metrics": metrics,
            "include_self_requests": include_self_requests,
            "has_requests": False,
            "active_filter": active_filter,
        }
        context.update(nav_context)
        return render(
            request, "indy_hub/blueprint_sharing/bp_copy_fulfill_requests.html", context
        )

    q = Q()
    for type_id, material_efficiency, time_efficiency in accessible_blueprint_keys:
        q |= Q(
            type_id=type_id,
            material_efficiency=material_efficiency,
            time_efficiency=time_efficiency,
        )

    if has_filters:
        base_qs = BlueprintCopyRequest.objects.filter(q)
        if include_self_requests:
            # Also include user's own requests even if they don't match blueprints
            base_qs = BlueprintCopyRequest.objects.filter(
                q | Q(requested_by=request.user)
            )
    else:
        base_qs = BlueprintCopyRequest.objects.filter(requested_by=request.user)

    if not include_self_requests:
        base_qs = base_qs.exclude(requested_by=request.user)

    state_filter = (
        Q(fulfilled=False)
        | Q(fulfilled=True, delivered=False, offers__owner=request.user)
        | Q(fulfilled=True, delivered=False, fulfilled_by=request.user)
    )

    if include_self_requests:
        state_filter = state_filter | Q(requested_by=request.user, delivered=False)

    candidate_request_keys = set(
        base_qs.filter(state_filter)
        .values_list("type_id", "material_efficiency", "time_efficiency")
        .distinct()
    )

    if candidate_request_keys:
        key_filter = Q()
        for type_id, material_efficiency, time_efficiency in candidate_request_keys:
            key_filter |= Q(
                type_id=type_id,
                material_efficiency=material_efficiency,
                time_efficiency=time_efficiency,
            )

        visible_blueprints_q = Q(
            owner_kind=Blueprint.OwnerKind.CHARACTER, owner_user=request.user
        )
        if accessible_corporation_ids:
            visible_blueprints_q |= Q(
                owner_kind=Blueprint.OwnerKind.CORPORATION,
                corporation_id__in=accessible_corporation_ids,
            )

        relevant_blueprints = Blueprint.objects.filter(
            visible_blueprints_q,
            bp_type=Blueprint.BPType.ORIGINAL,
        ).filter(key_filter)

        for blueprint in relevant_blueprints:
            key = (
                int(blueprint.type_id),
                int(blueprint.material_efficiency),
                int(blueprint.time_efficiency),
            )
            bp_index[key].append(blueprint)
            if blueprint.item_id is not None:
                bp_item_map[int(blueprint.item_id)] = key

    def _init_occupancy():
        return {"count": 0, "soonest_end": None}

    corporate_occupancy_map = defaultdict(_init_occupancy)
    personal_occupancy_map = defaultdict(_init_occupancy)

    def _update_soonest(info, end_date):
        if end_date and (info["soonest_end"] is None or end_date < info["soonest_end"]):
            info["soonest_end"] = end_date

    blocking_activities = [1, 3, 4, 5, 8, 9]
    if accessible_corporation_ids:
        corp_jobs = IndustryJob.objects.filter(
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id__in=accessible_corporation_ids,
            status="active",
            activity_id__in=blocking_activities,
        ).only("blueprint_id", "blueprint_type_id", "end_date")

        for job in corp_jobs:
            matched_key = bp_item_map.get(job.blueprint_id)
            if matched_key is not None:
                info = corporate_occupancy_map[matched_key]
                info["count"] += 1
                _update_soonest(info, job.end_date)

    personal_active_type_ids: set[int] = set()

    personal_jobs = IndustryJob.objects.filter(
        owner_user=request.user,
        owner_kind=Blueprint.OwnerKind.CHARACTER,
        status="active",
        activity_id__in=blocking_activities,
    ).only("blueprint_id", "blueprint_type_id", "end_date")

    for job in personal_jobs:
        if job.blueprint_type_id:
            personal_active_type_ids.add(job.blueprint_type_id)
        matched_key = bp_item_map.get(job.blueprint_id)
        if matched_key is not None:
            info = personal_occupancy_map[matched_key]
            info["count"] += 1
            _update_soonest(info, job.end_date)

    offer_status_labels = {
        "accepted": _("Accepted"),
        "conditional": _("Conditional"),
        "rejected": _("Rejected"),
    }

    user_cache: dict[int, User | None] = {}
    identity_cache: dict[int, UserIdentity] = {}
    corp_name_cache: dict[int, str] = {}

    def _get_user(user_id: int) -> User | None:
        if user_id in user_cache:
            return user_cache[user_id]
        user_obj = User.objects.filter(id=user_id).first()
        user_cache[user_id] = user_obj
        return user_obj

    def _identity_for(
        user_obj: User | None = None,
        *,
        user_id: int | None = None,
    ) -> UserIdentity:
        if user_obj is not None:
            user_id = user_obj.id
        if user_id is None:
            return UserIdentity(
                user_id=0,
                username="",
                character_id=None,
                character_name="",
                corporation_id=None,
                corporation_name="",
                corporation_ticker="",
            )
        cached = identity_cache.get(user_id)
        if cached:
            return cached
        if user_obj is None:
            user_obj = _get_user(user_id)
        identity = _resolve_user_identity(user_obj)
        identity_cache[user_id] = identity
        return identity

    def _corporation_display(corp_id: int | None) -> str:
        if not corp_id:
            return ""
        if corp_id in corp_name_cache:
            return corp_name_cache[corp_id]
        corp_name = get_corporation_name(corp_id) or (
            CorporationSharingSetting.objects.filter(corporation_id=corp_id)
            .exclude(corporation_name="")
            .values_list("corporation_name", flat=True)
            .first()
        )
        if not corp_name:
            corp_name = (
                Blueprint.objects.filter(corporation_id=corp_id)
                .exclude(corporation_name="")
                .values_list("corporation_name", flat=True)
                .first()
            )
        corp_name_cache[corp_id] = corp_name or str(corp_id)
        return corp_name_cache[corp_id]

    qset = (
        base_qs.filter(state_filter)
        .select_related("requested_by")
        .prefetch_related("offers__owner", "offers__chat")
        .order_by("-created_at")
        .distinct()
    )
    copy_producer_advisor = build_craft_character_advisor(
        user=request.user,
        production_time_map={
            int(type_id): {
                "type_id": int(type_id),
                "type_name": get_type_name(type_id),
                "blueprint_type_id": int(type_id),
                "activity_id": IndustryActivityMixin.ACTIVITY_COPYING,
            }
            for type_id in sorted(
                {
                    int(type_id)
                    for type_id in qset.values_list("type_id", flat=True)
                    if int(type_id or 0) > 0
                }
            )
        },
        fetch_character_skill_levels=_fetch_character_skill_levels,
        update_skill_snapshot=_update_skill_snapshot,
        skill_cache_ttl=SKILL_CACHE_TTL,
    )
    copy_producer_items = copy_producer_advisor.get("items", {})
    copy_activity_times = _fetch_blueprint_activity_times(
        list(qset.values_list("type_id", flat=True))
    )
    copy_estimated_item_values = _build_copy_estimated_item_values(list(qset))
    copy_cost_breakdown_cache: dict[
        tuple[int, int], tuple[IndustryStructure, Any] | None
    ] = {}

    requests_to_fulfill = []
    eligible_details_cache: dict[tuple[int, int, int, int], EligibleOwnerDetails] = {}

    for req in qset:
        if req.requested_by_id == request.user.id and not include_self_requests:
            continue

        offers = list(req.offers.all())
        my_offer = next(
            (offer for offer in offers if offer.owner_id == request.user.id), None
        )
        offers_by_owner = {offer.owner_id: offer for offer in offers}
        eligible_details_key = (
            int(req.type_id or 0),
            int(req.material_efficiency or 0),
            int(req.time_efficiency or 0),
            int(req.requested_by_id or 0),
        )
        eligible_details = eligible_details_cache.get(eligible_details_key)
        if eligible_details is None:
            eligible_details = _eligible_owner_details_for_request(req)
            eligible_details_cache[eligible_details_key] = eligible_details
        requester_identity = _identity_for(req.requested_by)

        eligible_character_entries: list[dict[str, Any]] = []
        eligible_corporation_entries: list[dict[str, Any]] = []

        # Temporarily set, will be refined after determining source types
        is_self_request_preliminary = req.requested_by_id == request.user.id

        if req.fulfilled and (req.delivered or not my_offer):
            # Already delivered or fulfilled by someone else
            # But allow the fulfiller (no offer record) or own requests when enabled.
            if not (
                (is_self_request_preliminary and include_self_requests)
                or req.fulfilled_by_id == request.user.id
            ):
                continue

        status_key = "awaiting_response"
        can_mark_delivered = False
        handshake_state = None

        key = (req.type_id, req.material_efficiency, req.time_efficiency)
        matching_blueprints = bp_index.get(key, [])

        corporate_names: list[str] = []
        corporate_tickers: list[str] = []
        personal_sources: list[Blueprint] = []
        corporate_sources: list[Blueprint] = []
        if matching_blueprints:
            seen_corporations: set[int] = set()
            for blueprint in matching_blueprints:
                if (
                    blueprint.owner_kind == Blueprint.OwnerKind.CHARACTER
                    and blueprint.owner_user_id == request.user.id
                ):
                    personal_sources.append(blueprint)
                elif (
                    blueprint.owner_kind == Blueprint.OwnerKind.CORPORATION
                    and blueprint.corporation_id in accessible_corporation_ids
                ):
                    corporate_sources.append(blueprint)

                if blueprint.owner_kind != Blueprint.OwnerKind.CORPORATION:
                    continue
                corp_id = blueprint.corporation_id
                if corp_id is not None and corp_id in seen_corporations:
                    continue
                if corp_id is not None:
                    seen_corporations.add(corp_id)
                display_name = blueprint.corporation_name or (
                    str(corp_id) if corp_id is not None else ""
                )
                if display_name:
                    corporate_names.append(display_name)

                ticker_value = ""
                if corp_id:
                    ticker_value = getattr(blueprint, "corporation_ticker", "")
                    if not ticker_value:
                        ticker_value = get_corporation_ticker(corp_id)
                if ticker_value:
                    corporate_tickers.append(ticker_value)

        personal_source_names = sorted(
            {
                blueprint.character_name.strip()
                for blueprint in personal_sources
                if getattr(blueprint, "character_name", "").strip()
            },
            key=lambda name: name.lower(),
        )

        if not personal_source_names and personal_sources:
            personal_source_names = [_("Your character")]

        def _sort_unique(values: list[str]) -> list[str]:
            seen_lower: set[str] = set()
            unique_values: list[str] = []
            for value in values:
                lowered = value.lower()
                if lowered in seen_lower:
                    continue
                seen_lower.add(lowered)
                unique_values.append(value)
            return sorted(unique_values, key=lambda entry: entry.lower())

        corporate_names = _sort_unique(corporate_names)
        corporate_tickers = _sort_unique(corporate_tickers)
        corporate_count = len(corporate_sources)
        personal_count = len(personal_sources)

        personal_info = personal_occupancy_map.get(key)
        has_active_personal_jobs = bool(personal_info and personal_info["count"] > 0)
        if not has_active_personal_jobs and req.type_id in personal_active_type_ids:
            has_active_personal_jobs = True

        # Skip if no matching blueprints, unless it's a self-request in include mode
        if corporate_count == 0 and personal_count == 0:
            if not (is_self_request_preliminary and include_self_requests):
                continue

        if personal_sources:
            identity = _identity_for(user_id=request.user.id)
            display_name = identity.character_name or identity.username
            eligible_character_entries.append(
                {
                    "name": display_name,
                    "corporation": identity.corporation_name,
                    "is_self": True,
                }
            )

        for corp_id, members in eligible_details.corporate_members_by_corp.items():
            if request.user.id not in members:
                continue
            corp_name = _corporation_display(corp_id)
            eligible_corporation_entries.append(
                {
                    "id": corp_id,
                    "name": corp_name,
                    "member_count": len(members),
                    "includes_self": True,
                }
            )

        eligible_character_entries.sort(key=lambda item: item["name"].lower())
        eligible_corporation_entries.sort(key=lambda item: item["name"].lower())
        eligible_total = len(eligible_character_entries) + len(
            eligible_corporation_entries
        )

        rejected_personal_scope = _offer_rejects_scope(my_offer, "personal")
        rejected_corporate_scope = _offer_rejects_scope(my_offer, "corporation")
        revived_after_scope_rejection = False

        if rejected_personal_scope:
            personal_sources = []
            personal_source_names = []
            eligible_character_entries = []
            revived_after_scope_rejection = True

        if rejected_corporate_scope:
            corporate_sources = []
            corporate_names = []
            corporate_tickers = []
            eligible_corporation_entries = []
            revived_after_scope_rejection = True

        corporate_count = len(corporate_sources)
        personal_count = len(personal_sources)
        eligible_total = len(eligible_character_entries) + len(
            eligible_corporation_entries
        )

        if my_offer and my_offer.status == "rejected":
            if corporate_count == 0 and personal_count == 0:
                continue
            if revived_after_scope_rejection:
                my_offer = None
        if corporate_count == 0:
            if (
                can_manage_corporate
                and not (is_self_request_preliminary and include_self_requests)
                and (is_self_request_preliminary or personal_count == 0)
            ):
                continue
            show_personal_sources = True
        else:
            show_personal_sources = personal_count > 0

        if not show_personal_sources and corporate_count == 0:
            continue

        displayed_personal_count = personal_count if show_personal_sources else 0
        displayed_personal_names = (
            personal_source_names if show_personal_sources else []
        )

        total_sources = corporate_count + displayed_personal_count
        if total_sources == 0:
            if not (is_self_request_preliminary and include_self_requests):
                continue

        has_dual_sources = displayed_personal_count > 0 and corporate_count > 0
        default_scope = "corporation" if corporate_count else "personal"
        is_corporate_source = corporate_count > 0

        # Determine if this is truly a self-request (can auto-accept via personal BPs)
        # Only disable actions if user requested AND has personal BPs to fulfill it
        # If fulfillment is only via corporate BPs, allow actions even if it's user's request
        is_self_request = is_self_request_preliminary and displayed_personal_count > 0

        corp_info = corporate_occupancy_map.get(key)

        corp_active_jobs = min(corporate_count, corp_info["count"]) if corp_info else 0
        personal_active_jobs = (
            min(displayed_personal_count, personal_info["count"])
            if personal_info and displayed_personal_count
            else 0
        )
        owned_blueprints = total_sources
        total_active_jobs = corp_active_jobs + personal_active_jobs
        available_blueprints = max(owned_blueprints - total_active_jobs, 0)

        busy_candidates = []
        if personal_info and displayed_personal_count and personal_info["soonest_end"]:
            busy_candidates.append(personal_info["soonest_end"])
        if corp_info and corp_info["soonest_end"]:
            busy_candidates.append(corp_info["soonest_end"])
        busy_until = min(busy_candidates) if busy_candidates else None
        busy_overdue = bool(busy_until and busy_until < timezone.now())
        all_copies_busy = (
            owned_blueprints > 0 and available_blueprints == 0 and total_active_jobs > 0
        )

        user_corp_id = eligible_details.user_to_corporation.get(request.user.id)
        if user_corp_id is not None and personal_count == 0 and not is_self_request:
            corp_members = eligible_details.corporate_members_by_corp.get(
                user_corp_id, set()
            )
            if any(
                _offer_rejects_scope(offers_by_owner.get(member_id), "corporation")
                for member_id in corp_members
            ):
                # Another authorised manager already declined on behalf of the corporation
                if not (is_self_request_preliminary and include_self_requests):
                    continue

        if is_self_request:
            status_key = "self_request"
        elif req.fulfilled and not req.delivered:
            status_key = "ready_to_deliver"
            can_mark_delivered = True
        elif my_offer:
            if my_offer.status == "conditional":
                if my_offer.accepted_by_buyer and my_offer.accepted_by_seller:
                    status_key = "ready_to_deliver"
                    can_mark_delivered = True
                elif my_offer.accepted_by_buyer and not my_offer.accepted_by_seller:
                    status_key = "waiting_on_you"
                else:
                    status_key = "waiting_on_buyer"
                handshake_state = {
                    "accepted_by_buyer": my_offer.accepted_by_buyer,
                    "accepted_by_seller": my_offer.accepted_by_seller,
                    "state": status_key,
                }
            elif my_offer.status == "rejected":
                status_key = "offer_rejected"
            elif my_offer.status == "accepted":
                status_key = "ready_to_deliver"
                can_mark_delivered = True
        else:
            status_key = "awaiting_response"

        metrics["total"] += 1
        metrics_key = {
            "awaiting_response": "awaiting_response",
            "waiting_on_buyer": "waiting_on_buyer",
            "waiting_on_you": "waiting_on_you",
            "ready_to_deliver": "ready_to_deliver",
            "offer_rejected": "offer_rejected",
        }.get(status_key)
        if metrics_key and not (metrics_key == "awaiting_response" and is_self_request):
            metrics[metrics_key] += 1

        status_info = status_meta[status_key]
        status_hint = status_info["hint"]

        show_offer_actions = status_key in {
            "awaiting_response",
            "offer_rejected",
        }
        if is_self_request:
            show_offer_actions = False

        offer_chat_payload = None
        if my_offer and my_offer.status in {"conditional", "accepted"}:
            offer_chat_payload = _build_offer_chat_payload(
                my_offer,
                viewer_role="seller",
                reopen=not req.delivered,
            )
        if offer_chat_payload:
            show_offer_actions = False
        elif my_offer and my_offer.status in {"conditional", "accepted"}:
            show_offer_actions = False

        type_name = get_type_name(req.type_id)
        copy_cost = None
        producer_item = copy_producer_items.get(str(int(req.type_id)), {})
        copy_producer_options: list[dict[str, Any]] = []
        selected_producer_id = None
        selected_producer_name = ""
        selected_producer_bonus_percent = Decimal("0")
        producer_character_ids = [
            int(producer.get("character_id") or 0)
            for producer in producer_item.get("eligible_characters", [])
            if int(producer.get("character_id") or 0) > 0
        ]
        producer_alpha_map: dict[int, bool] = {}
        if producer_character_ids:
            for snapshot in IndustrySkillSnapshot.objects.filter(
                character_id__in=producer_character_ids
            ):
                producer_alpha_map[int(snapshot.character_id)] = bool(
                    snapshot.is_alpha_clone
                )
        for producer in producer_item.get("eligible_characters", []):
            producer_bonus = Decimal(str(producer.get("time_bonus_percent") or 0))
            character_id = int(producer.get("character_id") or 0)
            option = {
                "id": character_id,
                "name": str(producer.get("name") or ""),
                "time_bonus_percent": producer_bonus,
                "time_bonus_label": _format_percent_compact(producer_bonus),
                "available_slots": int(producer.get("available_slots") or 0),
                "total_slots": int(producer.get("total_slots") or 0),
                "is_alpha_clone": bool(producer_alpha_map.get(character_id, False)),
            }
            copy_producer_options.append(option)

        best_producer = producer_item.get("best_character") or {}
        if int(best_producer.get("character_id") or 0) > 0:
            selected_producer_id = int(best_producer.get("character_id") or 0)
            selected_producer_name = str(best_producer.get("name") or "")
            selected_producer_bonus_percent = Decimal(
                str(best_producer.get("time_bonus_percent") or 0)
            )

        copy_duration = None
        copy_structure_options: list[dict[str, Any]] = []
        estimated_item_value_meta = copy_estimated_item_values.get(int(req.type_id), {})
        estimated_item_value = Decimal(
            str(estimated_item_value_meta.get("estimated_item_value") or 0)
        )
        copies_requested = max(int(getattr(req, "copies_requested", 1) or 1), 1)
        runs_requested = max(int(getattr(req, "runs_requested", 1) or 1), 1)
        copy_base_time_seconds = int(
            copy_activity_times.get(int(req.type_id), {}).get(
                IndustryActivityMixin.ACTIVITY_COPYING
            )
            or 0
        )
        if estimated_item_value > 0 and visible_copying_structures:
            matched_structures: list[IndustryStructure] = []
            seen_structure_ids: set[int] = set()

            for blueprint in matching_blueprints:
                matched_structure = None
                if blueprint.location_id:
                    matched_structure = structure_by_location_id.get(
                        int(blueprint.location_id)
                    )
                if matched_structure is None:
                    location_name = _normalize_structure_lookup_name(
                        getattr(blueprint, "location_name", "")
                    )
                    if location_name:
                        candidates = structures_by_location_name.get(location_name, [])
                        if candidates:
                            matched_structure = candidates[0]
                if matched_structure is None:
                    continue
                if int(matched_structure.id) in seen_structure_ids:
                    continue
                seen_structure_ids.add(int(matched_structure.id))
                matched_structures.append(matched_structure)

            selected_option = None
            candidate_structures = matched_structures or visible_copying_structures

            for structure in candidate_structures:
                cache_key = (int(req.type_id), int(structure.id))
                if cache_key not in copy_cost_breakdown_cache:
                    system_cost_index = visible_copying_system_indices.get(
                        int(structure.solar_system_id or 0)
                    )
                    if system_cost_index is None:
                        copy_cost_breakdown_cache[cache_key] = None
                    else:
                        try:
                            breakdown = calculate_installation_cost(
                                structure=structure,
                                activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
                                estimated_item_value=estimated_item_value,
                                system_cost_index=system_cost_index,
                            )
                        except ValidationError:
                            copy_cost_breakdown_cache[cache_key] = None
                        else:
                            copy_cost_breakdown_cache[cache_key] = (
                                structure,
                                breakdown,
                            )

                cached_breakdown = copy_cost_breakdown_cache.get(cache_key)
                if cached_breakdown is None:
                    continue

                cached_structure, breakdown = cached_breakdown
                jcb_percent_value = Decimal("2")  # copying activity job-cost-base
                copies_decimal = Decimal(int(copies_requested or 1))
                # Single EVE copy job groups all copies × all runs into one
                # installation. Recompute the breakdown with EIV scaled by the
                # number of copies so each ISK row is rounded ONCE on the true
                # total — multiplying pre-rounded per-copy values would inflate
                # small taxes by up to (copies − 1) ISK.
                try:
                    total_breakdown = calculate_installation_cost(
                        structure=cached_structure,
                        activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
                        estimated_item_value=(estimated_item_value * copies_decimal),
                        system_cost_index=visible_copying_system_indices.get(
                            int(cached_structure.solar_system_id or 0)
                        ),
                    )
                except ValidationError:
                    total_breakdown = breakdown
                total_job_cost_base = (
                    total_breakdown.estimated_item_value
                    * jcb_percent_value
                    / Decimal("100")
                )
                total_taxes_modal = (
                    total_breakdown.facility_tax + total_breakdown.scc_surcharge
                )
                selected_producer_is_alpha = bool(
                    producer_alpha_map.get(int(selected_producer_id or 0), False)
                )
                if selected_producer_is_alpha:
                    total_taxes_modal = (
                        total_taxes_modal + total_breakdown.alpha_clone_tax
                    )
                breakdown_payload = {
                    "structure_name": cached_structure.name,
                    "solar_system_name": cached_structure.solar_system_name or "",
                    "runs_requested": int(
                        estimated_item_value_meta.get(
                            "runs_requested", req.runs_requested
                        )
                        or 1
                    ),
                    "copies_requested": int(copies_requested),
                    "estimated_item_value": total_breakdown.estimated_item_value,
                    "jcb_percent": jcb_percent_value,
                    "job_cost_base": total_job_cost_base,
                    "system_cost_index_percent": total_breakdown.system_cost_index_percent,
                    "base_job_cost": total_breakdown.base_job_cost,
                    "structure_role_bonus_percent": total_breakdown.structure_role_bonus_percent,
                    "rig_bonus_percent": total_breakdown.rig_bonus_percent,
                    "total_job_cost_bonus_percent": total_breakdown.total_job_cost_bonus_percent,
                    "adjusted_job_cost": total_breakdown.adjusted_job_cost,
                    "facility_tax_percent": total_breakdown.facility_tax_percent,
                    "facility_tax": total_breakdown.facility_tax,
                    "scc_surcharge_percent": total_breakdown.scc_surcharge_percent,
                    "scc_surcharge": total_breakdown.scc_surcharge,
                    "alpha_clone_tax_percent": total_breakdown.alpha_clone_tax_percent,
                    "alpha_clone_tax": total_breakdown.alpha_clone_tax,
                    "is_alpha_clone": selected_producer_is_alpha,
                    "total_taxes": total_taxes_modal,
                    "per_copy_installation_cost": (
                        breakdown.total_installation_cost
                        + (
                            breakdown.alpha_clone_tax
                            if selected_producer_is_alpha
                            else Decimal("0")
                        )
                    ),
                    "total_installation_cost": (
                        total_breakdown.total_installation_cost
                        + (
                            total_breakdown.alpha_clone_tax
                            if selected_producer_is_alpha
                            else Decimal("0")
                        )
                    ),
                }
                option = {
                    "id": int(cached_structure.id),
                    "structure_name": cached_structure.name,
                    "solar_system_name": cached_structure.solar_system_name,
                    "estimated_item_value": breakdown.estimated_item_value,
                    "estimated_item_value_source": estimated_item_value_meta.get(
                        "source", ""
                    ),
                    "estimated_item_unit_value": estimated_item_value_meta.get(
                        "unit_value", Decimal("0")
                    ),
                    "structure_time_bonus_percent": breakdown.time_bonus_percent,
                    "system_cost_index_percent": breakdown.system_cost_index_percent,
                    "job_cost_bonus_percent": breakdown.total_job_cost_bonus_percent,
                    "facility_tax_percent": breakdown.facility_tax_percent,
                    "scc_surcharge_percent": breakdown.scc_surcharge_percent,
                    "alpha_clone_tax_percent": total_breakdown.alpha_clone_tax_percent,
                    "alpha_clone_tax_total": total_breakdown.alpha_clone_tax,
                    "per_copy_installation_cost": breakdown.total_installation_cost,
                    "total_installation_cost": total_breakdown.total_installation_cost,
                    "total_installation_cost_alpha": (
                        total_breakdown.total_installation_cost
                        + total_breakdown.alpha_clone_tax
                    ),
                    "copies_requested": copies_requested,
                    "runs_requested": estimated_item_value_meta.get(
                        "runs_requested", req.runs_requested
                    ),
                    "uses_fallback_structure": not bool(matched_structures),
                    "breakdown_payload": breakdown_payload,
                }
                copy_structure_options.append(option)

                if (
                    selected_option is None
                    or option["total_installation_cost"]
                    < selected_option["total_installation_cost"]
                ):
                    selected_option = option

            copy_structure_options.sort(
                key=lambda option: (
                    str(option.get("structure_name") or "").lower(),
                    int(option.get("id") or 0),
                )
            )

            if selected_option is not None:
                copy_cost = selected_option

        copy_duration = _build_copy_duration_payload(
            base_time_seconds=copy_base_time_seconds,
            runs_requested=runs_requested,
            copies_requested=copies_requested,
            structure_time_bonus_percent=(
                copy_cost.get("structure_time_bonus_percent")
                if copy_cost
                else Decimal("0")
            ),
            character_time_bonus_percent=selected_producer_bonus_percent,
        )

        scope_modal_payload = {
            "requestId": req.id,
            "typeName": type_name,
            "characters": eligible_character_entries,
            "corporations": eligible_corporation_entries,
            "personalCount": personal_count,
            "corporateCount": corporate_count,
            "defaultScope": default_scope,
        }

        requests_to_fulfill.append(
            {
                "id": req.id,
                "type_id": req.type_id,
                "type_name": type_name,
                "icon_url": f"https://images.evetech.net/types/{req.type_id}/bp?size=64",
                "material_efficiency": req.material_efficiency,
                "time_efficiency": req.time_efficiency,
                "runs_requested": req.runs_requested,
                "copies_requested": getattr(req, "copies_requested", 1),
                "created_at": req.created_at,
                "requester": req.requested_by.username,
                "requester_character": requester_identity.character_name,
                "requester_character_id": requester_identity.character_id,
                "requester_corporation": requester_identity.corporation_name,
                "requester_corporation_id": requester_identity.corporation_id,
                "requester_corporation_ticker": requester_identity.corporation_ticker,
                "requester_bp_source_label": (
                    _("BP user + BP corp")
                    if displayed_personal_count > 0 and corporate_count > 0
                    else _("BP corp") if corporate_count > 0 else _("BP user")
                ),
                "is_self_request": is_self_request,
                "status_key": status_key,
                "status_label": status_info["label"],
                "status_class": status_info["badge"],
                "status_hint": status_hint,
                "my_offer_status": getattr(my_offer, "status", None),
                "my_offer_status_label": offer_status_labels.get(
                    getattr(my_offer, "status", None), ""
                ),
                "my_offer_message": getattr(my_offer, "message", ""),
                "my_offer_accepted_by_buyer": getattr(
                    my_offer, "accepted_by_buyer", False
                ),
                "my_offer_accepted_by_seller": getattr(
                    my_offer, "accepted_by_seller", False
                ),
                "show_offer_actions": show_offer_actions,
                "conditional_collapse_id": f"cond-{req.id}",
                "can_mark_delivered": can_mark_delivered
                and req.requested_by_id != request.user.id,
                "owned_blueprints": owned_blueprints,
                "available_blueprints": available_blueprints,
                "active_copy_jobs": total_active_jobs,
                "all_copies_busy": all_copies_busy,
                "busy_until": busy_until,
                "busy_overdue": busy_overdue,
                "copy_base_time_seconds": copy_base_time_seconds,
                "copy_cost": copy_cost,
                "copy_duration": copy_duration,
                "copy_producer_options": copy_producer_options,
                "copy_producer_selected_id": selected_producer_id,
                "copy_producer_selected_name": selected_producer_name,
                "copy_structure_options": copy_structure_options,
                "copy_structure_selected_id": (
                    copy_cost.get("id") if copy_cost else None
                ),
                "chat": offer_chat_payload,
                "show_negotiation_actions": bool(
                    offer_chat_payload and my_offer and my_offer.status == "conditional"
                ),
                "negotiation_can_accept": bool(
                    my_offer
                    and my_offer.status == "conditional"
                    and my_offer.proposed_amount is not None
                    and not my_offer.accepted_by_seller
                ),
                "negotiation_can_decline": bool(
                    my_offer and my_offer.status == "conditional"
                ),
                "negotiation_amount_display": _format_isk_amount(
                    getattr(my_offer, "proposed_amount", None)
                ),
                "negotiation_notice": (
                    _(
                        "Current proposal sent: %(amount)s ISK. Waiting for the buyer to confirm or counter."
                    )
                    % {"amount": _format_isk_amount(my_offer.proposed_amount)}
                    if my_offer
                    and my_offer.status == "conditional"
                    and my_offer.proposed_amount is not None
                    and my_offer.accepted_by_seller
                    and not my_offer.accepted_by_buyer
                    else (
                        _(
                            "Buyer proposed %(amount)s ISK. Confirm it here or counter from the proposal bar."
                        )
                        % {"amount": _format_isk_amount(my_offer.proposed_amount)}
                        if my_offer
                        and my_offer.status == "conditional"
                        and my_offer.proposed_amount is not None
                        and my_offer.accepted_by_buyer
                        and not my_offer.accepted_by_seller
                        else ""
                    )
                ),
                "chat_preview": (
                    offer_chat_payload.get("preview", []) if offer_chat_payload else []
                ),
                "handshake": handshake_state,
                "is_corporate": is_corporate_source,
                "corporation_names": corporate_names,
                "corporation_tickers": corporate_tickers,
                "personal_source_names": displayed_personal_names,
                "personal_blueprints": displayed_personal_count,
                "corporate_blueprints": corporate_count,
                "has_dual_sources": has_dual_sources,
                "default_scope": default_scope,
                "eligible_builders": {
                    "characters": eligible_character_entries,
                    "corporations": eligible_corporation_entries,
                    "total": eligible_total,
                },
                "scope_modal_payload": scope_modal_payload,
            }
        )

    valid_filters = {"all", *status_meta.keys()}
    if active_filter not in valid_filters:
        active_filter = "all"

    filtered_requests = (
        [req for req in requests_to_fulfill if req.get("status_key") == active_filter]
        if active_filter != "all"
        else requests_to_fulfill
    )

    context = {
        "requests": filtered_requests,
        "has_requests": bool(requests_to_fulfill),
        "active_filter": active_filter,
        "metrics": metrics,
        "include_self_requests": include_self_requests,
    }
    if auto_open_chat_id:
        context["auto_open_chat_id"] = auto_open_chat_id
    context.update(nav_context)

    return render(
        request, "indy_hub/blueprint_sharing/bp_copy_fulfill_requests.html", context
    )


@indy_hub_access_required
@indy_hub_permission_required("can_manage_corp_bp_requests")
@login_required
def bp_copy_history(request):
    emit_view_analytics_event(view_name="industry.bp_copy_history", request=request)
    """Show a simple history of copy requests and their acceptor (when known).

    Visibility is restricted to users with the `can_manage_corp_bp_requests` permission.
    """

    status = (request.GET.get("status") or "all").strip().lower()
    search = (request.GET.get("search") or "").strip()
    per_page = request.GET.get("per_page")
    page = request.GET.get("page")

    try:
        per_page_val = int(per_page or 50)
    except (TypeError, ValueError):
        per_page_val = 50
    per_page_val = max(10, min(200, per_page_val))

    qs = (
        BlueprintCopyRequest.objects.select_related("requested_by", "fulfilled_by")
        .prefetch_related(
            Prefetch(
                "offers",
                queryset=BlueprintCopyOffer.objects.select_related("owner").order_by(
                    "-accepted_at", "-created_at", "-id"
                ),
            )
        )
        .order_by("-created_at", "-id")
    )

    if status == "open":
        qs = qs.filter(fulfilled=False)
    elif status == "fulfilled":
        qs = qs.filter(fulfilled=True, delivered=False)
    elif status == "delivered":
        qs = qs.filter(delivered=True)
    else:
        status = "all"

    if search:
        if search.isdigit():
            qs = qs.filter(type_id=int(search))
        else:
            qs = qs.filter(requested_by__username__icontains=search)

    metrics = {
        "total": BlueprintCopyRequest.objects.count(),
        "open": BlueprintCopyRequest.objects.filter(fulfilled=False).count(),
        "fulfilled": BlueprintCopyRequest.objects.filter(
            fulfilled=True, delivered=False
        ).count(),
        "delivered": BlueprintCopyRequest.objects.filter(delivered=True).count(),
    }

    paginator = Paginator(qs, per_page_val)
    page_obj = paginator.get_page(page)
    page_range = paginator.get_elided_page_range(
        number=page_obj.number, on_each_side=3, on_ends=1
    )

    rows = []
    for req in page_obj:
        offers = list(req.offers.all())
        accepted_offer = next(
            (
                offer
                for offer in offers
                if offer.status == "accepted"
                and offer.accepted_by_buyer
                and offer.accepted_by_seller
            ),
            None,
        )
        if accepted_offer is None:
            accepted_offer = next(
                (offer for offer in offers if offer.status == "accepted"), None
            )

        acceptor = req.fulfilled_by or (
            accepted_offer.owner if accepted_offer else None
        )
        rows.append(
            {
                "id": req.id,
                "type_id": req.type_id,
                "type_name": get_type_name(req.type_id),
                "icon_url": f"https://images.evetech.net/types/{req.type_id}/bp?size=32",
                "material_efficiency": req.material_efficiency,
                "time_efficiency": req.time_efficiency,
                "runs_requested": req.runs_requested,
                "copies_requested": req.copies_requested,
                "requested_by": req.requested_by,
                "created_at": req.created_at,
                "fulfilled": req.fulfilled,
                "fulfilled_at": req.fulfilled_at,
                "delivered": req.delivered,
                "delivered_at": req.delivered_at,
                "acceptor": acceptor,
                "source_scope": (
                    getattr(accepted_offer, "source_scope", None)
                    if accepted_offer
                    else None
                ),
            }
        )

    context = {
        "status": status,
        "search": search,
        "per_page": per_page_val,
        "per_page_options": [25, 50, 100, 200],
        "metrics": metrics,
        "page_obj": page_obj,
        "page_range": page_range,
        "rows": rows,
    }
    context.update(build_nav_context(request.user, active_tab="blueprint_sharing"))

    return render(request, "indy_hub/blueprint_sharing/bp_copy_history.html", context)


def _process_offer_action(
    *,
    request_obj,
    req: BlueprintCopyRequest,
    owner,
    action: str | None,
    message: str = "",
    source_scope: str | None = None,
    proposed_amount: Decimal | None = None,
) -> bool:
    return process_offer_action_with_deps(
        request_obj=request_obj,
        req=req,
        owner=owner,
        action=action,
        message=message,
        source_scope=source_scope,
        proposed_amount=proposed_amount,
        deps=_offer_flow_deps(),
    )


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_offer_copy_request(request, request_id):
    """Handle offering to fulfill a blueprint copy request."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    if req.requested_by_id == request.user.id:
        messages.error(request, _("You cannot make an offer on your own request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    if not _user_can_fulfill_request(req, request.user):
        messages.error(request, _("You are not allowed to fulfill this request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")
    action = request.POST.get("action")
    source_scope = request.POST.get("source_scope") or request.POST.get("scope")
    message = request.POST.get("message", "").strip()
    raw_amount = (
        request.POST.get("proposed_amount") or request.POST.get("amount") or ""
    ).strip()
    proposed_amount = _normalize_offer_amount(raw_amount)
    if raw_amount and proposed_amount is None:
        messages.error(request, _("Enter a valid proposal amount in ISK."))
        return redirect("indy_hub:bp_copy_fulfill_requests")
    handled = _process_offer_action(
        request_obj=request,
        req=req,
        owner=request.user,
        action=action,
        message=message,
        source_scope=source_scope,
        proposed_amount=proposed_amount,
    )
    redirect_url = reverse("indy_hub:bp_copy_fulfill_requests")
    if handled:
        if action == "conditional":
            offer = (
                BlueprintCopyOffer.objects.filter(request=req, owner=request.user)
                .select_related("chat")
                .first()
            )
            if offer:
                try:
                    chat_id = offer.chat.id
                except BlueprintCopyChat.DoesNotExist:
                    chat_id = None
                if chat_id:
                    redirect_url = f"{redirect_url}?{urlencode({'open_chat': chat_id})}"
    else:
        messages.error(request, _("Unsupported action for this request."))
    return redirect(redirect_url)


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_discord_action(request):
    """Process quick actions triggered from Discord notifications."""

    redirect_url = reverse("indy_hub:bp_copy_fulfill_requests")
    token = (request.GET.get("token") or "").strip()
    if not token:
        messages.error(request, _("Missing action token."))
        return redirect(redirect_url)

    try:
        payload = decode_action_token(token, max_age=_DEFAULT_TOKEN_MAX_AGE)
    except SignatureExpired:
        messages.error(request, _("This action link has expired."))
        return redirect(redirect_url)
    except BadSignature:
        messages.error(request, _("Invalid action token."))
        return redirect(redirect_url)

    expected_user_id = payload.get("u")
    request_id = payload.get("r")
    action = payload.get("a")
    source_scope = request.GET.get("source_scope") or request.GET.get("scope")

    if expected_user_id is not None and expected_user_id != request.user.id:
        messages.error(request, _("This action link is not for your account."))
        return redirect(redirect_url)

    if not request_id or not action:
        messages.error(request, _("Incomplete action token."))
        return redirect(redirect_url)

    req = (
        BlueprintCopyRequest.objects.filter(id=request_id, fulfilled=False)
        .select_related("requested_by")
        .first()
    )
    if not req:
        messages.warning(
            request,
            _("This copy request is no longer available."),
        )
        return redirect(redirect_url)

    if req.requested_by_id == request.user.id:
        messages.error(
            request,
            _("You cannot respond to a copy request you created."),
        )
        return redirect(redirect_url)

    existing_offer = BlueprintCopyOffer.objects.filter(
        request=req, owner=request.user
    ).first()
    if not existing_offer and request.user.id not in _eligible_owner_ids_for_request(
        req
    ):
        messages.error(
            request,
            _("You are no longer eligible to fulfil this copy request."),
        )
        return redirect(redirect_url)

    chat_id = None
    if action == "conditional":
        handled = _process_offer_action(
            request_obj=request,
            req=req,
            owner=request.user,
            action=action,
            message="",
            source_scope=source_scope,
        )
        if handled:
            offer = (
                BlueprintCopyOffer.objects.filter(request=req, owner=request.user)
                .select_related("chat")
                .first()
            )
            if offer:
                try:
                    chat_id = offer.chat.id
                except BlueprintCopyChat.DoesNotExist:
                    chat_id = None
    else:
        handled = _process_offer_action(
            request_obj=request,
            req=req,
            owner=request.user,
            action=action,
            source_scope=source_scope,
        )

    if not handled:
        messages.error(request, _("Unsupported action for this request."))
        return redirect(redirect_url)

    if chat_id:
        redirect_url = f"{redirect_url}?{urlencode({'open_chat': chat_id})}"
    return redirect(redirect_url)


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_POST
def bp_buyer_accept_offer(request, offer_id):
    """Allow buyer to accept a conditional offer."""
    offer = get_object_or_404(BlueprintCopyOffer, id=offer_id, status="conditional")

    if offer.request.requested_by_id != request.user.id:
        messages.error(request, _("Only the requester can accept this offer."))
        return redirect("indy_hub:bp_copy_request_page")

    if (
        offer.accepted_by_buyer
        and offer.accepted_by_seller
        and offer.status == "accepted"
    ):
        messages.info(request, _("This offer has already been confirmed."))
        return redirect("indy_hub:bp_copy_request_page")

    if offer.accepted_by_buyer:
        messages.info(request, _("You have already accepted these terms."))
        return redirect("indy_hub:bp_copy_request_page")

    finalized = _mark_offer_buyer_accept(offer)
    if finalized:
        messages.success(request, _("Offer accepted. Seller notified."))
        return redirect("indy_hub:bp_copy_request_page")

    fulfill_queue_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_fulfill_requests")
    )
    notify_user(
        offer.owner,
        _("Conditional offer accepted"),
        _(
            "%(buyer)s accepted your terms for %(type)s (ME%(me)s, TE%(te)s). Confirm in chat to finalise the agreement."
        )
        % {
            "buyer": offer.request.requested_by.username,
            "type": get_type_name(offer.request.type_id),
            "me": offer.request.material_efficiency,
            "te": offer.request.time_efficiency,
        },
        "info",
        link=fulfill_queue_url,
        link_label=_("Open fulfill queue"),
    )
    messages.info(
        request,
        _("You accepted the terms. Waiting for the builder to confirm."),
    )
    return redirect("indy_hub:bp_copy_request_page")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_accept_copy_request(request, request_id):
    """Legacy endpoint: accept request via modern offer flow."""
    if request.method != "POST":
        messages.error(request, _("You can only accept via POST."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)

    if req.requested_by_id == request.user.id:
        messages.error(request, _("You cannot accept your own request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    if not _user_can_fulfill_request(req, request.user):
        messages.error(request, _("You are not allowed to accept this request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    source_scope = request.POST.get("source_scope") or request.POST.get("scope")
    handled = _process_offer_action(
        request_obj=request,
        req=req,
        owner=request.user,
        action="accept",
        source_scope=source_scope,
    )
    if not handled:
        messages.error(request, _("Unsupported action for this request."))
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_cond_copy_request(request, request_id):
    """Legacy endpoint: send conditional offer via modern offer flow."""
    if request.method != "POST":
        messages.error(request, _("You can only respond via POST."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)

    if req.requested_by_id == request.user.id:
        messages.error(request, _("You cannot respond to your own request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    if not _user_can_fulfill_request(req, request.user):
        messages.error(request, _("You are not allowed to respond to this request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    source_scope = request.POST.get("source_scope") or request.POST.get("scope")
    message = request.POST.get("message", "").strip()
    raw_amount = (
        request.POST.get("proposed_amount") or request.POST.get("amount") or ""
    ).strip()
    proposed_amount = _normalize_offer_amount(raw_amount)
    if raw_amount and proposed_amount is None:
        messages.error(request, _("Enter a valid proposal amount in ISK."))
        return redirect("indy_hub:bp_copy_fulfill_requests")
    handled = _process_offer_action(
        request_obj=request,
        req=req,
        owner=request.user,
        action="conditional",
        message=message,
        source_scope=source_scope,
        proposed_amount=proposed_amount,
    )
    if not handled:
        messages.error(request, _("Unsupported action for this request."))
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_reject_copy_request(request, request_id):
    """Legacy endpoint: reject offer via modern offer flow."""
    if request.method != "POST":
        messages.error(request, _("You can only reject via POST."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)

    if req.requested_by_id == request.user.id:
        messages.error(request, _("You cannot reject your own request here."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    if not _user_can_fulfill_request(req, request.user):
        messages.error(request, _("You are not allowed to reject this request."))
        return redirect("indy_hub:bp_copy_fulfill_requests")

    source_scope = request.POST.get("source_scope") or request.POST.get("scope")
    message = request.POST.get("message", "").strip()
    handled = _process_offer_action(
        request_obj=request,
        req=req,
        owner=request.user,
        action="reject",
        message=message,
        source_scope=source_scope,
    )
    if not handled:
        messages.error(request, _("Unsupported action for this request."))
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_POST
def bp_cancel_copy_request(request, request_id):
    """Allow user to cancel their own copy request before delivery."""
    req = get_object_or_404(
        BlueprintCopyRequest,
        id=request_id,
        requested_by=request.user,
        delivered=False,
    )
    offers = req.offers.all()
    fulfill_queue_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_fulfill_requests")
    )
    _close_request_chats(req, BlueprintCopyChat.CloseReason.REQUEST_WITHDRAWN)
    for offer in offers:
        notify_user(
            offer.owner,
            "Blueprint Copy Request Cancelled",
            f"{request.user.username} cancelled their copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}).",
            "warning",
            link=fulfill_queue_url,
            link_label=_("Open fulfill queue"),
        )
    webhook_messages = NotificationWebhookMessage.objects.filter(copy_request=req)
    for webhook_message in webhook_messages:
        delete_discord_webhook_message(
            webhook_message.webhook_url,
            webhook_message.message_id,
        )
    webhook_messages.delete()
    offers.delete()
    req.delete()
    messages.success(request, "Copy request cancelled.")

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)

    return redirect("indy_hub:bp_copy_my_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
@require_POST
def bp_mark_copy_delivered(request, request_id):
    """Mark a fulfilled blueprint copy request as delivered (provider action)."""
    req = get_object_or_404(
        BlueprintCopyRequest, id=request_id, fulfilled=True, delivered=False
    )

    offer = (
        req.offers.filter(owner=request.user, status__in=["accepted", "conditional"])
        .select_related("request")
        .first()
    )
    if not offer and req.fulfilled_by_id != request.user.id:
        messages.error(
            request, _("You do not have an accepted offer for this request.")
        )
        return redirect("indy_hub:bp_copy_fulfill_requests")

    if (
        offer
        and offer.status == "conditional"
        and not (offer.accepted_by_buyer and offer.accepted_by_seller)
    ):
        messages.error(
            request,
            _("You must finalize the conditional offer before marking delivered."),
        )
        return redirect("indy_hub:bp_copy_fulfill_requests")

    req.delivered = True
    req.delivered_at = timezone.now()
    req.save()
    _close_request_chats(req, BlueprintCopyChat.CloseReason.MANUAL)
    my_requests_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_my_requests")
    )
    notify_user(
        req.requested_by,
        "Blueprint Copy Request Delivered",
        f"Your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) has been marked as delivered.",
        "success",
        link=my_requests_url,
        link_label=_("Review your requests"),
    )
    messages.success(request, "Request marked as delivered.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_update_copy_request(request, request_id):
    """Allow requester to update runs / copies for an open request."""
    if request.method != "POST":
        messages.error(request, _("You can only update a request via POST."))
        return redirect("indy_hub:bp_copy_my_requests")

    req = get_object_or_404(
        BlueprintCopyRequest,
        id=request_id,
        requested_by=request.user,
        fulfilled=False,
    )

    try:
        runs = max(1, int(request.POST.get("runs_requested", req.runs_requested)))
        copies = max(1, int(request.POST.get("copies_requested", req.copies_requested)))
    except (TypeError, ValueError):
        messages.error(request, _("Invalid values provided for the request update."))
        return redirect("indy_hub:bp_copy_my_requests")

    req.runs_requested = runs
    req.copies_requested = copies
    req.save(update_fields=["runs_requested", "copies_requested"])

    # Django
    from django.contrib.auth.models import User

    owner_ids = (
        Blueprint.objects.filter(
            type_id=req.type_id,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            bp_type=Blueprint.BPType.ORIGINAL,
        )
        .values_list("owner_user", flat=True)
        .distinct()
    )

    notification_context = {
        "username": request.user.username,
        "type_name": get_type_name(req.type_id),
        "me": req.material_efficiency,
        "te": req.time_efficiency,
        "runs": runs,
        "copies": copies,
    }
    notification_title = _("Updated blueprint copy request")
    notification_body = (
        _(
            "%(username)s updated their request for %(type_name)s (ME%(me)s, TE%(te)s): %(runs)s runs, %(copies)s copies."
        )
        % notification_context
    )

    fulfill_queue_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_fulfill_requests")
    )
    fulfill_label = _("Review copy requests")

    sent_to: set[int] = set()
    for owner in User.objects.filter(id__in=owner_ids, is_active=True):
        if owner.id in sent_to:
            continue
        sent_to.add(owner.id)
        notify_user(
            owner,
            notification_title,
            notification_body,
            "info",
            link=fulfill_queue_url,
            link_label=fulfill_label,
        )

    messages.success(request, _("Request updated."))
    return redirect("indy_hub:bp_copy_my_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_access_indy_hub")
@login_required
def bp_copy_my_requests(request):
    emit_view_analytics_event(view_name="industry.bp_copy_my_requests", request=request)
    """List copy requests made by the current user."""
    requested_filter = (request.GET.get("status") or "all").strip().lower()
    active_filter = requested_filter
    qs = (
        BlueprintCopyRequest.objects.filter(requested_by=request.user)
        .select_related("requested_by")
        .prefetch_related("offers__owner", "offers__chat")
        .order_by("-created_at")
    )

    auto_open_chat_id: str | None = None
    requested_chat = request.GET.get("open_chat")
    if requested_chat:
        try:
            requested_chat_id = int(requested_chat)
        except (TypeError, ValueError):
            requested_chat_id = None
        if requested_chat_id:
            exists = BlueprintCopyChat.objects.filter(
                id=requested_chat_id, buyer=request.user
            ).exists()
            if exists:
                auto_open_chat_id = str(requested_chat_id)

    status_meta = {
        "open": {
            "label": _("Awaiting provider"),
            "badge": "bg-warning text-dark",
            "hint": _("No builder has accepted yet. Keep an eye out for new offers."),
        },
        "action_required": {
            "label": _("Your action needed"),
            "badge": "bg-info text-white",
            "hint": _(
                "Review conditional offers and accept the one that suits you best."
            ),
        },
        "awaiting_delivery": {
            "label": _("In progress"),
            "badge": "bg-success text-white",
            "hint": _(
                "A builder accepted. Coordinate delivery and watch for the completion notice."
            ),
        },
        "waiting_on_builder": {
            "label": _("Waiting on builder"),
            "badge": "bg-warning text-dark",
            "hint": _("You've confirmed the terms. Waiting for the builder to accept."),
        },
        "waiting_on_you": {
            "label": _("Confirm agreement"),
            "badge": "bg-warning text-dark",
            "hint": _(
                "The builder accepted your terms. Confirm in chat to finalise the agreement."
            ),
        },
        "delivered": {
            "label": _("Delivered"),
            "badge": "bg-secondary text-white",
            "hint": _("Blueprint copies have been delivered. Enjoy!"),
        },
    }

    metrics = {
        "total": 0,
        "open": 0,
        "action_required": 0,
        "awaiting_delivery": 0,
        "delivered": 0,
    }

    active_requests: list[dict[str, Any]] = []
    history_requests: list[dict[str, Any]] = []
    for req in qs:
        offers = list(req.offers.all())
        accepted_offer_obj = next(
            (offer for offer in offers if offer.status == "accepted"), None
        )

        conditional_offers = [
            offer for offer in offers if offer.status == "conditional"
        ]
        cond_offer_data = []
        cond_accepted = None
        cond_waiting_builder = None
        cond_waiting_buyer = None

        for idx, offer in enumerate(conditional_offers, start=1):
            label = _("Builder #%d") % idx
            chat_payload = _build_offer_chat_payload(
                offer,
                viewer_role="buyer",
                reopen=not req.delivered,
            )

            if offer.accepted_by_buyer and offer.accepted_by_seller:
                cond_accepted = {
                    "builder_label": label,
                    "chat": chat_payload,
                }
                continue
            if offer.accepted_by_buyer and not offer.accepted_by_seller:
                cond_waiting_builder = {
                    "builder_label": label,
                    "chat": chat_payload,
                }
                continue
            if offer.accepted_by_seller and not offer.accepted_by_buyer:
                cond_waiting_buyer = {
                    "builder_label": label,
                    "chat": chat_payload,
                }
                continue

            cond_offer_data.append(
                {
                    "id": offer.id,
                    "builder_label": label,
                    "chat": chat_payload,
                }
            )

        status_key = "open"
        if req.delivered:
            status_key = "delivered"
        elif req.fulfilled:
            status_key = "awaiting_delivery"
        elif cond_offer_data:
            status_key = "action_required"
        elif cond_waiting_buyer:
            status_key = "waiting_on_you"
        elif cond_waiting_builder:
            status_key = "waiting_on_builder"

        metrics["total"] += 1
        metrics_key = {
            "open": "open",
            "waiting_on_builder": "open",
            "action_required": "action_required",
            "waiting_on_you": "action_required",
            "awaiting_delivery": "awaiting_delivery",
            "delivered": "delivered",
        }.get(status_key)
        if metrics_key:
            metrics[metrics_key] += 1

        status_info = status_meta[status_key]
        status_hint = status_info["hint"]

        chat_actions = []
        if cond_waiting_buyer and cond_waiting_buyer.get("chat"):
            chat_actions.append(
                {
                    "builder_label": cond_waiting_buyer["builder_label"],
                    "chat": cond_waiting_buyer["chat"],
                }
            )

        if cond_waiting_builder and cond_waiting_builder.get("chat"):
            chat_actions.append(
                {
                    "builder_label": cond_waiting_builder["builder_label"],
                    "chat": cond_waiting_builder["chat"],
                }
            )

        if cond_accepted and cond_accepted.get("chat"):
            chat_actions.append(
                {
                    "builder_label": cond_accepted["builder_label"],
                    "chat": cond_accepted["chat"],
                }
            )

        accepted_chat_payload = None
        if accepted_offer_obj and not req.delivered:
            accepted_chat_payload = _build_offer_chat_payload(
                accepted_offer_obj,
                viewer_role="buyer",
                reopen=True,
            )
        if accepted_chat_payload and not any(
            action["chat"]["id"] == accepted_chat_payload["id"]
            for action in chat_actions
            if action.get("chat")
        ):
            chat_actions.append(
                {
                    "builder_label": accepted_offer_obj.owner.username,
                    "chat": accepted_chat_payload,
                }
            )

        for entry in cond_offer_data:
            chat_payload = entry.get("chat")
            if chat_payload:
                chat_actions.append(
                    {
                        "builder_label": entry["builder_label"],
                        "chat": chat_payload,
                    }
                )

        accepted_offer = (
            {
                "owner_username": accepted_offer_obj.owner.username,
                "message": accepted_offer_obj.message,
                "amount_display": _format_isk_amount(
                    accepted_offer_obj.proposed_amount
                ),
            }
            if accepted_offer_obj
            else None
        )

        is_history = status_key == "delivered"
        if is_history:
            closed_at = req.delivered_at or req.fulfilled_at or req.created_at
            history_requests.append(
                {
                    "id": req.id,
                    "type_id": req.type_id,
                    "type_name": get_type_name(req.type_id),
                    "material_efficiency": req.material_efficiency,
                    "time_efficiency": req.time_efficiency,
                    "copies_requested": req.copies_requested,
                    "runs_requested": req.runs_requested,
                    "status_label": status_info["label"],
                    "status_hint": status_hint,
                    "closed_at": closed_at,
                }
            )

        active_requests.append(
            {
                "id": req.id,
                "type_id": req.type_id,
                "type_name": get_type_name(req.type_id),
                "icon_url": f"https://images.evetech.net/types/{req.type_id}/bp?size=64",
                "material_efficiency": req.material_efficiency,
                "time_efficiency": req.time_efficiency,
                "copies_requested": req.copies_requested,
                "runs_requested": req.runs_requested,
                "accepted_offer": accepted_offer,
                "cond_accepted": cond_accepted,
                "cond_waiting_builder": cond_waiting_builder,
                "cond_waiting_buyer": cond_waiting_buyer,
                "cond_offers": cond_offer_data,
                "chat_actions": chat_actions,
                "delivered": req.delivered,
                "is_history": is_history,
                "status_key": status_key,
                "status_label": status_info["label"],
                "status_class": status_info["badge"],
                "status_hint": status_hint,
                "created_at": req.created_at,
                "can_cancel": not req.delivered,
            }
        )

    context = {
        "my_requests": active_requests,
        "history_requests": sorted(
            history_requests,
            key=lambda item: item.get("closed_at") or timezone.now(),
            reverse=True,
        ),
        "metrics": metrics,
        "active_filter": "all",
    }
    valid_filters = {"all", *status_meta.keys()}
    if active_filter not in valid_filters:
        active_filter = "all"
    context["active_filter"] = active_filter
    if active_filter != "all":
        context["my_requests"] = [
            req for req in active_requests if req.get("status_key") == active_filter
        ]
    if auto_open_chat_id:
        context["auto_open_chat_id"] = auto_open_chat_id
    context.update(build_nav_context(request.user, active_tab="blueprint_sharing"))

    return render(
        request, "indy_hub/blueprint_sharing/bp_copy_my_requests.html", context
    )


@indy_hub_access_required
@login_required
@require_http_methods(["GET"])
def bp_chat_history(request, chat_id: int):
    chat = get_object_or_404(
        BlueprintCopyChat.objects.select_related("request", "offer", "buyer", "seller"),
        id=chat_id,
    )

    logger.debug("bp_chat_history chat=%s user=%s", chat.id, request.user.id)

    payload = build_bp_chat_history_payload(
        chat=chat,
        user=request.user,
        requested_role=request.GET.get("viewer_role"),
        resolve_chat_viewer_role_fn=_resolve_chat_viewer_role,
        classify_bp_chat_message_fn=_classify_bp_chat_message,
        format_isk_amount_fn=_format_isk_amount,
        get_type_name_fn=get_type_name,
    )
    if not payload.get("ok"):
        return JsonResponse(
            payload.get("data", {"error": _("Unauthorized")}),
            status=int(payload.get("status") or 403),
        )

    viewer_role = payload["viewer_role"]
    data = payload["data"]
    if chat.buyer_id == chat.seller_id == request.user.id:
        now = timezone.now()
        chat.buyer_last_seen_at = now
        chat.seller_last_seen_at = now
        chat.save(
            update_fields=["buyer_last_seen_at", "seller_last_seen_at", "updated_at"]
        )
    else:
        chat.mark_seen(viewer_role, force=True)
    return JsonResponse(data)


@indy_hub_access_required
@login_required
@require_http_methods(["POST"])
def bp_chat_send(request, chat_id: int):
    chat = get_object_or_404(
        BlueprintCopyChat.objects.select_related("request", "offer", "buyer", "seller"),
        id=chat_id,
    )
    base_role = chat.role_for(request.user)
    if base_role not in {"buyer", "seller"}:
        return JsonResponse({"error": _("Unauthorized")}, status=403)
    if not chat.is_open:
        return JsonResponse(
            {"error": _("This chat is closed."), "closed": True}, status=409
        )

    payload = {}
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
    if not payload:
        payload = request.POST

    requested_role = payload.get("viewer_role") or payload.get("role")
    viewer_role = _resolve_chat_viewer_role(
        chat,
        request.user,
        base_role=base_role,
        override=requested_role,
    )
    if viewer_role not in {"buyer", "seller"}:
        return JsonResponse({"error": _("Unauthorized")}, status=403)

    message_content = (payload.get("message") or payload.get("content") or "").strip()
    if not message_content:
        return JsonResponse({"error": _("Message cannot be empty.")}, status=400)

    msg = BlueprintCopyMessage(
        chat=chat,
        sender=request.user,
        sender_role=viewer_role,
        content=message_content,
    )
    try:
        msg.full_clean()
    except ValidationError as exc:
        detail = ""
        if hasattr(exc, "messages") and exc.messages:
            detail = exc.messages[0]
        else:
            detail = str(exc)
        return JsonResponse(
            {"error": _("Invalid message."), "details": detail}, status=400
        )
    msg.save()
    chat.register_message(sender_role=viewer_role)

    logger.debug(
        "bp_chat_send chat=%s user=%s role=%s", chat.id, request.user.id, viewer_role
    )

    other_user = chat.seller if viewer_role == "buyer" else chat.buyer
    if getattr(other_user, "id", None):
        link = request.build_absolute_uri(
            reverse(
                "indy_hub:bp_copy_my_requests"
                if viewer_role == "seller"
                else "indy_hub:bp_copy_fulfill_requests"
            )
        )
        notify_user(
            other_user,
            _("New message in conditional offer"),
            _("You received a new message for %(type)s (ME%(me)s, TE%(te)s).")
            % {
                "type": get_type_name(chat.request.type_id),
                "me": chat.request.material_efficiency,
                "te": chat.request.time_efficiency,
            },
            "info",
            link=link,
            link_label=_("Open details"),
        )

    created_local = timezone.localtime(msg.created_at)
    response = {
        "message": {
            "id": msg.id,
            "role": msg.sender_role,
            "kind": "message",
            "content": msg.content,
            "created_at": created_local.isoformat(),
            "created_display": created_local.strftime("%Y-%m-%d %H:%M"),
        }
    }
    return JsonResponse(response, status=201)


@indy_hub_access_required
@login_required
@require_http_methods(["POST"])
def bp_chat_decide(request, chat_id: int):
    chat = get_object_or_404(
        BlueprintCopyChat.objects.select_related("request", "offer", "buyer", "seller"),
        id=chat_id,
    )

    expects_json = (
        request.content_type == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )

    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
    else:
        payload = request.POST

    requested_role = payload.get("viewer_role") or payload.get("role")
    fallback_viewer_role = "seller"
    if requested_role in {"buyer", "seller"}:
        fallback_viewer_role = requested_role

    fallback_url = reverse(
        "indy_hub:bp_copy_fulfill_requests"
        if fallback_viewer_role == "seller"
        else "indy_hub:bp_copy_my_requests"
    )
    next_url = payload.get("next") or request.headers.get("referer") or fallback_url
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = fallback_url

    def respond(
        data: dict[str, Any], *, status: int = 200, flash: tuple[str, str] | None = None
    ):
        if expects_json:
            return JsonResponse(data, status=status)
        if flash:
            level, text = flash
            getattr(messages, level)(request, text)
        return redirect(next_url)

    base_role = chat.role_for(request.user)
    if base_role not in {"buyer", "seller"}:
        return respond(
            {"error": _("Unauthorized")},
            status=403,
            flash=("error", _("Unauthorized negotiation action.")),
        )

    if not chat.is_open or chat.offer.status != "conditional":
        return respond(
            {"error": _("This conversation is already closed.")},
            status=409,
            flash=("warning", _("This negotiation is already closed.")),
        )

    viewer_role = _resolve_chat_viewer_role(
        chat,
        request.user,
        base_role=base_role,
        override=requested_role,
    )
    if viewer_role not in {"buyer", "seller"}:
        return respond(
            {"error": _("Unauthorized")},
            status=403,
            flash=("error", _("Unauthorized negotiation action.")),
        )

    decision = (payload.get("decision") or "").strip().lower()
    if decision not in {"accept", "reject", "propose"}:
        return respond(
            {"error": _("Unsupported decision.")},
            status=400,
            flash=("error", _("Unsupported negotiation action.")),
        )

    decision_result = execute_bp_chat_decision(
        chat=chat,
        request_user=request.user,
        viewer_role=viewer_role,
        decision=decision,
        payload=payload,
        normalize_offer_amount_fn=_normalize_offer_amount,
        record_offer_proposal_fn=_record_offer_proposal,
        mark_offer_buyer_accept_fn=_mark_offer_buyer_accept,
        mark_offer_seller_accept_fn=_mark_offer_seller_accept,
        format_isk_amount_fn=_format_isk_amount,
        notify_user_fn=notify_user,
        get_type_name_fn=get_type_name,
        build_site_url_fn=build_site_url,
        finalize_all_rejected_fn=_finalize_request_if_all_rejected,
    )

    return respond(
        decision_result.get("data", {}),
        status=int(decision_result.get("status") or 200),
        flash=decision_result.get("flash"),
    )


@indy_hub_access_required
@login_required
def production_simulations_list(request):
    emit_view_analytics_event(
        view_name="industry.production_simulations_list", request=request
    )
    """
    Display the list of production simulations saved by the user.
    Return JSON when api=1 is included in the query string.
    """
    if request.method == "POST":
        raw_project_refs = request.POST.getlist("project_refs")
        normalized_project_refs: list[str] = []
        for project_ref in raw_project_refs:
            try:
                normalized_ref = normalize_production_project_ref(project_ref)
            except Exception:
                continue
            if normalized_ref not in normalized_project_refs:
                normalized_project_refs.append(normalized_ref)

        if not normalized_project_refs:
            messages.warning(
                request,
                _("Select at least one craft table before deleting multiple projects."),
            )
            return redirect("indy_hub:production_simulations_list")

        projects_to_delete = list(
            ProductionProject.objects.filter(
                user=request.user, project_ref__in=normalized_project_refs
            ).only("project_ref", "name")
        )

        if not projects_to_delete:
            messages.warning(
                request,
                _(
                    "No selected craft tables were available to delete. Refresh the page and try again."
                ),
            )
            return redirect("indy_hub:production_simulations_list")

        deleted_count = len(projects_to_delete)
        deleted_names = [project.name for project in projects_to_delete if project.name]
        with transaction.atomic():
            ProductionProject.objects.filter(
                user=request.user, project_ref__in=normalized_project_refs
            ).delete()

        if deleted_count == 1:
            deleted_name = deleted_names[0] if deleted_names else _("craft table")
            messages.success(
                request,
                _(f'Craft table "{deleted_name}" deleted successfully.'),
            )
        else:
            messages.success(
                request,
                _("Deleted %(count)s craft tables.") % {"count": deleted_count},
            )
        return redirect("indy_hub:production_simulations_list")

    production_projects = list(
        ProductionProject.objects.filter(user=request.user)
        .order_by("-updated_at")
        .prefetch_related("items")
    )
    for project in production_projects:
        project.workspace_url = reverse(
            "indy_hub:craft_project", args=[project.project_ref]
        )
        project.activity_progress = normalize_project_progress(
            project, (project.summary or {}).get("item_progress")
        )
        project.progress_json_id = f"project-progress-{project.project_ref}"
        # Determine the hero icon for the project card.
        selected_items = [
            item for item in project.items.all() if item.is_selected and item.type_id
        ]
        produce_items = [
            item
            for item in selected_items
            if item.inclusion_mode == ProductionProjectItem.InclusionMode.PRODUCE
        ]
        hero_pool = produce_items or selected_items
        hero_item = None
        if len(hero_pool) == 1:
            hero_item = hero_pool[0]
        else:
            hero_item = next(
                (item for item in hero_pool if item.category_key == "hull"),
                None,
            )
        if hero_item is not None:
            hero_type_id = int(hero_item.type_id)
            hero_name = hero_item.type_name or ""
            # Resolve blueprints to their manufactured product so the icon
            # shows the actual item being produced, not the blueprint itself.
            product_type_id = get_blueprint_product_type_id(hero_type_id)
            if product_type_id and int(product_type_id) != hero_type_id:
                hero_type_id = int(product_type_id)
                product_name = get_type_name(hero_type_id)
                if product_name:
                    hero_name = product_name
            project.display_icon_type_id = hero_type_id
            project.display_icon_alt = hero_name
            project.display_icon_is_multi = False
        else:
            project.display_icon_type_id = None
            project.display_icon_alt = ""
            project.display_icon_is_multi = bool(hero_pool)

        # Plan-level scope counters (from the workspace BoM rather than the
        # top-level project items). These reflect the *real* number of
        # produce/buy lines the user planned in the workspace, including
        # intermediate components.
        ws_state = project.workspace_state or {}
        if isinstance(ws_state, dict):
            plan_prod = int(ws_state.get("total_prod_items") or 0)
            plan_buy = int(ws_state.get("total_buy_items") or 0)
            plan_total = int(ws_state.get("total_items") or 0) or (plan_prod + plan_buy)
        else:
            plan_prod = plan_buy = plan_total = 0
        project.plan_prod_lines = plan_prod
        project.plan_buy_lines = plan_buy
        project.plan_total_lines = plan_total
        project.has_plan_scope = bool(plan_prod or plan_buy)

    # Return JSON when the API payload is requested
    if request.GET.get("api") == "1":
        projects_data = []
        for project in production_projects:
            summary = project.summary or {}
            projects_data.append(
                {
                    "project_ref": project.project_ref,
                    "name": project.name,
                    "status": project.status,
                    "status_label": project.get_status_display(),
                    "source_kind": project.source_kind,
                    "source_kind_label": project.get_source_kind_display(),
                    "source_name": project.source_name,
                    "selected_items": int(summary.get("selected_items") or 0),
                    "selected_quantity": int(summary.get("selected_quantity") or 0),
                    "craftable_items": int(summary.get("craftable_items") or 0),
                    "buy_items": int(summary.get("buy_items") or 0),
                    "activity_progress": normalize_project_progress(
                        project, summary.get("item_progress")
                    ),
                    "updated_at": project.updated_at.strftime("%Y-%m-%d %H:%M"),
                    "workspace_url": project.workspace_url,
                }
            )

        return JsonResponse(
            {
                "success": True,
                "simulations": [],
                "projects": projects_data,
                "total_simulations": 0,
                "total_projects": len(projects_data),
            }
        )
    market_group_map = {}

    draft_projects = [
        project
        for project in production_projects
        if project.status == ProductionProject.Status.DRAFT
    ]
    saved_projects = [
        project
        for project in production_projects
        if project.status == ProductionProject.Status.SAVED
    ]
    archived_projects = [
        project
        for project in production_projects
        if project.status == ProductionProject.Status.ARCHIVED
    ]

    active_projects = draft_projects + saved_projects
    in_prod_jobs = 0
    tracked_projects = 0
    craftable_lines = 0
    buy_lines = 0
    for project in active_projects:
        craftable_lines += int(getattr(project, "plan_prod_lines", 0) or 0)
        buy_lines += int(getattr(project, "plan_buy_lines", 0) or 0)
        progress = project.activity_progress or {}
        if int(progress.get("in_progress_count") or 0) > 0:
            tracked_projects += 1
            in_prod_jobs += int(progress.get("in_progress_count") or 0)

    project_stats = {
        "total_projects": len(production_projects),
        "draft_projects": len(draft_projects),
        "saved_projects": len(saved_projects),
        "archived_projects": len(archived_projects),
        "selected_items": sum(
            int((project.summary or {}).get("selected_items") or 0)
            for project in production_projects
        ),
        "selected_quantity": sum(
            int((project.summary or {}).get("selected_quantity") or 0)
            for project in production_projects
        ),
        "active_projects": len(active_projects),
        "tracked_projects": tracked_projects,
        "in_prod_jobs": in_prod_jobs,
        "craftable_lines": craftable_lines,
        "buy_lines": buy_lines,
    }

    context = {
        "simulations": [],
        "total_simulations": 0,
        "market_group_map": json.dumps(market_group_map),
        "production_projects": production_projects,
        "draft_projects": draft_projects,
        "saved_projects": saved_projects,
        "archived_projects": archived_projects,
        "project_stats": project_stats,
        "production_project_import_preview_url": reverse(
            "indy_hub:production_project_import_preview"
        ),
        "create_production_project_url": reverse("indy_hub:create_production_project"),
    }
    context.update(build_nav_context(request.user, active_tab="industry"))
    return render(
        request, "indy_hub/industry/production_simulations_list.html", context
    )


@indy_hub_access_required
@login_required
def delete_production_simulation(request, simulation_id):
    messages.info(
        request,
        _(
            "Legacy single-blueprint simulations were removed. Use craft tables instead."
        ),
    )
    return redirect("indy_hub:production_simulations_list")


@indy_hub_access_required
@login_required
def delete_production_project(request, project_ref):
    """Delete a production project and its related items."""
    project = get_object_or_404(
        ProductionProject,
        project_ref=normalize_production_project_ref(project_ref),
        user=request.user,
    )

    if request.method == "POST":
        project_name = project.name
        project.delete()
        messages.success(request, f'Craft table "{project_name}" deleted successfully.')
        return redirect("indy_hub:production_simulations_list")

    context = {
        "project": project,
    }

    return render(
        request, "indy_hub/industry/confirm_delete_production_project.html", context
    )


@indy_hub_access_required
@login_required
def edit_simulation_name(request, simulation_id):
    messages.info(
        request,
        _(
            "Legacy single-blueprint simulations were removed. Use craft tables instead."
        ),
    )
    return redirect("indy_hub:production_simulations_list")


def _patch_payload_structure_planner_favorites(
    payload: dict, favorite_structure_ids: frozenset[int]
) -> None:
    """Patch is_favorite in structure_planner.structures; keeps cached payloads current."""
    sp = payload.get("structure_planner")
    if not isinstance(sp, dict):
        return
    structures = sp.get("structures")
    if not isinstance(structures, list):
        return
    for structure in structures:
        sid = int(structure.get("structure_id") or 0)
        structure["is_favorite"] = sid in favorite_structure_ids


def _get_industry_structure_rows():
    return _get_industry_structure_rows_for_filters({})


def _get_visible_industry_structures_queryset(user: User | None):
    queryset = IndustryStructure.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return queryset.filter(
            Q(visibility_scope=IndustryStructure.VisibilityScope.PUBLIC)
            | Q(
                visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
                owner_user=user,
            )
        )
    return queryset.filter(visibility_scope=IndustryStructure.VisibilityScope.PUBLIC)


def _get_visible_public_industry_structures_queryset():
    return IndustryStructure.objects.filter(
        visibility_scope=IndustryStructure.VisibilityScope.PUBLIC
    )


def _get_industry_structure_queryset_for_scope(user: User | None, scope: str):
    visible_queryset = _get_visible_industry_structures_queryset(user)
    if scope == "public":
        return visible_queryset.filter(
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC
        )
    if scope == "personal":
        return visible_queryset.filter(
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
        )
    return visible_queryset


def _get_accessible_industry_structure_or_404(user: User | None, structure_id: int):
    return get_object_or_404(
        _get_visible_industry_structures_queryset(user),
        pk=structure_id,
    )


def _get_structure_registry_filter_options(
    user: User | None,
) -> dict[str, list[tuple[str, str]]]:
    visible_queryset = _get_visible_industry_structures_queryset(user)
    region_options = [
        ("", _("All regions")),
        *[
            (str(name), str(name))
            for name in visible_queryset.exclude(region_name="")
            .values_list("region_name", flat=True)
            .distinct()
            .order_by("region_name")
        ],
    ]
    constellation_options = [
        ("", _("All constellations")),
        *[
            (str(name), str(name))
            for name in visible_queryset.exclude(constellation_name="")
            .values_list("constellation_name", flat=True)
            .distinct()
            .order_by("constellation_name")
        ],
    ]
    return {
        "security_band": [
            ("", _("All security bands")),
            *[
                (str(value), str(label))
                for value, label in IndustryStructure.SecurityBand.choices
            ],
        ],
        "region_name": region_options,
        "constellation_name": constellation_options,
        "structure_type_id": [
            ("", _("All structure types")),
            *[
                (str(type_id), str(label))
                for type_id, label in get_structure_type_options()
            ],
        ],
        "activity": [
            ("", _("All activities")),
            ("enable_manufacturing", _("Manufacturing")),
            ("enable_manufacturing_capitals", _("Manufacturing (Capitals)")),
            (
                "enable_manufacturing_super_capitals",
                _("Manufacturing (Super-Capitals)"),
            ),
            ("enable_research", _("Research")),
            ("enable_invention", _("Invention")),
            ("enable_biochemical_reactions", _("Biochemical Reactions")),
            ("enable_hybrid_reactions", _("Hybrid Reactions")),
            ("enable_composite_reactions", _("Composite Reactions")),
        ],
        "completion": [
            ("", _("All completion states")),
            ("complete", _("Complete profiles")),
            ("incomplete", _("Need setup")),
        ],
        "scope": [
            ("all", _("All visible structures")),
            ("public", _("Shared structures")),
            ("personal", _("My personal copies")),
        ],
    }


def _get_structure_registry_current_filters(request) -> dict[str, str]:
    filter_options = _get_structure_registry_filter_options(request.user)
    current_filters = {
        "search": str(request.GET.get("search") or "").strip(),
        "security_band": str(request.GET.get("security_band") or "").strip(),
        "region_name": str(request.GET.get("region_name") or "").strip(),
        "constellation_name": str(request.GET.get("constellation_name") or "").strip(),
        "structure_type_id": str(request.GET.get("structure_type_id") or "").strip(),
        "activity": str(request.GET.get("activity") or "").strip(),
        "completion": str(request.GET.get("completion") or "").strip(),
        "scope": str(request.GET.get("scope") or "all").strip(),
    }
    current_filters["activity"] = {
        "manufacturing": "enable_manufacturing",
        "manufacturing_capitals": "enable_manufacturing_capitals",
        "manufacturing_super_capitals": "enable_manufacturing_super_capitals",
        "research": "enable_research",
        "reactions": "enable_reactions",
    }.get(current_filters["activity"], current_filters["activity"])
    for filter_name in [
        "security_band",
        "region_name",
        "constellation_name",
        "structure_type_id",
        "activity",
        "completion",
        "scope",
    ]:
        valid_values = {value for value, _label in filter_options[filter_name]}
        if current_filters[filter_name] not in valid_values:
            current_filters[filter_name] = "all" if filter_name == "scope" else ""
    return current_filters


def _build_structure_registry_active_filter_badges(
    current_filters: dict[str, str],
    filter_options: dict[str, list[tuple[str, str]]],
) -> list[dict[str, str]]:
    option_maps = {
        filter_name: {value: label for value, label in options}
        for filter_name, options in filter_options.items()
    }
    filter_labels = {
        "search": _("Search"),
        "security_band": _("Security"),
        "region_name": _("Region"),
        "constellation_name": _("Constellation"),
        "structure_type_id": _("Structure Type"),
        "activity": _("Activity"),
        "completion": _("Completion"),
        "scope": _("Visibility"),
    }
    active_badges: list[dict[str, str]] = []

    for filter_name, filter_value in current_filters.items():
        if not filter_value:
            continue
        if filter_name == "scope" and filter_value == "all":
            continue
        badge_value = filter_value
        if filter_name != "search":
            badge_value = str(
                option_maps.get(filter_name, {}).get(filter_value, filter_value)
            )
        cleared_filters = {
            key: value
            for key, value in current_filters.items()
            if key != filter_name and value
        }
        clear_url = reverse("indy_hub:industry_structure_registry")
        if cleared_filters:
            clear_url = f"{clear_url}?{urlencode(cleared_filters)}"
        active_badges.append(
            {
                "label": str(filter_labels[filter_name]),
                "value": str(badge_value),
                "clear_url": clear_url,
            }
        )

    return active_badges


def _get_industry_structure_rows_for_filters(
    current_filters: dict[str, str],
    *,
    user: User | None = None,
):
    structures = _get_industry_structure_queryset_for_scope(
        user,
        current_filters.get("scope") or "all",
    ).prefetch_related("rigs")
    search_query = str(current_filters.get("search") or "").strip()
    if search_query:
        structures = structures.filter(
            Q(name__icontains=search_query)
            | Q(solar_system_name__icontains=search_query)
            | Q(constellation_name__icontains=search_query)
            | Q(region_name__icontains=search_query)
            | Q(structure_type_name__icontains=search_query)
            | Q(owner_corporation_name__icontains=search_query)
        )

    security_band = current_filters.get("security_band")
    if security_band:
        structures = structures.filter(system_security_band=security_band)

    region_name = current_filters.get("region_name")
    if region_name:
        structures = structures.filter(region_name=region_name)

    constellation_name = current_filters.get("constellation_name")
    if constellation_name:
        structures = structures.filter(constellation_name=constellation_name)

    structure_type_id = current_filters.get("structure_type_id")
    if structure_type_id:
        try:
            structures = structures.filter(structure_type_id=int(structure_type_id))
        except (TypeError, ValueError):
            pass

    activity_filter = current_filters.get("activity")
    if activity_filter in {
        "enable_manufacturing",
        "enable_manufacturing_capitals",
        "enable_manufacturing_super_capitals",
        "enable_research",
        "enable_invention",
        "enable_biochemical_reactions",
        "enable_hybrid_reactions",
        "enable_composite_reactions",
    }:
        structures = structures.filter(**{activity_filter: True})
    elif activity_filter == "enable_reactions":
        structures = structures.filter(
            Q(enable_biochemical_reactions=True)
            | Q(enable_hybrid_reactions=True)
            | Q(enable_composite_reactions=True)
        )
    elif activity_filter in {
        "enable_te_research",
        "enable_me_research",
        "enable_copying",
    }:
        structures = structures.filter(enable_research=True)

    structures = structures.order_by("name")
    structure_rows = []
    activity_labels = dict(IndustryActivityMixin.INDUSTRY_ACTIVITY_CHOICES)
    activity_group_sort_order = {
        "manufacturing": 1,
        "research": 2,
        "invention": 3,
        "reactions": 4,
    }
    for structure in structures:
        enabled_activity_ids = set(
            get_enabled_activity_ids_from_flags(
                {
                    "enable_manufacturing": structure.enable_manufacturing,
                    "enable_manufacturing_capitals": structure.enable_manufacturing_capitals,
                    "enable_manufacturing_super_capitals": structure.enable_manufacturing_super_capitals,
                    "enable_research": structure.enable_research,
                    "enable_invention": structure.enable_invention,
                    "enable_biochemical_reactions": structure.enable_biochemical_reactions,
                    "enable_hybrid_reactions": structure.enable_hybrid_reactions,
                    "enable_composite_reactions": structure.enable_composite_reactions,
                },
                structure_type_id=structure.structure_type_id,
            )
        )
        grouped_bonuses = {}
        resolved_bonuses = sorted(
            [
                bonus
                for bonus in structure.get_resolved_bonuses()
                if bonus.activity_id in enabled_activity_ids
            ],
            key=lambda bonus: (bonus.activity_id, bonus.source, bonus.label),
        )
        present_activity_ids = {bonus.activity_id for bonus in resolved_bonuses}
        should_collapse_research_bonuses = {
            IndustryActivityMixin.ACTIVITY_TE_RESEARCH,
            IndustryActivityMixin.ACTIVITY_ME_RESEARCH,
            IndustryActivityMixin.ACTIVITY_COPYING,
        }.issubset(present_activity_ids)
        for bonus in resolved_bonuses:
            if bonus.activity_id == IndustryActivityMixin.ACTIVITY_MANUFACTURING:
                activity_group_key = "manufacturing"
                activity_group_label = "Manufacturing"
            elif bonus.activity_id in {
                IndustryActivityMixin.ACTIVITY_TE_RESEARCH,
                IndustryActivityMixin.ACTIVITY_ME_RESEARCH,
                IndustryActivityMixin.ACTIVITY_COPYING,
            }:
                if should_collapse_research_bonuses:
                    activity_group_key = "research"
                    activity_group_label = "Research"
                else:
                    activity_group_key = f"activity_{bonus.activity_id}"
                    activity_group_label = activity_labels.get(
                        bonus.activity_id,
                        str(bonus.activity_id),
                    )
            elif bonus.activity_id == IndustryActivityMixin.ACTIVITY_INVENTION:
                activity_group_key = "invention"
                activity_group_label = "Invention"
            elif bonus.activity_id in {
                IndustryActivityMixin.ACTIVITY_REACTIONS,
                IndustryActivityMixin.ACTIVITY_REACTIONS_LEGACY,
            }:
                activity_group_key = "reactions"
                activity_group_label = "Reactions"
            else:
                activity_group_key = f"activity_{bonus.activity_id}"
                activity_group_label = activity_labels.get(
                    bonus.activity_id,
                    str(bonus.activity_id),
                )

            activity_group = grouped_bonuses.setdefault(
                activity_group_key,
                {
                    "activity_id": bonus.activity_id,
                    "activity_label": activity_group_label,
                    "sort_order": activity_group_sort_order.get(
                        activity_group_key,
                        bonus.activity_id,
                    ),
                    "rows": {},
                },
            )
            activity_rows = activity_group["rows"]
            summary = activity_rows.setdefault(
                (bonus.source, bonus.label),
                {
                    "source": bonus.source,
                    "label": bonus.label,
                    "material_efficiency_percent": Decimal("0"),
                    "time_efficiency_percent": Decimal("0"),
                    "job_cost_percent": Decimal("0"),
                },
            )
            summary["material_efficiency_percent"] = max(
                summary["material_efficiency_percent"],
                bonus.material_efficiency_percent or Decimal("0"),
            )
            summary["time_efficiency_percent"] = max(
                summary["time_efficiency_percent"],
                bonus.time_efficiency_percent or Decimal("0"),
            )
            summary["job_cost_percent"] = max(
                summary["job_cost_percent"],
                bonus.job_cost_percent or Decimal("0"),
            )
        normalized_grouped_bonuses = [
            {
                "activity_id": activity_group["activity_id"],
                "activity_label": activity_group["activity_label"],
                "rows": list(activity_group["rows"].values()),
            }
            for activity_group in sorted(
                grouped_bonuses.values(),
                key=lambda activity_group: (
                    activity_group["sort_order"],
                    activity_group["activity_label"],
                ),
            )
        ]
        structure_rows.append(
            {
                "structure": structure,
                "display_name": structure.display_name,
                "rigs": list(structure.rigs.all().order_by("slot_index")),
                "grouped_bonuses": normalized_grouped_bonuses,
                "missing_profile_sections": structure.get_missing_profile_sections(),
                "is_profile_incomplete": structure.is_profile_incomplete(),
            }
        )

    completion_filter = current_filters.get("completion")
    if completion_filter == "incomplete":
        structure_rows = [row for row in structure_rows if row["is_profile_incomplete"]]
    elif completion_filter == "complete":
        structure_rows = [
            row for row in structure_rows if not row["is_profile_incomplete"]
        ]

    return structure_rows


def _get_structure_filter_corporation_choices(
    user: User | None,
) -> list[tuple[int, str]]:
    corporation_rows = (
        _get_visible_industry_structures_queryset(user)
        .exclude(owner_corporation_id__isnull=True)
        .exclude(owner_corporation_name="")
        .values_list("owner_corporation_id", "owner_corporation_name")
        .distinct()
        .order_by("owner_corporation_name")
    )
    return [
        (int(corporation_id), str(corporation_name))
        for corporation_id, corporation_name in corporation_rows
        if corporation_id is not None and corporation_name
    ]


def _get_structure_filter_solar_system_choices(
    user: User | None,
) -> list[tuple[str, str]]:
    return [
        (str(name), str(name))
        for name in _get_visible_industry_structures_queryset(user)
        .exclude(solar_system_name="")
        .values_list("solar_system_name", flat=True)
        .distinct()
        .order_by("solar_system_name")
    ]


def _get_structure_filter_constellation_choices(
    user: User | None,
) -> list[tuple[str, str]]:
    return [
        (str(name), str(name))
        for name in _get_visible_industry_structures_queryset(user)
        .exclude(constellation_name="")
        .values_list("constellation_name", flat=True)
        .distinct()
        .order_by("constellation_name")
    ]


def _get_structure_filter_region_choices(user: User | None) -> list[tuple[str, str]]:
    return [
        (str(name), str(name))
        for name in _get_visible_industry_structures_queryset(user)
        .exclude(region_name="")
        .values_list("region_name", flat=True)
        .distinct()
        .order_by("region_name")
    ]


def _build_bulk_tax_form(user: User | None, *, data=None):
    return IndustryStructureBulkTaxUpdateForm(
        data,
        corporation_choices=_get_structure_filter_corporation_choices(user),
        solar_system_choices=_get_structure_filter_solar_system_choices(user),
        constellation_choices=_get_structure_filter_constellation_choices(user),
        region_choices=_get_structure_filter_region_choices(user),
    )


def _build_bulk_tax_preview_form(user: User | None, *, data=None):
    return IndustryStructureBulkTaxUpdateForm(
        data,
        corporation_choices=_get_structure_filter_corporation_choices(user),
        solar_system_choices=_get_structure_filter_solar_system_choices(user),
        constellation_choices=_get_structure_filter_constellation_choices(user),
        region_choices=_get_structure_filter_region_choices(user),
        enforce_tax_selection=False,
    )


def _get_bulk_tax_target_queryset(cleaned_data, *, user: User | None):
    queryset = _get_visible_industry_structures_queryset(user)

    source_scope = cleaned_data.get("source_scope")
    if source_scope == IndustryStructureBulkTaxUpdateForm.SOURCE_SCOPE_SYNCED:
        queryset = queryset.filter(
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION
        )
    elif source_scope == IndustryStructureBulkTaxUpdateForm.SOURCE_SCOPE_MANUAL:
        queryset = queryset.exclude(
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION
        )

    solar_system_name = cleaned_data.get("solar_system_name")
    if solar_system_name:
        queryset = queryset.filter(solar_system_name=solar_system_name)

    constellation_name = cleaned_data.get("constellation_name")
    if constellation_name:
        queryset = queryset.filter(constellation_name=constellation_name)

    region_name = cleaned_data.get("region_name")
    if region_name:
        queryset = queryset.filter(region_name=region_name)

    system_security_band = cleaned_data.get("system_security_band")
    if system_security_band:
        queryset = queryset.filter(system_security_band=system_security_band)

    structure_type_id = cleaned_data.get("structure_type_id")
    if structure_type_id:
        queryset = queryset.filter(structure_type_id=int(structure_type_id))

    owner_corporation_id = cleaned_data.get("owner_corporation_id")
    if owner_corporation_id:
        queryset = queryset.filter(owner_corporation_id=int(owner_corporation_id))

    return queryset


def _count_bulk_tax_eligible_structures(
    queryset, tax_updates, *, only_when_zero: bool
) -> int:
    eligible_count = 0
    for structure in queryset:
        for field_name, field_value in tax_updates.items():
            current_value = getattr(structure, field_name) or Decimal("0")
            if only_when_zero and current_value > 0:
                continue
            if current_value != field_value:
                eligible_count += 1
                break
    return eligible_count


def _get_bulk_tax_preview_payload(
    cleaned_data, *, user: User | None
) -> dict[str, object]:
    queryset = _get_bulk_tax_target_queryset(cleaned_data, user=user)
    matched_count = queryset.count()
    tax_updates = {
        field_name: cleaned_data[field_name]
        for field_name in IndustryStructureBulkTaxUpdateForm.tax_field_names
        if cleaned_data.get(field_name) is not None
    }
    only_when_zero = bool(cleaned_data.get("only_when_zero"))

    if not tax_updates:
        return {
            "matched_count": matched_count,
            "eligible_count": 0,
            "structure_names": [],
            "has_tax_updates": False,
            "message": _("Set at least one tax value to preview affected structures."),
        }

    structure_names: list[str] = []
    for structure in queryset.order_by("name"):
        for field_name, field_value in tax_updates.items():
            current_value = getattr(structure, field_name) or Decimal("0")
            if only_when_zero and current_value > 0:
                continue
            if current_value != field_value:
                structure_names.append(structure.name)
                break

    eligible_count = len(structure_names)
    return {
        "matched_count": matched_count,
        "eligible_count": eligible_count,
        "structure_names": structure_names,
        "has_tax_updates": True,
        "message": (
            _("No structure currently matches the selected bulk tax filters.")
            if not structure_names
            else ""
        ),
    }


def _user_can_manage_industry_structure_sync(user: User) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


def _build_structure_rig_initial_data(structure: IndustryStructure | None = None):
    rigs_by_slot = {}
    if structure is not None:
        rigs_by_slot = {rig.slot_index: rig for rig in structure.rigs.all()}
    return [
        {
            "slot_index": slot_index,
            "rig_type_id": getattr(rigs_by_slot.get(slot_index), "rig_type_id", None),
        }
        for slot_index in range(1, 4)
    ]


def _build_structure_rig_formset(
    *, structure: IndustryStructure | None = None, data=None
):
    structure_type_id = None if structure is None else structure.structure_type_id
    if data is not None:
        return IndustryStructureRigFormSet(
            data,
            prefix="rigs",
            structure_type_id=data.get("structure_type_id") or structure_type_id,
        )
    return IndustryStructureRigFormSet(
        prefix="rigs",
        structure_type_id=structure_type_id,
        initial=_build_structure_rig_initial_data(structure),
    )


def _set_structure_form_read_only(
    structure_form,
    *,
    editable_field_names: set[str] | None = None,
) -> None:
    editable_fields = editable_field_names or set()
    for field_name, field in structure_form.fields.items():
        if field_name in editable_fields:
            field.disabled = False
            field.widget.attrs.pop("disabled", None)
            continue
        field.disabled = True
        field.widget.attrs["disabled"] = "disabled"


def _emit_structure_form_errors(request, structure_form, rig_formset) -> None:
    """Surface form/formset errors as a flash message.

    Without this, an invalid POST silently re-renders the same page and the
    user perceives the submission as a no-op (see issue #70: NPC Station with
    capital activity flag was rejected with no visible feedback).
    """

    error_fragments: list[str] = []
    for field_name, errors in structure_form.errors.items():
        label = field_name
        bound_field = structure_form.fields.get(field_name)
        if bound_field is not None and getattr(bound_field, "label", None):
            label = str(bound_field.label)
        elif field_name == "__all__":
            label = str(_("Form"))
        for error in errors:
            error_fragments.append(f"{label}: {error}")

    for index, formset_errors in enumerate(rig_formset.errors):
        for field_name, errors in formset_errors.items():
            for error in errors:
                error_fragments.append(
                    str(_("Rig slot %(index)s — %(field)s: %(error)s"))
                    % {"index": index + 1, "field": field_name, "error": error}
                )
    for error in rig_formset.non_form_errors():
        error_fragments.append(f"{_('Rigs')}: {error}")

    if not error_fragments:
        return

    summary = "; ".join(error_fragments[:6])
    if len(error_fragments) > 6:
        summary += "…"
    messages.error(
        request,
        _("Could not save the structure: %(details)s") % {"details": summary},
    )


def _save_structure_rigs(structure: IndustryStructure, rig_formset) -> int:
    structure.rigs.all().delete()
    created_rig_count = 0
    for rig_form in rig_formset:
        cleaned_data = getattr(rig_form, "cleaned_data", None) or {}
        if not cleaned_data or cleaned_data.get("is_empty"):
            continue
        if "slot_index" not in cleaned_data or "rig_type_id" not in cleaned_data:
            continue
        IndustryStructureRig.objects.create(
            structure=structure,
            slot_index=cleaned_data["slot_index"],
            rig_type_id=cleaned_data["rig_type_id"],
            rig_type_name=cleaned_data["rig_type_name"],
        )
        created_rig_count += 1
    return created_rig_count


def _generate_duplicate_structure_name(source_structure: IndustryStructure) -> str:
    base_name = _("%(name)s Copy") % {"name": source_structure.name}
    if not IndustryStructure.objects.filter(name=base_name).exists():
        return str(base_name)

    suffix = 2
    while True:
        candidate = _("%(name)s Copy %(suffix)s") % {
            "name": source_structure.name,
            "suffix": suffix,
        }
        if not IndustryStructure.objects.filter(name=candidate).exists():
            return str(candidate)
        suffix += 1


def _build_next_personal_structure_tag(
    owner_user: User,
    source_structure: IndustryStructure,
) -> str:
    base_source_structure = source_structure.source_structure or source_structure
    username = str(getattr(owner_user, "username", "") or "user").strip() or "user"
    tag_prefix = f"{username} - "
    next_suffix = 1

    existing_tags = IndustryStructure.objects.filter(
        visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
        owner_user=owner_user,
        source_structure=base_source_structure,
    ).values_list("personal_tag", flat=True)

    for personal_tag in existing_tags:
        normalized_tag = str(personal_tag or "").strip()
        if not normalized_tag.startswith(tag_prefix):
            continue
        try:
            suffix = int(normalized_tag[len(tag_prefix) :])
        except (TypeError, ValueError):
            continue
        next_suffix = max(next_suffix, suffix + 1)

    return f"{username} - {next_suffix}"


def _build_structure_add_page_context(request, structure_form, rig_formset):
    context = {
        "structure_form": structure_form,
        "rig_formset": rig_formset,
        "structure_type_catalog_json": json.dumps(get_structure_type_catalog()),
        "rig_option_catalog_json": json.dumps(get_industry_rig_catalog()),
        "structure_registry_url": reverse("indy_hub:industry_structure_registry"),
        "back_to_industry_url": reverse("indy_hub:personnal_job_list"),
    }
    context.update(build_nav_context(request.user, active_tab="industry"))
    return context


def _build_structure_edit_page_context(request, structure, structure_form, rig_formset):
    context = _build_structure_add_page_context(request, structure_form, rig_formset)
    is_synced_structure_locked = structure.is_synced_structure()
    context.update(
        {
            "page_icon_class": "fas fa-pen",
            "page_title_text": (
                _("Manage Structure")
                if is_synced_structure_locked
                else _("Edit Structure")
            ),
            "page_subtitle": (
                _(
                    "Automatically synchronized structures keep identity and activities locked. Taxes and installed rigs can be edited here."
                )
                if is_synced_structure_locked
                else _(
                    "Update the registered structure, its enabled activities and installed rigs."
                )
            ),
            "page_back_url": reverse("indy_hub:industry_structure_registry"),
            "page_back_label": _("Back to Registry"),
            "submit_label": _("Save Changes"),
            "submit_icon_class": "fas fa-save",
            "structure": structure,
            "is_edit_mode": True,
            "is_synced_structure_locked": is_synced_structure_locked,
        }
    )
    return context


def _build_structure_duplicate_page_context(
    request,
    *,
    source_structure: IndustryStructure,
    tax_form,
    personal_structure: IndustryStructure | None = None,
    is_edit_mode: bool = False,
):
    context = {
        "source_structure": source_structure,
        "personal_structure": personal_structure,
        "is_personal_copy_edit_mode": is_edit_mode,
        "tax_form": tax_form,
        "structure_registry_url": reverse("indy_hub:industry_structure_registry"),
        "back_to_industry_url": reverse("indy_hub:personnal_job_list"),
    }
    context.update(build_nav_context(request.user, active_tab="industry"))
    return context


def _build_structure_registry_page_context(
    request,
    *,
    bulk_import_form=None,
    bulk_tax_form=None,
    bulk_tax_confirmation=None,
):
    current_filters = _get_structure_registry_current_filters(request)
    structure_filter_options = _get_structure_registry_filter_options(request.user)
    active_filter_badges = _build_structure_registry_active_filter_badges(
        current_filters,
        structure_filter_options,
    )
    structure_rows = _get_industry_structure_rows_for_filters(
        current_filters,
        user=request.user,
    )
    total_structure_count = _get_industry_structure_queryset_for_scope(
        request.user,
        current_filters.get("scope") or "all",
    ).count()
    can_manage_structure_sync = _user_can_manage_industry_structure_sync(request.user)
    auto_sync_targets = (
        get_available_structure_sync_targets() if can_manage_structure_sync else []
    )
    context = {
        "structure_rows": structure_rows,
        "filtered_structure_count": len(structure_rows),
        "total_structure_count": total_structure_count,
        "add_structure_url": reverse("indy_hub:industry_structure_add"),
        "back_to_industry_url": reverse("indy_hub:personnal_job_list"),
        "bulk_import_form": bulk_import_form or IndustryStructureBulkImportForm(),
        "bulk_tax_form": bulk_tax_form or _build_bulk_tax_form(request.user),
        "bulk_tax_confirmation": bulk_tax_confirmation,
        "auto_sync_corporations_count": len(auto_sync_targets),
        "authorize_corp_all_url": reverse("indy_hub:authorize_corp_all"),
        "can_manage_structure_sync": can_manage_structure_sync,
        "current_filters": current_filters,
        "structure_filter_options": structure_filter_options,
        "active_filter_badges": active_filter_badges,
        "has_active_filters": any(
            value
            for key, value in current_filters.items()
            if key != "scope" or value != "all"
        ),
        "synced_structure_count": sum(
            1 for row in structure_rows if row["structure"].is_synced_structure()
        ),
        "incomplete_structure_count": sum(
            1 for row in structure_rows if row["is_profile_incomplete"]
        ),
        "toggle_favorite_structure_url": reverse("indy_hub:toggle_favorite_structure"),
    }
    # Annotate each row with the current user's favorite state
    favorite_ids = frozenset(
        UserFavoriteStructure.objects.filter(user=request.user).values_list(
            "structure_id", flat=True
        )
    )
    for row in structure_rows:
        row["is_favorite"] = row["structure"].id in favorite_ids
    context.update(build_nav_context(request.user, active_tab="industry"))
    return context


@indy_hub_access_required
@login_required
def industry_structure_add(request):
    emit_view_analytics_event(
        view_name="industry.structure_add",
        request=request,
    )

    if request.method == "POST":
        structure_form = IndustryStructureRegistryForm(request.POST)
        rig_formset = IndustryStructureRigFormSet(
            request.POST,
            prefix="rigs",
            structure_type_id=request.POST.get("structure_type_id") or None,
        )

        if structure_form.is_valid() and rig_formset.is_valid():
            structure = structure_form.save(commit=False)
            structure.visibility_scope = IndustryStructure.VisibilityScope.PUBLIC
            structure.owner_user = None
            structure.personal_tag = ""
            structure.source_structure = None
            structure.save()

            created_rig_count = _save_structure_rigs(structure, rig_formset)

            messages.success(
                request,
                _(
                    "Structure registry entry created successfully. "
                    "Rigs saved: %(count)s."
                )
                % {"count": created_rig_count},
            )
            return redirect("indy_hub:industry_structure_registry")

        _emit_structure_form_errors(request, structure_form, rig_formset)
    else:
        structure_form = IndustryStructureRegistryForm()
        rig_formset = _build_structure_rig_formset()

    if not sde_item_types_loaded():
        messages.warning(
            request,
            _(
                "eve_sde item types are not loaded yet. Load the SDE before registering structures and rigs."
            ),
        )

    context = _build_structure_add_page_context(
        request=request,
        structure_form=structure_form,
        rig_formset=rig_formset,
    )
    return render(
        request,
        "indy_hub/industry/structure_add.html",
        context,
    )


@indy_hub_access_required
@login_required
def industry_structure_edit(request, structure_id):
    structure = _get_accessible_industry_structure_or_404(request.user, structure_id)
    if structure.is_personal_copy():
        emit_view_analytics_event(
            view_name="industry.structure_personal_copy_edit",
            request=request,
        )
        if request.method == "POST":
            tax_form = IndustryStructureTaxProfileDuplicateForm(
                request.POST,
                instance=structure,
                owner_user=request.user,
            )
            if tax_form.is_valid():
                structure.personal_tag = tax_form.cleaned_data["personal_tag"]
                for field_name, _label in IndustryStructure.TAX_FIELD_LABELS:
                    setattr(structure, field_name, tax_form.cleaned_data[field_name])
                structure.save()
                messages.success(
                    request,
                    _("Personal structure updated successfully."),
                )
                return redirect(
                    f'{reverse("indy_hub:industry_structure_registry")}?scope=personal'
                )
        else:
            tax_form = IndustryStructureTaxProfileDuplicateForm(
                instance=structure,
                owner_user=request.user,
            )

        context = _build_structure_duplicate_page_context(
            request,
            source_structure=structure.source_structure or structure,
            personal_structure=structure,
            tax_form=tax_form,
            is_edit_mode=True,
        )
        return render(
            request,
            "indy_hub/industry/structure_duplicate.html",
            context,
        )

    is_synced_structure_locked = structure.is_synced_structure()
    synced_editable_tax_fields = {
        field_name for field_name, _label in IndustryStructure.TAX_FIELD_LABELS
    }
    emit_view_analytics_event(
        view_name="industry.structure_edit",
        request=request,
    )

    if request.method == "POST":
        rig_formset = _build_structure_rig_formset(
            structure=structure, data=request.POST
        )
        if is_synced_structure_locked:
            structure_form = IndustryStructureRegistryForm(
                request.POST, instance=structure
            )
            _set_structure_form_read_only(
                structure_form,
                editable_field_names=synced_editable_tax_fields,
            )
        else:
            structure_form = IndustryStructureRegistryForm(
                request.POST, instance=structure
            )

        rig_formset_is_valid = rig_formset.is_valid()
        form_is_valid = structure_form.is_valid()
        if rig_formset_is_valid and form_is_valid:
            structure = structure_form.save(commit=False)
            structure.save()
            saved_rig_count = _save_structure_rigs(structure, rig_formset)
            messages.success(
                request,
                (
                    _("Structure updated successfully. Rigs saved: %(count)s.")
                    if not is_synced_structure_locked
                    else _(
                        "Synchronized structure updated successfully. Rigs saved: %(count)s."
                    )
                )
                % {"count": saved_rig_count},
            )
            return redirect("indy_hub:industry_structure_registry")

        # For synced structures, allow partial save: persist editable fields (taxes) + rigs
        # even if non-editable identity fields fail validation (e.g., ESI lookup failed).
        if is_synced_structure_locked and rig_formset_is_valid and not form_is_valid:
            # Try to save only the editable tax fields and rigs, skipping identity validation
            editable_fields_data = {
                field_name: structure_form.cleaned_data.get(field_name)
                for field_name in synced_editable_tax_fields
                if field_name in structure_form.cleaned_data
            }
            if editable_fields_data:
                # Form clean may exit early when identity lookups fail; normalize
                # any missing tax values before persisting to non-null DecimalFields.
                for field_name in synced_editable_tax_fields:
                    if not field_name.endswith("_tax_percent"):
                        continue
                    value = editable_fields_data.get(field_name)
                    if value in (None, ""):
                        editable_fields_data[field_name] = Decimal("0")

                for field_name, value in editable_fields_data.items():
                    setattr(structure, field_name, value)
                structure.save(
                    update_fields=[*editable_fields_data.keys(), "updated_at"]
                )
                saved_rig_count = _save_structure_rigs(structure, rig_formset)
                messages.success(
                    request,
                    _(
                        "Taxes and rigs updated successfully. Rigs saved: %(count)s. "
                        "(Identity fields could not be updated due to ESI lookup issues.)"
                    )
                    % {"count": saved_rig_count},
                )
                return redirect("indy_hub:industry_structure_registry")

        _emit_structure_form_errors(request, structure_form, rig_formset)
    else:
        structure_form = IndustryStructureRegistryForm(instance=structure)
        if is_synced_structure_locked:
            _set_structure_form_read_only(
                structure_form,
                editable_field_names=synced_editable_tax_fields,
            )
        rig_formset = _build_structure_rig_formset(structure=structure)

    context = _build_structure_edit_page_context(
        request,
        structure,
        structure_form,
        rig_formset,
    )
    return render(request, "indy_hub/industry/structure_add.html", context)


@indy_hub_access_required
@login_required
def industry_structure_duplicate(request, structure_id):
    source_structure = _get_accessible_industry_structure_or_404(
        request.user, structure_id
    )
    emit_view_analytics_event(
        view_name="industry.structure_duplicate",
        request=request,
    )
    suggested_personal_tag = _build_next_personal_structure_tag(
        request.user,
        source_structure,
    )

    if request.method == "POST":
        tax_form = IndustryStructureTaxProfileDuplicateForm(
            request.POST,
            instance=source_structure,
            owner_user=request.user,
            suggested_personal_tag=suggested_personal_tag,
        )
        if tax_form.is_valid():
            duplicated_structure = IndustryStructure.objects.create(
                name=source_structure.name,
                personal_tag=tax_form.cleaned_data["personal_tag"],
                structure_type_id=source_structure.structure_type_id,
                structure_type_name=source_structure.structure_type_name,
                solar_system_id=source_structure.solar_system_id,
                solar_system_name=source_structure.solar_system_name,
                constellation_id=source_structure.constellation_id,
                constellation_name=source_structure.constellation_name,
                region_id=source_structure.region_id,
                region_name=source_structure.region_name,
                system_security_band=source_structure.system_security_band,
                owner_corporation_id=source_structure.owner_corporation_id,
                owner_corporation_name=source_structure.owner_corporation_name,
                sync_source=IndustryStructure.SyncSource.MANUAL,
                visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
                owner_user=request.user,
                source_structure=source_structure,
                enable_manufacturing=source_structure.enable_manufacturing,
                enable_manufacturing_capitals=source_structure.enable_manufacturing_capitals,
                enable_manufacturing_super_capitals=source_structure.enable_manufacturing_super_capitals,
                enable_research=source_structure.enable_research,
                enable_invention=source_structure.enable_invention,
                enable_biochemical_reactions=source_structure.enable_biochemical_reactions,
                enable_hybrid_reactions=source_structure.enable_hybrid_reactions,
                enable_composite_reactions=source_structure.enable_composite_reactions,
                manufacturing_tax_percent=tax_form.cleaned_data[
                    "manufacturing_tax_percent"
                ],
                manufacturing_capitals_tax_percent=tax_form.cleaned_data[
                    "manufacturing_capitals_tax_percent"
                ],
                manufacturing_super_capitals_tax_percent=tax_form.cleaned_data[
                    "manufacturing_super_capitals_tax_percent"
                ],
                research_tax_percent=tax_form.cleaned_data["research_tax_percent"],
                invention_tax_percent=tax_form.cleaned_data["invention_tax_percent"],
                biochemical_reactions_tax_percent=tax_form.cleaned_data[
                    "biochemical_reactions_tax_percent"
                ],
                hybrid_reactions_tax_percent=tax_form.cleaned_data[
                    "hybrid_reactions_tax_percent"
                ],
                composite_reactions_tax_percent=tax_form.cleaned_data[
                    "composite_reactions_tax_percent"
                ],
            )
            for rig in source_structure.rigs.all().order_by("slot_index"):
                IndustryStructureRig.objects.create(
                    structure=duplicated_structure,
                    slot_index=rig.slot_index,
                    rig_type_id=rig.rig_type_id,
                    rig_type_name=rig.rig_type_name,
                )

            messages.success(
                request,
                _("Personal structure copy created successfully as %(name)s.")
                % {"name": duplicated_structure.display_name},
            )
            return redirect(
                f'{reverse("indy_hub:industry_structure_registry")}?scope=personal'
            )
    else:
        tax_form = IndustryStructureTaxProfileDuplicateForm(
            instance=source_structure,
            owner_user=request.user,
            suggested_personal_tag=suggested_personal_tag,
        )

    context = _build_structure_duplicate_page_context(
        request,
        source_structure=source_structure,
        tax_form=tax_form,
    )
    return render(
        request,
        "indy_hub/industry/structure_duplicate.html",
        context,
    )


@indy_hub_access_required
@login_required
def industry_structure_delete(request, structure_id):
    structure = _get_accessible_industry_structure_or_404(request.user, structure_id)
    emit_view_analytics_event(
        view_name="industry.structure_delete",
        request=request,
    )

    if request.method == "POST":
        structure_name = structure.display_name
        structure.delete()
        messages.success(
            request,
            _("Structure %(name)s deleted successfully.") % {"name": structure_name},
        )
        return redirect("indy_hub:industry_structure_registry")

    context = {
        "structure": structure,
        "structure_registry_url": reverse("indy_hub:industry_structure_registry"),
        "back_to_industry_url": reverse("indy_hub:personnal_job_list"),
    }
    context.update(build_nav_context(request.user, active_tab="industry"))
    return render(
        request,
        "indy_hub/industry/confirm_delete_structure.html",
        context,
    )


@indy_hub_access_required
@login_required
def industry_structure_registry(request):
    emit_view_analytics_event(
        view_name="industry.structure_registry",
        request=request,
    )

    if not sde_item_types_loaded():
        messages.warning(
            request,
            _(
                "eve_sde item types are not loaded yet. Load the SDE before browsing structure bonuses."
            ),
        )

    context = _build_structure_registry_page_context(request)
    return render(
        request,
        "indy_hub/industry/structure_registry.html",
        context,
    )


@indy_hub_access_required
@login_required
@require_http_methods(["POST"])
def industry_structure_bulk_import(request):
    emit_view_analytics_event(
        view_name="industry.structure_bulk_import",
        request=request,
    )

    bulk_import_form = IndustryStructureBulkImportForm(request.POST)
    if not bulk_import_form.is_valid():
        context = _build_structure_registry_page_context(request)
        context["bulk_import_form"] = bulk_import_form
        return render(
            request,
            "indy_hub/industry/structure_registry.html",
            context,
            status=200,
        )

    summary = import_indy_structure_paste(
        bulk_import_form.cleaned_data["raw_text"],
        update_existing_manual=bool(
            bulk_import_form.cleaned_data.get("update_existing_manual")
        ),
    )
    if summary["created"] or summary["updated"]:
        messages.success(
            request,
            _(
                "Bulk import completed. Created: %(created)s, updated: %(updated)s, skipped: %(skipped)s."
            )
            % {
                "created": summary["created"],
                "updated": summary["updated"],
                "skipped": summary["skipped"],
            },
        )
    else:
        messages.warning(
            request,
            _(
                "Bulk import did not add any structure. Processed: %(processed)s, skipped: %(skipped)s."
            )
            % {
                "processed": summary["processed"],
                "skipped": summary["skipped"],
            },
        )

    warning_messages = list(summary.get("warnings") or [])
    for warning_message in warning_messages[:10]:
        messages.warning(request, warning_message)
    if len(warning_messages) > 10:
        messages.warning(
            request,
            _("Additional import warnings were omitted: %(count)s more.")
            % {"count": len(warning_messages) - 10},
        )
    return redirect("indy_hub:industry_structure_registry")


@indy_hub_access_required
@login_required
@require_http_methods(["POST"])
def industry_structure_bulk_update(request):
    emit_view_analytics_event(
        view_name="industry.structure_bulk_update",
        request=request,
    )

    bulk_tax_form = _build_bulk_tax_form(request.user, data=request.POST)
    if not bulk_tax_form.is_valid():
        context = _build_structure_registry_page_context(
            request,
            bulk_tax_form=bulk_tax_form,
        )
        return render(request, "indy_hub/industry/structure_registry.html", context)

    queryset = _get_bulk_tax_target_queryset(
        bulk_tax_form.cleaned_data,
        user=request.user,
    )

    tax_updates = bulk_tax_form.get_tax_updates()
    only_when_zero = bool(bulk_tax_form.cleaned_data.get("only_when_zero"))
    matched_count = queryset.count()
    eligible_count = _count_bulk_tax_eligible_structures(
        queryset,
        tax_updates,
        only_when_zero=only_when_zero,
    )

    if not bulk_tax_form.cleaned_data.get("confirm_apply"):
        confirmation_message = (
            _(
                "Warning: you are about to apply the configured taxes to %(count)s structure(s) that currently have zero tax for at least one selected category."
            )
            if only_when_zero
            else _(
                "Warning: you are about to apply the configured taxes to %(count)s matched structure(s)."
            )
        ) % {"count": eligible_count}
        context = _build_structure_registry_page_context(
            request,
            bulk_tax_form=bulk_tax_form,
            bulk_tax_confirmation={
                "message": confirmation_message,
                "matched_count": matched_count,
                "eligible_count": eligible_count,
                "only_when_zero": only_when_zero,
            },
        )
        return render(request, "indy_hub/industry/structure_registry.html", context)

    updated_count = 0
    for structure in queryset:
        changed_fields: list[str] = []
        for field_name, field_value in tax_updates.items():
            current_value = getattr(structure, field_name) or Decimal("0")
            if only_when_zero and current_value > 0:
                continue
            if current_value != field_value:
                setattr(structure, field_name, field_value)
                changed_fields.append(field_name)
        if changed_fields:
            structure.save(update_fields=[*changed_fields, "updated_at"])
            updated_count += 1

    messages.success(
        request,
        _(
            "Bulk tax update applied to %(updated)s structure(s) out of %(matched)s matched."
        )
        % {"updated": updated_count, "matched": matched_count},
    )
    return redirect("indy_hub:industry_structure_registry")


@indy_hub_access_required
@login_required
def industry_structure_bulk_update_preview(request):
    bulk_tax_form = _build_bulk_tax_preview_form(request.user, data=request.GET or None)
    if not bulk_tax_form.is_valid():
        return JsonResponse(
            {
                "matched_count": 0,
                "eligible_count": 0,
                "structure_names": [],
                "has_tax_updates": False,
                "message": _("Unable to preview the selected bulk tax filters."),
            },
            status=400,
        )

    return JsonResponse(
        _get_bulk_tax_preview_payload(
            bulk_tax_form.cleaned_data,
            user=request.user,
        )
    )


@indy_hub_access_required
@login_required
def industry_structure_solar_system_search(request):
    query = (request.GET.get("q") or "").strip()
    return JsonResponse(
        {
            "results": search_solar_system_options(query),
        }
    )


@indy_hub_access_required
@login_required
def industry_structure_existing_system_structures(request):
    solar_system_name = (request.GET.get("name") or "").strip()
    raw_structure_type_id = request.GET.get("structure_type_id")
    raw_exclude_structure_id = request.GET.get("exclude_structure_id")

    try:
        structure_type_id = (
            int(raw_structure_type_id) if raw_structure_type_id else None
        )
    except (TypeError, ValueError):
        structure_type_id = None

    try:
        exclude_structure_id = (
            int(raw_exclude_structure_id) if raw_exclude_structure_id else None
        )
    except (TypeError, ValueError):
        exclude_structure_id = None

    if not solar_system_name or structure_type_id is None:
        return JsonResponse(
            {
                "solar_system_name": "",
                "total_count": 0,
                "same_type_count": 0,
                "structures": [],
            }
        )

    queryset = _get_visible_public_industry_structures_queryset().filter(
        solar_system_name__iexact=solar_system_name,
        structure_type_id=structure_type_id,
    )
    if exclude_structure_id is not None:
        queryset = queryset.exclude(pk=exclude_structure_id)

    structures = list(
        queryset.order_by("name").values(
            "id",
            "name",
            "structure_type_id",
            "structure_type_name",
            "sync_source",
        )
    )
    payload_rows = [
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "structure_type_name": str(row.get("structure_type_name") or "-"),
            "is_same_type": bool(
                structure_type_id is not None
                and row.get("structure_type_id") == structure_type_id
            ),
            "is_synced": row.get("sync_source")
            == IndustryStructure.SyncSource.ESI_CORPORATION,
            "edit_url": reverse("indy_hub:industry_structure_edit", args=[row["id"]]),
            "duplicate_url": reverse(
                "indy_hub:industry_structure_duplicate", args=[row["id"]]
            ),
        }
        for row in structures
    ]
    return JsonResponse(
        {
            "solar_system_name": solar_system_name,
            "total_count": len(payload_rows),
            "same_type_count": len(payload_rows),
            "structures": payload_rows,
        }
    )


@indy_hub_access_required
@login_required
def industry_structure_solar_system_cost_indices(request):
    solar_system_name = (request.GET.get("name") or "").strip()
    solar_system_reference = resolve_solar_system_reference(
        solar_system_name=solar_system_name or None,
    )
    if solar_system_reference is None:
        return JsonResponse({"found": False, "cost_indices": []})

    solar_system_id, resolved_name, security_band = solar_system_reference
    ordered_activity_ids = [
        IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
        IndustrySystemCostIndex.ACTIVITY_TE_RESEARCH,
        IndustrySystemCostIndex.ACTIVITY_ME_RESEARCH,
        IndustrySystemCostIndex.ACTIVITY_COPYING,
        IndustrySystemCostIndex.ACTIVITY_INVENTION,
        IndustrySystemCostIndex.ACTIVITY_REACTIONS,
    ]
    rows_by_activity = {
        row.activity_id: row
        for row in IndustrySystemCostIndex.objects.filter(
            solar_system_id=solar_system_id,
        )
    }

    cost_indices = []
    for activity_id in ordered_activity_ids:
        row = rows_by_activity.get(activity_id)
        if row is None:
            continue
        cost_indices.append(
            {
                "activity_id": activity_id,
                "activity_label": row.get_activity_id_display(),
                "cost_index_percent": str(row.cost_index_percent),
            }
        )

    return JsonResponse(
        {
            "found": True,
            "solar_system_id": solar_system_id,
            "solar_system_name": resolved_name,
            "security_band": security_band,
            "cost_indices": cost_indices,
        }
    )


@indy_hub_access_required
@login_required
def industry_structure_bonus_preview(request):
    structure_type_id = request.GET.get("structure_type_id")
    solar_system_name = (request.GET.get("solar_system_name") or "").strip()
    raw_rig_type_ids = request.GET.getlist("rig_type_id")
    enabled_activity_flags = {
        "enable_manufacturing": request.GET.get("enable_manufacturing") == "1",
        "enable_manufacturing_capitals": request.GET.get(
            "enable_manufacturing_capitals"
        )
        == "1",
        "enable_manufacturing_super_capitals": request.GET.get(
            "enable_manufacturing_super_capitals"
        )
        == "1",
        "enable_research": request.GET.get("enable_research") == "1",
        "enable_invention": request.GET.get("enable_invention") == "1",
        "enable_biochemical_reactions": request.GET.get("enable_biochemical_reactions")
        == "1",
        "enable_hybrid_reactions": request.GET.get("enable_hybrid_reactions") == "1",
        "enable_composite_reactions": request.GET.get("enable_composite_reactions")
        == "1",
    }

    try:
        resolved_structure_type_id = (
            int(structure_type_id) if structure_type_id else None
        )
    except (TypeError, ValueError):
        resolved_structure_type_id = None

    rig_type_ids = []
    for raw_value in raw_rig_type_ids:
        try:
            rig_type_ids.append(int(raw_value))
        except (TypeError, ValueError):
            continue

    previews = build_structure_activity_previews(
        structure_type_id=resolved_structure_type_id,
        solar_system_name=solar_system_name,
        rig_type_ids=rig_type_ids,
        enabled_activity_flags=enabled_activity_flags,
    )

    def _metric_entries(summary):
        metrics = [
            ("ME", summary.material_efficiency_percent),
            ("TE", summary.time_efficiency_percent),
            ("Cost", summary.job_cost_percent),
        ]
        return [
            {
                "label": label,
                "value": f"{value:.3f}",
            }
            for label, value in metrics
            if value > 0
        ]

    return JsonResponse(
        {
            "rows": [
                {
                    "activity_id": preview.activity_id,
                    "activity_label": preview.activity_label,
                    "system_cost_index_percent": f"{preview.system_cost_index_percent:.2f}",
                    "structure_role_metrics": (
                        _metric_entries(preview.structure_role_bonus)
                        if preview.structure_role_bonus is not None
                        else []
                    ),
                    "supported_type_rows": [
                        {
                            "type_name": row.type_name,
                            "metrics": _metric_entries(row),
                        }
                        for row in preview.supported_type_rows
                        if _metric_entries(row)
                    ],
                    "rig_profiles": [
                        {
                            "label": profile.label,
                            "supported_types_label": profile.supported_types_label,
                            "supported_type_names": list(profile.supported_type_names),
                            "metrics": _metric_entries(profile),
                        }
                        for profile in preview.rig_profiles
                        if _metric_entries(profile)
                    ],
                }
                for preview in previews
            ]
        }
    )


@indy_hub_access_required
@login_required
def industry_structure_rig_advisor(request):
    structure_type_id = request.GET.get("structure_type_id")
    solar_system_name = (request.GET.get("solar_system_name") or "").strip()
    enabled_activity_flags = {
        "enable_manufacturing": request.GET.get("enable_manufacturing") == "1",
        "enable_manufacturing_capitals": request.GET.get(
            "enable_manufacturing_capitals"
        )
        == "1",
        "enable_manufacturing_super_capitals": request.GET.get(
            "enable_manufacturing_super_capitals"
        )
        == "1",
        "enable_research": request.GET.get("enable_research") == "1",
        "enable_invention": request.GET.get("enable_invention") == "1",
        "enable_biochemical_reactions": request.GET.get("enable_biochemical_reactions")
        == "1",
        "enable_hybrid_reactions": request.GET.get("enable_hybrid_reactions") == "1",
        "enable_composite_reactions": request.GET.get("enable_composite_reactions")
        == "1",
    }

    try:
        resolved_structure_type_id = (
            int(structure_type_id) if structure_type_id else None
        )
    except (TypeError, ValueError):
        resolved_structure_type_id = None

    rows = build_structure_rig_advisor_rows(
        structure_type_id=resolved_structure_type_id,
        solar_system_name=solar_system_name,
        enabled_activity_flags=enabled_activity_flags,
    )

    def _metric_entries(row):
        metrics = [
            ("ME", row.material_efficiency_percent),
            ("TE", row.time_efficiency_percent),
            ("Cost", row.job_cost_percent),
        ]
        return [
            {
                "label": label,
                "value": f"{value:.3f}",
            }
            for label, value in metrics
            if value > 0
        ]

    grouped: dict[int, dict[str, object]] = {}
    for row in rows:
        activity_payload = grouped.setdefault(
            row.activity_id,
            {
                "activity_id": row.activity_id,
                "activity_label": row.activity_label,
                "categories": set(),
                "rig_options": [],
            },
        )
        activity_payload["categories"].update(row.supported_type_names)
        activity_payload["rig_options"].append(
            {
                "rig_type_id": row.rig_type_id,
                "label": row.label,
                "family": row.family,
                "supported_types_label": row.supported_types_label,
                "supported_type_names": list(row.supported_type_names),
                "metrics": _metric_entries(row),
            }
        )

    activities = []
    for activity in grouped.values():
        activity["categories"] = sorted(activity["categories"])
        activity["rig_options"].sort(
            key=lambda entry: (
                entry["family"],
                entry["label"],
                entry["supported_type_names"],
            )
        )
        activities.append(activity)

    activities.sort(key=lambda entry: entry["activity_id"])
    return JsonResponse({"activities": activities})


@indy_hub_access_required
@login_required
@require_http_methods(["POST"])
def industry_structure_scan_import(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_payload"}, status=400)

    raw_text = str(payload.get("raw_text") or "")
    raw_structure_type_id = payload.get("structure_type_id")
    try:
        structure_type_id = (
            int(raw_structure_type_id)
            if raw_structure_type_id not in {None, ""}
            else None
        )
    except (TypeError, ValueError):
        structure_type_id = None

    return JsonResponse(
        resolve_structure_scan_loadout(
            raw_text,
            structure_type_id=structure_type_id,
        )
    )
