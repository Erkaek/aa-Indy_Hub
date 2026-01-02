from __future__ import annotations

# Django
from django.urls import reverse


def build_nav_context(
    user,
    *,
    active_tab: str | None = None,
    can_manage_corp: bool | None = None,
    can_manage_copy_requests: bool | None = None,
    material_hub_enabled: bool | None = None,
) -> dict[str, str | None]:
    """Return navbar context entries for templates extending the Indy Hub base."""

    if can_manage_corp is None:
        can_manage_corp = user.has_perm("indy_hub.can_manage_corporate_assets")

    if can_manage_copy_requests is None:
        can_manage_copy_requests = user.has_perm("indy_hub.can_manage_copy_requests")

    if material_hub_enabled is None:
        try:
            from ..models import MaterialExchangeConfig

            material_hub_enabled = MaterialExchangeConfig.objects.filter(
                is_active=True
            ).exists()
        except Exception:
            material_hub_enabled = False

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

    context: dict[str, str | None] = {
        # New top-level sections
        "overview_nav_url": overview_url,
        "overview_nav_class": overview_class,
        "blueprints_nav_url": blueprints_url,
        "blueprints_nav_class": blueprints_class,
        "blueprint_sharing_nav_url": blueprint_sharing_url,
        "blueprint_sharing_nav_class": blueprint_sharing_class,
        "material_hub_nav_url": material_hub_nav_url,
        "material_hub_nav_class": material_hub_class if material_hub_nav_url else "",
        "industry_nav_url": industry_url,
        "industry_nav_class": industry_class,
        "esi_nav_url": esi_url,
        "esi_nav_class": esi_class,
        "settings_nav_url": settings_url,
        "settings_nav_class": settings_class,
        # Permission flags for dropdowns
        "can_manage_corporate_assets": can_manage_corp,
        "can_manage_copy_requests": can_manage_copy_requests,
        "material_hub_enabled": material_hub_enabled,
        # Legacy keys (kept so we don't break older templates / buttons)
        "personal_nav_url": personal_url,
        "personal_nav_class": "",
    }

    if active_tab:
        context["active_tab"] = active_tab

    return context
