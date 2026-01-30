# indy_hub/notifications.py
"""
Notification helpers for Indy Hub.
Supports Alliance Auth notifications and (future) Discord/webhook fallback.
"""
# Standard Library
from urllib.parse import urljoin, urlparse

# Django
from django.apps import apps
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.notifications.models import Notification
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)

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
EMBED_FOOTER_TEXT = getattr(
    settings,
    "INDY_HUB_DISCORD_FOOTER_TEXT",
    getattr(settings, "Indy_Hub", "Alliance Auth"),
)
DEFAULT_LINK_LABEL = _("View details")
SHORT_LINK_LABEL = _("clic here")


def build_site_url(path: str | None) -> str | None:
    """Return an absolute URL for the given path based on SITE_URL."""

    if not path:
        return None

    parsed = urlparse(path)
    if parsed.scheme:
        return path

    base_url = getattr(settings, "INDY_HUB_SITE_URL", "") or getattr(
        settings, "SITE_URL", ""
    )
    if not base_url:
        origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
        base_url = next(
            (
                origin
                for origin in origins
                if isinstance(origin, str)
                and origin.startswith(("http://", "https://"))
            ),
            "",
        )
    if not base_url:
        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        host = next(
            (
                value
                for value in allowed_hosts
                if isinstance(value, str) and value and value != "*"
            ),
            "",
        )
        if host:
            base_url = f"https://{host}"
    if not base_url:
        return None

    normalized_base = base_url.rstrip("/") + "/"
    normalized_path = path.lstrip("/")
    return urljoin(normalized_base, normalized_path)


def build_cta(
    url: str,
    label: str,
    *,
    icon: str | None = None,
    include_url: bool = True,
    markdown: bool = False,
) -> str:
    """Return a short call-to-action line with an optional icon."""

    prefix = f"{icon} " if icon else ""
    if markdown:
        if url:
            return f"{prefix}[{label}]({url})".strip()
        return f"{prefix}{label}".strip()
    if include_url:
        return f"{prefix}{label}: {url}".strip()
    return f"{prefix}{label}".strip()


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


def _build_discord_embed(
    title: str,
    body: str,
    level: str,
    *,
    url: str | None = None,
    thumbnail_url: str | None = None,
):
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
    if url:
        embed.url = url

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    if EMBED_FOOTER_TEXT:
        embed.set_footer(text=str(EMBED_FOOTER_TEXT))
    return embed


def _build_discord_content(title: str, body: str) -> str:
    title_text = str(title or "")
    body_text = str(body or "")
    if not title_text and not body_text:
        return ""
    if not body_text:
        return title_text
    if title_text and title_text not in body_text:
        return f"{title_text}\n\n{body_text}"
    return body_text


def _build_discord_webhook_payload(
    title: str,
    message: str,
    level: str,
    *,
    link: str | None = None,
    thumbnail_url: str | None = None,
    embed_title: str | None = None,
    embed_color: int | None = None,
    mention_everyone: bool = False,
) -> dict:
    normalized_link = link
    if link:
        parsed = urlparse(link)
        if not parsed.scheme:
            normalized_link = build_site_url(link) or link

    cta_line = None
    if normalized_link:
        cta_line = build_cta(
            normalized_link,
            SHORT_LINK_LABEL,
            include_url=False,
            markdown=True,
        )

    title_text = str(title or "")
    message_text = str(message or "")
    description = message_text
    if cta_line:
        description = f"{description}\n\n{cta_line}" if description else cta_line

    payload = {"content": "@here" if mention_everyone else ""}
    if mention_everyone:
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    embed = {
        "title": embed_title or title_text,
        "description": description,
        "color": (
            embed_color
            if embed_color is not None
            else DISCORD_EMBED_COLORS.get(level, DISCORD_EMBED_COLORS["info"])
        ),
        "timestamp": timezone.now().isoformat(),
    }
    if normalized_link:
        embed["url"] = normalized_link
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    payload["embeds"] = [embed]

    return payload


def send_discord_webhook(
    webhook_url: str,
    title: str,
    message: str,
    level: str = "info",
    *,
    link: str | None = None,
    thumbnail_url: str | None = None,
    embed_title: str | None = None,
    embed_color: int | None = None,
    mention_everyone: bool = False,
    retries: int = 3,
) -> bool:
    """Send a notification to a Discord webhook URL.

    Returns True when the webhook call succeeds.
    """
    if not webhook_url:
        return False

    payload = _build_discord_webhook_payload(
        title,
        message,
        level,
        link=link,
        thumbnail_url=thumbnail_url,
        embed_title=embed_title,
        embed_color=embed_color,
        mention_everyone=mention_everyone,
    )

    # Third Party
    import requests

    attempt_count = max(1, retries)
    for attempt in range(1, attempt_count + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            if response.status_code >= 400:
                logger.warning(
                    "Discord webhook failed (%s): %s",
                    response.status_code,
                    response.text,
                )
                continue
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Discord webhook failed (attempt %s): %s", attempt, exc, exc_info=True
            )
            continue

    return False


