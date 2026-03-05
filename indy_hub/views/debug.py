from __future__ import annotations

# Standard Library
from datetime import timedelta
from time import perf_counter
from urllib.parse import urlencode

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Max, Q, Subquery, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

# AA Example App
from indy_hub.app_settings import STRUCTURE_NAME_STALE_HOURS
from indy_hub.models import (
    Blueprint,
    BlueprintCopyChat,
    BlueprintCopyMessage,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    CachedCharacterAsset,
    CachedCorporationAsset,
    CachedStructureName,
    CharacterSettings,
    CorporationSharingSetting,
    ESIContract,
    IndustryJob,
    JobNotificationDigestEntry,
    MaterialExchangeBuyOrder,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSettings,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
    NotificationWebhook,
    NotificationWebhookMessage,
    ProductionConfig,
    ProductionSimulation,
    SDEBlueprintActivityMaterial,
    SDEBlueprintActivityProduct,
    SDEIndustryActivity,
    SDEMarketGroup,
    SDESyncCompatState,
    UserOnboardingProgress,
)
from indy_hub.services.asset_cache import (
    PLACEHOLDER_PREFIX,
    PUBLIC_ID_PLACEHOLDER_TTL,
    STRUCTURE_PLACEHOLDER_TTL,
)
from indy_hub.services.esi_client import shared_client
from indy_hub.utils.eve import resolve_location_name

logger = get_extension_logger(__name__)
User = get_user_model()

STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
CORP_STRUCTURES_SCOPE = "esi-corporations.read_structures.v1"
CORP_ASSETS_SCOPE = "esi-assets.read_corporation_assets.v1"
CHAR_ASSETS_SCOPE = "esi-assets.read_assets.v1"


def _token_scope_counts(scope: str) -> dict[str, int]:
    """Return token counts for a scope (total + valid), best-effort."""

    try:
        total = (
            Token.objects.filter()
            .require_scopes([scope])
            .values("character_id")
            .distinct()
            .count()
        )
    except Exception:
        total = 0

    try:
        valid = (
            Token.objects.filter()
            .require_scopes([scope])
            .require_valid()
            .values("character_id")
            .distinct()
            .count()
        )
    except Exception:
        valid = 0

    return {"total": int(total), "valid": int(valid)}


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


