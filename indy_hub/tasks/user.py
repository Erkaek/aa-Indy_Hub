# User-specific asynchronous tasks
"""
User-specific Celery tasks for the Indy Hub module.
These tasks handle user profile management, preferences, cleanup, etc.
"""

# Standard Library
import logging
from datetime import timedelta

# Third Party
from celery import shared_task

# Django
from django.contrib.auth.models import User
from django.utils import timezone

# Indy Hub
from ..models import Blueprint, CharacterSettings, IndustryJob

logger = logging.getLogger(__name__)


@shared_task
def cleanup_inactive_user_data():
    """
    Clean up data for users who haven't been active for a long time.
    Runs weekly to maintain database performance.
    """
    # Define inactive threshold (6 months)
    inactive_threshold = timezone.now() - timedelta(days=180)

    # Since CharacterSettings don't track last_refresh_request anymore,
    # we'll identify inactive users by their Django last_login timestamp
    inactive_users = User.objects.filter(last_login__lt=inactive_threshold).exclude(
        last_login__isnull=True
    )

    count = 0
    for user in inactive_users:

        # Clean up old blueprint data for inactive users
        old_blueprints = Blueprint.objects.filter(
            owner_user=user, updated_at__lt=inactive_threshold
        )
        blueprint_count = old_blueprints.count()
        old_blueprints.delete()

        if blueprint_count > 0:
            count += 1
            logger.info(
                f"Cleaned up {blueprint_count} old blueprints for inactive user {user.username}"
            )

    logger.info(f"Cleaned up data for {count} inactive users")
    return {"inactive_users_cleaned": count}


@shared_task
def update_user_preferences_defaults():
    """
    Ensure all users have proper default notification preferences.
    Useful after adding new preference fields.
    """
    # Alternative: find users with no global settings (character_id=0)
    users_without_global_settings = User.objects.exclude(
        charactersettings__character_id=0
    )

    count = 0
    for user in users_without_global_settings:
        settings, created = CharacterSettings.objects.get_or_create(
            user=user,
            character_id=0,  # Global settings
            defaults={
                "jobs_notify_completed": True,  # Default to enabled
                "allow_copy_requests": False,  # Default to disabled
                "copy_sharing_scope": CharacterSettings.SCOPE_NONE,
            },
        )
        if created:
            count += 1

    logger.info(f"Created default preferences for {count} users")
    return {"users_updated": count}


@shared_task
def sync_user_character_names():
    """
    Update cached character names for all user data.
    Useful when character names change in EVE Online.
    """
    from ..utils import batch_cache_character_names
    from ..utils.eve import get_character_name

    # Get all unique character IDs from blueprints and jobs
    bp_char_ids = set(
        Blueprint.objects.exclude(character_id__isnull=True).values_list(
            "character_id", flat=True
        )
    )

    job_char_ids = set(
        IndustryJob.objects.exclude(character_id__isnull=True).values_list(
            "character_id", flat=True
        )
    )

    all_char_ids = list(bp_char_ids | job_char_ids)

    if all_char_ids:
        # Batch update character names
        batch_cache_character_names(all_char_ids)

        updated_blueprints = 0
        bp_qs = Blueprint.objects.filter(character_id__in=all_char_ids).only(
            "id", "character_id", "character_name"
        )
        for bp in bp_qs.iterator():
            if not bp.character_id:
                continue
            new_name = get_character_name(bp.character_id)
            if new_name and new_name != bp.character_name:
                bp.character_name = new_name
                bp.save(update_fields=["character_name"])
                updated_blueprints += 1

        updated_jobs = 0
        job_qs = IndustryJob.objects.filter(character_id__in=all_char_ids).only(
            "id", "character_id", "character_name"
        )
        for job in job_qs.iterator():
            if not job.character_id:
                continue
            new_name = get_character_name(job.character_id)
            if new_name and new_name != job.character_name:
                job.character_name = new_name
                job.save(update_fields=["character_name"])
                updated_jobs += 1

        logger.info(
            "Updated character names for %s characters (%s blueprints, %s jobs)",
            len(all_char_ids),
            updated_blueprints,
            updated_jobs,
        )
        return {
            "characters_considered": len(all_char_ids),
            "blueprints_updated": updated_blueprints,
            "jobs_updated": updated_jobs,
        }

    logger.info("Updated character names for 0 characters")
    return {"characters_considered": 0, "blueprints_updated": 0, "jobs_updated": 0}


@shared_task
def generate_user_activity_report():
    """
    Generate activity statistics for users.
    Can be used for analytics and monitoring.
    """
    total_users = User.objects.count()
    # Since we no longer track last_refresh_request, use login activity for "active"
    active_users = (
        User.objects.filter(last_login__gte=timezone.now() - timedelta(days=30))
        .exclude(last_login__isnull=True)
        .count()
    )

    users_with_blueprints = (
        User.objects.filter(blueprint__isnull=False).distinct().count()
    )

    users_with_jobs = User.objects.filter(industryjob__isnull=False).distinct().count()

    users_with_notifications = CharacterSettings.objects.filter(
        character_id=0, jobs_notify_completed=True  # Global settings only
    ).count()

    report = {
        "total_users": total_users,
        "active_users_30d": active_users,
        "users_with_blueprints": users_with_blueprints,
        "users_with_jobs": users_with_jobs,
        "users_with_notifications_enabled": users_with_notifications,
        "generated_at": timezone.now().isoformat(),
    }

    logger.info(f"Generated user activity report: {report}")
    return report
