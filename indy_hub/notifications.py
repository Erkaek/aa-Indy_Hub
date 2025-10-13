# indy_hub/notifications.py
"""
Notification helpers for Indy Hub, inspired by FortunaISK's notification system.
Supports Alliance Auth notifications and (future) Discord/webhook fallback.
"""
# Standard Library
import logging

# Django
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.notifications import notify as aa_notify

logger = logging.getLogger(__name__)

LEVELS = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "danger",
}


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
