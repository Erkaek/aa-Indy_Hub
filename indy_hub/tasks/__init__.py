# Package marker for indy_hub.tasks

from .industry import (
    update_blueprints_for_user, 
    update_industry_jobs_for_user, 
    notify_completed_jobs,
    update_all_blueprints,
    update_all_industry_jobs,
    cleanup_old_jobs,
    update_type_names
)

from .user import (
    cleanup_inactive_user_data,
    update_user_preferences_defaults,
    sync_user_character_names,
    generate_user_activity_report
)

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
            crontab, _ = CrontabSchedule.objects.get_or_create(
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

# ...importez ici d'autres tâches si besoin...
