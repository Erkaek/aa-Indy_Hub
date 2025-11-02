"""Celery tasks related to job notifications."""

# Standard Library
import logging

# Third Party
from celery import shared_task

# Django
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Indy Hub
from ..models import CharacterSettings, JobNotificationDigestEntry
from ..notifications import build_site_url, notify_user
from ..utils.job_notifications import build_digest_notification_body

logger = logging.getLogger(__name__)


@shared_task
def dispatch_job_notification_digests() -> dict[str, int]:
    """Send grouped industry job notifications based on user preferences."""

    now = timezone.now()
    processed = 0
    skipped = 0

    eligible_settings = (
        CharacterSettings.objects.select_related("user")
        .filter(
            character_id=0,
            jobs_notify_completed=True,
            jobs_notify_frequency__in=[
                CharacterSettings.NOTIFY_DAILY,
                CharacterSettings.NOTIFY_WEEKLY,
                CharacterSettings.NOTIFY_MONTHLY,
                CharacterSettings.NOTIFY_CUSTOM,
            ],
            jobs_next_digest_at__isnull=False,
            jobs_next_digest_at__lte=now,
        )
        .order_by("user__id")
    )

    jobs_url = build_site_url(reverse("indy_hub:personnal_job_list"))

    for settings in eligible_settings:
        user = settings.user
        pending_entries = list(
            JobNotificationDigestEntry.objects.filter(
                user=user,
                sent_at__isnull=True,
            ).order_by("created_at")
        )

        if not pending_entries:
            settings.schedule_next_digest(reference=now)
            settings.save(update_fields=["jobs_next_digest_at", "updated_at"])
            skipped += 1
            continue

        payload_rows = [entry.payload or {} for entry in pending_entries]
        if not payload_rows:
            logger.debug("No payload data for user %s digest", user)
            settings.schedule_next_digest(reference=now)
            settings.save(update_fields=["jobs_next_digest_at", "updated_at"])
            skipped += 1
            continue

        visible_rows = payload_rows[:10]
        try:
            title, body, thumbnail_url = build_digest_notification_body(visible_rows)
        except ValueError:
            title = _("Industry jobs summary")
            body = _(
                "Jobs were completed, but no details were captured for this digest."
            )
            thumbnail_url = None

        remaining_count = len(payload_rows) - len(visible_rows)
        if remaining_count > 0:
            body = f"{body}\n• +{remaining_count} more completion(s)"

        try:
            notify_user(
                user,
                title,
                body,
                level="info",
                link=jobs_url,
                link_label=_("Open industry jobs"),
                thumbnail_url=thumbnail_url,
            )
        except Exception:  # pragma: no cover - defensive fallback
            logger.error(
                "Failed to send digest notification for user %s",
                getattr(user, "username", user),
                exc_info=True,
            )
            skipped += 1
            continue

        sent_at = timezone.now()
        for entry in pending_entries:
            entry.mark_sent()
            entry.save(update_fields=["sent_at", "updated_at"])

        settings.jobs_last_digest_at = sent_at
        settings.schedule_next_digest(reference=sent_at)
        settings.save(
            update_fields=["jobs_last_digest_at", "jobs_next_digest_at", "updated_at"]
        )
        processed += 1

    return {"processed": processed, "skipped": skipped}
