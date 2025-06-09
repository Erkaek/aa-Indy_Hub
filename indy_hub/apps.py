# Standard Library
import logging
import sys
from importlib import import_module

# Django
from django.apps import AppConfig, apps
from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


class IndyHubConfig(AppConfig):
    """
    Django application configuration for IndyHub.

    Handles initialization of the application, including signal registration
    and configuration of periodic tasks for industry data updates.
    """

    name = "indy_hub"
    verbose_name = "Indy Hub"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """
        Initializes the application when Django starts.

        This method:
        1. Loads signal handlers for event processing
        2. Sets up periodic tasks for automated industry data updates
        3. Injects beat schedule for compatibility
        """
        super().ready()

        # Load signals
        try:
            import_module("indy_hub.signals")
            logger.info("IndyHub signals loaded.")
        except Exception as e:
            logger.exception(f"Error loading signals: {e}")

        # Skip tasks configuration during tests
        if (
            "test" in sys.argv
            or "runtests.py" in sys.argv[0]
            or hasattr(settings, "TESTING")
            or "pytest" in sys.modules
        ):
            logger.info("Skipping periodic tasks setup during tests.")
            return

        # Skip during migrations
        if "migrate" in sys.argv or "makemigrations" in sys.argv:
            logger.info("Skipping periodic tasks setup during migrations.")
            return

        # Check that Celery Beat tables exist
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM django_celery_beat_crontabschedule LIMIT 1"
                )
        except Exception as e:
            logger.warning(
                f"Celery Beat tables not available, skipping periodic tasks setup: {e}"
            )
            return

        # Inject beat schedule for compatibility (optional, non-blocking)
        try:
            # AA Example App
            from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE

            if hasattr(settings, "CELERYBEAT_SCHEDULE"):
                settings.CELERYBEAT_SCHEDULE.update(INDY_HUB_BEAT_SCHEDULE)
            else:
                settings.CELERYBEAT_SCHEDULE = INDY_HUB_BEAT_SCHEDULE.copy()
        except Exception as e:
            logger.warning(f"Could not inject indy_hub beat schedule: {e}")

        # Configure periodic tasks
        try:
            from .tasks import setup_periodic_tasks

            setup_periodic_tasks()
            logger.info("IndyHub periodic tasks configured.")
        except Exception as e:
            logger.exception(f"Error setting up periodic tasks: {e}")

        # Check dependencies (optional logging)
        if not apps.is_installed("esi"):
            logger.warning("ESI not installed; some features may be disabled.")
