"""Middleware for Indy Hub usage tracking."""

from __future__ import annotations

# Django
from django.utils.deprecation import MiddlewareMixin

# AA Example App
# Local
from indy_hub.services.user_usage import (
    is_usage_tracking_path_excluded,
    track_indy_hub_usage_for_user,
)


class IndyHubUsageTrackingMiddleware(MiddlewareMixin):
    """Record authenticated page views once per request."""

    _EXCLUDED_PREFIXES = ("/static/", "/media/")

    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(request, "_indy_hub_usage_tracked", False):
            return None

        if request.method != "GET":
            return None

        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return None

        path = str(getattr(request, "path", "") or "").strip()
        if (
            not path
            or path.startswith(self._EXCLUDED_PREFIXES)
            or is_usage_tracking_path_excluded(path)
        ):
            return None

        resolver_match = getattr(request, "resolver_match", None)
        app_names = tuple(getattr(resolver_match, "app_names", ()) or ())
        if "indy_hub" in app_names:
            page_label = (
                getattr(resolver_match, "url_name", None)
                or getattr(resolver_match, "view_name", None)
                or getattr(resolver_match, "route", None)
                or path
            )
            page_label = str(page_label).replace("_", " ").strip() or path
        else:
            page_label = path

        track_indy_hub_usage_for_user(
            user,
            page_path=path,
            page_label=page_label,
        )
        request._indy_hub_usage_tracked = True
        return None
