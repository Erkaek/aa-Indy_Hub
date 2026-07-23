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
    error_count = len(summary["errors"])
    logger_method = logger.warning if error_count else logger.info
    logger_method(
        "Automatic industry structure sync complete: corporations=%s created=%s updated=%s unchanged=%s deleted=%s skipped_unsupported=%s skipped_forbidden=%s skipped_unusable_token=%s rate_limited=%s deferred=%s errors=%s",
        summary["corporations"],
        summary["created"],
        summary["updated"],
        summary["unchanged"],
        summary.get("deleted", 0),
        summary.get("skipped_unsupported", 0),
        summary.get("skipped_forbidden", 0),
        summary.get("skipped_unusable_token", 0),
        summary.get("rate_limited", 0),
        summary.get("deferred_due_to_rate_limit", 0),
        error_count,
    )
    if summary.get("forbidden_samples"):
        logger.info(
            "Structure sync 403 sample: %s", " | ".join(summary["forbidden_samples"])
        )
    if summary.get("unusable_token_samples"):
        logger.info(
            "Structure sync unusable-token sample: %s",
            " | ".join(summary["unusable_token_samples"]),
        )
    if summary.get("rate_limit_samples"):
        logger.info(
            "Structure sync rate-limit sample: %s",
            " | ".join(summary["rate_limit_samples"]),
        )
    return {"status": "ok", **summary}
