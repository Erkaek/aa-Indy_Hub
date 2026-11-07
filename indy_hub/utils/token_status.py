"""Cache keys and invalidation for token status across Indy Hub pages."""

# Standard Library
from datetime import timedelta

# Django
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

# Alliance Auth
from esi import app_settings as esi_app_settings


def apply_token_validity_filter(token_queryset, *, validate_tokens: bool):
    """Check live validity or recorded, renewable authorization for display."""
    if validate_tokens:
        return token_queryset.require_valid()

    max_age = timezone.now() - timedelta(
        seconds=esi_app_settings.ESI_TOKEN_VALID_DURATION
    )
    # Access expiry does not revoke a renewable authorization. Verify it on use,
    # without blocking display on an OAuth refresh.
    return token_queryset.filter(
        Q(created__gt=max_age) | (Q(refresh_token__isnull=False) & ~Q(refresh_token=""))
    )


def token_management_live_cache_key(user_id: int) -> str:
    return f"indy_hub:token_management_live:{int(user_id)}"


def corporation_scope_status_cache_key(user_id: int, page: str) -> str:
    return f"indy_hub:corp_scope_status:{page}:{int(user_id)}"


def invalidate_token_status_cache(*user_ids) -> None:
    keys = []
    for user_id in {int(value) for value in user_ids if value}:
        keys.append(token_management_live_cache_key(user_id))
        keys.extend(
            corporation_scope_status_cache_key(user_id, page)
            for page in ("dashboard", "settings")
        )
    if keys:
        cache.delete_many(keys)
