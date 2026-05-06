"""Shared helpers for Material Exchange user asset refresh progress."""

# Django
from django.core.cache import cache
from django.utils import timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)

SELL_ASSETS_REFRESH_PROGRESS_TTL_SECONDS = 10 * 60
SELL_ASSETS_REFRESH_STALE_PROGRESS_SECONDS = 180


def material_exchange_sell_assets_progress_key(user_id: int) -> str:
    return f"indy_hub:material_exchange:sell_assets_refresh:{int(user_id)}"


def _default_sell_assets_refresh_state() -> dict:
    return {
        "running": False,
        "finished": False,
        "error": None,
        "total": 0,
        "done": 0,
        "failed": 0,
    }


def _retry_after_minutes(cooldown_until) -> int:
    try:
        retry_seconds = max(0, int(float(cooldown_until) - timezone.now().timestamp()))
    except (TypeError, ValueError):
        from ..tasks.material_exchange import ESI_DOWN_COOLDOWN_SECONDS

        retry_seconds = int(ESI_DOWN_COOLDOWN_SECONDS)
    return int((retry_seconds + 59) // 60)


def _mark_stale_progress_if_needed(state: dict, *, progress_key: str) -> dict:
    if not state.get("running"):
        return state

    try:
        started_at = float(state.get("started_at") or 0)
        last_progress_at = float(state.get("last_progress_at") or started_at or 0)
        elapsed = timezone.now().timestamp() - last_progress_at
    except (TypeError, ValueError):
        elapsed = 0
    if not state.get("started_at") and not state.get("last_progress_at"):
        elapsed = 999999
    if elapsed <= SELL_ASSETS_REFRESH_STALE_PROGRESS_SECONDS:
        return state

    state.update({"running": False, "finished": True, "error": "timeout"})
    cache.set(progress_key, state, SELL_ASSETS_REFRESH_PROGRESS_TTL_SECONDS)
    return state


def get_sell_assets_refresh_progress(user_id: int) -> dict:
    progress_key = material_exchange_sell_assets_progress_key(int(user_id))
    state = cache.get(progress_key) or _default_sell_assets_refresh_state()
    return _mark_stale_progress_if_needed(state, progress_key=progress_key)


def ensure_sell_assets_refresh_started(user, *, log_context: str = "asset") -> dict:
    """Start an async user asset refresh when needed and return progress state."""

    from ..tasks.material_exchange import (
        me_sell_assets_esi_cooldown_key,
        refresh_material_exchange_sell_user_assets,
    )

    user_id = int(user.id)
    progress_key = material_exchange_sell_assets_progress_key(user_id)
    state = cache.get(progress_key) or {}

    cooldown_until = cache.get(me_sell_assets_esi_cooldown_key(user_id))
    if cooldown_until:
        state = {
            "running": False,
            "finished": True,
            "error": "esi_down",
            "retry_after_minutes": _retry_after_minutes(cooldown_until),
        }
        cache.set(progress_key, state, SELL_ASSETS_REFRESH_PROGRESS_TTL_SECONDS)
        return state

    state = _mark_stale_progress_if_needed(state, progress_key=progress_key)
    if state.get("running"):
        return state

    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership
        from esi.models import Token

        total = int(
            CharacterOwnership.objects.filter(user=user)
            .values_list("character__character_id", flat=True)
            .distinct()
            .count()
        )
        has_assets_token = (
            Token.objects.filter(user=user)
            .require_scopes(["esi-assets.read_assets.v1"])
            .require_valid()
            .exists()
        )
    except Exception:
        total = 0
        has_assets_token = False

    if total > 0 and not has_assets_token:
        state = {
            "running": False,
            "finished": True,
            "error": "missing_assets_scope",
            "total": total,
            "done": 0,
            "failed": 0,
        }
        cache.set(progress_key, state, SELL_ASSETS_REFRESH_PROGRESS_TTL_SECONDS)
        return state

    started_at = timezone.now().timestamp()
    state = {
        "running": True,
        "finished": False,
        "error": None,
        "total": total,
        "done": 0,
        "failed": 0,
        "started_at": started_at,
        "last_progress_at": started_at,
    }
    cache.set(progress_key, state, SELL_ASSETS_REFRESH_PROGRESS_TTL_SECONDS)

    try:
        task_result = refresh_material_exchange_sell_user_assets.delay(user_id)
        logger.info(
            "Started %s refresh task for user %s (task_id=%s)",
            log_context,
            user_id,
            task_result.id,
        )
    except Exception as exc:
        logger.error(
            "Failed to start %s refresh task for user %s: %s",
            log_context,
            user_id,
            exc,
            exc_info=True,
        )
        state.update({"running": False, "finished": True, "error": "task_start_failed"})
        cache.set(progress_key, state, SELL_ASSETS_REFRESH_PROGRESS_TTL_SECONDS)

    return state
