# Django
from django.db import migrations


def update_periodic_tasks(apps, schema_editor):
    """Re-apply IndyHub periodic task schedules after schedule changes."""
    try:
        # AA Example App
        from indy_hub.tasks import setup_periodic_tasks as _setup_periodic_tasks

        _setup_periodic_tasks()
    except Exception:
        # Standard Library
        import logging

        logging.getLogger(__name__).exception(
            "Failed to update IndyHub periodic tasks during migration."
        )


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0105_remove_project_item_unique_type_category"),
    ]

    operations = [
        migrations.RunPython(update_periodic_tasks, noop),
    ]
