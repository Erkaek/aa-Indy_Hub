"""Industry-related views for Indy Hub."""

# Standard Library
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import ceil
from typing import Any
from urllib.parse import urlencode

# Django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

# AA Example App
from indy_hub.models import CharacterSettings, CorporationSharingSetting

from ..decorators import indy_hub_access_required, indy_hub_permission_required
from ..models import (
    Blueprint,
    BlueprintCopyChat,
    BlueprintCopyMessage,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    IndustryJob,
    ProductionConfig,
    ProductionSimulation,
)
from ..notifications import build_site_url, notify_user
from ..services.simulations import summarize_simulations
from ..tasks.industry import (
    MANUAL_REFRESH_KIND_BLUEPRINTS,
    MANUAL_REFRESH_KIND_JOBS,
    request_manual_refresh,
)
from ..utils.discord_actions import (
    _DEFAULT_TOKEN_MAX_AGE,
    BadSignature,
    SignatureExpired,
    build_action_link,
    decode_action_token,
)
from ..utils.eve import get_character_name, get_corporation_name, get_type_name
from ..utils.job_notifications import (
    build_digest_notification_body,
    build_job_notification_payload,
    serialize_job_notification_for_digest,
)
from .navigation import build_nav_context

if "eveuniverse" in getattr(settings, "INSTALLED_APPS", ()):  # pragma: no branch
    try:  # pragma: no cover - EveUniverse optional
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveType
    except ImportError:  # pragma: no cover - fallback when EveUniverse absent
        EveType = None
else:  # pragma: no cover - EveUniverse not installed
    EveType = None

logger = logging.getLogger(__name__)


@dataclass
class EligibleOwnerDetails:
    owner_ids: set[int]
    character_owner_ids: set[int]
    corporate_members_by_corp: dict[int, set[int]]
    user_to_corporation: dict[int, int]


