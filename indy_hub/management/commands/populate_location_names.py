"""Management command to populate blueprint and industry job location names."""

from __future__ import annotations

# Standard Library
import logging
from collections.abc import Iterable

# Django
from django.core.management.base import BaseCommand

# AA Example App
from indy_hub.services.location_population import populate_location_names

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Populate the location_name fields for indy hub blueprints and industry jobs. "
        "Use --enqueue to run asynchronously via Celery."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--location-id",
            dest="location_ids",
            action="append",
            type=int,
            help="Limit the run to one or more specific structure/station IDs (repeatable).",
        )
        parser.add_argument(
            "--force-refresh",
            dest="force_refresh",
            action="store_true",
            help="Force ESI refresh even when cached placeholder values exist.",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Compute the number of updates without writing any changes.",
        )
        parser.add_argument(
            "--enqueue",
            dest="enqueue",
            action="store_true",
            help="Queue the job asynchronously via Celery instead of running inline.",
        )

    def handle(self, *args, **options):
        location_ids: Iterable[int] | None = options.get("location_ids")
        force_refresh: bool = options.get("force_refresh", False)
        dry_run: bool = options.get("dry_run", False)
        enqueue: bool = options.get("enqueue", False)

        normalized_ids = None
        if location_ids:
            normalized_ids = [int(value) for value in location_ids if value is not None]
            if not normalized_ids:
                self.stdout.write(self.style.WARNING("No valid location IDs provided."))
                return

        if enqueue:
            # AA Example App
            from indy_hub.tasks.industry import populate_location_names_async

            result = populate_location_names_async.delay(
                location_ids=normalized_ids,
                force_refresh=force_refresh,
                dry_run=dry_run,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enqueued populate_location_names_async task with id {result.id}."
                )
            )
            return

        summary = populate_location_names(
            location_ids=normalized_ids,
            force_refresh=force_refresh,
            dry_run=dry_run,
            logger_override=logger,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Location name population completed: "
                f"{summary.get('blueprints', 0)} blueprints, "
                f"{summary.get('jobs', 0)} jobs across {summary.get('locations', 0)} locations."
            )
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry-run mode: no records were updated.")
            )
