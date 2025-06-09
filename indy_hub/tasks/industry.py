# Tâches asynchrones pour l'industrie (exemple)
# Copie ici les tâches liées à l'industrie extraites de tasks.py
# Place ici les tâches asynchrones spécifiques à l'industrie extraites de tasks.py si besoin

from celery import shared_task
from django.utils import timezone
from django.contrib.auth.models import User
from datetime import timedelta
from ..models import Blueprint, CharacterUpdateTracker, IndustryJob, get_character_name, get_type_name
from ..notifications import notify_user
from ..esi_helpers import fetch_character_blueprints
from django.db import transaction
import logging
from allianceauth.authentication.models import CharacterOwnership
logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def update_blueprints_for_user(self, user_id):
    try:
        user = User.objects.get(id=user_id)
        logger.info(f"Starting blueprint update for user {user.username}")
        updated_count = 0
        error_messages = []
        # Get all characters owned by user
        ownerships = CharacterOwnership.objects.filter(user=user)
        for ownership in ownerships:
            char_id = ownership.character.character_id
            # Find a valid token for this character with blueprint scope
            try:
                Token = None
                try:
                    from allianceauth.eveonline.models import Token as AuthToken
                    Token = AuthToken
                except ImportError:
                    pass
                if not Token:
                    raise Exception("Token model not found")
                token = Token.objects.filter(character_id=char_id, user=user).require_scopes(["esi-characters.read_blueprints.v1"]).first()
                if not token:
                    logger.info(f"No valid blueprint token for character {char_id}")
                    continue
                # Fetch blueprints from ESI
                try:
                    blueprints = fetch_character_blueprints(char_id)
                except Exception as e:
                    error_messages.append(f"Char {char_id}: {e}")
                    # Mark error in tracker
                    CharacterUpdateTracker.objects.update_or_create(
                        user=user, character_id=char_id,
                        defaults={"last_error": str(e), "blueprints_last_update": timezone.now()}
                    )
                    continue
                # Update blueprints in DB
                with transaction.atomic():
                    # Remove old blueprints for this character
                    Blueprint.objects.filter(owner_user=user, character_id=char_id).delete()
                    for bp in blueprints:
                        Blueprint.objects.create(
                            owner_user=user,
                            character_id=char_id,
                            item_id=bp.get("item_id"),
                            blueprint_id=bp.get("blueprint_id", None),
                            type_id=bp.get("type_id"),
                            location_id=bp.get("location_id"),
                            location_flag=bp.get("location_flag", ""),
                            quantity=bp.get("quantity"),
                            time_efficiency=bp.get("time_efficiency", 0),
                            material_efficiency=bp.get("material_efficiency", 0),
                            runs=bp.get("runs", 0),
                            character_name=get_character_name(char_id),
                            type_name=get_type_name(bp.get("type_id")),
                        )
                    CharacterUpdateTracker.objects.update_or_create(
                        user=user, character_id=char_id,
                        defaults={"blueprints_last_update": timezone.now(), "last_error": ""}
                    )
                    updated_count += len(blueprints)
            except Exception as e:
                logger.error(f"Error updating blueprints for character {char_id}: {e}")
                error_messages.append(f"Char {char_id}: {e}")
        logger.info(f"Updated {updated_count} blueprints for user {user.username}")
        if error_messages:
            logger.warning(f"Blueprint sync errors for user {user.username}: {'; '.join(error_messages)}")
        return {"success": True, "blueprints_updated": updated_count, "errors": error_messages}
    except Exception as e:
        logger.error(f"Error updating blueprints for user {user_id}: {e}")
        try:
            user = User.objects.get(id=user_id)
            for tracker in CharacterUpdateTracker.objects.filter(user=user):
                tracker.last_error = str(e)
                tracker.save()
        except Exception:
            pass
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

