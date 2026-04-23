from __future__ import annotations

# Django
from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from ..services.corporation_blueprint_visibility import (
    can_view_corporation_blueprints,
    can_view_corporation_jobs,
)


def build_nav_context(
    user,
    *,
    active_tab: str | None = None,
    can_manage_corp: bool | None = None,
    can_view_corporation_bp: bool | None = None,
    can_view_corporation_jobs_flag: bool | None = None,
    show_corporation_workflow_jobs: bool | None = None,
    can_access_indy_hub: bool | None = None,
    material_hub_enabled: bool | None = None,
) -> dict[str, object]:
    """Return navbar context entries for templates extending the Indy Hub base."""

    def _build_blueprint_sharing_badges() -> dict[str, int]:
        from ..models import Blueprint, BlueprintCopyRequest

        my_request_counts = BlueprintCopyRequest.objects.filter(
            requested_by_id=user.id
        ).aggregate(
            open_count=Count("id", filter=Q(fulfilled=False)),
            pending_delivery_count=Count(
                "id", filter=Q(fulfilled=True, delivered=False)
            ),
        )
        my_requests_total = int(my_request_counts.get("open_count") or 0) + int(
            my_request_counts.get("pending_delivery_count") or 0
        )

        provider_blueprints = Blueprint.objects.filter(
            owner_user_id=user.id,
            bp_type__in=[
                Blueprint.BPType.ORIGINAL,
                Blueprint.BPType.REACTION,
            ],
            type_id=OuterRef("type_id"),
            material_efficiency=OuterRef("material_efficiency"),
            time_efficiency=OuterRef("time_efficiency"),
        )

        fulfill_count = (
            BlueprintCopyRequest.objects.annotate(
                can_fulfill=Exists(provider_blueprints)
            )
            .filter(can_fulfill=True)
            .filter(
                Q(fulfilled=False)
                | Q(
                    fulfilled=True,
                    delivered=False,
                    offers__owner_id=user.id,
                    offers__status="accepted",
                    offers__accepted_by_buyer=True,
                    offers__accepted_by_seller=True,
                )
            )
            .exclude(requested_by_id=user.id)
            .exclude(
                offers__owner_id=user.id,
                offers__status="rejected",
            )
            .distinct()
            .count()
        )

        return {
            "blueprint_sharing_nav_badge_count": my_requests_total + int(fulfill_count),
            "blueprint_sharing_my_requests_badge_count": my_requests_total,
            "blueprint_sharing_fulfill_badge_count": int(fulfill_count),
        }

    def _build_material_hub_badges() -> dict[str, int]:
        from ..models import MaterialExchangeBuyOrder, MaterialExchangeSellOrder

        closed_statuses = ["completed", "rejected", "cancelled"]
        my_sell_orders = (
            MaterialExchangeSellOrder.objects.filter(seller_id=user.id)
            .exclude(status__in=closed_statuses)
            .count()
        )
        my_buy_orders = (
            MaterialExchangeBuyOrder.objects.filter(buyer_id=user.id)
            .exclude(status__in=closed_statuses)
            .count()
        )
        return {
            "material_hub_nav_badge_count": int(my_sell_orders) + int(my_buy_orders),
            "material_hub_my_orders_badge_count": int(my_sell_orders)
            + int(my_buy_orders),
            "material_hub_my_sell_orders_badge_count": int(my_sell_orders),
            "material_hub_my_buy_orders_badge_count": int(my_buy_orders),
        }

    if can_manage_corp is None:
        can_manage_corp = user.has_perm("indy_hub.can_manage_corp_bp_requests")

    if can_access_indy_hub is None:
        can_access_indy_hub = user.has_perm("indy_hub.can_access_indy_hub")

    if can_view_corporation_bp is None:
        can_view_corporation_bp = can_view_corporation_blueprints(user)

    if can_view_corporation_jobs_flag is None:
        can_view_corporation_jobs_flag = can_view_corporation_jobs(user)

    if show_corporation_workflow_jobs is None:
        show_corporation_workflow_jobs = bool(
            getattr(user, "is_authenticated", False)
            and CharacterOwnership.objects.filter(user=user)
            .exclude(character__corporation_id__isnull=True)
            .exists()
        )

    if material_hub_enabled is None:
        try:
            from ..models import MaterialExchangeSettings

            material_hub_enabled = MaterialExchangeSettings.get_solo().is_enabled
        except Exception:
            material_hub_enabled = True

    # Primary sections
    overview_url = reverse("indy_hub:index")
    blueprints_url = reverse("indy_hub:all_bp_list")
    blueprint_sharing_url = reverse("indy_hub:bp_copy_request_page")
    material_hub_url = reverse("indy_hub:material_exchange_index")
    industry_url = reverse("indy_hub:personnal_job_list")
    esi_url = reverse("indy_hub:esi_hub")
    settings_url = reverse("indy_hub:settings_hub")

    # Legacy dashboard URLs (still used by some templates for "Back" buttons)
    personal_url = reverse("indy_hub:index")

    active_tab = (active_tab or "").strip() or None

    overview_class = ""
    blueprints_class = ""
    blueprint_sharing_class = ""
    material_hub_class = ""
    industry_class = ""
    esi_class = ""
    settings_class = ""

    if active_tab in {
        "overview",
        "blueprints",
        "blueprint_sharing",
        "material_hub",
        "industry",
        "esi",
        "settings",
    }:
        if active_tab == "overview":
            overview_class = "active fw-semibold"
        elif active_tab == "blueprints":
            blueprints_class = "active fw-semibold"
        elif active_tab == "blueprint_sharing":
            blueprint_sharing_class = "active fw-semibold"
        elif active_tab == "material_hub":
            material_hub_class = "active fw-semibold"
        elif active_tab == "industry":
            industry_class = "active fw-semibold"
        elif active_tab == "esi":
            esi_class = "active fw-semibold"
        elif active_tab == "settings":
            settings_class = "active fw-semibold"

    material_hub_nav_url = material_hub_url if material_hub_enabled else None

    context: dict[str, object] = {
        # New top-level sections
        "overview_nav_url": overview_url,
        "overview_nav_class": overview_class,
        "blueprints_nav_url": blueprints_url,
        "blueprints_nav_class": blueprints_class,
        "blueprint_sharing_nav_url": blueprint_sharing_url,
        "blueprint_sharing_nav_class": blueprint_sharing_class,
        "material_hub_nav_url": material_hub_nav_url,
        "material_hub_nav_class": material_hub_class,
        "industry_nav_url": industry_url,
        "industry_nav_class": industry_class,
        "esi_nav_url": esi_url,
        "esi_nav_class": esi_class,
        "settings_nav_url": settings_url,
        "settings_nav_class": settings_class,
        # Permission flags for dropdowns
        "can_manage_corp_bp_requests": can_manage_corp,
        "can_view_corporation_blueprints": can_view_corporation_bp,
        "can_view_corporation_jobs": can_view_corporation_jobs_flag,
        "show_corporation_workflow_jobs": show_corporation_workflow_jobs,
        "can_access_indy_hub": can_access_indy_hub,
        "material_hub_enabled": material_hub_enabled,
        # Legacy keys (kept so we don't break older templates / buttons)
        "personal_nav_url": personal_url,
        "personal_nav_class": "",
    }

    if getattr(user, "is_authenticated", False) and can_access_indy_hub:
        context.update(_build_blueprint_sharing_badges())
        if material_hub_nav_url:
            context.update(_build_material_hub_badges())

    if active_tab:
        context["active_tab"] = active_tab

    return context
