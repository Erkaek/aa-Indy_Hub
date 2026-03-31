"""Tests for corporation ESI structure sync helpers."""

from __future__ import annotations

# Standard Library
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.models import IndustryStructure
from indy_hub.services.industry_structure_sync import (
    _get_online_industry_activity_flags,
    sync_corporation_structure_targets,
)


class IndustryStructureSyncServiceTests(TestCase):
    def test_get_online_industry_activity_flags_maps_new_categories(self) -> None:
        payload = {
            "services": [
                {"name": "Manufacturing (Standard)", "state": "online"},
                {"name": "Manufacturing (Capitals)", "state": "online"},
                {"name": "manufacturing (super capitals)", "state": "online"},
                {"name": "Time Efficiency Research", "state": "online"},
                {"name": "Invention", "state": "online"},
                {"name": "Biochemical Reactions", "state": "online"},
                {"name": "Hybrid Reactions", "state": "offline"},
                {"name": "Composite Reactions", "state": "online"},
                {"name": "Moon Drilling", "state": "online"},
            ]
        }

        flags = _get_online_industry_activity_flags(payload)

        self.assertTrue(flags["enable_manufacturing"])
        self.assertTrue(flags["enable_manufacturing_capitals"])
        self.assertTrue(flags["enable_manufacturing_super_capitals"])
        self.assertTrue(flags["enable_research"])
        self.assertTrue(flags["enable_invention"])
        self.assertTrue(flags["enable_biochemical_reactions"])
        self.assertFalse(flags["enable_hybrid_reactions"])
        self.assertTrue(flags["enable_composite_reactions"])

    @patch(
        "indy_hub.services.industry_structure_sync.resolve_solar_system_location_reference"
    )
    @patch("indy_hub.services.industry_structure_sync.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structure_sync.resolve_item_type_reference")
    @patch(
        "indy_hub.services.industry_structure_sync.shared_client.fetch_corporation_structures"
    )
    def test_sync_corporation_structure_targets_creates_synced_structure(
        self,
        mock_fetch_corporation_structures,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
    ) -> None:
        mock_fetch_corporation_structures.return_value = [
            {
                "structure_id": 1020000000001,
                "name": "Azbel ESI",
                "type_id": 35826,
                "solar_system_id": 30000142,
                "services": [
                    {"name": "Manufacturing (Standard)", "state": "online"},
                    {"name": "Manufacturing (Capitals)", "state": "online"},
                    {"name": "Blueprint Copying", "state": "online"},
                    {"name": "Invention", "state": "online"},
                    {"name": "Composite Reactions", "state": "online"},
                ],
            }
        ]
        mock_resolve_item_type_reference.return_value = (35826, "Azbel")
        mock_resolve_solar_system_reference.return_value = (
            30000142,
            "Jita",
            IndustryStructure.SecurityBand.HIGHSEC,
        )
        mock_resolve_solar_system_location_reference.return_value = {
            "solar_system_id": 30000142,
            "solar_system_name": "Jita",
            "system_security_band": IndustryStructure.SecurityBand.HIGHSEC,
            "constellation_id": 20000020,
            "constellation_name": "Kimotoro",
            "region_id": 10000002,
            "region_name": "The Forge",
        }

        summary = sync_corporation_structure_targets(
            [{"id": 98134807, "name": "Acme Corp", "character_id": 2112625428}],
            force_refresh=True,
        )

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["errors"], [])

        structure = IndustryStructure.objects.get(external_structure_id=1020000000001)
        self.assertEqual(structure.name, "Azbel ESI")
        self.assertEqual(structure.structure_type_name, "Azbel")
        self.assertEqual(structure.solar_system_name, "Jita")
        self.assertEqual(structure.constellation_name, "Kimotoro")
        self.assertEqual(structure.region_name, "The Forge")
        self.assertEqual(
            structure.sync_source,
            IndustryStructure.SyncSource.ESI_CORPORATION,
        )
        self.assertTrue(structure.enable_manufacturing)
        self.assertTrue(structure.enable_manufacturing_capitals)
        self.assertFalse(structure.enable_manufacturing_super_capitals)
        self.assertTrue(structure.enable_research)
        self.assertTrue(structure.enable_invention)
        self.assertFalse(structure.enable_biochemical_reactions)
        self.assertFalse(structure.enable_hybrid_reactions)
        self.assertTrue(structure.enable_composite_reactions)
