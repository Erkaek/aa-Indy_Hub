# Standard Library
import logging
import sys
from importlib import import_module

# Django
from django.apps import AppConfig, apps
from django.conf import settings
from django.db import connection
from django.db.models.signals import post_migrate


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

        try:
            # Django
            from django.contrib.auth.models import Permission

            if not getattr(Permission, "_indy_hub_custom_str", False):
                original_str = Permission.__str__
                indy_hub_codenames = {
                    "can_access_indy_hub",
                    "can_manage_corp_bp_requests",
                    "can_manage_material_hub",
                }

                def _indy_hub_permission_str(permission):
                    if (
                        permission.content_type.app_label == "indy_hub"
                        and permission.codename in indy_hub_codenames
                    ):
                        return f"indy_hub | {permission.name}"
                    return original_str(permission)

                Permission.__str__ = _indy_hub_permission_str
                Permission._indy_hub_custom_str = True
        except Exception:
            pass

        try:
            # Alliance Auth
            from allianceauth.services.hooks import get_extension_logger

            logger = get_extension_logger(__name__)
        except Exception:
            logger = logging.getLogger(__name__)

        # Load signals
        try:
            import_module("indy_hub.signals")
            logger.info("IndyHub signals loaded.")
        except Exception as e:
            logger.exception(f"Error loading signals: {e}")

        # Ensure Celery task modules are registered.
        # Some modules (e.g. signals) may import a single task submodule early,
        # which can prevent Celery autodiscovery from registering all tasks.
        try:
            from .tasks import ensure_task_submodules_imported

            ensure_task_submodules_imported()
        except Exception as e:
            logger.warning(f"Could not import indy_hub task submodules: {e}")

        def _setup_periodic_tasks(sender, **kwargs):
            # Skip tasks configuration during tests
            if (
                "test" in sys.argv
                or "runtests.py" in sys.argv[0]
                or hasattr(settings, "TESTING")
                or "pytest" in sys.modules
            ):
                logger.info("Skipping periodic tasks setup during tests.")
                return

            plan = kwargs.get("plan")
            if plan:
                indy_plan = [
                    backwards
                    for migration, backwards in plan
                    if migration.app_label == "indy_hub"
                ]
                if indy_plan and all(indy_plan):
                    try:
                        from .tasks import remove_periodic_tasks

                        remove_periodic_tasks()
                        logger.info("IndyHub periodic tasks removed during rollback.")
                    except Exception as e:
                        logger.exception(
                            "Error removing IndyHub periodic tasks during rollback: %s",
                            e,
                        )
                    return

            if not apps.is_installed("django_celery_beat"):
                logger.warning(
                    "django_celery_beat not installed; skipping periodic tasks setup."
                )
                return

            # Check that Celery Beat tables exist
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM django_celery_beat_crontabschedule LIMIT 1"
                    )
            except Exception as e:
                logger.warning(
                    "Celery Beat tables not available, skipping periodic tasks setup: %s",
                    e,
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
                logger.warning("Could not inject indy_hub beat schedule: %s", e)

            # Configure periodic tasks
            try:
                from .tasks import setup_periodic_tasks

                setup_periodic_tasks()
            except Exception as e:
                logger.exception("Error setting up periodic tasks: %s", e)

        post_migrate.connect(_setup_periodic_tasks, sender=self)

        # Check dependencies (optional logging)
        if not apps.is_installed("esi"):
            logger.warning("ESI not installed; some features may be disabled.")
