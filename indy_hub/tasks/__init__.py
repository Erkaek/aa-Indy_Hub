# Package marker for indy_hub.tasks


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
        return  # django_celery_beat n'est pas installé

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


# ...importez ici d'autres tâches si besoin...
