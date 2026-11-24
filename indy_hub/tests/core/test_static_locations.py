"""Public location reads stay local and honor the installed SDE generation."""

# Standard Library
import json
from pathlib import Path
from unittest.mock import patch

# Django
from django.test import TestCase

# Alliance Auth (External Libs)
from eve_sde import models as sde

# AA Example App
from indy_hub.models import StaticLocationName
from indy_hub.services.esi_client import ESIClient
from indy_hub.services.static_locations import sync_static_location_names


class StaticLocationTests(TestCase):
    def setUp(self):
        self.release = "20261120T165826Z"
        state = sde.EveSDE.get_solo()
        state.release = self.release
        state.save()

    def test_reads_use_local_snapshot_even_during_esi_outage(self):
        StaticLocationName.objects.create(
            location_id=60003760, name="Local station", release=self.release + ":1"
        )
        client = ESIClient()
        with patch.object(
            client,
            "_enforce_task_scope_budget",
            side_effect=AssertionError("ESI forbidden"),
        ):
            self.assertEqual(client.fetch_station_name(60003760), "Local station")
            self.assertEqual(
                client.resolve_ids_to_names([60003760, 30000142]),
                {60003760: "Local station"},
            )
        sde.EveSDE.objects.filter(pk=1).update(release="20261121T165826Z")
        sde.EveSDE.clear_cache()
        self.assertIsNone(client.fetch_station_name(60003760))

    def test_station_generation_uses_unique_orbit_names_and_skips_repeated_downloads(
        self,
    ):
        sde.SolarSystem.objects.create(id=30000142, name="Jita")
        sde.Planet.objects.create(
            id=40000001, name="Jita IV", solar_system_id=30000142, celestial_index=4
        )
        sde.Moon.objects.create(
            id=40000002,
            name="",
            solar_system_id=30000142,
            orbit_id_raw=40000001,
            orbit_index=4,
        )
        sde.NPCStation.objects.create(
            id=60003760,
            name="",
            orbit_id_raw=40000002,
            owner_id_raw=1000035,
            operation_id_raw=14,
            use_operation_name=True,
        )
        data = {
            "npcCorporations": [
                {"corporationID": 1000035, "name": {"en": "Caldari Navy"}}
            ],
            "stationOperations": [
                {"operationID": 14, "operationName": {"en": "Assembly Plant"}}
            ],
            "mapPlanets": [
                {"planetID": 40000001, "uniqueName": {"en": "Jita IV (Named)"}}
            ],
            "mapMoons": [{"moonID": 40000002}],
            "npcStations": [{"stationID": 60003760}],
        }
        versions = {name: f"{i:064x}" for i, name in enumerate(data, 1)}

        def download(url, target, budget):
            if url.endswith("/"):
                content = json.dumps(
                    {
                        "release": self.release,
                        "tables": {
                            name: {"version": v} for name, v in versions.items()
                        },
                    }
                )
            else:
                name = next(k for k, v in versions.items() if v in url)
                content = "".join(json.dumps(row) + "\n" for row in data[name])
            Path(target).write_text(content)
            return "0" * 64

        with patch(
            "indy_hub.services.static_locations.download", side_effect=download
        ) as http:
            sync_static_location_names()
            sync_static_location_names()
            self.assertEqual(http.call_count, 6)
        self.assertEqual(
            ESIClient().fetch_station_name(60003760),
            "Jita IV (Named) - Moon 4 - Caldari Navy Assembly Plant",
        )
