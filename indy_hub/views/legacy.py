"""Navigation shared by the retired single-blueprint simulation routes."""

# Django
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _


def redirect_legacy_simulation(request):
    """Keep old links informative without interpreting their historical IDs."""
    messages.info(
        request,
        _(
            "Legacy single-blueprint simulations were removed. Use craft tables instead."
        ),
    )
    return redirect("indy_hub:production_simulations_list")
