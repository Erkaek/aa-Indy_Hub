"""Helpers to build rich notifications for industry jobs."""

from __future__ import annotations

# Standard Library
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

# Django
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

# Indy Hub
from ..models import (
    CharacterSettings,
    IndustryJob,
    JobNotificationDigestEntry,
)
from ..notifications import build_site_url, notify_user
from .eve import get_character_name

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # AA Example App
    from indy_hub.models import Blueprint


@dataclass(frozen=True)
class JobNotificationPayload:
    """Structured notification content for a completed industry job."""

    title: str
    message: str
    summary: str
    thumbnail_url: str | None = None
    metadata: dict[str, Any] | None = None


def build_job_notification_payload(job, *, blueprint=None) -> JobNotificationPayload:
    """Return a formatted notification payload for the given industry job.

    Args:
        job: An :class:`~indy_hub.models.IndustryJob` instance (saved or unsaved).
        blueprint: Optional blueprint instance associated to the job. When omitted,
            the helper attempts to resolve it automatically.
    """

    character_name = _resolve_character_name(job)
    blueprint_obj = blueprint or _resolve_blueprint(job)
    blueprint_name = _resolve_blueprint_name(job, blueprint_obj)
    activity_label = _resolve_activity_label(job)
    result_line = _resolve_result(job, blueprint_obj)
    location_label = _resolve_location(job)
    thumbnail_url = _resolve_image_url(job, blueprint_obj)

    title = _("%(character)s - Job #%(job_id)s completed") % {
        "character": character_name,
        "job_id": getattr(job, "job_id", "?"),
    }

    lines: list[str] = [
        _("Character: %(name)s") % {"name": character_name},
        _("Job: #%(job_id)s") % {"job_id": getattr(job, "job_id", "?")},
        _("Blueprint: %(name)s") % {"name": blueprint_name},
        _("Activity: %(activity)s") % {"activity": activity_label},
    ]

    if result_line:
        lines.append(_("Result: %(result)s") % {"result": result_line})

    lines.append(_("Location: %(location)s") % {"location": location_label})

    if thumbnail_url:
        lines.append(_("Image preview: %(url)s") % {"url": thumbnail_url})

    message = "\n".join(lines)

    summary_parts = [blueprint_name, activity_label]
    if result_line:
        summary_parts.append(result_line)
    summary_text = " — ".join(str(part) for part in summary_parts if part)

    metadata = {
        "character_name": character_name,
        "job_id": getattr(job, "job_id", None),
        "blueprint_name": blueprint_name,
        "activity_label": activity_label,
        "result": result_line,
        "location": location_label,
    }

    return JobNotificationPayload(
        title=title,
        message=message,
        summary=summary_text,
        thumbnail_url=thumbnail_url,
        metadata=metadata,
    )


def _resolve_character_name(job) -> str:
    explicit_name = getattr(job, "character_name", None)
    if explicit_name:
        return explicit_name

    for field in ("character_id", "installer_id"):
        identifier = getattr(job, field, None)
        if identifier:
            name = get_character_name(identifier)
            if name:
                return name

    owner = getattr(job, "owner_user", None)
    if owner and getattr(owner, "username", None):
        return owner.username

    return _("Unknown pilot")


def _resolve_blueprint(job) -> Blueprint | None:
    blueprint_id = getattr(job, "blueprint_id", None)
    blueprint_type_id = getattr(job, "blueprint_type_id", None)
    owner = getattr(job, "owner_user", None)
    owner_kind = getattr(job, "owner_kind", None)

    if not owner:
        return None

    # AA Example App
    from indy_hub.models import Blueprint

    query = Blueprint.objects.filter(owner_user=owner)
    if owner_kind:
        query = query.filter(owner_kind=owner_kind)

    if blueprint_id:
        candidate = (
            query.filter(blueprint_id=blueprint_id).order_by("-last_updated").first()
        )
        if candidate:
            return candidate

    if blueprint_type_id:
        return query.filter(type_id=blueprint_type_id).order_by("-last_updated").first()

    return None


