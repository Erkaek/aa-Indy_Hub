# Django
from django.db import migrations


def setup_periodic_tasks(apps, schema_editor):
    """Ensure IndyHub periodic tasks are synchronized after migration."""
    try:
        from indy_hub.tasks import setup_periodic_tasks as _setup_periodic_tasks

        _setup_periodic_tasks()
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failed to setup IndyHub periodic tasks during migration."
        )


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("indy_hub", "0082_character_online_status"),
    ]

    operations = [
        migrations.RunPython(setup_periodic_tasks, reverse_code=noop),
    ]
