# indy_hub/decorators.py
# Standard Library
from functools import wraps

# Django
from django.contrib import messages
from django.shortcuts import redirect

# Alliance Auth
from esi.decorators import single_use_token as esi_single_use_token
from esi.decorators import token_required as esi_token_required
from esi.decorators import tokens_required as esi_tokens_required


def _normalize_scopes(scopes):
    if scopes is None:
        return []
    if isinstance(scopes, str):
        return [scopes]
    return list(scopes)


def token_required(scopes=None, new=False):
    """Compatibility wrapper around django-esi's `token_required`."""
    return esi_token_required(scopes=_normalize_scopes(scopes), new=new)


def tokens_required(scopes=None, new=False):
    """Compatibility wrapper around django-esi's `tokens_required`."""
    return esi_tokens_required(scopes=_normalize_scopes(scopes), new=new)


def single_use_token(scopes=None, new=False):
    """Compatibility wrapper around django-esi's `single_use_token`."""
    return esi_single_use_token(scopes=_normalize_scopes(scopes), new=new)


STRUCTURE_SCOPE = "esi-universe.read_structures.v1"


def blueprints_token_required(view_func):
    """Decorator specifically for blueprint views."""
    return token_required(
        [
            "esi-characters.read_blueprints.v1",
            STRUCTURE_SCOPE,
        ]
    )(view_func)


def industry_jobs_token_required(view_func):
    """Decorator specifically for industry jobs views."""
    return token_required(
        [
            "esi-industry.read_character_jobs.v1",
            STRUCTURE_SCOPE,
        ]
    )(view_func)


def indy_hub_access_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("auth_login_user")
        if not request.user.has_perm("indy_hub.can_access_indy_hub"):
            messages.error(request, "You do not have permission to access Indy Hub.")
            return redirect("indy_hub:index")
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
                messages.error(
                    request, "You do not have the required Indy Hub permission."
                )
                return redirect("indy_hub:index")
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
