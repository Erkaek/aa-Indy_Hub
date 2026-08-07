"""Middleware for Indy Hub usage tracking."""

from __future__ import annotations

# Django
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

# AA Example App
# Local
from indy_hub.services.user_usage import (
    is_usage_tracking_path_excluded,
    track_indy_hub_usage_once_per_request,
)


class IndyHubUsageTrackingMiddleware(MiddlewareMixin):
    """Record authenticated page views once per request.

    This middleware is intentionally opt-in and only tracks routes from an
    explicit app-name allowlist to avoid recording sensitive dynamic URL paths.
    """

    _EXCLUDED_PREFIXES = ("/static/", "/media/")

    @staticmethod
    def _is_enabled() -> bool:
        return bool(getattr(settings, "INDY_HUB_USAGE_MIDDLEWARE_ENABLED", False))

    @staticmethod
    def _allowed_app_names() -> set[str]:
        raw_value = getattr(
            settings,
            "INDY_HUB_USAGE_MIDDLEWARE_ALLOWED_APP_NAMES",
            ("indy_hub",),
        )
        if isinstance(raw_value, str):
            values = [raw_value]
        elif isinstance(raw_value, (list, tuple, set)):
            values = list(raw_value)
        else:
            values = ["indy_hub"]
        normalized = {str(value).strip() for value in values if str(value).strip()}
        return normalized or {"indy_hub"}

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not self._is_enabled():
            return None

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
        if resolver_match is None:
            return None

        app_names = {
            str(app_name).strip()
            for app_name in (getattr(resolver_match, "app_names", ()) or ())
            if str(app_name).strip()
        }
        if not app_names.intersection(self._allowed_app_names()):
            return None

        view_name = str(getattr(resolver_match, "view_name", None) or "").strip()
        if not view_name:
            return None

        track_indy_hub_usage_once_per_request(request)
        return None
