"""Management command to populate blueprint and industry job location names."""

from __future__ import annotations

# Standard Library
from collections.abc import Iterable

# Django
from django.apps import apps
from django.core.management.base import BaseCommand

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.services.location_population import populate_location_names
from indy_hub.utils.eve import (
    _STATION_ID_MAX,
    _STATION_ID_MIN,
    PLACEHOLDER_PREFIX,
    resolve_location_name,
)

logger = get_extension_logger(__name__)


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
        parser.add_argument(
            "--repair-stations",
            dest="repair_stations",
            action="store_true",
            help=(
                "Find all CachedStructureName rows whose name is a placeholder "
                "(Structure <id>) for NPC station IDs (60 000 000–69 999 999) "
                "and re-resolve them using the public /universe/stations/ endpoint. "
                "Combine with --dry-run to only report without writing."
            ),
        )

    def handle(self, *args, **options):
        location_ids: Iterable[int] | None = options.get("location_ids")
        force_refresh: bool = options.get("force_refresh", False)
        dry_run: bool = options.get("dry_run", False)
        enqueue: bool = options.get("enqueue", False)
        repair_stations: bool = options.get("repair_stations", False)

        if repair_stations:
            self._repair_station_names(dry_run=dry_run)
            return

        logger.info(
            "populate_location_names invoked (enqueue=%s, force_refresh=%s, dry_run=%s, location_ids=%s)",
            enqueue,
            force_refresh,
            dry_run,
            location_ids,
        )

        normalized_ids = None
        if location_ids:
            normalized_ids = [int(value) for value in location_ids if value is not None]
            if not normalized_ids:
                self.stdout.write(self.style.WARNING("No valid location IDs provided."))
                logger.warning(
                    "populate_location_names aborted: no valid location IDs."
                )
                return

        if enqueue:
            # AA Example App
            from indy_hub.tasks.industry import populate_location_names_async

            try:
                result = populate_location_names_async.delay(
                    location_ids=normalized_ids,
                    force_refresh=force_refresh,
                    dry_run=dry_run,
                )
                logger.info(
                    "populate_location_names_async enqueued (task_id=%s)",
                    getattr(result, "id", "<unknown>"),
                )
            except Exception as exc:
                logger.exception(
                    "Failed to enqueue populate_location_names_async: %s", exc
                )
                raise
            self.stdout.write(
                self.style.SUCCESS(
                    f"Enqueued populate_location_names_async task with id {result.id}."
                )
            )
            return

        try:
            summary = populate_location_names(
                location_ids=normalized_ids,
                force_refresh=force_refresh,
                dry_run=dry_run,
                logger_override=logger,
            )
        except Exception as exc:
            logger.exception("populate_location_names failed: %s", exc)
            raise

        logger.info(
            "populate_location_names completed (blueprints=%s, jobs=%s, locations=%s, dry_run=%s)",
            summary.get("blueprints", 0),
            summary.get("jobs", 0),
            summary.get("locations", 0),
            dry_run,
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

    def _repair_station_names(self, *, dry_run: bool) -> None:
        """Re-resolve all CachedStructureName placeholders for NPC station IDs."""
        try:
            cached_model = apps.get_model("indy_hub", "CachedStructureName")
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"CachedStructureName model not found: {exc}")
            )
            return

        placeholder_rows = list(
            cached_model.objects.filter(
                structure_id__gte=_STATION_ID_MIN,
                structure_id__lte=_STATION_ID_MAX,
                name__startswith=PLACEHOLDER_PREFIX,
            ).values_list("structure_id", flat=True)
        )

        if not placeholder_rows:
            self.stdout.write(self.style.SUCCESS("No placeholder station names found."))
            return

        self.stdout.write(
            f"Found {len(placeholder_rows)} station(s) with placeholder names "
            f"(IDs {_STATION_ID_MIN:,}–{_STATION_ID_MAX:,})."
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry-run: would repair the above stations.")
            )
            return

        fixed = 0
        failed = 0
        for station_id in placeholder_rows:
            name = resolve_location_name(int(station_id), force_refresh=True)
            if name and not name.startswith(PLACEHOLDER_PREFIX):
                fixed += 1
                logger.info("Repaired station %s → %s", station_id, name)
            else:
                failed += 1
                logger.warning(
                    "Could not resolve station %s (got: %s)", station_id, name
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Station name repair: {fixed} fixed, {failed} unresolved."
            )
        )
