"""Tasks for syncing Indy Hub SDE compatibility tables."""

# Standard Library
import os
from datetime import datetime

# Third Party
import httpx
from celery import shared_task

# Django
from django.conf import settings
from django.utils import timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

# AA Example App
from indy_hub.models import SDESyncCompatState
from indy_hub.services.sde_sync import sync_sde_compat_tables

logger = get_extension_logger(__name__)

_SDE_VERSION_URL = (
    "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
)


def _fetch_latest_sde_source_metadata() -> tuple[int | None, datetime | None]:
    try:
        payload = httpx.get(_SDE_VERSION_URL, timeout=15).json()
        build_number = payload.get("buildNumber")
        release_date_raw = payload.get("releaseDate")

        build_number_value = int(build_number) if build_number is not None else None

        release_date_value = None
        if isinstance(release_date_raw, str) and release_date_raw.strip():
            normalized = release_date_raw.replace("Z", "+00:00")
            release_date_value = datetime.fromisoformat(normalized)

        return build_number_value, release_date_value
    except Exception:
        logger.warning(
            "Unable to fetch latest SDE source metadata; compatibility sync will proceed",
            exc_info=True,
        )
        return None, None


@shared_task(bind=True, base=QueueOnce)
def sync_sde_compatibility_data(self):
    source_build_number, source_release_date = _fetch_latest_sde_source_metadata()
    state, _ = SDESyncCompatState.objects.get_or_create(pk=1)

    if (
        source_build_number is not None
        and state.last_source_build_number == source_build_number
    ):
        logger.info(
            "Skipping Indy Hub SDE compatibility sync: source build %s already processed",
            source_build_number,
        )
        return {
            "skipped": 1,
            "reason": "source_build_unchanged",
            "source_build_number": source_build_number,
        }

    sde_folder = getattr(settings, "INDY_HUB_SDE_FOLDER", "").strip()
    downloaded_folder = False

    if not sde_folder:
        try:
            # Alliance Auth (External Libs)
            from eve_sde.sde_tasks import SDE_FOLDER

            sde_folder = SDE_FOLDER
        except Exception:
            sde_folder = "eve-sde"

    if not os.path.isdir(sde_folder):
        try:
            # Alliance Auth (External Libs)
            from eve_sde.sde_tasks import SDE_FOLDER, download_extract_sde

            logger.info(
                "SDE folder %s missing, downloading latest SDE archive for compatibility sync",
                sde_folder,
            )
            download_extract_sde()
            sde_folder = SDE_FOLDER
            downloaded_folder = True
        except Exception as exc:
            logger.error("Unable to prepare SDE source folder: %s", exc, exc_info=True)
            raise

    try:
        summary = sync_sde_compat_tables(sde_folder=sde_folder)
    finally:
        if downloaded_folder:
            try:
                # Alliance Auth (External Libs)
                from eve_sde.sde_tasks import delete_sde_folder

                delete_sde_folder()
            except Exception:
                logger.warning("Failed to delete temporary SDE folder %s", sde_folder)

    state.last_source_build_number = source_build_number
    state.last_source_release_date = source_release_date
    state.last_synced_at = timezone.now()
    state.save(
        update_fields=[
            "last_source_build_number",
            "last_source_release_date",
            "last_synced_at",
            "updated_at",
        ]
    )

    if source_build_number is not None:
        summary["source_build_number"] = source_build_number
    if source_release_date is not None:
        summary["source_release_date"] = source_release_date.isoformat()

    return summary
