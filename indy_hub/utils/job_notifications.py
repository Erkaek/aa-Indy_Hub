"""Helpers to build rich notifications for industry jobs."""

from __future__ import annotations

# Standard Library
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Django
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.encoding import force_str
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Indy Hub
from ..models import (
    Blueprint,
    CharacterSettings,
    CorporationSharingSetting,
    IndustryJob,
    JobNotificationDigestEntry,
)
from ..notifications import build_site_url, notify_user
from .eve import get_character_name

logger = get_extension_logger(__name__)


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


def _coerce_json_value(value: Any) -> Any:
    if isinstance(value, Promise):
        return force_str(value)
    if isinstance(value, dict):
        return {key: _coerce_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_json_value(item) for item in value]
    return value


def serialize_job_notification_for_digest(
    job,
    payload: JobNotificationPayload,
) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot for digest aggregation."""

    data: dict[str, Any] = {
        "job_id": getattr(job, "job_id", None),
        "summary": _coerce_json_value(payload.summary),
        "message": _coerce_json_value(payload.message),
        "thumbnail_url": _coerce_json_value(payload.thumbnail_url),
        "recorded_at": timezone.now().isoformat(),
    }
    if payload.metadata:
        data["metadata"] = _coerce_json_value(payload.metadata)
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
    scope: str = JobNotificationDigestEntry.SCOPE_PERSONAL,
) -> None:
    job_id = getattr(job, "job_id", None)
    if job_id is None:
        logger.debug("Skipping digest queue; job has no job_id")
        return

    snapshot = serialize_job_notification_for_digest(job, payload)
    lookup = {
        "user": user,
        "job_id": job_id,
        "scope": scope,
    }
    if scope == JobNotificationDigestEntry.SCOPE_CORPORATION:
        corp_id = getattr(job, "corporation_id", None)
        try:
            corp_id = int(corp_id)
        except (TypeError, ValueError):
            corp_id = 0
        lookup["corporation_id"] = corp_id

    entry, created = JobNotificationDigestEntry.objects.update_or_create(
        **lookup,
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
    if scope == JobNotificationDigestEntry.SCOPE_CORPORATION:
        if (
            not settings.corp_jobs_next_digest_at
            or settings.corp_jobs_next_digest_at <= now
            or settings.corp_jobs_notify_frequency
            in {CharacterSettings.NOTIFY_CUSTOM, CharacterSettings.NOTIFY_CUSTOM_HOURS}
        ):
            settings.schedule_next_corp_digest(reference=now)
            settings.save(update_fields=["corp_jobs_next_digest_at", "updated_at"])
    else:
        if (
            not settings.jobs_next_digest_at
            or settings.jobs_next_digest_at <= now
            or settings.jobs_notify_frequency
            in {CharacterSettings.NOTIFY_CUSTOM, CharacterSettings.NOTIFY_CUSTOM_HOURS}
        ):
            settings.schedule_next_digest(reference=now)
            settings.save(update_fields=["jobs_next_digest_at", "updated_at"])


def _resolve_user_corporation_id(user: User | None) -> int | None:
    if not user:
        return None

    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership, UserProfile
    except Exception:  # pragma: no cover
        return None

    profile = getattr(user, "profile", None)
    main_character = getattr(profile, "main_character", None) if profile else None
    if not main_character:
        try:
            profile = UserProfile.objects.select_related("main_character").get(
                user=user
            )
        except Exception:
            profile = None
        else:
            main_character = getattr(profile, "main_character", None)

    if main_character and getattr(main_character, "corporation_id", None):
        try:
            return int(main_character.corporation_id)
        except (TypeError, ValueError):
            return None

    ownership_qs = CharacterOwnership.objects.filter(user=user).select_related(
        "character"
    )
    try:
        CharacterOwnership._meta.get_field("is_main")
    except Exception:
        ownership = ownership_qs.first()
    else:
        ownership = ownership_qs.order_by("-is_main").first()

    character = getattr(ownership, "character", None) if ownership else None
    if character and getattr(character, "corporation_id", None):
        try:
            return int(character.corporation_id)
        except (TypeError, ValueError):
            return None

    return None


def _user_is_member_of_corporation(user: User | None, corporation_id: int) -> bool:
    if not user:
        return False

    try:
        corp_id = int(corporation_id)
    except (TypeError, ValueError):
        return False

    profile = getattr(user, "profile", None)
    main_character = getattr(profile, "main_character", None) if profile else None
    if main_character and getattr(main_character, "corporation_id", None) is not None:
        try:
            if int(main_character.corporation_id) == corp_id:
                return True
        except (TypeError, ValueError):
            pass

    try:
        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership
    except Exception:  # pragma: no cover
        return False

    return CharacterOwnership.objects.filter(
        user=user,
        character__corporation_id=corp_id,
    ).exists()


def _compute_next_digest_at(
    *,
    frequency: str,
    custom_days: int | None,
    custom_hours: int | None = None,
    reference: datetime | None = None,
) -> datetime | None:
    if reference is None:
        reference = timezone.now()

    if frequency in {
        CharacterSettings.NOTIFY_DISABLED,
        CharacterSettings.NOTIFY_IMMEDIATE,
    }:
        return None

    if frequency == CharacterSettings.NOTIFY_DAILY:
        return reference + timedelta(days=1)
    if frequency == CharacterSettings.NOTIFY_WEEKLY:
        return reference + timedelta(days=7)
    if frequency == CharacterSettings.NOTIFY_MONTHLY:
        return reference + timedelta(days=30)
    if frequency == CharacterSettings.NOTIFY_CUSTOM_HOURS:
        try:
            hours = int(custom_hours or 1)
        except (TypeError, ValueError):
            hours = 1
        hours = max(1, hours)
        return reference + timedelta(hours=hours)

    try:
        days = int(custom_days or 1)
    except (TypeError, ValueError):
        days = 1
    days = max(1, days)
    return reference + timedelta(days=days)


def compute_next_digest_at(
    *,
    frequency: str,
    custom_days: int | None = None,
    custom_hours: int | None = None,
    reference: datetime | None = None,
) -> datetime | None:
    """Compute the next digest timestamp for the given cadence."""

    return _compute_next_digest_at(
        frequency=frequency,
        custom_days=custom_days,
        custom_hours=custom_hours,
        reference=reference,
    )


def _enqueue_corp_job_notification_digest(
    *,
    user,
    job: IndustryJob,
    payload: JobNotificationPayload,
    setting: CorporationSharingSetting,
) -> None:
    job_id = getattr(job, "job_id", None)
    corporation_id = getattr(job, "corporation_id", None)
    if job_id is None or not corporation_id:
        return

    try:
        corporation_id = int(corporation_id)
    except (TypeError, ValueError):
        return

    snapshot = serialize_job_notification_for_digest(job, payload)
    JobNotificationDigestEntry.objects.update_or_create(
        user=user,
        job_id=job_id,
        scope=JobNotificationDigestEntry.SCOPE_CORPORATION,
        corporation_id=corporation_id,
        defaults={
            "payload": snapshot,
            "sent_at": None,
        },
    )

    now = timezone.now()
    if (
        not setting.corp_jobs_next_digest_at
        or setting.corp_jobs_next_digest_at <= now
        or setting.corp_jobs_notify_frequency
        in {CharacterSettings.NOTIFY_CUSTOM, CharacterSettings.NOTIFY_CUSTOM_HOURS}
    ):
        setting.corp_jobs_next_digest_at = _compute_next_digest_at(
            frequency=setting.corp_jobs_notify_frequency,
            custom_days=setting.corp_jobs_notify_custom_days,
            custom_hours=setting.corp_jobs_notify_custom_hours,
            reference=now,
        )
        setting.save(update_fields=["corp_jobs_next_digest_at", "updated_at"])


def _eligible_corporation_notification_settings(corporation_id: int):
    """Yield CorporationSharingSetting entries eligible to receive corp job notifications."""

    try:
        corp_id = int(corporation_id)
    except (TypeError, ValueError):
        return

    enabled_settings = (
        CorporationSharingSetting.objects.select_related("user")
        .filter(corporation_id=corp_id)
        .exclude(corp_jobs_notify_frequency=CharacterSettings.NOTIFY_DISABLED)
        .order_by("user__id")
    )

    for setting in enabled_settings.iterator():
        user = setting.user
        if not getattr(user, "is_active", True):
            continue
        if not user.has_perm("indy_hub.can_manage_corp_bp_requests"):
            continue
        if not _user_is_member_of_corporation(user, corp_id):
            continue
        yield setting


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

    is_corp_job = getattr(job, "owner_kind", None) == Blueprint.OwnerKind.CORPORATION

    if is_corp_job:
        corporation_id = getattr(job, "corporation_id", None)
        if not corporation_id:
            _mark_job_notified(job)
            return True

        eligible_settings = list(
            _eligible_corporation_notification_settings(int(corporation_id))
        )
        if not eligible_settings:
            _mark_job_notified(job)
            return True

        payload = build_job_notification_payload(job)
        jobs_url = build_site_url(reverse("indy_hub:corporation_job_list"))

        for corp_setting in eligible_settings:
            user = corp_setting.user
            frequency = (
                corp_setting.corp_jobs_notify_frequency
                or CharacterSettings.NOTIFY_DISABLED
            )
            if frequency == CharacterSettings.NOTIFY_DISABLED:
                continue

            if frequency == CharacterSettings.NOTIFY_IMMEDIATE:
                try:
                    notify_user(
                        user,
                        payload.title,
                        payload.message,
                        level="success",
                        link=jobs_url,
                        link_label=_("View corporation jobs"),
                        thumbnail_url=payload.thumbnail_url,
                    )
                except Exception:  # pragma: no cover - defensive fallback
                    logger.error(
                        "Failed to notify user %s about corp job %s",
                        getattr(user, "username", user),
                        job.job_id,
                        exc_info=True,
                    )
            else:
                _enqueue_corp_job_notification_digest(
                    user=user,
                    job=job,
                    payload=payload,
                    setting=corp_setting,
                )

        _mark_job_notified(job)
        return True

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