@login_required
def debug_health(request):
    """Minimal debug health page (unlinked) for superusers only."""

    if not request.user.is_superuser:
        raise Http404()

    if request.method == "POST":
        action = str(request.POST.get("action", "") or "").strip()
        if action == "queue_structure_refresh":
            raw_sid = str(request.POST.get("structure_id", "") or "").strip()
            if raw_sid:
                try:
                    sid = int(raw_sid)
                except ValueError:
                    sid = None
                if sid is not None:
                    try:
                        # AA Example App
                        from indy_hub.tasks.location import cache_structure_name

                        cache_structure_name.delay(int(sid))
                        query = urlencode(
                            {
                                "structure_id": int(sid),
                                "queued": 1,
                            }
                        )
                        return redirect(f"{reverse('indy_hub:debug_health')}?{query}")
                    except Exception:
                        query = urlencode(
                            {
                                "structure_id": int(sid),
                                "queue_error": 1,
                            }
                        )
                        return redirect(f"{reverse('indy_hub:debug_health')}?{query}")

    active_tab_raw = str(request.GET.get("tab", "") or "").strip().lower()
    allowed_tabs = {"overview", "locations", "features", "user", "inspector"}
    active_tab = active_tab_raw if active_tab_raw in allowed_tabs else ""

    now = timezone.now()
    structure_cutoff = now - timedelta(hours=STRUCTURE_NAME_STALE_HOURS)
    placeholder_cutoff = now - STRUCTURE_PLACEHOLDER_TTL
    one_hour_ago = now - timedelta(hours=1)
    six_hours_ago = now - timedelta(hours=6)
    twenty_four_hours_ago = now - timedelta(hours=24)

    structure_metrics = CachedStructureName.objects.aggregate(
        total=Count("structure_id"),
        int32_ids=Count("structure_id", filter=Q(structure_id__lte=2_147_483_647)),
        int64_ids=Count("structure_id", filter=Q(structure_id__gt=2_147_483_647)),
        negative_ids=Count("structure_id", filter=Q(structure_id__lt=0)),
        placeholders=Count(
            "structure_id", filter=Q(name__startswith=PLACEHOLDER_PREFIX)
        ),
        stale_resolved=Count(
            "structure_id",
            filter=Q(last_resolved__lt=structure_cutoff)
            & ~Q(name__startswith=PLACEHOLDER_PREFIX),
        ),
        stale_placeholders=Count(
            "structure_id",
            filter=Q(name__startswith=PLACEHOLDER_PREFIX)
            & Q(last_resolved__lt=placeholder_cutoff),
        ),
        fresh_resolved=Count(
            "structure_id",
            filter=~Q(name__startswith=PLACEHOLDER_PREFIX)
            & Q(last_resolved__gte=structure_cutoff),
        ),
        updated_last_24h=Count(
            "structure_id", filter=Q(last_resolved__gte=twenty_four_hours_ago)
        ),
        placeholder_older_24h=Count(
            "structure_id",
            filter=Q(name__startswith=PLACEHOLDER_PREFIX)
            & Q(last_resolved__lt=twenty_four_hours_ago),
        ),
        age_lt_1h=Count("structure_id", filter=Q(last_resolved__gte=one_hour_ago)),
        age_1h_6h=Count(
            "structure_id",
            filter=Q(last_resolved__lt=one_hour_ago)
            & Q(last_resolved__gte=six_hours_ago),
        ),
        age_6h_24h=Count(
            "structure_id",
            filter=Q(last_resolved__lt=six_hours_ago)
            & Q(last_resolved__gte=twenty_four_hours_ago),
        ),
        age_gt_24h=Count(
            "structure_id", filter=Q(last_resolved__lt=twenty_four_hours_ago)
        ),
        latest_resolved=Max("last_resolved"),
    )

    structure_total = int(structure_metrics.get("total") or 0)
    placeholders = int(structure_metrics.get("placeholders") or 0)
    stale_placeholders = int(structure_metrics.get("stale_placeholders") or 0)
    stale_resolved = int(structure_metrics.get("stale_resolved") or 0)

    resolver_kpis = {
        "coverage_pct": _pct(structure_total - placeholders, structure_total),
        "placeholder_pct": _pct(placeholders, structure_total),
        "stale_pct": _pct(stale_placeholders + stale_resolved, structure_total),
        "stale_placeholder_pct": _pct(stale_placeholders, max(placeholders, 1)),
    }

    corp_assets_metrics = CachedCorporationAsset.objects.aggregate(
        total=Count("id"),
        latest_synced_at=Max("synced_at"),
        corporations=Count("corporation_id", distinct=True),
        office_folders=Count("id", filter=Q(location_flag="OfficeFolder")),
        stale_rows=Count("id", filter=Q(synced_at__lt=twenty_four_hours_ago)),
    )
    char_assets_metrics = CachedCharacterAsset.objects.aggregate(
        total=Count("id"),
        latest_synced_at=Max("synced_at"),
        users=Count("user_id", distinct=True),
        characters=Count("character_id", distinct=True),
        stale_rows=Count("id", filter=Q(synced_at__lt=twenty_four_hours_ago)),
        nested_rows=Count(
            "id",
            filter=Q(raw_location_id__isnull=False)
            & ~Q(raw_location_id=0)
            & ~Q(raw_location_id=F("location_id")),
        ),
    )

    blueprint_metrics = Blueprint.objects.aggregate(
        total=Count("id"),
        corp=Count("id", filter=Q(owner_kind=Blueprint.OwnerKind.CORPORATION)),
        char=Count("id", filter=Q(owner_kind=Blueprint.OwnerKind.CHARACTER)),
        latest_updated=Max("last_updated"),
    )
    blueprint_location_ids_qs = (
        Blueprint.objects.exclude(location_id__isnull=True)
        .exclude(location_id=0)
        .values("location_id")
        .distinct()
    )
    blueprint_location_total = int(blueprint_location_ids_qs.count() or 0)
    blueprint_locations_cached = int(
        CachedStructureName.objects.filter(
            structure_id__in=Subquery(blueprint_location_ids_qs)
        ).count()
        or 0
    )
    blueprint_locations_cached_resolved = int(
        CachedStructureName.objects.filter(
            structure_id__in=Subquery(blueprint_location_ids_qs)
        )
        .exclude(name__startswith=PLACEHOLDER_PREFIX)
        .count()
        or 0
    )
    blueprint_locations_cached_placeholder = int(
        CachedStructureName.objects.filter(
            structure_id__in=Subquery(blueprint_location_ids_qs),
            name__startswith=PLACEHOLDER_PREFIX,
        ).count()
        or 0
    )
    blueprint_location_cache_metrics = {
        "distinct_ids": blueprint_location_total,
        "cached": blueprint_locations_cached,
        "missing": max(blueprint_location_total - blueprint_locations_cached, 0),
        "resolved": blueprint_locations_cached_resolved,
        "placeholder": blueprint_locations_cached_placeholder,
        "coverage_pct": _pct(blueprint_locations_cached, blueprint_location_total),
        "resolved_pct": _pct(
            blueprint_locations_cached_resolved, blueprint_location_total
        ),
    }
    jobs_metrics = IndustryJob.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status="active") & Q(end_date__gt=now)),
        latest_updated=Max("last_updated"),
        stale_rows=Count("id", filter=Q(last_updated__lt=twenty_four_hours_ago)),
    )

    copy_request_metrics_raw = BlueprintCopyRequest.objects.aggregate(
        total=Count("id"),
        fulfilled_count=Count("id", filter=Q(fulfilled=True)),
        delivered_count=Count("id", filter=Q(delivered=True)),
        open_count=Count("id", filter=Q(fulfilled=False)),
    )
    copy_request_metrics = {
        "total": int(copy_request_metrics_raw.get("total") or 0),
        "fulfilled": int(copy_request_metrics_raw.get("fulfilled_count") or 0),
        "delivered": int(copy_request_metrics_raw.get("delivered_count") or 0),
        "open": int(copy_request_metrics_raw.get("open_count") or 0),
    }

    offer_status_breakdown = list(
        BlueprintCopyOffer.objects.values("status")
        .annotate(total=Count("id"))
        .order_by("-total", "status")
    )

    chat_metrics = BlueprintCopyChat.objects.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(is_open=True)),
        closed=Count("id", filter=Q(is_open=False)),
        unread_buyer=Count(
            "id",
            filter=Q(last_message_at__isnull=False)
            & ~Q(last_message_role__in=[None, "", "buyer", "system"])
            & (
                Q(buyer_last_seen_at__isnull=True)
                | Q(buyer_last_seen_at__lt=F("last_message_at"))
            ),
        ),
        unread_seller=Count(
            "id",
            filter=Q(last_message_at__isnull=False)
            & ~Q(last_message_role__in=[None, "", "seller", "system"])
            & (
                Q(seller_last_seen_at__isnull=True)
                | Q(seller_last_seen_at__lt=F("last_message_at"))
            ),
        ),
    )
    copy_message_total = BlueprintCopyMessage.objects.count()

    onboarding_metrics_raw = UserOnboardingProgress.objects.aggregate(
        total=Count("id"),
        dismissed_count=Count("id", filter=Q(dismissed=True)),
        visible_count=Count("id", filter=Q(dismissed=False)),
    )
    onboarding_metrics = {
        "total": int(onboarding_metrics_raw.get("total") or 0),
        "dismissed": int(onboarding_metrics_raw.get("dismissed_count") or 0),
        "visible": int(onboarding_metrics_raw.get("visible_count") or 0),
    }

    character_settings_metrics = CharacterSettings.objects.aggregate(
        total=Count("id"),
        copy_enabled=Count("id", filter=Q(allow_copy_requests=True)),
        jobs_notify_enabled=Count(
            "id", filter=~Q(jobs_notify_frequency=CharacterSettings.NOTIFY_DISABLED)
        ),
        corp_jobs_notify_enabled=Count(
            "id",
            filter=~Q(corp_jobs_notify_frequency=CharacterSettings.NOTIFY_DISABLED),
        ),
    )
    character_scope_breakdown = list(
        CharacterSettings.objects.values("copy_sharing_scope")
        .annotate(total=Count("id"))
        .order_by("-total", "copy_sharing_scope")
    )

    corp_sharing_metrics = CorporationSharingSetting.objects.aggregate(
        total=Count("id"),
        copy_enabled=Count("id", filter=Q(allow_copy_requests=True)),
    )
    corp_scope_breakdown = list(
        CorporationSharingSetting.objects.values("share_scope")
        .annotate(total=Count("id"))
        .order_by("-total", "share_scope")
    )

    digest_metrics = JobNotificationDigestEntry.objects.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(sent_at__isnull=True)),
        sent=Count("id", filter=Q(sent_at__isnull=False)),
    )

    webhook_metrics = NotificationWebhook.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
        blueprint=Count(
            "id", filter=Q(webhook_type=NotificationWebhook.TYPE_BLUEPRINT_SHARING)
        ),
        material=Count(
            "id", filter=Q(webhook_type=NotificationWebhook.TYPE_MATERIAL_EXCHANGE)
        ),
    )
    webhook_messages_metrics = NotificationWebhookMessage.objects.aggregate(
        total=Count("id"),
        with_buy_order=Count("id", filter=Q(buy_order__isnull=False)),
        with_copy_request=Count("id", filter=Q(copy_request__isnull=False)),
    )

    simulation_metrics = ProductionSimulation.objects.aggregate(
        total=Count("id"),
        users=Count("user_id", distinct=True),
        latest_updated=Max("updated_at"),
    )
    production_config_metrics = ProductionConfig.objects.aggregate(total=Count("id"))

    material_settings = MaterialExchangeSettings.get_solo()
    material_config_metrics = MaterialExchangeConfig.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )
    material_stock_metrics = MaterialExchangeStock.objects.aggregate(
        total=Count("id"),
        latest_updated=Max("updated_at"),
        zero_qty=Count("id", filter=Q(quantity=0)),
    )

    sell_order_metrics = MaterialExchangeSellOrder.objects.aggregate(total=Count("id"))
    sell_status_breakdown = list(
        MaterialExchangeSellOrder.objects.values("status")
        .annotate(total=Count("id"))
        .order_by("-total", "status")
    )
    buy_order_metrics = MaterialExchangeBuyOrder.objects.aggregate(total=Count("id"))
    buy_status_breakdown = list(
        MaterialExchangeBuyOrder.objects.values("status")
        .annotate(total=Count("id"))
        .order_by("-total", "status")
    )

    transaction_metrics = MaterialExchangeTransaction.objects.aggregate(
        total=Count("id"),
        total_value=Sum("total_price"),
        latest_completed=Max("completed_at"),
    )
    transaction_type_breakdown = list(
        MaterialExchangeTransaction.objects.values("transaction_type")
        .annotate(total=Count("id"), total_value=Sum("total_price"))
        .order_by("-total", "transaction_type")
    )

    contracts_metrics = ESIContract.objects.aggregate(
        total=Count("contract_id"),
        latest_synced=Max("last_synced"),
    )
    contract_status_breakdown = list(
        ESIContract.objects.values("status")
        .annotate(total=Count("contract_id"))
        .order_by("-total", "status")
    )

    sde_metrics = {
        "market_groups": SDEMarketGroup.objects.count(),
        "industry_activities": SDEIndustryActivity.objects.count(),
        "activity_products": SDEBlueprintActivityProduct.objects.count(),
        "activity_materials": SDEBlueprintActivityMaterial.objects.count(),
    }
    sde_state = SDESyncCompatState.objects.filter(pk=1).values("last_synced_at").first()

    job_status_breakdown = list(
        IndustryJob.objects.values("status")
        .annotate(total=Count("id"))
        .order_by("-total", "status")[:15]
    )

    blueprint_kind_breakdown = list(
        Blueprint.objects.values("owner_kind")
        .annotate(total=Count("id"))
        .order_by("-total", "owner_kind")
    )

    token_metrics = [
        {
            "scope": STRUCTURE_SCOPE,
            "label": "universe.read_structures",
            **_token_scope_counts(STRUCTURE_SCOPE),
        },
        {
            "scope": CORP_STRUCTURES_SCOPE,
            "label": "corp.read_structures",
            **_token_scope_counts(CORP_STRUCTURES_SCOPE),
        },
        {
            "scope": CORP_ASSETS_SCOPE,
            "label": "corp.read_assets",
            **_token_scope_counts(CORP_ASSETS_SCOPE),
        },
        {
            "scope": CHAR_ASSETS_SCOPE,
            "label": "char.read_assets",
            **_token_scope_counts(CHAR_ASSETS_SCOPE),
        },
    ]
    for metric in token_metrics:
        metric["invalid_or_expired"] = max(
            int(metric.get("total", 0)) - int(metric.get("valid", 0)),
            0,
        )
        metric["valid_pct"] = _pct(
            int(metric.get("valid", 0)), int(metric.get("total", 0))
        )

    top_corporations_assets = list(
        CachedCorporationAsset.objects.values("corporation_id")
        .annotate(
            rows=Count("id"),
            latest_synced_at=Max("synced_at"),
            office_folders=Count("id", filter=Q(location_flag="OfficeFolder")),
        )
        .order_by("-rows", "corporation_id")[:20]
    )

    top_characters_assets = list(
        CachedCharacterAsset.objects.values("character_id")
        .annotate(
            rows=Count("id"),
            latest_synced_at=Max("synced_at"),
        )
        .order_by("-rows", "character_id")[:20]
    )

    oldest_placeholders = list(
        CachedStructureName.objects.filter(name__startswith=PLACEHOLDER_PREFIX)
        .order_by("last_resolved")
        .values("structure_id", "name", "last_resolved")[:25]
    )
    stale_resolved_rows = list(
        CachedStructureName.objects.exclude(name__startswith=PLACEHOLDER_PREFIX)
        .filter(last_resolved__lt=structure_cutoff)
        .order_by("last_resolved")
        .values("structure_id", "name", "last_resolved")[:25]
    )
    recent_resolved = list(
        CachedStructureName.objects.exclude(name__startswith=PLACEHOLDER_PREFIX)
        .order_by("-last_resolved")
        .values("structure_id", "name", "last_resolved")[:25]
    )

    alerts: list[str] = []
    recommended_actions: list[str] = []
    if resolver_kpis["placeholder_pct"] >= 20:
        alerts.append(
            f"High placeholder ratio ({resolver_kpis['placeholder_pct']}%). Check universe-scope tokens and private structure access."
        )
        recommended_actions.append(
            "Ensure at least one valid token has esi-universe.read_structures.v1, then rerun structure-name refresh."
        )
    if resolver_kpis["stale_pct"] >= 20:
        alerts.append(
            f"High stale-cache ratio ({resolver_kpis['stale_pct']}%). Check refresh task cadence."
        )
        recommended_actions.append(
            "Check Celery queue health and housekeeping.refresh_stale_snapshots scheduling."
        )
    if int(corp_assets_metrics.get("stale_rows") or 0) > 0:
        alerts.append("Some corporation assets are older than 24h.")
        recommended_actions.append(
            "Rerun corporation-asset refresh for the largest corporations first."
        )
    if int(char_assets_metrics.get("stale_rows") or 0) > 0:
        alerts.append("Some character assets are older than 24h.")
        recommended_actions.append(
            "Verify character-asset token validity and rerun user-asset sync."
        )
    weak_scopes = [m for m in token_metrics if int(m.get("valid", 0)) == 0]
    if weak_scopes:
        alerts.append(
            "Scopes with no valid token: "
            + ", ".join(str(m.get("label")) for m in weak_scopes)
        )
        recommended_actions.append(
            "Re-authorize missing scopes from the ESI token hub."
        )

    inspect_input = str(request.GET.get("structure_id", "") or "").strip()
    inspect_probe_requested = str(request.GET.get("probe", "") or "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    inspect_data = None
    inspect_error = ""
    queue_result = str(request.GET.get("queued", "") or "") == "1"
    queue_error = str(request.GET.get("queue_error", "") or "") == "1"

    if not active_tab:
        if inspect_input or queue_result or queue_error:
            active_tab = "inspector"
        else:
            active_tab = "overview"

    selected_user_id = None
    selected_user = None
    user_search_query = str(request.GET.get("user_q", "") or "").strip()
    user_search_results = []

    if user_search_query:
        user_search_filters = Q(username__icontains=user_search_query) | Q(
            email__icontains=user_search_query
        )
        if user_search_query.isdigit():
            user_search_filters = user_search_filters | Q(id=int(user_search_query))

        user_search_results = list(
            User.objects.filter(user_search_filters)
            .order_by("username", "id")
            .values(
                "id",
                "username",
                "email",
                "is_active",
                "is_staff",
                "is_superuser",
                "last_login",
                "date_joined",
            )[:60]
        )

    selected_user_raw = str(request.GET.get("user_id", "") or "").strip()
    if selected_user_raw:
        try:
            selected_user_id = int(selected_user_raw)
        except ValueError:
            selected_user_id = None
        if selected_user_id:
            selected_user = (
                User.objects.filter(id=selected_user_id)
                .values(
                    "id",
                    "username",
                    "email",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "last_login",
                    "date_joined",
                )
                .first()
            )
            if not selected_user:
                selected_user_id = None

    user_focus_metrics = None
    if selected_user_id:
        selected_blueprints_qs = Blueprint.objects.filter(
            owner_user_id=selected_user_id
        )
        selected_jobs_qs = IndustryJob.objects.filter(owner_user_id=selected_user_id)

        selected_blueprint_locations_qs = (
            selected_blueprints_qs.exclude(location_id__isnull=True)
            .exclude(location_id=0)
            .values("location_id")
            .distinct()
        )
        selected_location_total = int(selected_blueprint_locations_qs.count() or 0)
        selected_location_cached = int(
            CachedStructureName.objects.filter(
                structure_id__in=Subquery(selected_blueprint_locations_qs)
            ).count()
            or 0
        )
        selected_location_resolved = int(
            CachedStructureName.objects.filter(
                structure_id__in=Subquery(selected_blueprint_locations_qs)
            )
            .exclude(name__startswith=PLACEHOLDER_PREFIX)
            .count()
            or 0
        )
        selected_location_placeholder = int(
            CachedStructureName.objects.filter(
                structure_id__in=Subquery(selected_blueprint_locations_qs),
                name__startswith=PLACEHOLDER_PREFIX,
            ).count()
            or 0
        )

        user_character_rows = list(
            CharacterOwnership.objects.filter(user_id=selected_user_id)
            .select_related("character")
            .values(
                "character__character_id",
                "character__character_name",
                "character__corporation_id",
                "character__corporation_name",
                "character__alliance_id",
                "character__alliance_name",
            )
            .order_by("character__character_name", "character__character_id")
        )

        user_character_settings_rows = list(
            CharacterSettings.objects.filter(user_id=selected_user_id)
            .values(
                "character_id",
                "allow_copy_requests",
                "copy_sharing_scope",
                "jobs_notify_completed",
                "jobs_notify_frequency",
                "jobs_notify_custom_days",
                "jobs_notify_custom_hours",
                "jobs_next_digest_at",
                "jobs_last_digest_at",
                "corp_jobs_notify_frequency",
                "corp_jobs_notify_custom_days",
                "corp_jobs_notify_custom_hours",
                "corp_jobs_next_digest_at",
                "corp_jobs_last_digest_at",
                "updated_at",
            )
            .order_by("character_id")[:200]
        )

        user_corp_sharing_rows = list(
            CorporationSharingSetting.objects.filter(user_id=selected_user_id)
            .values(
                "corporation_id",
                "corporation_name",
                "share_scope",
                "allow_copy_requests",
                "corp_jobs_notify_frequency",
                "corp_jobs_notify_custom_days",
                "corp_jobs_notify_custom_hours",
                "corp_jobs_next_digest_at",
                "corp_jobs_last_digest_at",
                "authorized_characters",
                "updated_at",
            )
            .order_by("corporation_name", "corporation_id")[:200]
        )

        user_onboarding = (
            UserOnboardingProgress.objects.filter(user_id=selected_user_id)
            .values("dismissed", "manual_steps", "created_at", "updated_at")
            .first()
        )

        selected_location_ids = list(
            selected_blueprint_locations_qs.values_list("location_id", flat=True)
        )
        selected_cached_location_rows = list(
            CachedStructureName.objects.filter(structure_id__in=selected_location_ids)
            .values("structure_id", "name", "last_resolved")
            .order_by("structure_id")
        )
        selected_cached_ids = {
            int(row.get("structure_id"))
            for row in selected_cached_location_rows
            if row.get("structure_id") is not None
        }
        selected_missing_location_ids = sorted(
            {int(loc_id) for loc_id in selected_location_ids if loc_id is not None}
            - selected_cached_ids
        )

        user_token_metrics = []
        for scope_name, scope_label in [
            (STRUCTURE_SCOPE, "universe.read_structures"),
            (CORP_STRUCTURES_SCOPE, "corp.read_structures"),
            (CORP_ASSETS_SCOPE, "corp.read_assets"),
            (CHAR_ASSETS_SCOPE, "char.read_assets"),
        ]:
            try:
                scoped_tokens = Token.objects.filter(
                    user_id=selected_user_id
                ).require_scopes([scope_name])
                token_total = int(
                    scoped_tokens.values("character_id").distinct().count() or 0
                )
                token_valid = int(
                    scoped_tokens.require_valid()
                    .values("character_id")
                    .distinct()
                    .count()
                    or 0
                )
            except Exception:
                token_total = 0
                token_valid = 0

            user_token_metrics.append(
                {
                    "scope": scope_name,
                    "label": scope_label,
                    "total": token_total,
                    "valid": token_valid,
                    "invalid_or_expired": max(token_total - token_valid, 0),
                    "valid_pct": _pct(token_valid, token_total),
                }
            )

        user_focus_metrics = {
            "blueprints": selected_blueprints_qs.aggregate(
                total=Count("id"),
                latest_updated=Max("last_updated"),
            ),
            "jobs": selected_jobs_qs.aggregate(
                total=Count("id"),
                active=Count("id", filter=Q(status="active") & Q(end_date__gt=now)),
                latest_updated=Max("last_updated"),
            ),
            "char_assets": CachedCharacterAsset.objects.filter(
                user_id=selected_user_id
            ).aggregate(
                total=Count("id"),
                characters=Count("character_id", distinct=True),
                latest_synced_at=Max("synced_at"),
            ),
            "copy_requests": BlueprintCopyRequest.objects.filter(
                requested_by_id=selected_user_id
            ).aggregate(
                total=Count("id"),
                open=Count("id", filter=Q(fulfilled=False)),
                fulfilled=Count("id", filter=Q(fulfilled=True)),
                delivered=Count("id", filter=Q(delivered=True)),
            ),
            "location_cache": {
                "distinct_ids": selected_location_total,
                "cached": selected_location_cached,
                "missing": max(selected_location_total - selected_location_cached, 0),
                "resolved": selected_location_resolved,
                "placeholder": selected_location_placeholder,
                "coverage_pct": _pct(selected_location_cached, selected_location_total),
                "resolved_pct": _pct(
                    selected_location_resolved,
                    selected_location_total,
                ),
            },
            "characters": {
                "total": len(user_character_rows),
                "rows": user_character_rows,
            },
            "character_settings": {
                "total": len(user_character_settings_rows),
                "rows": user_character_settings_rows,
            },
            "corp_sharing": {
                "total": len(user_corp_sharing_rows),
                "rows": user_corp_sharing_rows,
            },
            "onboarding": user_onboarding,
            "tokens": user_token_metrics,
            "location_examples": {
                "missing_ids": selected_missing_location_ids[:50],
                "cached_rows": selected_cached_location_rows[:50],
            },
        }

    if inspect_input:
        try:
            inspect_id = int(inspect_input)
        except ValueError:
            inspect_error = "Invalid structure_id (expected integer)."
        else:
            cache_row = (
                CachedStructureName.objects.filter(structure_id=inspect_id)
                .values("structure_id", "name", "last_resolved")
                .first()
            )

            is_int32_public = inspect_id > 0 and inspect_id <= 2_147_483_647
            is_negative_managed = inspect_id < 0
            cached_name = str(cache_row.get("name") or "") if cache_row else ""
            cache_is_placeholder = bool(
                cached_name and cached_name.startswith(PLACEHOLDER_PREFIX)
            )
            cache_last = cache_row.get("last_resolved") if cache_row else None

            placeholder_ttl = (
                PUBLIC_ID_PLACEHOLDER_TTL
                if is_int32_public and not is_negative_managed
                else STRUCTURE_PLACEHOLDER_TTL
            )

            stale_by_policy = False
            if cache_last:
                if cache_is_placeholder:
                    stale_by_policy = (now - cache_last) >= placeholder_ttl
                else:
                    stale_by_policy = (now - cache_last) >= timedelta(
                        hours=STRUCTURE_NAME_STALE_HOURS
                    )

            public_name = ""
            public_lookup_error = ""
            if is_int32_public and not is_negative_managed:
                try:
                    public_name = str(
                        (shared_client.resolve_ids_to_names([inspect_id]) or {}).get(
                            inspect_id, ""
                        )
                        or ""
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    public_lookup_error = str(exc)

            probe_name = ""
            probe_duration_ms = 0
            probe_is_placeholder = False
            probe_error = ""
            if inspect_probe_requested:
                started = perf_counter()
                try:
                    probe_name = str(
                        resolve_location_name(
                            inspect_id,
                            force_refresh=True,
                            allow_public=True,
                        )
                        or ""
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    probe_error = str(exc)
                finally:
                    probe_duration_ms = int((perf_counter() - started) * 1000)
                probe_is_placeholder = bool(
                    probe_name and probe_name.startswith(PLACEHOLDER_PREFIX)
                )

            diagnosis: list[str] = []
            if not cache_row:
                diagnosis.append("Missing from CachedStructureName.")
            elif cache_is_placeholder:
                diagnosis.append("Present as placeholder in CachedStructureName.")
            else:
                diagnosis.append("Present with resolved name in CachedStructureName.")

            if is_int32_public:
                diagnosis.append(
                    "Public int32 ID: should resolve through /universe/names/ without auth token."
                )
            if stale_by_policy:
                diagnosis.append("Entry is stale according to current TTL policy.")

            if cache_is_placeholder and public_name:
                diagnosis.append(
                    "Cache still has a placeholder while a public name is currently available."
                )
            elif cache_is_placeholder and not public_name and not public_lookup_error:
                diagnosis.append(
                    "Placeholder is consistent with no immediate public resolution."
                )

            inspect_data = {
                "structure_id": inspect_id,
                "is_int32_public": is_int32_public,
                "is_negative_managed": is_negative_managed,
                "cache_row": cache_row,
                "cache_name": cached_name,
                "cache_is_placeholder": cache_is_placeholder,
                "cache_last_resolved": cache_last,
                "stale_by_policy": stale_by_policy,
                "placeholder_ttl_minutes": int(placeholder_ttl.total_seconds() // 60),
                "public_name": public_name,
                "public_lookup_error": public_lookup_error,
                "probe_requested": inspect_probe_requested,
                "probe_name": probe_name,
                "probe_is_placeholder": probe_is_placeholder,
                "probe_duration_ms": probe_duration_ms,
                "probe_error": probe_error,
                "diagnosis": diagnosis,
            }

    context = {
        "generated_at": now,
        "structure_cutoff": structure_cutoff,
        "placeholder_cutoff": placeholder_cutoff,
        "one_hour_ago": one_hour_ago,
        "six_hours_ago": six_hours_ago,
        "twenty_four_hours_ago": twenty_four_hours_ago,
        "structure_metrics": structure_metrics,
        "resolver_kpis": resolver_kpis,
        "corp_assets_metrics": corp_assets_metrics,
        "char_assets_metrics": char_assets_metrics,
        "blueprint_metrics": blueprint_metrics,
        "blueprint_location_cache_metrics": blueprint_location_cache_metrics,
        "jobs_metrics": jobs_metrics,
        "job_status_breakdown": job_status_breakdown,
        "blueprint_kind_breakdown": blueprint_kind_breakdown,
        "copy_request_metrics": copy_request_metrics,
        "offer_status_breakdown": offer_status_breakdown,
        "chat_metrics": chat_metrics,
        "copy_message_total": copy_message_total,
        "onboarding_metrics": onboarding_metrics,
        "character_settings_metrics": character_settings_metrics,
        "character_scope_breakdown": character_scope_breakdown,
        "corp_sharing_metrics": corp_sharing_metrics,
        "corp_scope_breakdown": corp_scope_breakdown,
        "digest_metrics": digest_metrics,
        "webhook_metrics": webhook_metrics,
        "webhook_messages_metrics": webhook_messages_metrics,
        "simulation_metrics": simulation_metrics,
        "production_config_metrics": production_config_metrics,
        "material_settings_enabled": bool(
            getattr(material_settings, "is_enabled", False)
        ),
        "material_config_metrics": material_config_metrics,
        "material_stock_metrics": material_stock_metrics,
        "sell_order_metrics": sell_order_metrics,
        "sell_status_breakdown": sell_status_breakdown,
        "buy_order_metrics": buy_order_metrics,
        "buy_status_breakdown": buy_status_breakdown,
        "transaction_metrics": transaction_metrics,
        "transaction_type_breakdown": transaction_type_breakdown,
        "contracts_metrics": contracts_metrics,
        "contract_status_breakdown": contract_status_breakdown,
        "sde_metrics": sde_metrics,
        "sde_state": sde_state,
        "token_metrics": token_metrics,
        "top_corporations_assets": top_corporations_assets,
        "top_characters_assets": top_characters_assets,
        "oldest_placeholders": oldest_placeholders,
        "stale_resolved_rows": stale_resolved_rows,
        "recent_resolved": recent_resolved,
        "alerts": alerts,
        "recommended_actions": list(dict.fromkeys(recommended_actions)),
        "inspect_input": inspect_input,
        "inspect_probe_requested": inspect_probe_requested,
        "inspect_data": inspect_data,
        "inspect_error": inspect_error,
        "queue_result": queue_result,
        "queue_error": queue_error,
        "active_tab": active_tab,
        "user_search_query": user_search_query,
        "user_search_results": user_search_results,
        "selected_user_id": selected_user_id,
        "selected_user": selected_user,
        "user_focus_metrics": user_focus_metrics,
    }

    if str(request.GET.get("format", "")).lower() == "json":
        return JsonResponse(context)

    return render(request, "indy_hub/debug_health.html", context)
