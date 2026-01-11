"""Celery tasks package for indy_hub.

Celery's Django autodiscovery imports the app's ``tasks`` module (or package).
Because this is a package with multiple submodules, we need to import those
submodules so their ``@shared_task`` decorators are registered.

However, importing task submodules too early (before Django has initialized the
app registry) can raise errors during test discovery or other contexts.
"""


def _import_task_submodules() -> None:
    """Import task submodules when Django is ready.

    This keeps imports safe during environments where Django isn't initialized
    yet (e.g. module discovery), while still registering Celery tasks when
    running under a fully configured Django/Celery process.
    """

    # Django
    from django.apps import apps

    # During AppConfig.ready(), Django has loaded apps and models, but the global
    # registry flag `apps.ready` is only set to True after *all* apps have run
    # their ready() methods. We only require apps + models to be ready here.
    if not (apps.apps_ready and apps.models_ready):
        return

    # Import task submodules so their @shared_task are registered
    from . import industry  # noqa: F401
    from . import location  # noqa: F401
    from . import material_exchange  # noqa: F401
    from . import material_exchange_contracts  # noqa: F401
    from . import notifications  # noqa: F401
    from . import user  # noqa: F401


def ensure_task_submodules_imported() -> None:
    """Ensure all indy_hub task submodules are imported.

    This is safe to call multiple times and is intended to be called from
    AppConfig.ready() to handle cases where ``indy_hub.tasks`` was imported
    before Django finished initializing.
    """

    _import_task_submodules()


try:
    ensure_task_submodules_imported()
except Exception:
    # Keep this package importable even if Django isn't initialized yet.
    # Submodules will be imported later when Django is ready.
    pass


# Import the setup function from the main tasks module
def setup_periodic_tasks():
    """Setup periodic tasks for IndyHub module."""
    # Standard Library
    import json
    import logging

    try:
        # Third Party
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        # AA Example App
        from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE
    except ImportError:
        return  # django_celery_beat is not installed

    for name, conf in INDY_HUB_BEAT_SCHEDULE.items():
        schedule = conf["schedule"]
        if hasattr(schedule, "_orig_minute"):  # crontab
            crontabs = CrontabSchedule.objects.filter(
                minute=str(schedule._orig_minute),
                hour=str(schedule._orig_hour),
                day_of_week=str(schedule._orig_day_of_week),
                day_of_month=str(schedule._orig_day_of_month),
                month_of_year=str(schedule._orig_month_of_year),
            )
            if crontabs.exists():
                crontab = crontabs.first()
            else:
                crontab = CrontabSchedule.objects.create(
                    minute=str(schedule._orig_minute),
                    hour=str(schedule._orig_hour),
                    day_of_week=str(schedule._orig_day_of_week),
                    day_of_month=str(schedule._orig_day_of_month),
                    month_of_year=str(schedule._orig_month_of_year),
                )
            PeriodicTask.objects.update_or_create(
                name=name,
                defaults={
                    "task": conf["task"],
                    "crontab": crontab,
                    "interval": None,
                    "args": json.dumps([]),
                    "enabled": True,
                },
            )
    logging.getLogger(__name__).info("IndyHub cron tasks registered.")

    # Clean up any legacy task entries that are no longer defined
    removed, _ = PeriodicTask.objects.filter(
        name="indy-hub-notify-completed-jobs"
    ).delete()
    if removed:
        logging.getLogger(__name__).info(
            "Removed legacy periodic task indy-hub-notify-completed-jobs"
        )


# ...import additional tasks here if needed...
