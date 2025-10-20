from __future__ import annotations

# Django
from django.urls import reverse


def build_nav_context(
    user, *, active_tab: str | None = "personal", can_manage_corp: bool | None = None
) -> dict[str, str | None]:
    """Return navbar context entries for templates extending the Indy Hub base."""

    if can_manage_corp is None:
        can_manage_corp = user.has_perm("indy_hub.can_manage_corporate_assets")

    personal_url = reverse("indy_hub:index")
    corporation_url = (
        reverse("indy_hub:corporation_dashboard") if can_manage_corp else None
    )

    personal_class = ""
    corporation_class = ""
    current_dashboard: str | None = None

    if active_tab in {"personal", "corporation"}:
        current_dashboard = active_tab
        if active_tab == "personal":
            personal_class = "active fw-semibold"
        elif active_tab == "corporation":
            corporation_class = "active fw-semibold"

    back_to_dashboard_url = (
        corporation_url
        if active_tab == "corporation" and corporation_url
        else personal_url
    )

    context: dict[str, str | None] = {
        "personal_nav_url": personal_url,
        "personal_nav_class": personal_class,
        "corporation_nav_url": corporation_url,
        "corporation_nav_class": corporation_class,
        "back_to_dashboard_url": back_to_dashboard_url,
    }

    if current_dashboard:
        context["current_dashboard"] = current_dashboard

    return context
