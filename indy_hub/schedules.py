"""
Celery periodic task configuration for indy_hub module
"""

# Third Party
from celery.schedules import crontab

# Configuration des tâches périodiques pour indy_hub
INDY_HUB_BEAT_SCHEDULE = {
    "indy-hub-update-all-blueprints": {
        "task": "indy_hub.tasks.industry.update_all_blueprints",
        "schedule": crontab(hour=3, minute=30),  # Quotidien à 03h30
        "options": {
            "priority": 7
        },  # Priorité basse pour les mises à jour en arrière-plan
    },
    "indy-hub-update-all-industry-jobs": {
        "task": "indy_hub.tasks.industry.update_all_industry_jobs",
        "schedule": crontab(minute=0, hour="*/2"),  # Toutes les 2 heures
        "options": {"priority": 7},  # Priorité un peu plus élevée pour les jobs
    },
    "indy-hub-cleanup-old-jobs": {
        "task": "indy_hub.tasks.industry.cleanup_old_jobs",
        "schedule": crontab(hour=2, minute=0),  # Quotidien à 2h du matin
        "options": {"priority": 8},  # Priorité basse pour le nettoyage
    },
    "indy-hub-update-type-names": {
        "task": "indy_hub.tasks.industry.update_type_names",
        "schedule": crontab(hour=3, minute=0),  # Quotidien à 3h du matin
        "options": {"priority": 8},  # Priorité basse pour la mise en cache
    },
}