def _eligible_owner_details_for_request(
    req: BlueprintCopyRequest,
) -> EligibleOwnerDetails:
    """Return detailed information about users who can fulfil a request."""

    matching_blueprints = Blueprint.objects.filter(
        bp_type__in=[Blueprint.BPType.ORIGINAL, Blueprint.BPType.REACTION],
        type_id=req.type_id,
        material_efficiency=req.material_efficiency,
        time_efficiency=req.time_efficiency,
    )

    character_owner_ids = set(
        matching_blueprints.filter(
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            owner_user__charactersettings__character_id=0,
            owner_user__charactersettings__allow_copy_requests=True,
        ).values_list("owner_user_id", flat=True)
    )

    corporation_ids = list(
        matching_blueprints.filter(owner_kind=Blueprint.OwnerKind.CORPORATION)
        .exclude(corporation_id__isnull=True)
        .values_list("corporation_id", flat=True)
        .distinct()
    )

    corporate_settings: list[CorporationSharingSetting] = []
    corporate_owner_ids: set[int] = set()
    corporate_members_by_corp: dict[int, set[int]] = defaultdict(set)
    user_to_corp: dict[int, int] = {}

    if corporation_ids:
        corporate_settings = list(
            CorporationSharingSetting.objects.filter(
                corporation_id__in=corporation_ids,
                allow_copy_requests=True,
                share_scope__in=[
                    CharacterSettings.SCOPE_CORPORATION,
                    CharacterSettings.SCOPE_ALLIANCE,
                    CharacterSettings.SCOPE_EVERYONE,
                ],
            )
        )
        for setting in corporate_settings:
            corp_id = setting.corporation_id
            if corp_id is None:
                continue
            corporate_members_by_corp[corp_id].add(setting.user_id)
            user_to_corp[setting.user_id] = corp_id
        corporate_owner_ids = {setting.user_id for setting in corporate_settings}

    additional_corp_manager_ids: set[int] = set()
    if corporation_ids and corporate_settings:
        settings_by_corp: dict[int, list[CorporationSharingSetting]] = defaultdict(list)
        for setting_obj in corporate_settings:
            settings_by_corp[setting_obj.corporation_id].append(setting_obj)

        corp_memberships = CharacterOwnership.objects.filter(
            character__corporation_id__in=corporation_ids
        ).values("user_id", "character__corporation_id", "character__character_id")

        corp_user_chars: dict[int, dict[int, set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )
        corp_member_user_ids: set[int] = set()
        for membership in corp_memberships:
            corp_id = membership.get("character__corporation_id")
            user_id = membership.get("user_id")
            char_id = membership.get("character__character_id")
            if corp_id and user_id:
                corp_user_chars[corp_id][user_id].add(char_id)
                corp_member_user_ids.add(user_id)

        if corp_member_user_ids:
            corp_manager_ids = set(
                User.objects.filter(id__in=corp_member_user_ids)
                .filter(
                    Q(user_permissions__codename="can_manage_corporate_assets")
                    | Q(groups__permissions__codename="can_manage_corporate_assets")
                )
                .values_list("id", flat=True)
            )

            for corp_id, users in corp_user_chars.items():
                corp_settings = settings_by_corp.get(corp_id)
                if not corp_settings:
                    continue
                for user_id, char_ids in users.items():
                    if user_id not in corp_manager_ids:
                        continue
                    if user_id in corporate_owner_ids:
                        continue
                    if user_id == req.requested_by_id:
                        continue
                    if any(
                        not setting_obj.restricts_characters
                        or any(
                            setting_obj.is_character_authorized(char_id)
                            for char_id in char_ids
                        )
                        for setting_obj in corp_settings
                    ):
                        additional_corp_manager_ids.add(user_id)
                        corporate_members_by_corp[corp_id].add(user_id)
                        user_to_corp[user_id] = corp_id

    owner_ids: set[int] = (
        set(character_owner_ids) | corporate_owner_ids | additional_corp_manager_ids
    )

    owner_ids.discard(req.requested_by_id)
    character_owner_ids.discard(req.requested_by_id)
    for members in corporate_members_by_corp.values():
        members.discard(req.requested_by_id)

    user_to_corp = {uid: cid for uid, cid in user_to_corp.items() if uid in owner_ids}
    corporate_members_by_corp = {
        corp_id: {uid for uid in members if uid in owner_ids}
        for corp_id, members in corporate_members_by_corp.items()
        if members
    }

    return EligibleOwnerDetails(
        owner_ids=owner_ids,
        character_owner_ids=set(character_owner_ids),
        corporate_members_by_corp=corporate_members_by_corp,
        user_to_corporation=user_to_corp,
    )


def _eligible_owner_ids_for_request(req: BlueprintCopyRequest) -> set[int]:
    """Return user IDs that can fulfil the request based on owned originals."""

    details = _eligible_owner_details_for_request(req)
    return set(details.owner_ids)


def _finalize_request_if_all_rejected(req: BlueprintCopyRequest) -> bool:
    """Notify requester and delete request if all eligible providers rejected."""

    details = _eligible_owner_details_for_request(req)
    eligible_owner_ids = details.owner_ids
    offers_by_owner = dict(
        req.offers.filter(owner_id__in=eligible_owner_ids).values_list(
            "owner_id", "status"
        )
    )

    if eligible_owner_ids:
        outstanding: list[int | tuple[str, int]] = []

        for owner_id in details.character_owner_ids:
            if offers_by_owner.get(owner_id) != "rejected":
                outstanding.append(owner_id)

        all_corp_member_ids: set[int] = set()
        for corp_id, members in details.corporate_members_by_corp.items():
            all_corp_member_ids.update(members)
            member_statuses = [offers_by_owner.get(member_id) for member_id in members]
            if not member_statuses:
                outstanding.append(("corporation", corp_id))
                continue

            has_active_status = any(
                status not in (None, "rejected") for status in member_statuses
            )
            has_rejection = any(status == "rejected" for status in member_statuses)
            if has_active_status:
                outstanding.append(("corporation", corp_id))
            elif not has_rejection:
                outstanding.append(("corporation", corp_id))

        remaining_owner_ids = (
            eligible_owner_ids - details.character_owner_ids - all_corp_member_ids
        )
        for owner_id in remaining_owner_ids:
            if offers_by_owner.get(owner_id) != "rejected":
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
    chat = offer.ensure_chat()
    chat.reopen()
    return chat


def _chat_has_unread(chat: BlueprintCopyChat, role: str) -> bool:
    try:
        return chat.has_unread_for(role)
    except AttributeError:
        return False


def _chat_preview_messages(chat: BlueprintCopyChat, *, limit: int = 3) -> list[dict]:
    if not chat:
        return []

    role_labels = {
        BlueprintCopyMessage.SenderRole.BUYER: _("Buyer"),
        BlueprintCopyMessage.SenderRole.SELLER: _("Builder"),
        BlueprintCopyMessage.SenderRole.SYSTEM: _("System"),
    }

    preview = []
    for message in chat.messages.order_by("-created_at", "-id")[:limit]:
        created_local = timezone.localtime(message.created_at)
        preview.append(
            {
                "role": message.sender_role,
                "role_label": role_labels.get(
                    message.sender_role, message.sender_role.title()
                ),
                "content": message.content,
                "created_display": created_local.strftime("%Y-%m-%d %H:%M"),
            }
        )

    return preview


def _close_offer_chat_if_exists(offer: BlueprintCopyOffer, reason: str) -> None:
    try:
        chat = offer.chat
    except BlueprintCopyChat.DoesNotExist:
        return
    chat.close(reason=reason)


def _close_request_chats(req: BlueprintCopyRequest, reason: str) -> None:
    chats = BlueprintCopyChat.objects.filter(request=req, is_open=True)
    for chat in chats:
        chat.close(reason=reason)


def _finalize_conditional_offer(offer: BlueprintCopyOffer) -> None:
    req = offer.request
    if offer.status == "accepted" and req.fulfilled:
        return

    offer.status = "accepted"
    offer.accepted_by_buyer = True
    offer.accepted_by_seller = True
    offer.accepted_at = timezone.now()
    offer.save(
        update_fields=[
            "status",
            "accepted_by_buyer",
            "accepted_by_seller",
            "accepted_at",
        ]
    )

    req.fulfilled = True
    req.fulfilled_at = timezone.now()
    req.save(update_fields=["fulfilled", "fulfilled_at"])

    _close_request_chats(req, BlueprintCopyChat.CloseReason.OFFER_ACCEPTED)
    BlueprintCopyOffer.objects.filter(request=req).exclude(id=offer.id).delete()

    fulfill_queue_url = build_site_url(reverse("indy_hub:bp_copy_fulfill_requests"))
    buyer_requests_url = build_site_url(reverse("indy_hub:bp_copy_my_requests"))

    notify_user(
        offer.owner,
        _("Blueprint Copy Request - Buyer Accepted"),
        _("%(buyer)s accepted your offer for %(type)s (ME%(me)s, TE%(te)s).")
        % {
            "buyer": req.requested_by.username,
            "type": get_type_name(req.type_id),
            "me": req.material_efficiency,
            "te": req.time_efficiency,
        },
        "success",
        link=fulfill_queue_url,
        link_label=_("Open fulfill queue"),
    )

    notify_user(
        req.requested_by,
        _("Conditional offer confirmed"),
        _("%(builder)s confirmed your agreement for %(type)s (ME%(me)s, TE%(te)s).")
        % {
            "builder": offer.owner.username,
            "type": get_type_name(req.type_id),
            "me": req.material_efficiency,
            "te": req.time_efficiency,
        },
        "success",
        link=buyer_requests_url,
        link_label=_("Review your requests"),
    )


def _mark_offer_buyer_accept(offer: BlueprintCopyOffer) -> bool:
    if (
        offer.status == "accepted"
        and offer.accepted_by_buyer
        and offer.accepted_by_seller
    ):
        return True

    if not offer.accepted_by_buyer:
        offer.accepted_by_buyer = True
        offer.save(update_fields=["accepted_by_buyer"])

    if offer.accepted_by_seller:
        _finalize_conditional_offer(offer)
        return True
    return False


def _mark_offer_seller_accept(offer: BlueprintCopyOffer) -> bool:
    if (
        offer.status == "accepted"
        and offer.accepted_by_buyer
        and offer.accepted_by_seller
    ):
        return True

    if not offer.accepted_by_seller:
        offer.accepted_by_seller = True
        offer.save(update_fields=["accepted_by_seller"])

    if offer.accepted_by_buyer:
        _finalize_conditional_offer(offer)
        return True
    return False


# --- Blueprint and job views ---
@indy_hub_access_required
@login_required
def personnal_bp_list(request, scope="character"):
    # Copy of the old blueprints_list code
    owner_options = []
    scope_param = request.GET.get("scope")
    scope = (scope_param or scope or "character").lower()
    if scope not in {"character", "corporation"}:
        scope = "character"

    is_corporation_scope = scope == "corporation"
    has_corporate_perm = request.user.has_perm("indy_hub.can_manage_corporate_assets")
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
                scheduled, remaining = request_manual_refresh(
                    MANUAL_REFRESH_KIND_BLUEPRINTS,
                    request.user.id,
                    priority=5,
                    scope=scope,
                )
                if scheduled:
                    messages.success(
                        request,
                        _(
                            "Blueprint refresh scheduled. Updated data will appear shortly."
                        ),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "Blueprint data was refreshed recently. Please try again in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
    except Exception as e:
        logger.error(f"Error handling blueprint refresh: {e}")
        messages.error(request, f"Error handling blueprint refresh: {e}")

    if is_corporation_scope and not has_corporate_perm:
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

    # Determine which activity IDs to include based on filter
    # Determine which activity IDs to include based on filter
    if activity_id == "1":
        filter_ids = [1]
    elif activity_id == "9,11":
        # Both IDs represent Reactions
        filter_ids = [9, 11]
    else:
        # All activities: manufacturing (1) and reactions (9,11)
        filter_ids = [1, 9, 11]
    try:
        # Fetch allowed type IDs for the selected activities
        id_list = ",".join(str(i) for i in filter_ids)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT eve_type_id
                FROM eveuniverse_eveindustryactivityproduct
                WHERE activity_id IN ({id_list})
                """
            )
            allowed_type_ids = [row[0] for row in cursor.fetchall()]
        owner_kind_filter = (
            Blueprint.OwnerKind.CORPORATION
            if is_corporation_scope
            else Blueprint.OwnerKind.CHARACTER
        )
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
        # Group identical items by type, ME, TE; compute normalized quantities & runs
        bp_items = []
        grouped = {}

        def normalized_quantity(value):
            if value in (-1, -2):
                return 1
            if value is None:
                return 0
            return max(value, 0)

        total_original_quantity = 0
        total_copy_quantity = 0
        total_quantity = 0

        for bp in blueprints_qs:
            quantity_value = normalized_quantity(bp.quantity)
            total_quantity += quantity_value

            if bp.is_copy:
                category = "copy"
                total_copy_quantity += quantity_value
            else:
                category = "reaction" if bp.is_reaction else "original"
                total_original_quantity += quantity_value

            key = (bp.type_id, bp.material_efficiency, bp.time_efficiency, category)
            if key not in grouped:
                bp.orig_quantity = 0
                bp.copy_quantity = 0
                bp.total_quantity = 0
                bp.total_runs = 0
                grouped[key] = bp
                bp_items.append(bp)

            agg = grouped[key]
            if category == "copy":
                agg.copy_quantity += quantity_value
                agg.total_runs += (bp.runs or 0) * max(quantity_value, 1)
            else:
                agg.orig_quantity += quantity_value

            agg.total_quantity = agg.orig_quantity + agg.copy_quantity
            agg.runs = agg.total_runs
        # Compute precise human-readable location path for each blueprint
        try:
            # Alliance Auth (External Libs)
            from eveuniverse.models import EveStation, EveStructure
        except (ImportError, RuntimeError, LookupError):
            EveStation = None
            EveStructure = None

        def get_location_path(location_id):
            if EveStation:
                try:
                    st = EveStation.objects.get(id=location_id)
                    sys = st.solar_system
                    cons = sys.constellation
                    reg = cons.region
                    return f"{reg.name} > {cons.name} > {sys.name} > {st.name}"
                except EveStation.DoesNotExist:
                    pass
            if EveStructure:
                try:
                    struct = EveStructure.objects.get(id=location_id)
                    sys = struct.solar_system
                    cons = sys.constellation
                    reg = cons.region
                    return f"{reg.name} > {cons.name} > {sys.name} > {struct.name}"
                except EveStructure.DoesNotExist:
                    pass
            return None

        for bp in bp_items:
            path = get_location_path(bp.location_id)
            bp.location_path = path if path else bp.location_flag

        owner_map = {owner_id: name for owner_id, name in owner_options}
        owner_field = "corporation_id" if is_corporation_scope else "character_id"
        owner_icon = (
            "fas fa-building" if is_corporation_scope else "fas fa-user-astronaut"
        )

        for bp in bp_items:
            owner_id_value = getattr(bp, owner_field)
            owner_display = owner_map.get(owner_id_value, owner_id_value)
            setattr(bp, "owner_display", owner_display)
            setattr(bp, "owner_id", owner_id_value)
            if is_corporation_scope:
                bp.character_name = owner_display

        paginator = Paginator(bp_items, per_page)
        blueprints_page = paginator.get_page(page)
        total_blueprints = total_quantity
        originals_count = total_original_quantity
        copies_count = total_copy_quantity
        # Removed update status tracking since unified settings don't track this

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
            "can_manage_corporate_assets": has_corporate_perm,
        }
        context.update(
            build_nav_context(
                request.user,
                active_tab="corporation" if is_corporation_scope else "personal",
                can_manage_corp=has_corporate_perm,
            )
        )

        return render(request, "indy_hub/Personnal_BP_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying blueprints: {e}")
        messages.error(request, f"Error displaying blueprints: {e}")
        return redirect("indy_hub:index")


@indy_hub_access_required
@login_required
def all_bp_list(request):
    search = request.GET.get("search", "").strip()
    activity_id = request.GET.get("activity_id", "")
    market_group_id = request.GET.get("market_group_id", "")

    # Base SQL
    sql = (
        "SELECT t.id, t.name "
        "FROM eveuniverse_evetype t "
        "JOIN eveuniverse_eveindustryactivityproduct a ON t.id = a.eve_type_id "
        "WHERE t.published = 1"
    )
    # Append activity filter
    if activity_id == "1":
        sql += " AND a.activity_id = 1"
    elif activity_id == "reactions":
        sql += " AND a.activity_id IN (9, 11)"
    else:
        sql += " AND a.activity_id IN (1, 9, 11)"
    # Params for search and market_group filters
    params = []
    if search:
        sql += " AND (t.name LIKE %s OR t.id LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    if market_group_id:
        sql += " AND t.eve_group_id = %s"
        params.append(market_group_id)
    sql += " ORDER BY t.name ASC"
    page = int(request.GET.get("page", 1))
    per_page = int(request.GET.get("per_page", 25))
    # Initial empty pagination before fetching data
    paginator = Paginator([], per_page)
    blueprints_page = paginator.get_page(page)
    # Fetch raw activity options for activity dropdown
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name FROM eveuniverse_eveindustryactivity WHERE id IN (1,9,11) ORDER BY id"
        )
        raw_activity_options = cursor.fetchall()
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
    market_group_options: list[tuple[int, str]] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            blueprints = [
                {
                    "type_id": row[0],
                    "type_name": row[1],
                }
                for row in cursor.fetchall()
            ]
        paginator = Paginator(blueprints, per_page)
        blueprints_page = paginator.get_page(page)

        # Fetch market group options based on all matching blueprints, not just current page
        with connection.cursor() as cursor:
            type_ids = [bp["type_id"] for bp in blueprints]
            if type_ids:
                placeholders = ",".join(["%s"] * len(type_ids))
                query = f"""
                    SELECT DISTINCT t.eve_group_id, g.name
                    FROM eveuniverse_evetype t
                    JOIN eveuniverse_evegroup g ON t.eve_group_id = g.id
                    WHERE t.eve_group_id IS NOT NULL
                        AND t.id IN ({placeholders})
                    ORDER BY g.name
                """
                cursor.execute(query, type_ids)
                market_group_options = [(row[0], row[1]) for row in cursor.fetchall()]
            else:
                market_group_options = []
    except Exception as e:
        logger.error(f"Error fetching blueprints: {e}")
        messages.error(request, f"Error fetching blueprints: {e}")
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
    }
    context.update(build_nav_context(request.user, active_tab="personal"))

    return render(request, "indy_hub/All_BP_list.html", context)


@indy_hub_access_required
@login_required
def personnal_job_list(request, scope="character"):
    owner_options: list[tuple[int, str]] = []
    scope_param = request.GET.get("scope")
    scope = (scope_param or scope or "character").lower()
    if scope not in {"character", "corporation"}:
        scope = "character"

    is_corporation_scope = scope == "corporation"
    has_corporate_perm = request.user.has_perm("indy_hub.can_manage_corporate_assets")
    try:
        force_update = request.GET.get("refresh") == "1"
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
                scheduled, remaining = request_manual_refresh(
                    MANUAL_REFRESH_KIND_JOBS,
                    request.user.id,
                    priority=5,
                    scope=scope,
                )
                if scheduled:
                    messages.success(
                        request,
                        _(
                            "Industry jobs refresh scheduled. Updated data will appear shortly."
                        ),
                    )
                else:
                    wait_minutes = max(1, ceil(remaining.total_seconds() / 60))
                    messages.warning(
                        request,
                        _(
                            "Industry data was refreshed recently. Please try again in %(minutes)s minute(s)."
                        )
                        % {"minutes": wait_minutes},
                    )
    except Exception as e:
        logger.error(f"Error handling jobs refresh: {e}")
        messages.error(request, f"Error handling jobs refresh: {e}")

    if is_corporation_scope and not has_corporate_perm:
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
    page = int(request.GET.get("page", 1))
    per_page = request.GET.get("per_page")
    if per_page:
        per_page = int(per_page)
        if per_page < 1:
            per_page = 1
    else:
        owner_kind_filter = (
            Blueprint.OwnerKind.CORPORATION
            if is_corporation_scope
            else Blueprint.OwnerKind.CHARACTER
        )
        per_page = IndustryJob.objects.filter(
            owner_user=request.user,
            owner_kind=owner_kind_filter,
        ).count()
        if per_page < 1:
            per_page = 1
    owner_kind_filter = (
        Blueprint.OwnerKind.CORPORATION
        if is_corporation_scope
        else Blueprint.OwnerKind.CHARACTER
    )
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
        total_jobs = jobs_qs.count()
        active_jobs = jobs_qs.filter(status="active", end_date__gt=now).count()
        completed_jobs = jobs_qs.filter(end_date__lte=now).count()
        statistics = {
            "total": total_jobs,
            "active": active_jobs,
            "completed": completed_jobs,
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
            "per_page_options": [10, 25, 50, 100, 200],
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
            "can_manage_corporate_assets": has_corporate_perm,
        }
        personal_nav_url = reverse("indy_hub:index")
        corporation_nav_url = (
            reverse("indy_hub:corporation_dashboard") if has_corporate_perm else None
        )
        context.update(
            {
                "personal_nav_url": personal_nav_url,
                "personal_nav_class": (
                    "" if is_corporation_scope else "active fw-semibold"
                ),
                "corporation_nav_url": corporation_nav_url,
                "corporation_nav_class": (
                    "active fw-semibold"
                    if is_corporation_scope and corporation_nav_url
                    else ""
                ),
                "current_dashboard": (
                    "corporation" if is_corporation_scope else "personal"
                ),
                "back_to_dashboard_url": (
                    corporation_nav_url
                    if is_corporation_scope and corporation_nav_url
                    else personal_nav_url
                ),
            }
        )
        # progress_percent and display_eta now available via model properties in template
        return render(request, "indy_hub/Personnal_Job_list.html", context)
    except Exception as e:
        logger.error(f"Error displaying industry jobs: {e}")
        messages.error(request, f"Error displaying industry jobs: {e}")
        return redirect("indy_hub:index")


def _resolve_preview_character(request):
    ownership = (
        CharacterOwnership.objects.filter(user=request.user)
        .select_related("character")
        .first()
    )
    character_id = None
    character_name = request.user.username
    if ownership and getattr(ownership, "character", None):
        character_id = getattr(ownership.character, "character_id", None)
        stored_name = getattr(ownership.character, "character_name", None)
        character_name = (
            get_character_name(character_id) or stored_name or request.user.username
        )
    return character_id, character_name


def _build_preview_job(
    request,
    *,
    job_overrides: dict | None = None,
    blueprint_overrides: dict | None = None,
):
    character_id, character_name = _resolve_preview_character(request)

    base_job_attrs = {
        "owner_user": request.user,
        "owner_kind": Blueprint.OwnerKind.CHARACTER,
        "character_id": character_id,
        "installer_id": character_id or request.user.id,
        "job_id": 999_999,
        "blueprint_id": 123_456_789,
        "blueprint_type_id": 23_528,
        "blueprint_type_name": "Drone Link Augmentor I Blueprint",
        "activity_id": 3,
        "activity_name": _("Time Efficiency Research"),
        "runs": 4,
        "successful_runs": 4,
        "location_name": _("Test Research Lab"),
        "product_type_name": _("Blueprint copy"),
        "status": "delivered",
        "duration": 3600,
        "start_date": timezone.now() - timedelta(hours=2),
        "end_date": timezone.now() - timedelta(minutes=5),
        "character_name": character_name,
    }

    if job_overrides:
        base_job_attrs.update(job_overrides)

    job = IndustryJob(**base_job_attrs)

    base_blueprint_attrs = {
        "owner_user": request.user,
        "item_id": 0,
        "type_id": base_job_attrs.get("blueprint_type_id", 23_528),
        "type_name": base_job_attrs.get(
            "blueprint_type_name", "Drone Link Augmentor I Blueprint"
        ),
        "time_efficiency": 20,
        "material_efficiency": 10,
    }

    if blueprint_overrides:
        base_blueprint_attrs.update(blueprint_overrides)

    blueprint = Blueprint(**base_blueprint_attrs)
    return job, blueprint


@indy_hub_access_required
@login_required
def personnal_job_notification_test(request):
    job, blueprint = _build_preview_job(request)
    payload = build_job_notification_payload(job, blueprint=blueprint)
    jobs_url = build_site_url(reverse("indy_hub:personnal_job_list"))

    notify_user(
        request.user,
        payload.title,
        payload.message,
        level="success",
        link=jobs_url,
        link_label=_("View job dashboard"),
        thumbnail_url=payload.thumbnail_url,
    )

    messages.success(
        request,
        _("Test industry job notification sent. Check your notifications panel."),
    )
    return redirect("indy_hub:personnal_job_list")


def _redirect_preview_back(request, *, default_route: str = "indy_hub:index"):
    """Redirect previews back to the originating page when safe."""

    allowed_hosts = {request.get_host()}
    next_url = request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    referer = request.headers.get("referer")
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure(),
    ):
        return redirect(referer)

    return redirect(default_route)


@indy_hub_access_required
@login_required
def preview_job_notification_live(request):
    job, blueprint = _build_preview_job(request)
    payload = build_job_notification_payload(job, blueprint=blueprint)
    jobs_url = build_site_url(reverse("indy_hub:personnal_job_list"))

    notify_user(
        request.user,
        payload.title,
        payload.message,
        level="success",
        link=jobs_url,
        link_label=_("View job dashboard"),
        thumbnail_url=payload.thumbnail_url,
    )

    messages.success(
        request,
        _("Live industry job notification preview sent. Check your notifications."),
    )
    return _redirect_preview_back(request)


@indy_hub_access_required
@login_required
def preview_job_notification_digest(request):
    scenarios = [
        {
            "job_overrides": {
                "job_id": 1_000_001,
                "activity_id": 1,
                "activity_name": _("Manufacturing"),
                "runs": 6,
                "product_type_name": "Heavy Missile",
                "product_type_id": 20308,
            },
            "blueprint_overrides": {
                "type_id": 51_223,
                "type_name": "Heavy Missile Blueprint",
            },
        },
        {
            "job_overrides": {
                "job_id": 1_000_002,
                "activity_id": 5,
                "activity_name": _("Copying"),
                "runs": 3,
                "licensed_runs": 10,
                "blueprint_type_name": "Capital Armor Plate Blueprint",
                "blueprint_type_id": 44_401,
                "product_type_name": _("Blueprint copy"),
            },
            "blueprint_overrides": {
                "type_id": 44_401,
                "type_name": "Capital Armor Plate Blueprint",
                "time_efficiency": 12,
                "material_efficiency": 8,
            },
        },
        {
            "job_overrides": {
                "job_id": 1_000_003,
                "activity_id": 3,
                "activity_name": _("Time Efficiency Research"),
                "runs": 1,
                "successful_runs": 1,
                "blueprint_type_name": "Warp Core Stabilizer Blueprint",
                "blueprint_type_id": 43_486,
            },
            "blueprint_overrides": {
                "type_id": 43_486,
                "type_name": "Warp Core Stabilizer Blueprint",
                "time_efficiency": 20,
                "material_efficiency": 12,
            },
        },
    ]

    digest_rows: list[dict] = []
    for scenario in scenarios:
        job, blueprint = _build_preview_job(
            request,
            job_overrides=scenario.get("job_overrides"),
            blueprint_overrides=scenario.get("blueprint_overrides"),
        )
        payload = build_job_notification_payload(job, blueprint=blueprint)
        digest_rows.append(serialize_job_notification_for_digest(job, payload))

    title, body, thumbnail_url = build_digest_notification_body(digest_rows)
    jobs_url = build_site_url(reverse("indy_hub:personnal_job_list"))

    notify_user(
        request.user,
        title,
        body,
        level="info",
        link=jobs_url,
        link_label=_("Open industry jobs"),
        thumbnail_url=thumbnail_url,
    )

    messages.success(
        request,
        _("Digest preview sent. Review the grouped message in your notifications."),
    )
    return _redirect_preview_back(request)


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

    active_tab = request.GET.get(
        "active_tab", "materials"
    )  # Active tab from query parameters, defaults to Materials

    buy_decisions = set()
    buy_list = request.GET.get("buy", "")
    if buy_list:
        try:
            # Parse comma-separated list of type_ids to buy instead of craft
            buy_decisions = {
                int(tid.strip()) for tid in buy_list.split(",") if tid.strip().isdigit()
            }
            logger.info(f"Buy decisions parsed: {buy_decisions}")  # Debug log
        except ValueError:
            buy_decisions = set()
    else:
        logger.info("No buy decisions found in URL parameters")  # Debug log

    try:
        # --- Récupération du nom du blueprint ---
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM eveuniverse_evetype WHERE id=%s", [type_id]
            )
            row = cursor.fetchone()
            bp_name = row[0] if row else str(type_id)

        # --- Récupération du produit final et quantité ---
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT product_eve_type_id, quantity
                FROM eveuniverse_eveindustryactivityproduct
                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                LIMIT 1
                """,
                [type_id],
            )
            product_row = cursor.fetchone()
            product_type_id = product_row[0] if product_row else None
            output_qty_per_run = (
                product_row[1] if product_row and len(product_row) > 1 else 1
            )
            final_product_qty = output_qty_per_run * num_runs

        # --- Construction de l'arbre des matériaux ---
        def get_materials_tree(
            bp_id, runs, blueprint_me=0, depth=0, max_depth=10, seen=None
        ):
            """Recursively build the material tree for a given blueprint."""
            if seen is None:
                seen = set()
            if depth > max_depth or bp_id in seen:
                return []
            seen.add(bp_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT m.material_eve_type_id, t.name, m.quantity
                    FROM eveuniverse_eveindustryactivitymaterial m
                    JOIN eveuniverse_evetype t ON m.material_eve_type_id = t.id
                    WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                    """,
                    [bp_id],
                )
                mats = []
                for row in cursor.fetchall():
                    base_qty = row[2] * runs
                    # Apply blueprint-specific ME and round up to the next whole number
                    # Standard Library

                    qty = ceil(base_qty * (100 - blueprint_me) / 100)
                    mat = {
                        "type_id": row[0],
                        "type_name": row[1],
                        "quantity": qty,
                        # Default values, will be overwritten if blueprint exists
                        "cycles": None,
                        "produced_per_cycle": None,
                        "total_produced": None,
                        "surplus": None,
                    }
                    # Check if this material can be produced by a blueprint (i.e. is a sub-product)
                    with connection.cursor() as sub_cursor:
                        sub_cursor.execute(
                            """
                            SELECT eve_type_id
                            FROM eveuniverse_eveindustryactivityproduct
                            WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [mat["type_id"]],
                        )
                        sub_bp_row = sub_cursor.fetchone()
                        if sub_bp_row:
                            sub_bp_id = sub_bp_row[0]
                            sub_cursor.execute(
                                """
                                SELECT quantity
                                FROM eveuniverse_eveindustryactivityproduct
                                WHERE eve_type_id = %s AND activity_id IN (1, 11)
                                LIMIT 1
                                """,
                                [sub_bp_id],
                            )
                            prod_qty_row = sub_cursor.fetchone()
                            output_qty = prod_qty_row[0] if prod_qty_row else 1
                            # Standard Library

                            cycles = ceil(mat["quantity"] / output_qty)
                            total_produced = cycles * output_qty
                            surplus = total_produced - mat["quantity"]
                            mat["cycles"] = cycles
                            mat["produced_per_cycle"] = output_qty
                            mat["total_produced"] = total_produced
                            mat["surplus"] = surplus
                            mat["sub_materials"] = get_materials_tree(
                                sub_bp_id, cycles, 0, depth + 1, max_depth, seen.copy()
                            )
                        else:
                            mat["sub_materials"] = []
                    mats.append(mat)
            return mats

        materials_tree = get_materials_tree(type_id, num_runs, me)

        # --- Function to collect all blueprints that should be excluded from configs ---
        def collect_buy_exclusions(tree, buy_set, excluded=None):
            """
            Collect all blueprint type_ids that need to be excluded from the blueprint configs.
            If an item is marked for purchase, the blueprint that produces it and all descendants are excluded.
            """
            if excluded is None:
                excluded = set()

            for mat in tree:
                # If this material is marked for purchase instead of production
                if mat["type_id"] in buy_set:
                    # Find the blueprint that produces this material and exclude it
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT eve_type_id
                            FROM eveuniverse_eveindustryactivityproduct
                            WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [mat["type_id"]],
                        )
                        bp_row = cursor.fetchone()
                        if bp_row:
                            excluded.add(
                                bp_row[0]
                            )  # Exclude the blueprint that produces this item

                    # Recursively exclude every downstream blueprint
                    if mat.get("sub_materials"):
                        collect_all_descendant_blueprints(
                            mat["sub_materials"], excluded
                        )
                elif mat.get("sub_materials"):
                    # Continue scanning child materials
                    collect_buy_exclusions(mat["sub_materials"], buy_set, excluded)

            return excluded

        def collect_all_descendant_blueprints(tree, excluded):
            """Recursively collect every descendant blueprint in a tree."""
            for mat in tree:
                # Find the blueprint that produces this material
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM eveuniverse_eveindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat["type_id"]],
                    )
                    bp_row = cursor.fetchone()
                    if bp_row:
                        excluded.add(bp_row[0])

                if mat.get("sub_materials"):
                    collect_all_descendant_blueprints(mat["sub_materials"], excluded)

        # Gather exclusions based on buy/craft decisions
        blueprint_exclusions = collect_buy_exclusions(materials_tree, buy_decisions)
        logger.info(f"Blueprint exclusions: {blueprint_exclusions}")  # Debug log

        def flatten_materials(materials, buy_as_final=None):
            """Recursively flatten the materials tree into a flat list of terminal inputs.

            Only leaf materials (or those explicitly marked for purchase) are retained so that
            the resulting list reflects the final resources required to complete the build.
            """
            # Standard Library
            from collections import defaultdict

            if buy_as_final is None:
                buy_as_final = set()

            def _flatten(mats, accumulator):
                for m in mats:
                    sub_items = m.get("sub_materials") or []
                    should_expand = bool(sub_items) and m["type_id"] not in buy_as_final

                    if not should_expand:
                        accumulator[m["type_id"]]["type_name"] = m["type_name"]
                        accumulator[m["type_id"]]["quantity"] += m["quantity"]

                    if should_expand:
                        _flatten(sub_items, accumulator)

            material_accumulator = defaultdict(lambda: {"type_name": "", "quantity": 0})
            _flatten(materials, material_accumulator)

            return [
                {
                    "type_id": type_id,
                    "type_name": data["type_name"],
                    "quantity": ceil(data["quantity"]),
                }
                for type_id, data in material_accumulator.items()
            ]

        # --- Extract every blueprint involved (root + children) ---
        def extract_all_blueprint_type_ids(bp_id, acc=None, depth=0, max_depth=10):
            """Recursively retrieve every blueprint type_id (root and descendants)."""
            if acc is None:
                acc = set()
            if depth > max_depth or bp_id in acc:
                return acc
            acc.add(bp_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT m.material_eve_type_id
                    FROM eveuniverse_eveindustryactivitymaterial m
                    WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)
                    """,
                    [bp_id],
                )
                material_type_ids = [row[0] for row in cursor.fetchall()]
            for mat_type_id in material_type_ids:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM eveuniverse_eveindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat_type_id],
                    )
                    sub_bp_row = cursor.fetchone()
                    if sub_bp_row:
                        sub_bp_id = sub_bp_row[0]
                        extract_all_blueprint_type_ids(
                            sub_bp_id, acc, depth + 1, max_depth
                        )
            return acc

        all_bp_ids = extract_all_blueprint_type_ids(type_id)

        # --- Retrieve configurations for every collected blueprint ---
        if all_bp_ids:
            placeholders = ",".join(["%s"] * len(all_bp_ids))
            params = list(all_bp_ids)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        t.id AS type_id,
                        t.name AS type_name,
                        t.eve_group_id AS group_id,
                        g.name AS group_name,
                        a.activity_id,
                        COALESCE(NULLIF(a.product_eve_type_id, 0), NULLIF(a.eve_type_id, 0)) AS product_type_id,
                        a.quantity,
                        0 AS material_efficiency,
                        0 AS time_efficiency
                    FROM eveuniverse_evetype t
                    JOIN eveuniverse_eveindustryactivityproduct a ON t.id = a.eve_type_id
                    LEFT JOIN eveuniverse_evegroup g ON t.eve_group_id = g.id
                    WHERE t.published = 1
                        AND a.activity_id IN (1, 11)
                        AND t.id IN ({placeholders})
                    ORDER BY g.name, a.quantity DESC
                """,
                    params,
                )
                blueprint_configs = [
                    {
                        "type_id": row[0],
                        "type_name": row[1],
                        "group_id": row[2],
                        "group_name": row[3],
                        "activity_id": row[4],
                        "product_type_id": row[5],
                        "quantity": row[6],
                        "material_efficiency": row[7],
                        "time_efficiency": row[8],
                    }
                    for row in cursor.fetchall()
                ]

                # --- Apply ME/TE values for the primary blueprint ---
                for bc in blueprint_configs:
                    if bc["type_id"] == type_id:  # Main blueprint
                        bc["material_efficiency"] = me
                        bc["time_efficiency"] = te
                        break
        else:
            blueprint_configs = []

        # --- Inject the material structure into each blueprint_config ---
        config_map = {bc["type_id"]: bc for bc in blueprint_configs}

        def inject_materials(tree):
            """Recursively inject child materials into each blueprint_config."""
            for node in tree:
                bc = config_map.get(node["type_id"])
                if bc is not None:
                    if "materials" not in bc:
                        bc["materials"] = []
                    existing = {m["type_id"] for m in bc["materials"]}
                    for sub in node.get("sub_materials", []):
                        if sub["type_id"] not in existing:
                            bc["materials"].append({"type_id": sub["type_id"]})
                            existing.add(sub["type_id"])
                        inject_materials([sub])

        inject_materials([{"type_id": type_id, "sub_materials": materials_tree}])

        # --- Compute the depth level for each blueprint ---
        blueprint_configs = collect_blueprints_with_level(blueprint_configs)

        # --- Group by EVE group and then by level ---
        grouping = {}
        for bc in blueprint_configs:
            # Keep only blueprints with useful data (materials present or quantity > 0)
            # Exclude reaction blueprints (activity_id 9, 11) since their ME/TE cannot be adjusted
            # Exclude blueprints whose items are marked for purchase
            if (
                bc["type_id"] is not None
                and (
                    (bc.get("materials") and len(bc["materials"]) > 0)
                    or bc.get("quantity", 0) > 0
                )
                and bc.get("activity_id")
                not in [9, 11]  # Exclude Composite Reaction Formulas
                and bc["type_id"] not in blueprint_exclusions
            ):  # Exclude blueprints flagged for purchase
                grouping.setdefault(
                    bc["group_id"], {"group_name": bc["group_name"], "levels": {}}
                )
                lvl = bc["level"]
                grouping[bc["group_id"]]["levels"].setdefault(lvl, []).append(bc)

        # --- Build the final structure for the template ---
        blueprint_configs_grouped = []
        for group_id, info in grouping.items():
            levels = []
            for lvl in sorted(info["levels"].keys()):
                # Filter to blueprints that remain useful (materials present or quantity > 0)
                # Exclude reaction blueprints (activity_id 9, 11) since their ME/TE cannot be adjusted
                # Exclude blueprints whose items are marked for purchase
                blueprints_utiles = [
                    bc
                    for bc in info["levels"][lvl]
                    if (
                        (bc.get("materials") and len(bc["materials"]) > 0)
                        or bc.get("quantity", 0) > 0
                    )
                    and bc.get("activity_id")
                    not in [9, 11]  # Exclude Composite Reaction Formulas
                    and bc["type_id"]
                    not in blueprint_exclusions  # Exclude blueprints flagged for purchase
                ]
                if blueprints_utiles:
                    levels.append({"level": lvl, "blueprints": blueprints_utiles})
            # Keep only groups that have at least one useful blueprint within a level
            if levels:
                blueprint_configs_grouped.append(
                    {
                        "group_id": group_id,
                        "group_name": info["group_name"],
                        "levels": levels,
                    }
                )
        if not blueprint_configs_grouped:
            blueprint_configs_grouped = None

        # --- Accumulate cycles/production/surplus for every craftable item ---
        # Standard Library
        from collections import defaultdict

        def collect_craftables(materials, craftables):
            for mat in materials:
                # Only accumulate values when the item is craftable (produced by a blueprint)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT eve_type_id
                        FROM eveuniverse_eveindustryactivityproduct
                        WHERE product_eve_type_id = %s AND activity_id IN (1, 11)
                        LIMIT 1
                        """,
                        [mat["type_id"]],
                    )
                    sub_bp_row = cursor.fetchone()
                    if sub_bp_row:
                        # Accumulate the requested quantity
                        craftables[mat["type_id"]]["type_name"] = mat["type_name"]
                        craftables[mat["type_id"]]["total_needed"] += ceil(
                            mat["quantity"]
                        )
                        # Retrieve the quantity produced per cycle
                        cursor.execute(
                            """
                            SELECT quantity
                            FROM eveuniverse_eveindustryactivityproduct
                            WHERE eve_type_id = %s AND activity_id IN (1, 11)
                            LIMIT 1
                            """,
                            [sub_bp_row[0]],
                        )
                        prod_qty_row = cursor.fetchone()
                        output_qty = prod_qty_row[0] if prod_qty_row else 1
                        craftables[mat["type_id"]]["produced_per_cycle"] = output_qty
                        # Continue descending into sub-materials
                        if "sub_materials" in mat:
                            collect_craftables(mat["sub_materials"], craftables)

        craftables = defaultdict(
            lambda: {"type_name": "", "total_needed": 0, "produced_per_cycle": 1}
        )
        collect_craftables(materials_tree, craftables)
        # Calcul cycles, total_produced, surplus
        for v in craftables.values():
            # Standard Library

            v["cycles"] = ceil(v["total_needed"] / v["produced_per_cycle"])
            v["total_produced"] = v["cycles"] * v["produced_per_cycle"]
            v["surplus"] = v["total_produced"] - v["total_needed"]

        # --- Prepare direct materials list (only direct children of the main blueprint) ---
        direct_materials_list = []
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT m.material_eve_type_id, t.name, m.quantity "
                "FROM eveuniverse_eveindustryactivitymaterial m "
                "JOIN eveuniverse_evetype t ON m.material_eve_type_id = t.id "
                "WHERE m.eve_type_id = %s AND m.activity_id IN (1, 11)",
                [type_id],
            )
            for row in cursor.fetchall():
                base_qty = row[2] * num_runs
                # Apply ME bonus if applicable and round up to integer
                # Standard Library

                qty = ceil(base_qty * (100 - me) / 100)
                direct_materials_list.append(
                    {
                        "type_id": row[0],
                        "type_name": row[1],
                        "quantity": qty,
                    }
                )

        # --- Prepare materials list (flattened), fallback to direct fetch if empty ---
        materials_list = flatten_materials(materials_tree, buy_decisions)
        if not materials_list:
            # Use direct materials as fallback
            materials_list = direct_materials_list
        # --- Add a type_id -> EVE group mapping for the Financial tab ---
        # Collect every type_id used in materials that will be purchased
        all_type_ids = {mat["type_id"] for mat in materials_list}
        eve_types_query = []
        if EveType is not None:
            eve_types_query = list(
                EveType.objects.filter(id__in=all_type_ids).select_related("eve_group")
            )
        eve_types = eve_types_query
        # On ne garde que les groupes ayant au moins un item dans materials_list
        group_ids_used = set()
        for mat in materials_list:
            eve_type = next((et for et in eve_types if et.id == mat["type_id"]), None)
            if eve_type and eve_type.eve_group:
                group_ids_used.add(eve_type.eve_group.id)
            elif eve_type:
                group_ids_used.add(None)
        market_group_map = {}
        if EveType is not None:
            for eve_type in eve_types:
                group_id = eve_type.eve_group.id if eve_type.eve_group else None
                group_name = eve_type.eve_group.name if eve_type.eve_group else "Other"
                if group_id in group_ids_used:
                    market_group_map[eve_type.id] = {
                        "group_id": group_id,
                        "group_name": group_name,
                    }

        # Nouveau: mapping group_id -> dict avec group_name et liste des items
        materials_by_group = {}
        for mat in materials_list:
            eve_type = next((et for et in eve_types if et.id == mat["type_id"]), None)
            group_id = (
                eve_type.eve_group.id if eve_type and eve_type.eve_group else None
            )
            group_name = (
                eve_type.eve_group.name if eve_type and eve_type.eve_group else "Other"
            )
            if group_id not in materials_by_group:
                materials_by_group[group_id] = {"group_name": group_name, "items": []}
            materials_by_group[group_id]["items"].append(mat)

        def _to_serializable(value):
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, dict):
                return {k: _to_serializable(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_to_serializable(item) for item in value]
            return value

        blueprint_payload = {
            "type_id": type_id,
            "bp_type_id": type_id,
            "name": bp_name,
            "num_runs": num_runs,
            "final_product_qty": final_product_qty,
            "product_type_id": product_type_id,
            "me": me,
            "te": te,
            "active_tab": active_tab,
            "materials": _to_serializable(materials_list),
            "direct_materials": _to_serializable(direct_materials_list),
            "materials_tree": _to_serializable(materials_tree),
            "craft_cycles_summary": _to_serializable(dict(craftables)),
            "blueprint_configs_grouped": (
                _to_serializable(blueprint_configs_grouped)
                if blueprint_configs_grouped
                else []
            ),
            "market_group_map": _to_serializable(market_group_map),
            "materials_by_group": _to_serializable(materials_by_group),
            "urls": {
                "save": reverse("indy_hub:save_production_config"),
                "load_list": reverse("indy_hub:production_simulations_list"),
                "load_config": reverse("indy_hub:load_production_config"),
                "fuzzwork_price": reverse("indy_hub:fuzzwork_price"),
            },
        }

        context = {
            "bp_type_id": type_id,
            "bp_name": bp_name,
            "materials": materials_list,
            "direct_materials": direct_materials_list,
            "materials_tree": materials_tree,
            "num_runs": num_runs,
            "product_type_id": product_type_id,
            "final_product_qty": final_product_qty,
            "me": me,
            "te": te,
            "active_tab": active_tab,
            "blueprint_configs_grouped": blueprint_configs_grouped,
            "craft_cycles_summary": dict(craftables),
            "market_group_map": market_group_map,
            "materials_by_group": materials_by_group,
            "blueprint_payload": blueprint_payload,
        }
        return render(request, "indy_hub/Craft_BP.html", context)

    except Exception as e:
        # Error handling: render the page with a message and default values
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM eveuniverse_evetype WHERE id=%s", [type_id]
            )
            row = cursor.fetchone()
            bp_name = row[0] if row else str(type_id)
        messages.error(request, f"Error crafting blueprint: {e}")
        return render(
            request,
            "indy_hub/Craft_BP.html",
            {
                "bp_type_id": type_id,
                "bp_name": bp_name,
                "materials": [],
                "direct_materials": [],
                "materials_tree": [],
                "num_runs": 1,
                "product_type_id": None,
                "me": 0,
                "te": 0,
            },
        )


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_copy_request_page(request):
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter

    search = request.GET.get("search", "").strip()
    min_me = request.GET.get("min_me", "")
    min_te = request.GET.get("min_te", "")
    page = request.GET.get("page", 1)
    per_page = int(request.GET.get("per_page", 24))
    # Determine viewer affiliations (corporation / alliance)
    viewer_corp_ids: set[int] = set()
    viewer_alliance_ids: set[int] = set()
    viewer_characters = EveCharacter.objects.filter(
        character_ownership__user=request.user
    ).values("corporation_id", "alliance_id")
    for char in viewer_characters:
        corp_id = char.get("corporation_id")
        if corp_id is not None:
            viewer_corp_ids.add(corp_id)
        alliance_id = char.get("alliance_id")
        if alliance_id is not None:
            viewer_alliance_ids.add(alliance_id)

    viewer_can_request_copies = request.user.has_perm(
        "indy_hub.can_manage_copy_requests"
    )

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

    if combined_blueprint_filter is None:
        qs = Blueprint.objects.none()
    else:
        qs = (
            Blueprint.objects.filter(combined_blueprint_filter)
            .filter(bp_type=Blueprint.BPType.ORIGINAL)
            .order_by("type_name", "material_efficiency", "time_efficiency")
        )
    seen = set()
    bp_list = []
    for bp in qs:
        key = (bp.type_id, bp.material_efficiency, bp.time_efficiency)
        if key in seen:
            continue
        seen.add(key)
        bp_list.append(
            {
                "type_id": bp.type_id,
                "type_name": bp.type_name or str(bp.type_id),
                "icon_url": f"https://images.evetech.net/types/{bp.type_id}/bp?size=32",
                "material_efficiency": bp.material_efficiency,
                "time_efficiency": bp.time_efficiency,
            }
        )
    if search:
        bp_list = [bp for bp in bp_list if search.lower() in bp["type_name"].lower()]
    if min_me.isdigit():
        min_me_val = int(min_me)
        bp_list = [bp for bp in bp_list if bp["material_efficiency"] >= min_me_val]
    if min_te.isdigit():
        min_te_val = int(min_te)
        bp_list = [bp for bp in bp_list if bp["time_efficiency"] >= min_te_val]
    per_page_options = [12, 24, 48, 96]
    me_options = list(range(0, 11))
    te_options = list(range(0, 21))
    paginator = Paginator(bp_list, per_page)
    page_obj = paginator.get_page(page)
    page_range = paginator.get_elided_page_range(
        number=page_obj.number, on_each_side=5, on_ends=1
    )
    if request.method == "POST":
        type_id = int(request.POST.get("type_id", 0))
        me = int(request.POST.get("material_efficiency", 0))
        te = int(request.POST.get("time_efficiency", 0))
        runs = max(1, int(request.POST.get("runs_requested", 1)))
        copies = max(1, int(request.POST.get("copies_requested", 1)))

        new_request = BlueprintCopyRequest.objects.create(
            type_id=type_id,
            material_efficiency=me,
            time_efficiency=te,
            requested_by=request.user,
            runs_requested=runs,
            copies_requested=copies,
        )

        flash_message = _("Copy request sent.")
        flash_level = messages.success
        # Django
        from django.contrib.auth.models import User

        eligible_owner_ids = _eligible_owner_ids_for_request(new_request)
        notification_context = {
            "username": request.user.username,
            "type_name": get_type_name(type_id),
            "me": me,
            "te": te,
            "runs": runs,
            "copies": copies,
        }

        corporate_source_line = ""
        corporate_blueprint_qs = Blueprint.objects.filter(
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            type_id=type_id,
            material_efficiency=me,
            time_efficiency=te,
        ).values("corporation_id", "corporation_name")

        corp_labels: set[str] = set()
        for corp_entry in corporate_blueprint_qs:
            corp_name = corp_entry.get("corporation_name")
            corp_id = corp_entry.get("corporation_id")
            label = corp_name.strip() if isinstance(corp_name, str) else ""
            if not label and corp_id:
                label = str(corp_id)
            if label:
                corp_labels.add(label)

        if corp_labels:
            formatted_corps = ", ".join(sorted(corp_labels, key=str.lower))
            corporate_source_line = (
                _(  # Inform providers when the blueprint comes from a corporation
                    "Corporate source: %(corporations)s"
                )
                % {"corporations": formatted_corps}
            )

        if eligible_owner_ids:
            notification_title = _("New blueprint copy request")
            notification_body = (
                _(
                    "%(username)s requested a copy of %(type_name)s (ME%(me)s, TE%(te)s) — %(runs)s runs, %(copies)s copies requested."
                )
                % notification_context
            )

            fulfill_queue_url = request.build_absolute_uri(
                reverse("indy_hub:bp_copy_fulfill_requests")
            )
            fulfill_label = _("Review copy requests")

            provider_users = User.objects.filter(id__in=eligible_owner_ids)
            for owner in provider_users:
                provider_body = notification_body
                if corporate_source_line:
                    provider_body = f"{provider_body}\n\n{corporate_source_line}"
                quick_actions = []
                link_cta = _("Click here")

                accept_link = build_action_link(
                    action="accept",
                    request_id=new_request.id,
                    user_id=owner.id,
                )
                if accept_link:
                    quick_actions.append(
                        _("Accept: %(link)s") % {"link": f"[{link_cta}]({accept_link})"}
                    )

                conditional_link = build_action_link(
                    action="conditional",
                    request_id=new_request.id,
                    user_id=owner.id,
                )
                if conditional_link:
                    quick_actions.append(
                        _("Send conditions: %(link)s")
                        % {"link": f"[{link_cta}]({conditional_link})"}
                    )

                reject_link = build_action_link(
                    action="reject",
                    request_id=new_request.id,
                    user_id=owner.id,
                )
                if reject_link:
                    quick_actions.append(
                        _("Decline: %(link)s")
                        % {"link": f"[{link_cta}]({reject_link})"}
                    )

                if quick_actions:
                    provider_body = (
                        f"{provider_body}\n\n"
                        f"{_('Quick actions:')}\n" + "\n".join(quick_actions)
                    )

                notify_user(
                    owner,
                    notification_title,
                    provider_body,
                    "info",
                    link=fulfill_queue_url,
                    link_label=fulfill_label,
                )

        flash_level(request, flash_message)
        return redirect("indy_hub:bp_copy_request_page")
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
    context.update(build_nav_context(request.user, active_tab="personal"))

    return render(request, "indy_hub/bp_copy_request_page.html", context)


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_copy_fulfill_requests(request):
    """List requests for blueprints the user owns and allows copy requests for."""
    from ..models import CharacterSettings

    setting = CharacterSettings.objects.filter(
        user=request.user,
        character_id=0,  # Global settings only
        allow_copy_requests=True,
    ).first()
    can_manage_corporate = request.user.has_perm("indy_hub.can_manage_corporate_assets")

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
    nav_context = build_nav_context(request.user, active_tab="personal")
    if not setting and not accessible_corporation_ids:
        context = {"requests": []}
        context.update(nav_context)
        if auto_open_chat_id:
            context["auto_open_chat_id"] = auto_open_chat_id
        return render(request, "indy_hub/bp_copy_fulfill_requests.html", context)

    my_bps_qs = Blueprint.objects.filter(
        owner_user=request.user,
        owner_kind=Blueprint.OwnerKind.CHARACTER,
        bp_type=Blueprint.BPType.ORIGINAL,
    )
    accessible_blueprints = list(my_bps_qs)

    if accessible_corporation_ids:
        corp_bp_qs = Blueprint.objects.filter(
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            bp_type=Blueprint.BPType.ORIGINAL,
            corporation_id__in=accessible_corporation_ids,
        )
        accessible_blueprints.extend(list(corp_bp_qs))

    bp_index = defaultdict(list)
    bp_item_map = {}

    for bp in accessible_blueprints:
        key = (bp.type_id, bp.material_efficiency, bp.time_efficiency)
        bp_index[key].append(bp)
        if bp.item_id is not None:
            bp_item_map[bp.item_id] = key

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
            "hint": _(
                "You posted this ask. Another builder will pick it up; no action needed from you."
            ),
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

    if not accessible_blueprints:
        context = {"requests": [], "metrics": metrics}
        context.update(nav_context)
        return render(request, "indy_hub/bp_copy_fulfill_requests.html", context)

    q = Q()
    has_filters = False
    for bp in accessible_blueprints:
        has_filters = True
        q |= Q(
            type_id=bp.type_id,
            material_efficiency=bp.material_efficiency,
            time_efficiency=bp.time_efficiency,
        )

    if not has_filters:
        context = {"requests": [], "metrics": metrics}
        context.update(nav_context)
        return render(request, "indy_hub/bp_copy_fulfill_requests.html", context)

    def _init_occupancy():
        return {"count": 0, "soonest_end": None}

    occupancy_map = defaultdict(_init_occupancy)

    def _update_soonest(info, end_date):
        if end_date and (info["soonest_end"] is None or end_date < info["soonest_end"]):
            info["soonest_end"] = end_date

    blocking_activities = [1, 3, 4, 5, 8, 9]
    active_jobs = IndustryJob.objects.filter(
        owner_user=request.user,
        owner_kind=Blueprint.OwnerKind.CHARACTER,
        status="active",
        activity_id__in=blocking_activities,
    ).only("blueprint_id", "blueprint_type_id", "end_date")

    for job in active_jobs:
        matched_key = bp_item_map.get(job.blueprint_id)
        if matched_key is not None:
            info = occupancy_map[matched_key]
            info["count"] += 1
            _update_soonest(info, job.end_date)

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
                info = occupancy_map[matched_key]
                info["count"] += 1
                _update_soonest(info, job.end_date)

    offer_status_labels = {
        "accepted": _("Accepted"),
        "conditional": _("Conditional"),
        "rejected": _("Rejected"),
    }

    qset = (
        BlueprintCopyRequest.objects.filter(q)
        .filter(
            Q(fulfilled=False)
            | Q(fulfilled=True, delivered=False, offers__owner=request.user)
        )
        .select_related("requested_by")
        .prefetch_related("offers__owner", "offers__chat")
        .order_by("-created_at")
        .distinct()
    )

    requests_to_fulfill = []
    for req in qset:
        offers = list(req.offers.all())
        my_offer = next(
            (offer for offer in offers if offer.owner_id == request.user.id), None
        )
        offers_by_owner = {offer.owner_id: offer.status for offer in offers}
        eligible_details = _eligible_owner_details_for_request(req)

        is_self_request = req.requested_by_id == request.user.id

        if req.fulfilled and (req.delivered or not my_offer):
            # Already delivered or fulfilled by someone else
            continue

        if my_offer and my_offer.status == "rejected":
            # Player already declined this request; hide from their fulfill queue
            continue

        status_key = "awaiting_response"
        can_mark_delivered = False
        handshake_state = None

        key = (req.type_id, req.material_efficiency, req.time_efficiency)
        matching_blueprints = bp_index.get(key, [])
        owned_blueprints = len(matching_blueprints)
        direct_info = occupancy_map.get(key)
        direct_count = direct_info["count"] if direct_info else 0
        total_active_jobs = min(owned_blueprints, direct_count)
        available_blueprints = max(owned_blueprints - total_active_jobs, 0)
        busy_until = direct_info["soonest_end"] if direct_info else None
        busy_overdue = bool(busy_until and busy_until < timezone.now())
        all_copies_busy = (
            owned_blueprints > 0 and available_blueprints == 0 and total_active_jobs > 0
        )

        corporate_names: list[str] = []
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

        corporate_names = sorted(set(corporate_names), key=lambda name: name.lower())
        is_corporate_source = bool(corporate_names)
        personal_count = len(personal_sources)
        corporate_count = len(corporate_sources)
        has_dual_sources = bool(personal_count and corporate_count)
        default_scope = "personal"
        if not personal_count and corporate_count:
            default_scope = "corporation"
        elif not personal_count and not corporate_count:
            default_scope = ""

        user_corp_id = eligible_details.user_to_corporation.get(request.user.id)
        if user_corp_id is not None and personal_count == 0 and not is_self_request:
            corp_members = eligible_details.corporate_members_by_corp.get(
                user_corp_id, set()
            )
            if any(
                offers_by_owner.get(member_id) == "rejected"
                for member_id in corp_members
            ):
                # Another authorised manager already declined on behalf of the corporation
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

        offer_chat_payload = None
        if my_offer and my_offer.status == "conditional":
            try:
                chat = my_offer.chat
            except BlueprintCopyChat.DoesNotExist:
                chat = _ensure_offer_chat(my_offer)
            else:
                if not chat.is_open:
                    chat.reopen()
            if chat and chat.is_open:
                offer_chat_payload = {
                    "id": chat.id,
                    "fetch_url": reverse("indy_hub:bp_chat_history", args=[chat.id]),
                    "send_url": reverse("indy_hub:bp_chat_send", args=[chat.id]),
                    "has_unread": _chat_has_unread(chat, "seller"),
                    "last_message_at": chat.last_message_at,
                    "last_message_display": (
                        timezone.localtime(chat.last_message_at).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        if chat.last_message_at
                        else ""
                    ),
                    "preview": _chat_preview_messages(chat),
                }
        if is_self_request:
            offer_chat_payload = None

        requests_to_fulfill.append(
            {
                "id": req.id,
                "type_id": req.type_id,
                "type_name": get_type_name(req.type_id),
                "icon_url": f"https://images.evetech.net/types/{req.type_id}/bp?size=64",
                "material_efficiency": req.material_efficiency,
                "time_efficiency": req.time_efficiency,
                "runs_requested": req.runs_requested,
                "copies_requested": getattr(req, "copies_requested", 1),
                "created_at": req.created_at,
                "requester": req.requested_by.username,
                "is_self_request": is_self_request,
                "status_key": status_key,
                "status_label": status_info["label"],
                "status_class": status_info["badge"],
                "status_hint": status_info["hint"],
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
                "show_offer_actions": (
                    status_key in {"awaiting_response", "offer_rejected"}
                    and not is_self_request
                ),
                "conditional_collapse_id": f"cond-{req.id}",
                "can_mark_delivered": can_mark_delivered
                and req.requested_by_id != request.user.id,
                "owned_blueprints": owned_blueprints,
                "available_blueprints": available_blueprints,
                "active_copy_jobs": total_active_jobs,
                "all_copies_busy": all_copies_busy,
                "busy_until": busy_until,
                "busy_overdue": busy_overdue,
                "chat": offer_chat_payload,
                "chat_preview": (
                    offer_chat_payload.get("preview", []) if offer_chat_payload else []
                ),
                "handshake": handshake_state,
                "is_corporate": is_corporate_source,
                "corporation_names": corporate_names,
                "personal_blueprints": personal_count,
                "corporate_blueprints": corporate_count,
                "has_dual_sources": has_dual_sources,
                "default_scope": default_scope,
            }
        )

    context = {"requests": requests_to_fulfill, "metrics": metrics}
    if auto_open_chat_id:
        context["auto_open_chat_id"] = auto_open_chat_id
    context.update(nav_context)

    return render(request, "indy_hub/bp_copy_fulfill_requests.html", context)


def _process_offer_action(
    *,
    request_obj,
    req: BlueprintCopyRequest,
    owner,
    action: str | None,
    message: str = "",
) -> bool:
    if not action:
        return False

    offer, _created = BlueprintCopyOffer.objects.get_or_create(request=req, owner=owner)
    my_requests_url = request_obj.build_absolute_uri(
        reverse("indy_hub:bp_copy_my_requests")
    )

    if action == "accept":
        offer.status = "accepted"
        offer.message = ""
        offer.accepted_by_buyer = True
        offer.accepted_by_seller = True
        offer.accepted_at = timezone.now()
        offer.save(
            update_fields=[
                "status",
                "message",
                "accepted_by_buyer",
                "accepted_by_seller",
                "accepted_at",
            ]
        )
        _close_offer_chat_if_exists(offer, BlueprintCopyChat.CloseReason.OFFER_ACCEPTED)
        notify_user(
            req.requested_by,
            "Blueprint Copy Request Accepted",
            f"{owner.username} accepted your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) for free.",
            "success",
            link=my_requests_url,
            link_label=_("Review your requests"),
        )
        req.fulfilled = True
        req.fulfilled_at = timezone.now()
        req.save(update_fields=["fulfilled", "fulfilled_at"])
        BlueprintCopyOffer.objects.filter(request=req).exclude(owner=owner).delete()
        messages.success(request_obj, _("Request accepted and requester notified."))
        return True

    if action == "conditional":
        offer.status = "conditional"
        offer.message = message
        offer.accepted_by_buyer = False
        offer.accepted_by_seller = False
        offer.accepted_at = None
        offer.save(
            update_fields=[
                "status",
                "message",
                "accepted_by_buyer",
                "accepted_by_seller",
                "accepted_at",
            ]
        )
        chat = _ensure_offer_chat(offer)
        if message:
            chat_message = BlueprintCopyMessage(
                chat=chat,
                sender=owner,
                sender_role=BlueprintCopyMessage.SenderRole.SELLER,
                content=message,
            )
            chat_message.full_clean()
            chat_message.save()
            chat.register_message(sender_role=BlueprintCopyMessage.SenderRole.SELLER)
        notify_user(
            req.requested_by,
            "Blueprint Copy Request - Conditional Offer",
            _(
                "You received a new conditional offer message for %(type)s (ME%(me)s, TE%(te)s)."
            )
            % {
                "type": get_type_name(req.type_id),
                "me": req.material_efficiency,
                "te": req.time_efficiency,
            },
            "info",
            link=my_requests_url,
            link_label=_("Review your requests"),
        )
        if message:
            messages.success(request_obj, _("Conditional offer sent."))
        else:
            messages.success(
                request_obj,
                _("Conditional offer started. Continue the discussion in chat."),
            )
        return True

    if action == "reject":
        offer.status = "rejected"
        offer.message = message
        offer.accepted_by_buyer = False
        offer.accepted_by_seller = False
        offer.accepted_at = None
        offer.save(
            update_fields=[
                "status",
                "message",
                "accepted_by_buyer",
                "accepted_by_seller",
                "accepted_at",
            ]
        )
        _close_offer_chat_if_exists(offer, BlueprintCopyChat.CloseReason.OFFER_REJECTED)
        if _finalize_request_if_all_rejected(req):
            messages.success(
                request_obj,
                _("Offer rejected. Requester notified that no builders are available."),
            )
        else:
            messages.success(request_obj, _("Offer rejected."))
        return True

    return False


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_offer_copy_request(request, request_id):
    """Handle offering to fulfill a blueprint copy request."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    action = request.POST.get("action")
    message = request.POST.get("message", "").strip()
    handled = _process_offer_action(
        request_obj=request,
        req=req,
        owner=request.user,
        action=action,
        message=message,
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
@indy_hub_permission_required("can_manage_copy_requests")
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

    if expected_user_id != request.user.id:
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
        )

    if not handled:
        messages.error(request, _("Unsupported action for this request."))
        return redirect(redirect_url)

    if chat_id:
        redirect_url = f"{redirect_url}?{urlencode({'open_chat': chat_id})}"
    return redirect(redirect_url)


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_buyer_accept_offer(request, offer_id):
    """Allow buyer to accept a conditional offer."""
    offer = get_object_or_404(BlueprintCopyOffer, id=offer_id, status="conditional")

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
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_accept_copy_request(request, request_id):
    """Accept a blueprint copy request and notify requester."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    req.fulfilled = True
    req.fulfilled_at = timezone.now()
    req.save()
    _close_request_chats(req, BlueprintCopyChat.CloseReason.OFFER_ACCEPTED)
    # Notify requester
    my_requests_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_my_requests")
    )
    notify_user(
        req.requested_by,
        "Blueprint Copy Request Accepted",
        f"Your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) has been accepted.",
        "success",
        link=my_requests_url,
        link_label=_("Review your requests"),
    )
    messages.success(request, "Copy request accepted.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_cond_copy_request(request, request_id):
    """Send conditional acceptance message for a blueprint copy request."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    message = request.POST.get("message", "").strip()
    if message:
        my_requests_url = request.build_absolute_uri(
            reverse("indy_hub:bp_copy_my_requests")
        )
        notify_user(
            req.requested_by,
            "Blueprint Copy Request Condition",
            message,
            "info",
            link=my_requests_url,
            link_label=_("Review your requests"),
        )
        messages.success(request, "Condition message sent to requester.")
    else:
        messages.error(request, "No message provided for condition.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_reject_copy_request(request, request_id):
    """Reject a blueprint copy request and notify requester."""
    req = get_object_or_404(BlueprintCopyRequest, id=request_id, fulfilled=False)
    my_requests_url = request.build_absolute_uri(
        reverse("indy_hub:bp_copy_my_requests")
    )
    notify_user(
        req.requested_by,
        "Blueprint Copy Request Rejected",
        f"Your copy request for {get_type_name(req.type_id)} (ME{req.material_efficiency}, TE{req.time_efficiency}) was rejected.",
        "warning",
        link=my_requests_url,
        link_label=_("Review your requests"),
    )
    _close_request_chats(req, BlueprintCopyChat.CloseReason.OFFER_REJECTED)
    req.delete()
    messages.success(request, "Copy request rejected.")
    return redirect("indy_hub:bp_copy_fulfill_requests")


@indy_hub_access_required
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_cancel_copy_request(request, request_id):
    """Allow user to cancel their own unfulfilled copy request."""
    req = get_object_or_404(
        BlueprintCopyRequest, id=request_id, requested_by=request.user, fulfilled=False
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
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_mark_copy_delivered(request, request_id):
    """Mark a fulfilled blueprint copy request as delivered (provider action)."""
    req = get_object_or_404(
        BlueprintCopyRequest, id=request_id, fulfilled=True, delivered=False
    )
    req.delivered = True
    req.delivered_at = timezone.now()
    req.save()
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
@indy_hub_permission_required("can_manage_copy_requests")
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

    for owner in User.objects.filter(id__in=owner_ids):
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
@indy_hub_permission_required("can_manage_copy_requests")
@login_required
def bp_copy_my_requests(request):
    """List copy requests made by the current user."""
    qs = (
        BlueprintCopyRequest.objects.filter(requested_by=request.user)
        .select_related("requested_by")
        .prefetch_related("offers__owner", "offers__chat")
        .order_by("-created_at")
    )

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
                "The buyer accepted your terms. Confirm in chat to finalise the agreement."
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

        for idx, offer in enumerate(conditional_offers, start=1):
            label = _("Builder #%d") % idx
            chat_payload = None
            try:
                chat = offer.chat
            except BlueprintCopyChat.DoesNotExist:
                chat = _ensure_offer_chat(offer)
            else:
                if not chat.is_open:
                    chat.reopen()
            if chat and chat.is_open:
                chat_payload = {
                    "id": chat.id,
                    "fetch_url": reverse("indy_hub:bp_chat_history", args=[chat.id]),
                    "send_url": reverse("indy_hub:bp_chat_send", args=[chat.id]),
                    "has_unread": _chat_has_unread(chat, "buyer"),
                    "last_message_at": chat.last_message_at,
                    "last_message_display": (
                        timezone.localtime(chat.last_message_at).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        if chat.last_message_at
                        else ""
                    ),
                    "preview": _chat_preview_messages(chat),
                }

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
        elif cond_waiting_builder:
            status_key = "waiting_on_builder"

        metrics["total"] += 1
        metrics_key = {
            "open": "open",
            "action_required": "action_required",
            "awaiting_delivery": "awaiting_delivery",
            "delivered": "delivered",
        }.get(status_key)
        if metrics_key:
            metrics[metrics_key] += 1

        status_info = status_meta[status_key]

        chat_actions = []
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
                    "status_hint": status_info["hint"],
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
                "cond_offers": cond_offer_data,
                "chat_actions": chat_actions,
                "delivered": req.delivered,
                "is_history": is_history,
                "status_key": status_key,
                "status_label": status_info["label"],
                "status_class": status_info["badge"],
                "status_hint": status_info["hint"],
                "created_at": req.created_at,
                "can_cancel": not req.fulfilled,
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
    }
    context.update(build_nav_context(request.user, active_tab="personal"))

    return render(request, "indy_hub/bp_copy_my_requests.html", context)


@indy_hub_access_required
@login_required
@require_http_methods(["GET"])
def bp_chat_history(request, chat_id: int):
    chat = get_object_or_404(
        BlueprintCopyChat.objects.select_related("request", "offer", "buyer", "seller"),
        id=chat_id,
    )

    logger.debug("bp_chat_history chat=%s user=%s", chat.id, request.user.id)

    viewer_role = chat.role_for(request.user)
    if viewer_role not in {"buyer", "seller"}:
        return JsonResponse({"error": _("Unauthorized")}, status=403)

    role_labels = {
        "buyer": _("Buyer"),
        "seller": _("Builder"),
        "system": _("System"),
    }
    messages_payload = []
    for msg in chat.messages.all():
        created_local = timezone.localtime(msg.created_at)
        messages_payload.append(
            {
                "id": msg.id,
                "role": msg.sender_role,
                "content": msg.content,
                "created_at": created_local.isoformat(),
                "created_display": created_local.strftime("%Y-%m-%d %H:%M"),
            }
        )

    other_role = "seller" if viewer_role == "buyer" else "buyer"

    decision_payload = None
    offer = getattr(chat, "offer", None)
    if offer and chat.is_open and offer.status == "conditional":
        accepted_by_buyer = offer.accepted_by_buyer
        accepted_by_seller = offer.accepted_by_seller

        if viewer_role == "buyer":
            viewer_can_accept = not accepted_by_buyer
            viewer_can_reject = not accepted_by_buyer
            accept_label = _("Accept terms")
            reject_label = _("Decline offer")
            if accepted_by_buyer and not accepted_by_seller:
                status_label = _("Waiting for the builder to confirm.")
                status_tone = "warning"
                state = "waiting_on_seller"
            elif not accepted_by_buyer and accepted_by_seller:
                status_label = _(
                    "The builder confirmed. Accept to finalise the agreement."
                )
                status_tone = "info"
                state = "waiting_on_you"
            else:
                status_label = _("Review the terms and confirm when ready.")
                status_tone = "info"
                state = "pending"
        else:
            viewer_can_accept = not accepted_by_seller
            viewer_can_reject = not (accepted_by_buyer and accepted_by_seller)
            accept_label = _("Confirm agreement")
            reject_label = _("Withdraw offer")
            if accepted_by_buyer and not accepted_by_seller:
                status_label = _("The buyer accepted your terms. Confirm to finalise.")
                status_tone = "warning"
                state = "waiting_on_you"
            elif accepted_by_seller and not accepted_by_buyer:
                status_label = _("Waiting for the buyer to accept your conditions.")
                status_tone = "info"
                state = "waiting_on_buyer"
            else:
                status_label = _("Share any adjustments or confirm when ready.")
                status_tone = "info"
                state = "pending"

        decision_payload = {
            "url": reverse("indy_hub:bp_chat_decide", args=[chat.id]),
            "accepted_by_buyer": accepted_by_buyer,
            "accepted_by_seller": accepted_by_seller,
            "viewer_can_accept": viewer_can_accept,
            "viewer_can_reject": viewer_can_reject,
            "accept_label": accept_label,
            "reject_label": reject_label,
            "status_label": status_label,
            "status_tone": status_tone,
            "state": state,
            "pending_label": _("Updating decision..."),
        }

    data = {
        "chat": {
            "id": chat.id,
            "is_open": chat.is_open,
            "closed_reason": chat.closed_reason,
            "viewer_role": viewer_role,
            "other_role": other_role,
            "labels": role_labels,
            "type_id": chat.request.type_id,
            "type_name": get_type_name(chat.request.type_id),
            "can_send": chat.is_open and viewer_role in {"buyer", "seller"},
            "decision": decision_payload,
        },
        "messages": messages_payload,
    }
    chat.mark_seen(viewer_role)
    return JsonResponse(data)


@indy_hub_access_required
@login_required
@require_http_methods(["POST"])
def bp_chat_send(request, chat_id: int):
    chat = get_object_or_404(
        BlueprintCopyChat.objects.select_related("request", "offer", "buyer", "seller"),
        id=chat_id,
    )
    viewer_role = chat.role_for(request.user)
    if viewer_role not in {"buyer", "seller"}:
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

    viewer_role = chat.role_for(request.user)
    if viewer_role not in {"buyer", "seller"}:
        return JsonResponse({"error": _("Unauthorized")}, status=403)

    if not chat.is_open or chat.offer.status != "conditional":
        return JsonResponse(
            {"error": _("This conversation is already closed.")}, status=409
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    decision = (payload.get("decision") or "").strip().lower()
    if decision not in {"accept", "reject"}:
        return JsonResponse({"error": _("Unsupported decision.")}, status=400)

    offer = chat.offer
    req = chat.request

    if decision == "accept":
        if viewer_role == "buyer":
            finalized = _mark_offer_buyer_accept(offer)
            if finalized:
                return JsonResponse({"status": "accepted"})

            fulfill_queue_url = build_site_url(
                reverse("indy_hub:bp_copy_fulfill_requests")
            )
            notify_user(
                chat.seller,
                _("Conditional offer accepted"),
                _(
                    "%(buyer)s accepted your terms for %(type)s (ME%(me)s, TE%(te)s). Confirm in chat to finalise the agreement."
                )
                % {
                    "buyer": req.requested_by.username,
                    "type": get_type_name(req.type_id),
                    "me": req.material_efficiency,
                    "te": req.time_efficiency,
                },
                "info",
                link=fulfill_queue_url,
                link_label=_("Open fulfill queue"),
            )
            return JsonResponse(
                {
                    "status": "pending",
                    "accepted_by_buyer": True,
                    "accepted_by_seller": offer.accepted_by_seller,
                }
            )

        finalized = _mark_offer_seller_accept(offer)
        if finalized:
            return JsonResponse({"status": "accepted"})

        buyer_requests_url = build_site_url(reverse("indy_hub:bp_copy_my_requests"))
        notify_user(
            chat.buyer,
            _("Builder confirmed your terms"),
            _(
                "%(builder)s confirmed the agreement for %(type)s (ME%(me)s, TE%(te)s). Accept in chat to finalise."
            )
            % {
                "builder": offer.owner.username,
                "type": get_type_name(req.type_id),
                "me": req.material_efficiency,
                "te": req.time_efficiency,
            },
            "info",
            link=buyer_requests_url,
            link_label=_("Review your requests"),
        )
        return JsonResponse(
            {
                "status": "pending",
                "accepted_by_buyer": offer.accepted_by_buyer,
                "accepted_by_seller": True,
            }
        )

    # Reject path
    offer.status = "rejected"
    offer.accepted_by_buyer = False
    offer.accepted_by_seller = False
    offer.accepted_at = None
    offer.save(
        update_fields=[
            "status",
            "accepted_by_buyer",
            "accepted_by_seller",
            "accepted_at",
        ]
    )

    chat.close(reason=BlueprintCopyChat.CloseReason.OFFER_REJECTED)

    recipient = chat.seller if viewer_role == "buyer" else chat.buyer
    if recipient:
        notify_user(
            recipient,
            _("Conditional offer declined"),
            _(
                "%(actor)s declined the conditional offer for %(type)s (ME%(me)s, TE%(te)s)."
            )
            % {
                "actor": request.user.username,
                "type": get_type_name(req.type_id),
                "me": req.material_efficiency,
                "te": req.time_efficiency,
            },
            "warning",
            link=build_site_url(
                reverse(
                    "indy_hub:bp_copy_fulfill_requests"
                    if viewer_role == "buyer"
                    else "indy_hub:bp_copy_my_requests"
                )
            ),
            link_label=_("Open details"),
        )

    if viewer_role == "seller":
        if _finalize_request_if_all_rejected(req):
            return JsonResponse({"status": "rejected", "request_closed": True})

    return JsonResponse({"status": "rejected"})


@indy_hub_access_required
@login_required
def production_simulations_list(request):
    """
    Display the list of production simulations saved by the user.
    Return JSON when api=1 is included in the query string.
    """
    simulations = (
        ProductionSimulation.objects.filter(user=request.user)
        .order_by("-updated_at")
        .prefetch_related("production_configs")
    )

    # Return JSON when the API payload is requested
    if request.GET.get("api") == "1":
        simulations_data = []
        for sim in simulations:
            simulations_data.append(
                {
                    "id": sim.id,
                    "blueprint_type_id": sim.blueprint_type_id,
                    "blueprint_name": sim.blueprint_name,
                    "runs": sim.runs,
                    "simulation_name": sim.simulation_name,
                    "display_name": sim.display_name,
                    "total_items": sim.total_items,
                    "total_buy_items": sim.total_buy_items,
                    "total_prod_items": sim.total_prod_items,
                    "estimated_cost": float(sim.estimated_cost),
                    "estimated_revenue": float(sim.estimated_revenue),
                    "estimated_profit": float(sim.estimated_profit),
                    "active_tab": sim.active_tab,
                    "created_at": sim.created_at.isoformat(),
                    "updated_at": sim.updated_at.strftime("%Y-%m-%d %H:%M"),
                }
            )

        return JsonResponse(
            {
                "success": True,
                "simulations": simulations_data,
                "total_simulations": simulations.count(),
            }
        )

    # Prepare aggregate statistics for the HTML view
    total_simulations, stats = summarize_simulations(simulations)

    # Otherwise render the standard HTML page
    # Pagination
    paginator = Paginator(simulations, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # --- Add a type_id -> EVE group mapping for Craft_BP.html ---
    # Collect every type_id used across the user's simulations
    type_ids = set()
    for sim in simulations:
        configs = sim.production_configs.all()
        for config in configs:
            type_ids.add(config.item_type_id)
    # Resolve the EVE group name for each type_id
    market_group_map = {}
    if EveType is not None and type_ids:
        eve_types = EveType.objects.filter(id__in=type_ids).select_related("eve_group")
        for eve_type in eve_types:
            market_group_map[eve_type.id] = (
                eve_type.eve_group.name if eve_type.eve_group else "Other"
            )
    context = {
        "simulations": page_obj,
        "total_simulations": total_simulations,
        "market_group_map": json.dumps(market_group_map),
        "stats": stats,
    }
    return render(request, "indy_hub/production_simulations_list.html", context)


@indy_hub_access_required
@login_required
def delete_production_simulation(request, simulation_id):
    """
    Delete a production simulation and its related configurations.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    if request.method == "POST":
        blueprint_type_id = simulation.blueprint_type_id
        runs = simulation.runs
        simulation_name = simulation.display_name

        # Delete the related configurations (new direct relation)
        related_configs = simulation.production_configs.all()
        if related_configs.exists():
            related_configs.delete()
        else:
            # Legacy fallback: clean old rows without a populated FK
            ProductionConfig.objects.filter(
                user=request.user,
                blueprint_type_id=blueprint_type_id,
                runs=runs,
                simulation__isnull=True,
            ).delete()

        # Delete the simulation itself
        simulation.delete()

        messages.success(
            request, f'Simulation "{simulation_name}" deleted successfully.'
        )
        return redirect("indy_hub:production_simulations_list")

    context = {
        "simulation": simulation,
    }

    return render(request, "indy_hub/confirm_delete_simulation.html", context)


@indy_hub_access_required
@login_required
def edit_simulation_name(request, simulation_id):
    """
    Allow editing the custom name of a simulation.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    if request.method == "POST":
        new_name = request.POST.get("simulation_name", "").strip()
        simulation.simulation_name = new_name
        simulation.save()

        messages.success(request, "Simulation name updated successfully.")
        return redirect("indy_hub:production_simulations_list")

    context = {
        "simulation": simulation,
    }

    return render(request, "indy_hub/edit_simulation_name.html", context)
