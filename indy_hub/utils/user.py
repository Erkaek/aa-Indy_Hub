# User-specific utility functions
"""
User-specific utility functions for the Indy Hub module.
These functions handle user preferences, character management, etc.
"""

# Standard Library
import logging

logger = logging.getLogger(__name__)


def get_user_preferences(user):
    """
    Get user preferences for notifications and settings.

    Args:
        user: Django User instance

    Returns:
        dict: User preferences
    """
    from ..models import CharacterUpdateTracker

    tracker, created = CharacterUpdateTracker.objects.get_or_create(user=user)
    return {
        "jobs_notify_completed": tracker.jobs_notify_completed,
        "last_refresh_request": tracker.last_refresh_request,
    }


def update_user_preferences(user, preferences):
    """
    Update user preferences.

    Args:
        user: Django User instance
        preferences: dict of preferences to update

    Returns:
        bool: Success status
    """
    from ..models import CharacterUpdateTracker

    try:
        tracker, created = CharacterUpdateTracker.objects.get_or_create(user=user)

        if "jobs_notify_completed" in preferences:
            tracker.jobs_notify_completed = preferences["jobs_notify_completed"]

        tracker.save()
        return True
    except Exception as e:
        logger.error(f"Failed to update user preferences: {e}")
        return False


def get_user_characters(user):
    """
    Get all characters associated with a user.

    Args:
        user: Django User instance

    Returns:
        list: List of character data
    """
    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership

        ownerships = CharacterOwnership.objects.filter(user=user)
        return [
            {
                "character_id": ownership.character.character_id,
                "character_name": ownership.character.character_name,
                "corporation_id": ownership.character.corporation_id,
                "corporation_name": ownership.character.corporation_name,
            }
            for ownership in ownerships
        ]
    except Exception as e:
        logger.error(f"Failed to get user characters: {e}")
        return []
