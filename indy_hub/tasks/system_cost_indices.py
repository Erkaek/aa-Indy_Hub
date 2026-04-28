"""Celery tasks for industry system cost index synchronization."""

from __future__ import annotations

# Third Party
from celery import shared_task

# Django
from django.core.cache import cache

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.services.esi_client import (
    ESIClientError,
    ESIRateLimitError,
    get_retry_after_seconds,
)
from indy_hub.services.system_cost_indices import sync_system_cost_indices

logger = get_extension_logger(__name__)

_SYNC_LOCK_KEY = "indy_hub:sync_system_cost_indices:lock"
_SYNC_LOCK_TTL = 30 * 60  # 30 minutes


@shared_task(bind=True, max_retries=3)
def sync_industry_system_cost_indices(
    self,
    *,
    force_refresh: bool = True,
) -> dict[str, int | str]:
    """Refresh public industry system cost indices from ESI."""
    if not cache.add(_SYNC_LOCK_KEY, 1, _SYNC_LOCK_TTL):
        logger.info(
            "Skipping sync_industry_system_cost_indices: another run is in progress"
        )
        return {"status": "skipped", "reason": "locked"}
    try:
        try:
            summary = sync_system_cost_indices(force_refresh=force_refresh)
        except ESIRateLimitError as exc:
            delay = get_retry_after_seconds(exc)
            logger.warning(
                "ESI rate limit hit while syncing industry system cost indices; retrying in %ss",
                delay,
            )
            raise self.retry(countdown=delay, exc=exc)
        except ESIClientError as exc:
            logger.warning("Failed to sync industry system cost indices: %s", exc)
            return {"status": "failed", "reason": str(exc)}

        logger.info(
            "Industry system cost indices synced: systems=%s entries=%s created=%s updated=%s unchanged=%s",
            summary["systems"],
            summary["entries_seen"],
            summary["created"],
            summary["updated"],
            summary["unchanged"],
        )
        return {"status": "ok", **summary}
    finally:
        cache.delete(_SYNC_LOCK_KEY)
