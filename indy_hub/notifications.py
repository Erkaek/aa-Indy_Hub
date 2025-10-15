# indy_hub/notifications.py
"""
Notification helpers for Indy Hub.
Supports Alliance Auth notifications and (future) Discord/webhook fallback.
"""
# Standard Library
import logging

# Django
from django.apps import apps
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.notifications.models import Notification

logger = logging.getLogger(__name__)

LEVELS = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "danger",
}

DISCORD_EMBED_COLORS = {
    "info": 0x3498DB,
    "success": 0x2ECC71,
    "warning": 0xF1C40F,
    "danger": 0xE74C3C,
}

DM_ENABLED = getattr(settings, "INDY_HUB_DISCORD_DM_ENABLED", True)


def build_cta(url: str, label: str, *, icon: str | None = None) -> str:
    """Return a short call-to-action line with an optional icon."""

    prefix = f"{icon} " if icon else ""
    return f"{prefix}{label}: {url}".strip()


def build_notification_card(
    *,
    title: str,
    subtitle: str | None = None,
    icon: str | None = None,
    lines: list[str] | None = None,
    body: str | None = None,
    cta: str | None = None,
) -> str:
    """Assemble a human-friendly message block for notifications."""

    parts: list[str] = []

    if title:
        heading = f"{icon} {title}" if icon else title
        parts.append(heading.strip())

    if subtitle:
        parts.append(subtitle.strip())

    if lines:
        parts.extend(line for line in lines if line)

    if body:
        parts.append(body.strip())

    if cta:
        parts.append(cta.strip())

    return "\n\n".join(filter(None, (segment.strip() for segment in parts)))


def build_blueprint_summary_lines(
    *,
    blueprint_name: str,
    material_efficiency: int | None = None,
    time_efficiency: int | None = None,
    runs: int | None = None,
    copies: int | None = None,
) -> list[str]:
    """Generate bullet-style summary lines describing a blueprint request."""

    summary: list[str] = [_("• Blueprint: {name}").format(name=blueprint_name)]

    if material_efficiency is not None:
        summary.append(
            _("• Material Efficiency: {value}%").format(value=int(material_efficiency))
        )

    if time_efficiency is not None:
        summary.append(
            _("• Time Efficiency: {value}%").format(value=int(time_efficiency))
        )

    if runs is not None:
        summary.append(_("• Runs requested: {value}").format(value=int(runs)))

    if copies is not None:
        summary.append(_("• Copies requested: {value}").format(value=int(copies)))

    return summary


def _build_discord_embed(title: str, body: str, level: str):
    try:
        # Third Party
        from discord import Embed
    except ImportError:
        return None

    embed = Embed(
        title=title.strip(),
        description=body.strip(),
        color=DISCORD_EMBED_COLORS.get(level, DISCORD_EMBED_COLORS["info"]),
    )
    embed.timestamp = timezone.now()
    return embed


def _build_discord_content(title: str, body: str) -> str:
    if not body:
        return title
    return f"**{title}**\n{body}".strip()


def _send_via_aadiscordbot(user, title: str, body: str, level: str) -> bool:
    if not apps.is_installed("aadiscordbot"):
        return False

    try:
        # Third Party
        from aadiscordbot.tasks import send_message as discordbot_send_message
    except ImportError:
        logger.debug("aadiscordbot.tasks.send_message unavailable", exc_info=True)
        return False

    content = _build_discord_content(title, body)
    embed = _build_discord_embed(title, body, level)
    discordbot_send_message(user=user, message=content, embed=embed)
    return True


def _send_via_discordnotify(notification: Notification, level: str) -> bool:
    if not apps.is_installed("discordnotify"):
        return False

    discord_profile = getattr(notification.user, "discord", None)
    if not discord_profile or not getattr(discord_profile, "uid", None):
        logger.debug(
            "User %s has no linked Discord profile for discordnotify", notification.user
        )
        return False

    try:
        # Third Party
        from discordnotify.core import forward_notification_to_discord
    except ImportError:
        logger.debug(
            "discordnotify.core.forward_notification_to_discord unavailable",
            exc_info=True,
        )
        return False

    forward_notification_to_discord(
        notification_id=notification.id,
        discord_uid=discord_profile.uid,
        title=notification.title,
        message=notification.message,
        level=level,
        timestamp=notification.timestamp.isoformat(),
    )
    return True


def _dispatch_discord_dm(
    notification: Notification | None, user, title: str, body: str, level: str
) -> None:
    if not DM_ENABLED or not user:
        return

    sent = False
    try:
        sent = _send_via_aadiscordbot(user, title, body, level)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to send Discord DM via aadiscordbot: %s", exc, exc_info=True
        )

    if sent or not notification:
        return

    try:
        if _send_via_discordnotify(notification, level):
            sent = True
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to forward notification via discordnotify: %s", exc, exc_info=True
        )

    if not sent:
        logger.debug("No Discord DM provider succeeded for user %s", user)


def notify_user(user, title, message, level="info"):
    """Send a notification to a user via Alliance Auth and mirror it to Discord DMs."""

    if not user:
        return

    level_value = LEVELS.get(level, "info")
    stored_message = message or ""
    dm_body = message or title
    notification = None

    try:
        notification = Notification.objects.notify_user(
            user=user,
            title=title,
            message=stored_message,
            level=level_value,
        )
        logger.info("Notification sent to %s: %s", user, title)
    except Exception as exc:
        logger.error(
            "Failed to persist notification for %s: %s", user, exc, exc_info=True
        )

    _dispatch_discord_dm(notification, user, title, dm_body, level_value)


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
