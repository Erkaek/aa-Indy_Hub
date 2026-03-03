# Standard Library
import os

# Django
from django.core.management.base import BaseCommand, CommandError

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.services.sde_sync import sync_sde_compat_tables

logger = get_extension_logger(__name__)


class Command(BaseCommand):
    help = "Sync Indy Hub SDE compatibility tables (market groups + industry activity data)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sde-folder",
            type=str,
            default="",
            help="Path to the extracted SDE JSONL folder (defaults to INDY_HUB_SDE_FOLDER or 'eve-sde').",
        )

    def handle(self, *args, **options):
        # Django
        from django.conf import settings

        downloaded_temp_sde = False
        sde_folder = (options.get("sde_folder") or "").strip()
        if not sde_folder:
            sde_folder = getattr(settings, "INDY_HUB_SDE_FOLDER", "").strip()

        if not sde_folder:
            try:
                # Alliance Auth (External Libs)
                from eve_sde.sde_tasks import SDE_FOLDER

                sde_folder = SDE_FOLDER
            except Exception:
                sde_folder = "eve-sde"

        if not os.path.isdir(sde_folder):
            self.stdout.write(
                self.style.WARNING(
                    f"SDE folder not found: {sde_folder}. Downloading temporary SDE data..."
                )
            )
            try:
                # Alliance Auth (External Libs)
                from eve_sde.sde_tasks import SDE_FOLDER, download_extract_sde

                download_extract_sde()
                sde_folder = SDE_FOLDER
                downloaded_temp_sde = True
            except Exception as ex:
                raise CommandError(
                    "SDE folder not found and automatic SDE download failed. "
                    "Try running `python manage.py indy_sde --with-esde-load` and retry."
                ) from ex

            if not os.path.isdir(sde_folder):
                raise CommandError(
                    f"SDE folder not found after automatic download attempt: {sde_folder}"
                )

        self.stdout.write(f"Syncing Indy Hub SDE compatibility data from: {sde_folder}")
        logger.info("Starting SDE compatibility sync from %s", sde_folder)

        try:
            summary = sync_sde_compat_tables(sde_folder=sde_folder)
        finally:
            if downloaded_temp_sde:
                try:
                    # Alliance Auth (External Libs)
                    from eve_sde.sde_tasks import delete_sde_folder

                    delete_sde_folder()
                except Exception:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Downloaded SDE folder could not be deleted automatically: {sde_folder}"
                        )
                    )

        self.stdout.write(self.style.SUCCESS("SDE compatibility sync completed."))
        self.stdout.write(self.style.SUCCESS(str(summary)))
