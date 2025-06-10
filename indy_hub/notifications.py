# indy_hub/notifications.py
"""
Notification helpers for Indy Hub, inspired by FortunaISK's notification system.
Supports Alliance Auth notifications and (future) Discord/webhook fallback.
"""
# Standard Library
import logging

# Alliance Auth
from allianceauth.notifications import notify as aa_notify

logger = logging.getLogger(__name__)

LEVELS = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "danger",
}


def notify_user(user, title, message, level="info"):
    """
    Send a notification to a user via Alliance Auth's notification system.
    """
    try:
        aa_notify(user, title, message, LEVELS.get(level, "info"))
        logger.info(f"Notification sent to {user}: {title}")
    except Exception as exc:
        logger.error(f"Failed to notify {user}: {exc}", exc_info=True)


def notify_multi(users, title, message, level="info"):
    """
    Send a notification to multiple users (QuerySet, list, or single user).
    """
    if not users:
        return
    if hasattr(users, "all"):
        users = list(users)
    if not isinstance(users, (list, tuple)):
        users = [users]
    for user in users:
        notify_user(user, title, message, level)
