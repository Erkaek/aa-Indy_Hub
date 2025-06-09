# Standard Library
import logging

# Django
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from .models import Blueprint, IndustryJob

# Alliance Auth: Token model
try:
    # Alliance Auth
    from esi.models import Token
except ImportError:
    Token = None

# AA Example App
# Task imports
from indy_hub.tasks.industry import (
    update_blueprints_for_user,
    update_industry_jobs_for_user,
)

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
    if getattr(sender, "name", None) != "indy_hub":
        return
    try:
        # AA Example App
        from indy_hub.tasks import setup_periodic_tasks

        setup_periodic_tasks()
    except Exception as e:
        # Standard Library
        import logging

        logging.getLogger(__name__).warning(
            f"Could not setup indy_hub periodic tasks after migrate: {e}"
        )


# --- NEW: Trigger blueprint sync after ESI token is saved ---
if Token:

    @receiver(post_save, sender=Token)
    def trigger_blueprint_sync_on_token_save(sender, instance, created, **kwargs):
        """
        When a new ESI token is saved (or updated), trigger blueprint sync if it has the blueprint scope.
        """
        if not instance.user_id:
            return
        # Only trigger if the token has blueprint scope
        if instance.scopes.filter(name="esi-characters.read_blueprints.v1").exists():
            update_blueprints_for_user.delay(instance.user_id)


# --- NEW: Trigger jobs sync after ESI token is saved ---
if Token:

    @receiver(post_save, sender=Token)
    def trigger_jobs_sync_on_token_save(sender, instance, created, **kwargs):
        """
        When a new ESI token is saved (or updated), trigger jobs sync if it has the jobs scope.
        """
        if not instance.user_id:
            return
        # Only trigger if the token has jobs scope
        if instance.scopes.filter(name="esi-industry.read_character_jobs.v1").exists():
            update_industry_jobs_for_user.delay(instance.user_id)


@receiver(post_save, sender=Token)
def remove_duplicate_tokens(sender, instance, created, **kwargs):
    # After saving a new token, delete any older duplicates for the same character and scopes
    tokens = Token.objects.filter(
        user=instance.user,
        character_id=instance.character_id,
    ).exclude(pk=instance.pk)
    # Compare exact scope sets to identify duplicates
    instance_scope_ids = set(instance.scopes.values_list("id", flat=True))
    for token in tokens:
        if set(token.scopes.values_list("id", flat=True)) == instance_scope_ids:
            token.delete()
