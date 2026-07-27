"""Celery tasks for industry system cost index synchronization."""

from __future__ import annotations

# Standard Library
import uuid

# Third Party
from celery import shared_task

# Django
from django.core.cache import cache

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException

# AA Example App
from indy_hub.models import IndustrySystemCostIndex
from indy_hub.services.esi_client import (
    ESIClientError,
    get_rate_limit_reset_seconds,
)
from indy_hub.services.system_cost_indices import sync_system_cost_indices

logger = get_extension_logger(__name__)

_SYNC_LOCK_KEY = "indy_hub:sync_system_cost_indices:lock"
_SYNC_LOCK_TTL = 30 * 60  # 30 minutes
_TRANQUILITY_RETRY_DELAY = 10 * 60
_TRANQUILITY_MAX_ATTEMPTS = 3


def _is_tranquility_outage_cooldown(exc: Exception) -> bool:
    return "Global Tranquility outage cooldown active" in str(exc)


@shared_task(bind=True, max_retries=3)
def sync_industry_system_cost_indices(
    self,
    *,
    force_refresh: bool = True,
) -> dict[str, int | str]:
    """Refresh public industry system cost indices from ESI."""
    # Use a unique token as the lock value so that, if our run exceeds the
    # TTL and a second run later acquires the lock, our `finally` block
    # below does not delete *that* second run's lock.
    lock_token = uuid.uuid4().hex
    if not cache.add(_SYNC_LOCK_KEY, lock_token, _SYNC_LOCK_TTL):
        logger.info(
            "Skipping sync_industry_system_cost_indices: another run is in progress"
        )
        return {"status": "skipped", "reason": "locked"}
    try:
        try:
            effective_force_refresh = not IndustrySystemCostIndex.objects.exists()
            summary = sync_system_cost_indices(force_refresh=effective_force_refresh)
        except (ESIErrorLimitException, ESIBucketLimitException) as exc:
            if _is_tranquility_outage_cooldown(exc):
                attempt = int(getattr(self.request, "retries", 0)) + 1
                if attempt >= _TRANQUILITY_MAX_ATTEMPTS:
                    logger.warning(
                        "Failed to sync industry system cost indices after %s attempts; reason=tranquility",
                        attempt,
                    )
                    return {"status": "failed", "reason": "tranquility"}

                logger.warning(
                    "Tranquility outage cooldown hit while syncing industry system cost indices; retrying in %ss (attempt %s/%s)",
                    _TRANQUILITY_RETRY_DELAY,
                    attempt,
                    _TRANQUILITY_MAX_ATTEMPTS,
                )
                raise self.retry(countdown=_TRANQUILITY_RETRY_DELAY)

            delay = get_rate_limit_reset_seconds(exc)
            logger.warning(
                "ESI rate limit hit while syncing industry system cost indices; retrying in %ss",
                delay,
            )
            raise self.retry(countdown=delay)
        except ESIClientError as exc:
            logger.warning("Failed to sync industry system cost indices: %s", exc)
            return {"status": "failed", "reason": str(exc)}

        if summary["created"] == 0 and summary["updated"] == 0:
            logger.debug(
                "Industry system cost indices unchanged: systems=%s entries=%s unchanged=%s",
                summary["systems"],
                summary["entries_seen"],
                summary["unchanged"],
            )
        else:
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
        # Only release the lock if it still belongs to us; if the TTL
        # elapsed and another worker grabbed it we must leave their lock
        # intact.
        if cache.get(_SYNC_LOCK_KEY) == lock_token:
            cache.delete(_SYNC_LOCK_KEY)
