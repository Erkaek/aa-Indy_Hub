"""Celery tasks related to ESI locations and structures."""

from __future__ import annotations

# Standard Library
import logging

# Third Party
from bravado.exception import HTTPBadGateway, HTTPGatewayTimeout, HTTPServiceUnavailable
from celery import group, shared_task

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


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    time_limit=300,
    soft_time_limit=280,
)
def refresh_multiple_structure_locations(structure_ids):
    """
    Refresh multiple structure locations in parallel using Celery group.
    Reduces overhead by submitting all refreshes at once instead of individually.

    This is a helper that uses Celery's group() to parallelize work.

    Example:
        # Instead of calling refresh_structure_location.delay(id1) then .delay(id2)
        # Call this once with both IDs
        refresh_multiple_structure_locations([id1, id2, id3])

    Args:
        structure_ids: List of structure IDs to refresh in parallel
    """
    if not structure_ids:
        logger.warning("No structure IDs provided to batch refresh")
        return {"total": 0, "results": []}

    # Normalize and deduplicate
    structure_ids = list({int(sid) for sid in structure_ids if sid})

    if not structure_ids:
        logger.warning("No valid structure IDs after normalization")
        return {"total": 0, "results": []}

    logger.info(
        "Queueing parallel refresh for %d structures: %s",
        len(structure_ids),
        structure_ids,
    )

    # Create group of refresh tasks for parallel execution
    job = group([refresh_structure_location.s(sid) for sid in structure_ids])

    # Execute group and wait for results
    result = job.apply_async()

    return {
        "total": len(structure_ids),
        "group_id": str(result.id),
        "results": structure_ids,
    }
