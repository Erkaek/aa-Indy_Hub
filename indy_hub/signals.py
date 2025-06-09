from django.db.models.signals import post_save, post_migrate
from django.dispatch import receiver
from .models import Blueprint, IndustryJob
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Blueprint)
def cache_blueprint_data(sender, instance, created, **kwargs):
    """
    No longer needed: ESI name caching is removed. All lookups are local DB only.
    """
    pass


@receiver(post_save, sender=IndustryJob)
def cache_industry_job_data(sender, instance, created, **kwargs):
    """
    No longer needed: ESI name caching is removed. All lookups are local DB only.
    """
    pass


@receiver(post_migrate)
def setup_indyhub_periodic_tasks(sender, **kwargs):
    # N'exécute que pour l'app indy_hub
    if getattr(sender, 'name', None) != "indy_hub":
        return
    try:
        from indy_hub.tasks import setup_periodic_tasks
        setup_periodic_tasks()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not setup indy_hub periodic tasks after migrate: {e}")
