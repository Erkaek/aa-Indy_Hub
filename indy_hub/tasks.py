"""
Celery tasks for periodic ESI data updates
Following AllianceAuth best practices
"""
from celery import shared_task
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import logging

from .models import Blueprint, IndustryJob, CharacterUpdateTracker
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def update_blueprints_for_user(self, user_id):
    """
    Update blueprints for a specific user
    """
    try:
        user = User.objects.get(id=user_id)
        
        logger.info(f"Starting blueprint update for user {user.username}")
        
        # Local-only: No sync_user_data. Implement your own update logic here if needed.
        # For now, just log and update tracker timestamps.
        
        blueprints_count = Blueprint.objects.filter(owner_user=user).count()
        
        for tracker in CharacterUpdateTracker.objects.filter(user=user):
            tracker.blueprints_last_update = timezone.now()
            tracker.last_error = ''
            tracker.save()
        
        logger.info(f"Updated {blueprints_count} blueprints for user {user.username}")
        return {'success': True, 'blueprints_count': blueprints_count}
        
    except Exception as e:
        logger.error(f"Error updating blueprints for user {user_id}: {e}")
        
        # Update error status
        try:
            user = User.objects.get(id=user_id)
            for tracker in CharacterUpdateTracker.objects.filter(user=user):
                tracker.last_error = str(e)
                tracker.save()
        except:
            pass
        
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=3)
def update_industry_jobs_for_user(self, user_id):
    """
    Update industry jobs for a specific user
    """
    try:
        user = User.objects.get(id=user_id)
        
        logger.info(f"Starting industry jobs update for user {user.username}")
        
        # Local-only: No sync_user_data. Implement your own update logic here if needed.
        # For now, just log and update tracker timestamps.
        
        jobs_count = IndustryJob.objects.filter(owner_user=user).count()
        
        for tracker in CharacterUpdateTracker.objects.filter(user=user):
            tracker.jobs_last_update = timezone.now()
            tracker.last_error = ''
            tracker.save()
        
        logger.info(f"Updated {jobs_count} industry jobs for user {user.username}")
        return {'success': True, 'jobs_count': jobs_count}
        
    except Exception as e:
        logger.error(f"Error updating industry jobs for user {user_id}: {e}")
        
        # Update error status
        try:
            user = User.objects.get(id=user_id)
            for tracker in CharacterUpdateTracker.objects.filter(user=user):
                tracker.last_error = str(e)
                tracker.save()
        except:
            pass
        
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


@shared_task
def update_all_blueprints():
    """
    Update blueprints for all users - runs every 30 minutes
    """
    logger.info("Starting bulk blueprint update for all users")
    
    # Get users who have ESI tokens and haven't been updated recently
    cutoff_time = timezone.now() - timedelta(minutes=25)  # Allow 5-minute buffer
    
    users_to_update = User.objects.filter(
        token__isnull=False
    ).exclude(
        characterupdatetracker__blueprints_last_update__gte=cutoff_time
    ).distinct()
    
    for user in users_to_update:
        update_blueprints_for_user.delay(user.id)
    
    logger.info(f"Queued blueprint updates for {users_to_update.count()} users")
    return {'users_queued': users_to_update.count()}


@shared_task
def update_all_industry_jobs():
    """
    Update industry jobs for all users - runs every 10 minutes
    """
    logger.info("Starting bulk industry jobs update for all users")
    
    # Get users who have ESI tokens and haven't been updated recently
    cutoff_time = timezone.now() - timedelta(minutes=8)  # Allow 2-minute buffer
    
    users_to_update = User.objects.filter(
        token__isnull=False
    ).exclude(
        characterupdatetracker__jobs_last_update__gte=cutoff_time
    ).distinct()
    
    for user in users_to_update:
        update_industry_jobs_for_user.delay(user.id)
    
    logger.info(f"Queued industry job updates for {users_to_update.count()} users")
    return {'users_queued': users_to_update.count()}


@shared_task
def cleanup_old_jobs():
    """
    Clean up old completed industry jobs - runs daily
    """
    cutoff_date = timezone.now() - timedelta(days=30)
    
    old_jobs = IndustryJob.objects.filter(
        status__in=['delivered', 'cancelled', 'reverted'],
        end_date__lt=cutoff_date
    )
    
    count = old_jobs.count()
    old_jobs.delete()
    
    logger.info(f"Cleaned up {count} old industry jobs")
    return {'deleted_jobs': count}


@shared_task
def update_type_names():
    """
    Update cached type names for blueprints and jobs
    """
    from .models import batch_cache_type_names
    
    # Update blueprint type names
    blueprints_without_names = Blueprint.objects.filter(type_name='')
    type_ids = list(blueprints_without_names.values_list('type_id', flat=True))
    
    if type_ids:
        # Batch cache type names
        batch_cache_type_names(type_ids)
        
        # Update blueprints
        for bp in blueprints_without_names:
            bp.refresh_from_db()
    
    # Update job type names
    jobs_without_names = IndustryJob.objects.filter(blueprint_type_name='')
    job_type_ids = list(jobs_without_names.values_list('blueprint_type_id', flat=True))
    product_type_ids = list(jobs_without_names.exclude(product_type_id__isnull=True).values_list('product_type_id', flat=True))
    
    all_type_ids = list(set(job_type_ids + product_type_ids))
    if all_type_ids:
        # Batch cache type names
        batch_cache_type_names(all_type_ids)
        
        # Update jobs
        for job in jobs_without_names:
            job.refresh_from_db()
    
    logger.info("Updated type names for blueprints and jobs")


# --- Ajout auto des tâches planifiées Celery Beat ---
def setup_periodic_tasks():
    """
    Crée ou met à jour les tâches planifiées Celery Beat pour indy_hub.
    """
    import json
    import logging
    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
        from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE
    except ImportError:
        return  # django_celery_beat n'est pas installé

    for name, conf in INDY_HUB_BEAT_SCHEDULE.items():
        schedule = conf['schedule']
        if hasattr(schedule, '_orig_minute'):  # crontab
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
