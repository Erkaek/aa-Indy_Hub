"""Tests for corporation ESI structure sync helpers."""

from __future__ import annotations

# Standard Library
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.models import IndustryStructure
from indy_hub.services.esi_client import ESIForbiddenError, ESIRateLimitError
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

    @patch(
        "indy_hub.services.industry_structure_sync.resolve_solar_system_location_reference"
    )
    @patch("indy_hub.services.industry_structure_sync.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structure_sync.resolve_item_type_reference")
    @patch(
        "indy_hub.services.industry_structure_sync.shared_client.fetch_corporation_structures"
    )
    def test_sync_does_not_wipe_identity_when_payload_omits_solar_system(
        self,
        mock_fetch_corporation_structures,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
    ) -> None:
        mock_fetch_corporation_structures.return_value = [
            {
                "structure_id": 1020000000002,
                "name": "Azbel ESI",
                "type_id": 35826,
                "solar_system_id": None,
                "services": [
                    {"name": "Manufacturing (Standard)", "state": "online"},
                ],
            }
        ]
        mock_resolve_item_type_reference.return_value = (35826, "Azbel")
        mock_resolve_solar_system_reference.return_value = None
        mock_resolve_solar_system_location_reference.return_value = None

        structure = IndustryStructure.objects.create(
            name="Azbel ESI [Acme Corp #1020000000002]",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_id=30000142,
            solar_system_name="Jita",
            constellation_id=20000020,
            constellation_name="Kimotoro",
            region_id=10000002,
            region_name="The Forge",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            external_structure_id=1020000000002,
            owner_corporation_id=98134807,
            owner_corporation_name="Acme Corp",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
            enable_manufacturing=True,
        )

        summary = sync_corporation_structure_targets(
            [{"id": 98134807, "name": "Acme Corp", "character_id": 2112625428}],
            force_refresh=True,
        )

        self.assertEqual(summary["errors"], [])
        structure.refresh_from_db()
        self.assertEqual(structure.solar_system_id, 30000142)
        self.assertEqual(structure.solar_system_name, "Jita")
        self.assertEqual(structure.constellation_id, 20000020)
        self.assertEqual(structure.region_id, 10000002)

    @patch(
        "indy_hub.services.industry_structure_sync.shared_client.fetch_corporation_structures"
    )
    def test_sync_aggregates_forbidden_errors_without_marking_as_failure(
        self,
        mock_fetch_corporation_structures,
    ) -> None:
        mock_fetch_corporation_structures.side_effect = ESIForbiddenError(
            "ESI returned 403 for /corporations/98134807/structures/"
        )

        summary = sync_corporation_structure_targets(
            [{"id": 98134807, "name": "Acme Corp", "character_id": 2112625428}],
            force_refresh=True,
        )

        self.assertEqual(summary["skipped_forbidden"], 1)
        self.assertEqual(summary["skipped_missing_token"], 0)
        self.assertEqual(summary["rate_limited"], 0)
        self.assertEqual(summary["deferred_due_to_rate_limit"], 0)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(len(summary["forbidden_samples"]), 1)

    @patch(
        "indy_hub.services.industry_structure_sync.shared_client.fetch_corporation_structures"
    )
    def test_sync_stops_after_rate_limit_and_defers_remaining_targets(
        self,
        mock_fetch_corporation_structures,
    ) -> None:
        mock_fetch_corporation_structures.side_effect = ESIRateLimitError(
            "Local task ESI throttle hit"
        )

        sync_targets = [
            {"id": 98134807, "name": "Acme Corp", "character_id": 2112625428},
            {"id": 98201666, "name": "Beta Corp", "character_id": 2112625429},
            {"id": 98209999, "name": "Gamma Corp", "character_id": 2112625430},
        ]
        summary = sync_corporation_structure_targets(sync_targets, force_refresh=True)

        self.assertEqual(summary["rate_limited"], 1)
        self.assertEqual(summary["deferred_due_to_rate_limit"], 2)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(mock_fetch_corporation_structures.call_count, 1)
