"""
Celery periodic task configuration for indy_hub module
"""

# Third Party
from celery.schedules import crontab

# Periodic task configuration for indy_hub
INDY_HUB_BEAT_SCHEDULE = {
    "indy-hub-update-all-blueprints": {
        "task": "indy_hub.tasks.industry.update_all_blueprints",
        "schedule": crontab(hour=3, minute=30),  # Daily at 03:30
        "options": {"priority": 7},  # Low priority for background updates
    },
    "indy-hub-update-all-industry-jobs": {
        "task": "indy_hub.tasks.industry.update_all_industry_jobs",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
        "options": {"priority": 7},  # Slightly higher priority for jobs
    },
    "indy-hub-cleanup-old-jobs": {
        "task": "indy_hub.tasks.industry.cleanup_old_jobs",
        "schedule": crontab(hour=2, minute=0),  # Daily at 02:00
        "options": {"priority": 8},  # Low priority for cleanup
    },
    "indy-hub-update-type-names": {
        "task": "indy_hub.tasks.industry.update_type_names",
        "schedule": crontab(hour=3, minute=0),  # Daily at 03:00
        "options": {"priority": 8},  # Low priority for caching
    },
}
