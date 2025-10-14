# Standard Library
import logging
from datetime import datetime

# Django
from django.db.models.signals import post_migrate, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Blueprint, CharacterSettings, IndustryJob
from .notifications import notify_user
from .utils.eve import PLACEHOLDER_PREFIX, resolve_location_name

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


def _normalize_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_previous_field_value(model, pk, field_name):
    if not pk:
        return None
    try:
        return model.objects.filter(pk=pk).values_list(field_name, flat=True).first()
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug(
            "Unable to load previous value for %s.%s (pk=%s)",
            model.__name__,
            field_name,
            pk,
            exc_info=True,
        )
        return None


def _ensure_location_name(instance, *, id_field: str, name_field: str) -> None:
    location_id = getattr(instance, id_field, None)
    current_name = getattr(instance, name_field, "") or ""

    normalized_id = _normalize_int(location_id)

    if normalized_id is None:
        if current_name:
            setattr(instance, name_field, "")
        return

    should_refresh = False

    if not current_name or current_name.startswith(PLACEHOLDER_PREFIX):
        should_refresh = True

    previous_id = _normalize_int(
        _get_previous_field_value(
            instance.__class__, getattr(instance, "pk", None), id_field
        )
    )

    if previous_id is not None and previous_id != normalized_id:
        should_refresh = True

    if not should_refresh:
        return

    owner_user_id = getattr(instance, "owner_user_id", None)
    character_id = getattr(instance, "character_id", None)

    try:
        resolved_name = resolve_location_name(
            normalized_id,
            character_id=character_id,
            owner_user_id=owner_user_id,
        )
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug(
            "Unable to resolve location name for %s via signal",
            normalized_id,
            exc_info=True,
        )
        resolved_name = None

    if not resolved_name:
        resolved_name = f"{PLACEHOLDER_PREFIX}{normalized_id}"

    setattr(instance, name_field, resolved_name)


def _mark_job_notified(job: IndustryJob) -> None:
    IndustryJob.objects.filter(pk=job.pk).update(job_completed_notified=True)
    job.job_completed_notified = True


def _handle_job_completion_notification(job: IndustryJob) -> None:
    if job.job_completed_notified:
        return

    end_date = getattr(job, "end_date", None)
    if isinstance(end_date, str):
        parsed = parse_datetime(end_date)
        if parsed is None:
            logger.debug(
                "Unable to parse end_date for job %s: %r",
                getattr(job, "job_id", None),
                end_date,
            )
            end_date = None
        else:
            end_date = parsed

    if isinstance(end_date, datetime) and timezone.is_naive(end_date):
        end_date = timezone.make_aware(end_date, timezone.utc)

    if not end_date or end_date > timezone.now():
        return

    user = getattr(job, "owner_user", None)
    if not user:
        _mark_job_notified(job)
        return

    settings = CharacterSettings.objects.filter(user=user, character_id=0).first()
    if not settings or not settings.jobs_notify_completed:
        _mark_job_notified(job)
        return

    title = "Industry Job Completed"
    job_display = job.blueprint_type_name or f"Type {job.blueprint_type_id}"
    message = f"Your industry job #{job.job_id} ({job_display}) has completed."

    try:
        notify_user(user, title, message, level="success")
        logger.info(
            "Notified user %s about completed job %s",
            getattr(user, "username", user),
            job.job_id,
        )
    except Exception:  # pragma: no cover - defensive fallback
        logger.error(
            "Failed to notify user %s about job %s",
            getattr(user, "username", user),
            job.job_id,
            exc_info=True,
        )
    finally:
        _mark_job_notified(job)


@receiver(pre_save, sender=Blueprint)
def sync_blueprint_location_name(sender, instance, **kwargs):
    _ensure_location_name(instance, id_field="location_id", name_field="location_name")


@receiver(pre_save, sender=IndustryJob)
def sync_industry_job_location_name(sender, instance, **kwargs):
    _ensure_location_name(instance, id_field="station_id", name_field="location_name")


@receiver(post_save, sender=Blueprint)
def cache_blueprint_data(sender, instance, created, **kwargs):
    """
    No longer needed: ESI name caching is removed. All lookups are local DB only.
    """
    pass


@receiver(post_save, sender=IndustryJob)
def cache_industry_job_data(sender, instance, created, **kwargs):
    _handle_job_completion_notification(instance)


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


# --- NEW: Combined token sync trigger ---
if Token:

    @receiver(post_save, sender=Token)
    def trigger_sync_on_token_save(sender, instance, created, **kwargs):
        """
        When a new ESI token is saved, trigger appropriate sync based on scopes.
        """
        if not instance.user_id:
            logger.debug(f"Token {instance.pk} has no user_id, skipping sync")
            return

        # Only trigger sync for newly created tokens or significant updates
        if not created:
            logger.debug(f"Token {instance.pk} updated but not created, skipping sync")
            return

        logger.info(
            f"New token created for user {instance.user_id}, character {instance.character_id}"
        )

        # Check blueprint scope
        blueprint_scopes = instance.scopes.filter(
            name="esi-characters.read_blueprints.v1"
        )
        if blueprint_scopes.exists():
            logger.info(f"Triggering blueprint sync for user {instance.user_id}")
            try:
                update_blueprints_for_user.delay(instance.user_id)
            except Exception as e:
                logger.error(f"Failed to trigger blueprint sync: {e}")

        # Check jobs scope
        jobs_scopes = instance.scopes.filter(name="esi-industry.read_character_jobs.v1")
        if jobs_scopes.exists():
            logger.info(f"Triggering jobs sync for user {instance.user_id}")
            try:
                update_industry_jobs_for_user.delay(instance.user_id)
            except Exception as e:
                logger.error(f"Failed to trigger jobs sync: {e}")


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
