"""Hub/landing pages for top navigation tabs."""

from __future__ import annotations

# Django
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

# Local
from ..decorators import indy_hub_permission_required
from ..models import (
    MaterialExchangeConfig,
)
from .navigation import build_nav_context
from .user import _build_dashboard_context


@indy_hub_permission_required("can_access_indy_hub")
@login_required
def settings_hub(request):
    can_manage_corp = request.user.has_perm("indy_hub.can_manage_corp_bp_requests")
    can_manage_material_hub = request.user.has_perm("indy_hub.can_manage_material_hub")

    context = _build_dashboard_context(request)
    context.update(
        {
            "can_manage_material_hub": can_manage_material_hub,
            "can_manage_corp": can_manage_corp,
        }
    )

    # Material Exchange counters
    context["material_exchange_config_total"] = MaterialExchangeConfig.objects.count()
    context["material_exchange_config_active"] = MaterialExchangeConfig.objects.filter(
        is_active=True
    ).count()
    context["material_exchange_enabled"] = bool(
        context["material_exchange_config_active"]
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


@login_required
def test_darkly_theme(request):
    """Test page for darkly theme CSS overrides."""
    return render(request, "indy_hub/test_darkly_theme.html")