@shared_task(bind=True, max_retries=3)
def update_industry_jobs_for_user(self, user_id):
    from ..esi_helpers import fetch_character_industry_jobs
    try:
        user = User.objects.get(id=user_id)
        logger.info(f"Starting industry jobs update for user {user.username}")
        updated_count = 0
        error_messages = []
        ownerships = CharacterOwnership.objects.filter(user=user)
        for ownership in ownerships:
            char_id = ownership.character.character_id
            try:
                Token = None
                try:
                    from allianceauth.eveonline.models import Token as AuthToken
                    Token = AuthToken
                except ImportError:
                    pass
                if not Token:
                    raise Exception("Token model not found")
                token = Token.objects.filter(character_id=char_id, user=user).require_scopes(["esi-industry.read_character_jobs.v1"]).first()
                if not token:
                    logger.info(f"No valid industry jobs token for character {char_id}")
                    continue
                try:
                    jobs = fetch_character_industry_jobs(char_id)
                except Exception as e:
                    error_messages.append(f"Char {char_id}: {e}")
                    CharacterUpdateTracker.objects.update_or_create(
                        user=user, character_id=char_id,
                        defaults={"last_error": str(e), "jobs_last_update": timezone.now()}
                    )
                    continue
                with transaction.atomic():
                    IndustryJob.objects.filter(owner_user=user, character_id=char_id).delete()
                    for job in jobs:
                        IndustryJob.objects.create(
                            owner_user=user,
                            character_id=char_id,
                            job_id=job.get("job_id"),
                            installer_id=job.get("installer_id"),
                            facility_id=job.get("facility_id"),
                            station_id=job.get("station_id"),
                            activity_id=job.get("activity_id"),
                            blueprint_id=job.get("blueprint_id"),
                            blueprint_type_id=job.get("blueprint_type_id"),
                            blueprint_location_id=job.get("blueprint_location_id"),
                            output_location_id=job.get("output_location_id"),
                            runs=job.get("runs"),
                            cost=job.get("cost"),
                            licensed_runs=job.get("licensed_runs"),
                            probability=job.get("probability"),
                            product_type_id=job.get("product_type_id"),
                            status=job.get("status"),
                            duration=job.get("duration"),
                            start_date=job.get("start_date"),
                            end_date=job.get("end_date"),
                            pause_date=job.get("pause_date"),
                            completed_date=job.get("completed_date"),
                            completed_character_id=job.get("completed_character_id"),
                            successful_runs=job.get("successful_runs"),
                            blueprint_type_name=get_type_name(job.get("blueprint_type_id")),
                            product_type_name=get_type_name(job.get("product_type_id")),
                            character_name=get_character_name(char_id),
                        )
                    CharacterUpdateTracker.objects.update_or_create(
                        user=user, character_id=char_id,
                        defaults={"jobs_last_update": timezone.now(), "last_error": ""}
                    )
                    updated_count += len(jobs)
            except Exception as e:
                logger.error(f"Error updating industry jobs for character {char_id}: {e}")
                error_messages.append(f"Char {char_id}: {e}")
        logger.info(f"Updated {updated_count} industry jobs for user {user.username}")
        if error_messages:
            logger.warning(f"Industry jobs sync errors for user {user.username}: {'; '.join(error_messages)}")
        return {"success": True, "jobs_updated": updated_count, "errors": error_messages}
    except Exception as e:
        logger.error(f"Error updating industry jobs for user {user_id}: {e}")
        try:
            user = User.objects.get(id=user_id)
            for tracker in CharacterUpdateTracker.objects.filter(user=user):
                tracker.last_error = str(e)
                tracker.save()
        except Exception:
            pass
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

@shared_task
def cleanup_old_jobs():
    cutoff_date = timezone.now() - timezone.timedelta(days=30)
    old_jobs = IndustryJob.objects.filter(
        status__in=["delivered", "cancelled", "reverted"], end_date__lt=cutoff_date
    )
    count = old_jobs.count()
    old_jobs.delete()
    logger.info(f"Cleaned up {count} old industry jobs")
    return {"deleted_jobs": count}

