# indy_hub/decorators.py
# Standard Library
from functools import wraps

# Django
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

# AA Example App
# Local
from indy_hub.services.user_usage import track_indy_hub_usage_once_per_request


def indy_hub_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("auth_login_user")
        if not request.user.has_perm("indy_hub.can_access_indy_hub"):
            return HttpResponseForbidden(
                "You do not have permission to access Indy Hub."
            )
        track_indy_hub_usage_once_per_request(request)
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def indy_hub_permission_required(permission_codename):
    """Ensure the logged-in user has the requested indy_hub permission."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("auth_login_user")
            full_codename = f"indy_hub.{permission_codename}"
            if not request.user.has_perm(full_codename):
                return HttpResponseForbidden(
                    "You do not have the required Indy Hub permission."
                )
            track_indy_hub_usage_once_per_request(request)
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
