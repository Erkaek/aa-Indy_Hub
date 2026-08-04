"""Management command to populate blueprint and industry job location names."""

from __future__ import annotations

# Standard Library
from collections.abc import Iterable

# Django
from django.apps import apps
from django.core.management.base import BaseCommand
from django.utils import timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.services.location_population import populate_location_names
from indy_hub.utils.eve import (
    _STATION_ID_MAX,
    _STATION_ID_MIN,
    PLACEHOLDER_PREFIX,
    get_type_name,
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
                "Repair placeholder location names by re-resolving NPC stations "
                "(60 000 000–69 999 999) and relabelling non-deployed asset-item IDs. "
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
        """Re-resolve NPC station placeholders and label non-deployed asset item placeholders."""
        try:
            cached_model = apps.get_model("indy_hub", "CachedStructureName")
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"CachedStructureName model not found: {exc}")
            )
            return

        # NPC stations (60 000 000 – 69 999 999): re-resolve via ESI
        station_rows = list(
            cached_model.objects.filter(
                structure_id__gte=_STATION_ID_MIN,
                structure_id__lte=_STATION_ID_MAX,
                name__startswith=PLACEHOLDER_PREFIX,
            ).values_list("structure_id", flat=True)
        )

        # Large IDs (>= 1T) that are non-deployed asset items: label from type
        try:
            corp_model = apps.get_model("indy_hub", "CachedCorporationAsset")
        except Exception:
            corp_model = None
        try:
            char_model = apps.get_model("indy_hub", "CachedCharacterAsset")
        except Exception:
            char_model = None

        asset_item_rows: list[tuple[int, int]] = []
        if corp_model is not None or char_model is not None:
            for sid in cached_model.objects.filter(
                structure_id__gte=1_000_000_000_000,
                name__startswith=PLACEHOLDER_PREFIX,
            ).values_list("structure_id", flat=True):
                sid = int(sid)
                type_id = None
                if corp_model is not None:
                    row = (
                        corp_model.objects.filter(item_id=sid)
                        .values("location_id", "type_id")
                        .first()
                    )
                    if row and int(row["location_id"] or 0) >= _STATION_ID_MIN:
                        type_id = row["type_id"]
                if type_id is None and char_model is not None:
                    row = (
                        char_model.objects.filter(item_id=sid)
                        .values("location_id", "type_id")
                        .first()
                    )
                    if row and int(row["location_id"] or 0) >= _STATION_ID_MIN:
                        type_id = row["type_id"]
                if type_id is not None:
                    asset_item_rows.append((sid, int(type_id)))

        if not station_rows and not asset_item_rows:
            self.stdout.write(self.style.SUCCESS("No placeholder names to repair."))
            return

        if station_rows:
            self.stdout.write(
                f"Found {len(station_rows)} NPC station(s) with placeholder names."
            )
        if asset_item_rows:
            self.stdout.write(
                f"Found {len(asset_item_rows)} non-deployed asset item(s) with placeholder names."
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry-run: would repair the above entries.")
            )
            return

        fixed = 0
        failed = 0

        for station_id in station_rows:
            name = resolve_location_name(int(station_id), force_refresh=True)
            if name and not name.startswith(PLACEHOLDER_PREFIX):
                self._persist_repaired_name(cached_model, int(station_id), name)
                fixed += 1
                logger.info("Repaired station %s → %s", station_id, name)
            else:
                failed += 1
                logger.warning(
                    "Could not resolve station %s (got: %s)", station_id, name
                )

        for sid, type_id in asset_item_rows:
            type_name = get_type_name(type_id) or str(type_id)
            resolved_name = f"{type_name} [asset]"
            try:
                self._persist_repaired_name(cached_model, sid, resolved_name)
                fixed += 1
                logger.info("Labelled non-deployed asset %s → %s", sid, resolved_name)
            except Exception as exc:
                failed += 1
                logger.warning("Could not update asset item %s: %s", sid, exc)

        self.stdout.write(
            self.style.SUCCESS(f"Repair completed: {fixed} fixed, {failed} unresolved.")
        )

    def _persist_repaired_name(self, cached_model, location_id: int, name: str) -> None:
        cached_model.objects.update_or_create(
            structure_id=int(location_id),
            defaults={"name": str(name), "last_resolved": timezone.now()},
        )

        blueprint_model = apps.get_model("indy_hub", "Blueprint")
        blueprint_model.objects.filter(location_id=int(location_id)).update(
            location_name=str(name)
        )

        industry_job_model = apps.get_model("indy_hub", "IndustryJob")
        industry_job_model.objects.filter(station_id=int(location_id)).update(
            location_name=str(name)
        )