def _resolve_blueprint_name(job, blueprint) -> str:
    if getattr(job, "blueprint_type_name", None):
        return job.blueprint_type_name
    if blueprint and getattr(blueprint, "type_name", None):
        return blueprint.type_name
    blueprint_type_id = getattr(job, "blueprint_type_id", None)
    if blueprint_type_id:
        return str(blueprint_type_id)
    return _("Unknown blueprint")


_ACTIVITY_LABELS = {
    1: _("Manufacturing"),
    3: _("Time Efficiency Research"),
    4: _("Material Efficiency Research"),
    5: _("Copying"),
    7: _("Reverse Engineering"),
    8: _("Invention"),
    9: _("Reactions"),
    11: _("Reactions"),
}


def _resolve_activity_label(job) -> str:
    label = getattr(job, "activity_name", None)
    if label:
        return label
    activity_id = getattr(job, "activity_id", None)
    if activity_id in _ACTIVITY_LABELS:
        return _ACTIVITY_LABELS[activity_id]
    return _("Industry job")


def _resolve_result(job, blueprint) -> str | None:
    activity_id = getattr(job, "activity_id", None)
    runs = _coalesce(getattr(job, "successful_runs", None), getattr(job, "runs", None))

    if activity_id == 3:  # Time Efficiency Research
        return _describe_efficiency_result(
            current=getattr(blueprint, "time_efficiency", None),
            increment=runs,
            label="TE",
        )
    if activity_id == 4:  # Material Efficiency Research
        return _describe_efficiency_result(
            current=getattr(blueprint, "material_efficiency", None),
            increment=runs,
            label="ME",
        )
    if activity_id == 5:  # Copying
        if runs:
            licensed = getattr(job, "licensed_runs", None)
            if licensed:
                return _("Copies: %(copies)s (runs: %(runs)s)") % {
                    "copies": runs,
                    "runs": licensed,
                }
            return _("Copies: %(copies)s") % {"copies": runs}
        return None
    if activity_id == 1:  # Manufacturing
        product_name = getattr(job, "product_type_name", None) or _("Unknown product")
        if runs:
            return _("Product: %(name)s (qty %(qty)s)") % {
                "name": product_name,
                "qty": runs,
            }
        return _("Product: %(name)s") % {"name": product_name}
    if runs:
        return _("Completed runs: %(count)s") % {"count": runs}
    return None


def _describe_efficiency_result(
    *, current: int | None, increment: int | None, label: str
) -> str | None:
    if increment in (None, 0):
        if current is not None:
            return f"{label} {current}"
        return None

    if current is None:
        return f"{label} +{increment}"

    previous = max(0, current - increment)
    return f"{label} {previous} -> {current}"


def _resolve_location(job) -> str:
    location = getattr(job, "location_name", None)
    if location:
        return location
    return _("Unknown location")


def _resolve_image_url(job, blueprint) -> str | None:
    activity_id = getattr(job, "activity_id", None)

    def _blueprint_type_id() -> int | None:
        for attr in ("blueprint_type_id",):
            value = getattr(job, attr, None)
            if value:
                return value
        if blueprint is not None and getattr(blueprint, "type_id", None):
            return blueprint.type_id
        return None

    def _product_type_id() -> int | None:
        for attr in ("product_type_id", "blueprint_type_id"):
            value = getattr(job, attr, None)
            if value:
                return value
        if blueprint is not None:
            for attr in ("product_type_id", "type_id"):
                value = getattr(blueprint, attr, None)
                if value:
                    return value
        return None

    if activity_id in {3, 4}:  # TE / ME research
        type_id = _blueprint_type_id()
        suffix = "bp"
    elif activity_id == 5:  # Copying
        type_id = _blueprint_type_id()
        suffix = "bpc"
    else:  # Manufacturing, reactions, or other
        type_id = _product_type_id()
        suffix = "icon"

    if not type_id:
        return None

    return f"https://images.evetech.net/types/{type_id}/{suffix}"


