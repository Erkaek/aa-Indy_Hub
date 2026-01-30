# User-specific utility functions
"""
User-specific utility functions for the Indy Hub module.
These functions handle user preferences, character management, etc.
"""

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)


def get_user_preferences(user):
    """
    Get user preferences for notifications and settings.

    Args:
        user: Django User instance

    Returns:
        dict: User preferences
    """
    from ..models import CharacterSettings

    settings, created = CharacterSettings.objects.get_or_create(
        user=user,
        character_id=0,  # Global settings
        defaults={
            "jobs_notify_completed": True,
            "allow_copy_requests": False,
            "copy_sharing_scope": CharacterSettings.SCOPE_NONE,
        },
    )
    return {
        "jobs_notify_completed": settings.jobs_notify_completed,
        "allow_copy_requests": settings.allow_copy_requests,
        "copy_sharing_scope": settings.copy_sharing_scope,
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
    from ..models import CharacterSettings

    try:
        settings, created = CharacterSettings.objects.get_or_create(
            user=user,
            character_id=0,  # Global settings
            defaults={
                "jobs_notify_completed": True,
                "allow_copy_requests": False,
                "copy_sharing_scope": CharacterSettings.SCOPE_NONE,
            },
        )

        if "jobs_notify_completed" in preferences:
            settings.jobs_notify_completed = preferences["jobs_notify_completed"]

        if "copy_sharing_scope" in preferences:
            settings.set_copy_sharing_scope(preferences["copy_sharing_scope"])
        elif "allow_copy_requests" in preferences:
            scope = (
                CharacterSettings.SCOPE_CORPORATION
                if preferences["allow_copy_requests"]
                else CharacterSettings.SCOPE_NONE
            )
            settings.set_copy_sharing_scope(scope)

        settings.save()
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
