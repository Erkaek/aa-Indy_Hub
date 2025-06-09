from django.apps import AppConfig


class IndyHubConfig(AppConfig):
    name = 'indy_hub'
    verbose_name = 'Indy Hub'
    default_auto_field = 'django.db.models.BigAutoField'
    
    def ready(self):
        import indy_hub.signals  # Import signals to register them
        # Injection automatique des tâches périodiques dans CELERYBEAT_SCHEDULE (optionnel, non bloquant)
        from django.conf import settings
        try:
            from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE
            if hasattr(settings, 'CELERYBEAT_SCHEDULE'):
                settings.CELERYBEAT_SCHEDULE.update(INDY_HUB_BEAT_SCHEDULE)
            else:
                settings.CELERYBEAT_SCHEDULE = INDY_HUB_BEAT_SCHEDULE.copy()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Could not inject indy_hub beat schedule: {e}")
