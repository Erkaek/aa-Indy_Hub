"""Celery tasks related to ESI locations and structures."""

from __future__ import annotations

# Standard Library
import logging

# Third Party
from bravado.exception import HTTPBadGateway, HTTPGatewayTimeout, HTTPServiceUnavailable
from celery import shared_task

# Alliance Auth
from allianceauth.services.tasks import QueueOnce

# AA Example App
# Indy Hub
from indy_hub.services.location_population import (
    DEFAULT_TASK_PRIORITY,
    populate_location_names,
)

logger = logging.getLogger(__name__)

_TASK_DEFAULT_KWARGS: dict[str, object] = {
    "time_limit": 300,
}

_TASK_ESI_KWARGS: dict[str, object] = {
    **_TASK_DEFAULT_KWARGS,
    **{
        "autoretry_for": (
            OSError,
            HTTPBadGateway,
            HTTPGatewayTimeout,
            HTTPServiceUnavailable,
        ),
        "retry_kwargs": {"max_retries": 3},
        "retry_backoff": 30,
    },
}


@shared_task(
    **{
        **_TASK_ESI_KWARGS,
        **{
            "bind": True,
            "base": QueueOnce,
            "once": {"keys": ["structure_id"], "graceful": True},
            "max_retries": None,
        },
    }
)
def refresh_structure_location(self, structure_id: int) -> dict[str, int]:
    """Re-run structure name resolution in the background."""

    logger.debug("Background task refreshing name for structure %s", structure_id)

    try:
        summary = populate_location_names(
            location_ids=[structure_id],
            force_refresh=True,
            schedule_async=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to refresh name for structure %s", structure_id)
        raise self.retry(exc=exc, countdown=DEFAULT_TASK_PRIORITY * 10) from exc

    logger.info(
        "Structure name updated (structure=%s, blueprints=%s, jobs=%s)",
        structure_id,
        summary.get("blueprints", 0),
        summary.get("jobs", 0),
    )
    return summary
