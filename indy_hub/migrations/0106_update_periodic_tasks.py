# Django
from django.db import migrations


def update_periodic_tasks(apps, schema_editor):
    """Re-apply IndyHub periodic task schedules after schedule changes."""
    try:
        # AA Example App
        from indy_hub.tasks import setup_periodic_tasks as _setup_periodic_tasks

        _setup_periodic_tasks()
    except ImportError:
        # Standard Library
        import logging

        logging.getLogger(__name__).warning(
            "IndyHub tasks module not available during migration; periodic tasks not updated. "
            "Run 'python manage.py shell' and call setup_periodic_tasks() manually if needed."
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
