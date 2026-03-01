# Django
from django.conf import settings
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = (
        "Populate Indy Hub SDE compatibility data without reloading full eve_sde by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--sde-folder",
            type=str,
            default="",
            help=(
                "Path to extracted SDE JSONL folder for sync_sde_compat "
                "(if omitted, latest SDE is downloaded first)."
            ),
        )
        parser.add_argument(
            "--with-esde-load",
            action="store_true",
            help="Also run full `esde_load_sde` before syncing compatibility tables.",
        )
        parser.add_argument(
            "--keep-downloaded-sde",
            action="store_true",
            help="Keep downloaded SDE folder on disk after sync.",
        )

    def handle(self, *args, **options):
        verbosity = int(options.get("verbosity", 1))
        sde_folder = (options.get("sde_folder") or "").strip()
        downloaded_sde = False

        if options.get("with_esde_load", False):
            self.stdout.write(
                self.style.NOTICE("[1/3] Running full eve_sde load (esde_load_sde)...")
            )
            call_command("esde_load_sde", verbosity=verbosity)
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "[1/3] Skipping full eve_sde load (use --with-esde-load to enable)."
                )
            )

        if not sde_folder:
            # AA Example App
            from eve_sde.sde_tasks import SDE_FOLDER, download_extract_sde

            self.stdout.write(
                self.style.NOTICE("[2/3] Downloading latest SDE JSONL files...")
            )
            download_extract_sde()
            sde_folder = SDE_FOLDER
            downloaded_sde = True
        else:
            self.stdout.write(
                self.style.NOTICE(f"[2/3] Using provided SDE folder: {sde_folder}")
            )

        self.stdout.write(
            self.style.NOTICE(
                f"[3/3] Syncing Indy Hub compatibility tables (folder={sde_folder})..."
            )
        )
        call_command("sync_sde_compat", sde_folder=sde_folder, verbosity=verbosity)

        if downloaded_sde and not options.get("keep_downloaded_sde", False):
            try:
                # AA Example App
                from eve_sde.sde_tasks import delete_sde_folder

                delete_sde_folder()
            except Exception:
                self.stdout.write(
                    self.style.WARNING(
                        f"Downloaded SDE folder could not be deleted automatically: {sde_folder}"
                    )
                )

        self.stdout.write(self.style.SUCCESS("Indy Hub SDE population completed."))
