"""Hub/landing pages for top navigation tabs."""

from __future__ import annotations

# Django
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Local
from ..decorators import indy_hub_permission_required
from ..models import MaterialExchangeConfig, MaterialExchangeSettings
from .navigation import build_nav_context
from .user import _build_dashboard_context

logger = get_extension_logger(__name__)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def settings_hub(request):
    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    can_manage_material_hub = request.user.has_perm("indy_hub.can_manage_material_hub")

    logger.debug(
        "Settings hub accessed (user_id=%s, can_manage_corp=%s, can_manage_material_hub=%s)",
        request.user.id,
        can_manage_corp,
        can_manage_material_hub,
    )

    context = _build_dashboard_context(request)
    context.update(
        {
            "can_manage_material_hub": can_manage_material_hub,
            "can_manage_corp": can_manage_corp,
        }
    )

    # Material Exchange counters
    context["material_exchange_config_total"] = MaterialExchangeConfig.objects.count()
    context["material_exchange_enabled"] = (
        MaterialExchangeSettings.get_solo().is_enabled
    )
    context["material_exchange_config_active"] = (
        1
        if context["material_exchange_enabled"]
        and context["material_exchange_config_total"]
        else 0
    )

    logger.debug(
        "Material exchange configs (total=%s, active=%s)",
        context["material_exchange_config_total"],
        context["material_exchange_config_active"],
    )

    context.update(
        build_nav_context(
            request.user,
            active_tab="settings",
            can_manage_corp=can_manage_corp,
            material_hub_enabled=context["material_exchange_enabled"],
        )
    )
    context["page_title"] = _("Settings")

    return render(request, "indy_hub/settings/hub.html", context)


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def test_darkly_theme(request):
    """Test page for darkly theme CSS overrides."""
    logger.debug("Darkly theme test page accessed (user_id=%s)", request.user.id)
    return render(request, "indy_hub/test_darkly_theme.html")