def send_discord_webhook_with_message_id(
    webhook_url: str,
    title: str,
    message: str,
    level: str = "info",
    *,
    link: str | None = None,
    thumbnail_url: str | None = None,
    embed_title: str | None = None,
    embed_color: int | None = None,
    mention_everyone: bool = False,
    retries: int = 3,
) -> tuple[bool, str | None]:
    """Send a Discord webhook message and return its message ID when available."""
    if not webhook_url:
        return False, None

    payload = _build_discord_webhook_payload(
        title,
        message,
        level,
        link=link,
        thumbnail_url=thumbnail_url,
        embed_title=embed_title,
        embed_color=embed_color,
        mention_everyone=mention_everyone,
    )

    # Third Party
    import requests

    attempt_count = max(1, retries)
    parsed = urlparse(webhook_url)
    if parsed.query:
        webhook_url_wait = f"{webhook_url}&wait=true"
    else:
        webhook_url_wait = f"{webhook_url}?wait=true"
    for attempt in range(1, attempt_count + 1):
        try:
            response = requests.post(webhook_url_wait, json=payload, timeout=10)
            if response.status_code >= 400:
                logger.warning(
                    "Discord webhook failed (%s): %s",
                    response.status_code,
                    response.text,
                )
                continue
            try:
                data = response.json()
            except ValueError:
                logger.warning("Discord webhook response JSON parse failed.")
                return True, None
            message_id = data.get("id") if isinstance(data, dict) else None
            return True, str(message_id) if message_id else None
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Discord webhook failed (attempt %s): %s", attempt, exc, exc_info=True
            )
            continue

    return False, None


def delete_discord_webhook_message(webhook_url: str, message_id: str) -> bool:
    """Delete a previously sent Discord webhook message by ID."""
    if not webhook_url or not message_id:
        return False

    parsed = urlparse(webhook_url)
    base_url = parsed._replace(query="", fragment="").geturl()
    delete_url = f"{base_url}/messages/{message_id}"

    # Third Party
    import requests

    try:
        response = requests.delete(delete_url, timeout=10)
        if response.status_code >= 400:
            logger.warning(
                "Discord webhook delete failed (%s): %s",
                response.status_code,
                response.text,
            )
            return False
        return True
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Discord webhook delete failed: %s", exc, exc_info=True)
        return False


def _send_via_aadiscordbot(
    user,
    title: str,
    body: str,
    level: str,
    *,
    link: str | None = None,
    thumbnail_url: str | None = None,
) -> bool:
    if not apps.is_installed("aadiscordbot"):
        return False

    try:
        # Third Party
        from aadiscordbot.tasks import send_message as discordbot_send_message
    except ImportError:
        logger.debug("aadiscordbot.tasks.send_message unavailable", exc_info=True)
        return False

    embed = _build_discord_embed(
        title,
        body,
        level,
        url=link,
        thumbnail_url=thumbnail_url,
    )
    if embed and embed.description:
        content = ""
    else:
        content = _build_discord_content(title, body)
    discordbot_send_message(user=user, message=content or "", embed=embed)
    return True


def _send_via_discordnotify(
    notification: Notification, level: str, *, message_override: str | None = None
) -> bool:
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
        message=message_override or notification.message,
        level=level,
        timestamp=notification.timestamp.isoformat(),
    )
    return True


def _dispatch_discord_dm(
    notification: Notification | None,
    user,
    title: str,
    body: str,
    level: str,
    *,
    allow_bot: bool = True,
    link: str | None = None,
    thumbnail_url: str | None = None,
) -> None:
    if not DM_ENABLED or not user:
        return

    sent = False
    if allow_bot:
        try:
            sent = _send_via_aadiscordbot(
                user,
                title,
                body,
                level,
                link=link,
                thumbnail_url=thumbnail_url,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to send Discord DM via aadiscordbot: %s", exc, exc_info=True
            )

    if sent or not notification:
        return

    try:
        if _send_via_discordnotify(notification, level, message_override=body):
            sent = True
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to forward notification via discordnotify: %s", exc, exc_info=True
        )

    if not sent:
        logger.debug("No Discord DM provider succeeded for user %s", user)


def notify_user(
    user,
    title,
    message,
    level="info",
    *,
    link: str | None = None,
    link_label: str | None = None,
    thumbnail_url: str | None = None,
):
    """Send a notification via Alliance Auth and mirror it to Discord DMs."""

    if not user:
        return

    level_value = LEVELS.get(level, "info")
    stored_message = message or title
    dm_body = message or title
    notification = None

    normalized_link = link
    if link:
        parsed = urlparse(link)
        if not parsed.scheme:
            normalized_link = build_site_url(link) or link

    cta_line = None
    dm_cta_line = None
    if normalized_link:
        cta_label = (link_label or DEFAULT_LINK_LABEL).strip()
        if cta_label:
            cta_line = build_cta(normalized_link, cta_label)
        dm_cta_line = build_cta(
            normalized_link,
            SHORT_LINK_LABEL,
            include_url=False,
            markdown=True,
        )

    if cta_line:
        stored_message = (
            f"{stored_message}\n\n{cta_line}" if stored_message else cta_line
        )
    if dm_cta_line:
        dm_body = f"{dm_body}\n\n{dm_cta_line}" if dm_body else dm_cta_line

    effective_link = normalized_link

    if DM_ENABLED:
        try:
            if _send_via_aadiscordbot(
                user,
                title,
                dm_body,
                level_value,
                link=effective_link,
                thumbnail_url=thumbnail_url,
            ):
                logger.info("Discord bot notification sent to %s: %s", user, title)
                return
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Discord bot notification failed for %s: %s", user, exc, exc_info=True
            )

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

    if DM_ENABLED:
        _dispatch_discord_dm(
            notification,
            user,
            title,
            dm_body,
            level_value,
            allow_bot=False,
            link=effective_link,
            thumbnail_url=thumbnail_url,
        )


def notify_multi(users, title, message, level="info", **kwargs):
    """
    Send a notification to multiple users (QuerySet, list, or single user).
    """
    if not users:
        return
    if hasattr(users, "all"):
        users = list(users)
    if not isinstance(users, (list, tuple)):
        users = [users]
    seen_ids = set()
    for user in users:
        if not user:
            continue
        user_id = getattr(user, "id", None)
        if user_id in seen_ids:
            continue
        if user_id is not None:
            seen_ids.add(user_id)
        notify_user(user, title, message, level=level, **kwargs)
