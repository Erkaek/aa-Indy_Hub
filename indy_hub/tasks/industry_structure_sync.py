"""Celery tasks for keeping synced industry structures up to date."""

from __future__ import annotations

# Third Party
from celery import shared_task

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.services.industry_structure_sync import sync_persisted_industry_structures

logger = get_extension_logger(__name__)


@shared_task
def sync_persisted_industry_structure_registry(
    *,
    force_refresh: bool = True,
) -> dict[str, int | str | list[str]]:
    summary = sync_persisted_industry_structures(force_refresh=force_refresh)
    logger.info(
        "Automatic industry structure sync complete: corporations=%s created=%s updated=%s unchanged=%s deleted=%s skipped_unsupported=%s errors=%s",
        summary["corporations"],
        summary["created"],
        summary["updated"],
        summary["unchanged"],
        summary.get("deleted", 0),
        summary.get("skipped_unsupported", 0),
        len(summary["errors"]),
    )
    return {"status": "ok", **summary}