@shared_task
def update_type_names():
    from ..models import batch_cache_type_names
    blueprints_without_names = Blueprint.objects.filter(type_name="")
    type_ids = list(blueprints_without_names.values_list("type_id", flat=True))
    if type_ids:
        batch_cache_type_names(type_ids)
        for bp in blueprints_without_names:
            bp.refresh_from_db()
    jobs_without_names = IndustryJob.objects.filter(blueprint_type_name="")
    job_type_ids = list(jobs_without_names.values_list("blueprint_type_id", flat=True))
    product_type_ids = list(
        jobs_without_names.exclude(product_type_id__isnull=True).values_list(
            "product_type_id", flat=True
        )
    )
    all_type_ids = list(set(job_type_ids + product_type_ids))
    if all_type_ids:
        batch_cache_type_names(all_type_ids)
        for job in jobs_without_names:
            job.refresh_from_db()
    logger.info("Updated type names for blueprints and jobs")

@shared_task(bind=True, max_retries=3)
def notify_completed_jobs(self):
    """
    Notify users about completed jobs based on their preferences
    Runs every 5 minutes to check for jobs whose end_date has passed
    """
    try:
        from django.utils import timezone
        logger.info("Starting job completion notification check")
        
        now = timezone.now()
        completed_jobs = IndustryJob.objects.filter(
            end_date__lte=now, 
            job_completed_notified=False
        ).select_related('owner_user')
        
        notified_count = 0
        
        for job in completed_jobs:
            user = job.owner_user
            if not user:
                # Mark job as notified even if no user to avoid repeated checks
                job.job_completed_notified = True
                job.save(update_fields=["job_completed_notified"])
                continue
            
            # Check user's notification preference
            from ..models import CharacterUpdateTracker
            tracker = CharacterUpdateTracker.objects.filter(user=user).first()
            
            # Skip notification if user has disabled job completion notifications
            if tracker and not tracker.jobs_notify_completed:
                job.job_completed_notified = True
                job.save(update_fields=["job_completed_notified"])
                continue
            
            # Send notification
            title = "Industry Job Completed"
            message = f"Your industry job #{job.job_id} ({job.blueprint_type_name or f'Type {job.blueprint_type_id}'}) has completed."
            
            try:
                notify_user(user, title, message, level="success")
                notified_count += 1
                logger.info(f"Notified user {user.username} about completed job {job.job_id}")
            except Exception as e:
                logger.error(f"Failed to notify user {user.username} about job {job.job_id}: {e}")
            
            # Mark job as notified regardless of notification success
            job.job_completed_notified = True
            job.save(update_fields=["job_completed_notified"])
        
        logger.info(f"Job completion notification check completed. Notified {notified_count} users about {completed_jobs.count()} completed jobs.")
        return {"total_completed_jobs": completed_jobs.count(), "notified_users": notified_count}
        
    except Exception as exc:
        logger.error(f"Error in notify_completed_jobs task: {exc}")
        # Retry the task with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def update_all_blueprints():
    """
    Update blueprints for all users - runs every 30 minutes
    """
    logger.info("Starting bulk blueprint update for all users")

    # Get users who have ESI tokens and haven't been updated recently
    cutoff_time = timezone.now() - timedelta(minutes=25)  # Allow 5-minute buffer

    users_to_update = (
        User.objects.filter(token__isnull=False)
        .exclude(characterupdatetracker__blueprints_last_update__gte=cutoff_time)
        .distinct()
    )

    for user in users_to_update:
        update_blueprints_for_user.delay(user.id)

    logger.info(f"Queued blueprint updates for {users_to_update.count()} users")
    return {"users_queued": users_to_update.count()}


@shared_task
def update_all_industry_jobs():
    """
    Update industry jobs for all users - runs every 10 minutes
    """
    logger.info("Starting bulk industry jobs update for all users")

    # Get users who have ESI tokens and haven't been updated récemment
    cutoff_time = timezone.now() - timedelta(minutes=8)  # Allow 2-minute buffer

    users_to_update = (
        User.objects.filter(token__isnull=False)
        .exclude(characterupdatetracker__jobs_last_update__gte=cutoff_time)
        .distinct()
    )

    for user in users_to_update:
        update_industry_jobs_for_user.delay(user.id)

    logger.info(f"Queued industry job updates for {users_to_update.count()} users")
    return {"users_queued": users_to_update.count()}
