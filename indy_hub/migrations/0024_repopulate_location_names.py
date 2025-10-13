"""Placeholder migration for location name population.

Population logic now runs via the asynchronous Celery task
``indy_hub.tasks.industry.populate_location_names_async`` or the
``populate_location_names`` service helper. This migration is retained
solely for schema history compatibility.
"""

from __future__ import annotations

# Django
from django.db import migrations


def _noop(apps, schema_editor):
    """No-op placeholder for historical migration."""


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0023_add_location_names_drop_facility_fields"),
    ]

    operations = [
        migrations.RunPython(_noop, migrations.RunPython.noop),
    ]
