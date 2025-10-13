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
    # AA Example App
    from indy_hub.services.location_population import populate_location_names

    summary = populate_location_names(logger_override=logger)
    logger.info(
        "populate_location_names executed during migration: %s blueprints, %s jobs, %s locations",
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
