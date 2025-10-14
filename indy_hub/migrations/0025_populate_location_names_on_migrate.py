"""Ensure location names are refreshed automatically during migration."""

from __future__ import annotations

# Standard Library
import logging

# Django
from django.db import migrations

logger = logging.getLogger(__name__)


def _populate_location_names(apps, schema_editor):
    """Invoke the location name population routine when applying this migration."""

    # Import inside the function so Django's migration loader can resolve dependencies.
    # Django
    from django.conf import settings

    # AA Example App
    from indy_hub.services.location_population import populate_location_names

    try:
        # AA Example App
        from indy_hub.tasks.industry import populate_location_names_async
    except Exception:  # pragma: no cover - Celery not configured or task unavailable
        populate_location_names_async = None

    eager = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
    can_enqueue = populate_location_names_async is not None and not eager

    if can_enqueue:
        try:
            result = populate_location_names_async.delay()
        except Exception:  # pragma: no cover - Celery broker misconfigured
            logger.exception(
                "Unable to enqueue populate_location_names_async task; falling back to synchronous execution during migration.",
            )
        else:
            logger.info(
                "populate_location_names_async enqueued during migration (task id: %s)",
                getattr(result, "id", "<unknown>"),
            )
            return

    summary = populate_location_names(
        logger_override=logger,
        force_refresh=True,
        schedule_async=False,
    )
    logger.info(
        "populate_location_names executed inline during migration: %s blueprints, %s jobs, %s locations",
        summary.get("blueprints", 0),
        summary.get("jobs", 0),
        summary.get("locations", 0),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0024_repopulate_location_names"),
    ]

    operations = [
        migrations.RunPython(_populate_location_names, migrations.RunPython.noop),
    ]