def _coalesce(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def serialize_job_notification_for_digest(
    job,
    payload: JobNotificationPayload,
) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot for digest aggregation."""

    data: dict[str, Any] = {
        "job_id": getattr(job, "job_id", None),
        "summary": payload.summary,
        "message": payload.message,
        "thumbnail_url": payload.thumbnail_url,
        "recorded_at": timezone.now().isoformat(),
    }
    if payload.metadata:
        data["metadata"] = payload.metadata
    return data


def build_digest_notification_body(
    entries: list[dict[str, Any]],
) -> tuple[str, str, str | None]:
    """Return (title, body, thumbnail) for a digest message."""

    if not entries:
        raise ValueError("Digest entries list cannot be empty")

    summaries = [entry.get("summary") for entry in entries if entry.get("summary")]
    count = len(entries)
    title = _("Industry jobs summary · %(count)s completion(s)") % {"count": count}

    if summaries:
        bullet_lines = [f"• {summary}" for summary in summaries]
        body = "\n".join(bullet_lines)
    else:
        body = _("No job details captured for this digest.")

    thumbnail_url = next(
        (entry.get("thumbnail_url") for entry in entries if entry.get("thumbnail_url")),
        None,
    )

    return title, body, thumbnail_url


def _enqueue_job_notification_digest(
    *,
    user,
    job: IndustryJob,
    payload: JobNotificationPayload,
    settings: CharacterSettings,
) -> None:
    job_id = getattr(job, "job_id", None)
    if job_id is None:
        logger.debug("Skipping digest queue; job has no job_id")
        return

    snapshot = serialize_job_notification_for_digest(job, payload)
    entry, created = JobNotificationDigestEntry.objects.update_or_create(
        user=user,
        job_id=job_id,
        defaults={
            "payload": snapshot,
            "sent_at": None,
        },
    )

    if created:
        logger.debug(
            "Queued job %s for digest notifications (user=%s)",
            job_id,
            getattr(user, "username", user),
        )
    else:
        logger.debug(
            "Updated digest entry for job %s (user=%s)",
            job_id,
            getattr(user, "username", user),
        )

    now = timezone.now()
    if (
        not settings.jobs_next_digest_at
        or settings.jobs_next_digest_at <= now
        or settings.jobs_notify_frequency == CharacterSettings.NOTIFY_CUSTOM
    ):
        settings.schedule_next_digest(reference=now)
        settings.save(update_fields=["jobs_next_digest_at", "updated_at"])


def _mark_job_notified(job: IndustryJob) -> None:
    IndustryJob.objects.filter(pk=job.pk).update(job_completed_notified=True)
    job.job_completed_notified = True


def process_job_completion_notification(job: IndustryJob) -> bool:
    """Send the appropriate notification for a finished job if needed.

    Returns True when the job required processing (and is now marked notified).
    """

    if not job or job.job_completed_notified:
        return False

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
        return False

    user = getattr(job, "owner_user", None)
    if not user:
        _mark_job_notified(job)
        return True

    settings = CharacterSettings.objects.filter(user=user, character_id=0).first()
    if not settings:
        _mark_job_notified(job)
        return True

    frequency = settings.jobs_notify_frequency or (
        CharacterSettings.NOTIFY_IMMEDIATE
        if settings.jobs_notify_completed
        else CharacterSettings.NOTIFY_DISABLED
    )

    if frequency == CharacterSettings.NOTIFY_DISABLED:
        _mark_job_notified(job)
        return True

    payload = build_job_notification_payload(job)
    if frequency == CharacterSettings.NOTIFY_IMMEDIATE:
        jobs_url = build_site_url(reverse("indy_hub:personnal_job_list"))
        try:
            notify_user(
                user,
                payload.title,
                payload.message,
                level="success",
                link=jobs_url,
                link_label=_("View job dashboard"),
                thumbnail_url=payload.thumbnail_url,
            )
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
    else:
        _enqueue_job_notification_digest(
            user=user,
            job=job,
            payload=payload,
            settings=settings,
        )

    _mark_job_notified(job)
    return True
