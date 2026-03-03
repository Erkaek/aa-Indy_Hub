# Django
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Alias for sync_sde_compat with identical behavior."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sde-folder",
            type=str,
            default="",
            help=(
                "Path to the extracted SDE JSONL folder "
                "(defaults to INDY_HUB_SDE_FOLDER or 'eve-sde')."
            ),
        )

    def handle(self, *args, **options):
        verbosity = int(options.get("verbosity", 1))
        sde_folder = (options.get("sde_folder") or "").strip()
        call_command("sync_sde_compat", sde_folder=sde_folder, verbosity=verbosity)
