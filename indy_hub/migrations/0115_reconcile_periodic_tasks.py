# Standard Library
import logging

# Django
from django.db import migrations


def reconcile_periodic_tasks(apps, schema_editor):
    """Synchronize schedules added since the previous bootstrap migration."""

    try:
        # AA Example App
        from indy_hub.tasks import setup_periodic_tasks

        setup_periodic_tasks()
    except Exception:
        # Keep fresh installs viable when django-celery-beat's tables have not
        # been migrated yet. post_migrate and Beat startup both retry safely.
        logging.getLogger(__name__).exception(
            "Failed to reconcile Indy Hub periodic tasks during migration 0115. "
            "The schedule will be retried after migrate and at Celery Beat startup."
        )


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0114_indyhubusagedailyrollup_and_sync_cursor"),
    ]

    operations = [
        migrations.RunPython(reconcile_periodic_tasks, noop),
    ]
