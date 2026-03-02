"""Tasks for syncing Indy Hub SDE compatibility tables."""

# Standard Library
import os

# Third Party
from celery import shared_task

# Django
from django.conf import settings

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

# AA Example App
from indy_hub.services.sde_sync import sync_sde_compat_tables

logger = get_extension_logger(__name__)


@shared_task(bind=True, base=QueueOnce)
def sync_sde_compatibility_data(self):
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

    summary = sync_sde_compat_tables(sde_folder=sde_folder)

    if downloaded_folder:
        try:
            # Alliance Auth (External Libs)
            from eve_sde.sde_tasks import delete_sde_folder

            delete_sde_folder()
        except Exception:
            logger.warning("Failed to delete temporary SDE folder %s", sde_folder)

    return summary
