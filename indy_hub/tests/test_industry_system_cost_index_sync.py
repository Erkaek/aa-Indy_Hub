"""Tests for syncing industry system cost indices from ESI."""

from __future__ import annotations

# Standard Library
from decimal import Decimal
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.models import IndustryActivityMixin, IndustrySystemCostIndex
from indy_hub.services.system_cost_indices import sync_system_cost_indices


class IndustrySystemCostIndexSyncTests(TestCase):
    @patch("indy_hub.services.system_cost_indices._resolve_solar_system_names")
    @patch("indy_hub.services.system_cost_indices.shared_client.fetch_industry_systems")
    def test_sync_creates_cost_index_rows_for_public_esi_payload(
        self,
        mock_fetch_industry_systems,
        mock_resolve_names,
    ) -> None:
        mock_fetch_industry_systems.return_value = [
            {
                "solar_system_id": 30000142,
                "cost_indices": [
                    {"activity": "manufacturing", "cost_index": 0.0014},
                    {"activity": "researching_time_efficiency", "cost_index": 0.0025},
                    {
                        "activity": "researching_material_efficiency",
                        "cost_index": 0.0035,
                    },
                    {"activity": "copying", "cost_index": 0.0045},
                    {"activity": "invention", "cost_index": 0.0055},
                    {"activity": "reaction", "cost_index": 0.0065},
                ],
            }
        ]
        mock_resolve_names.return_value = {30000142: "Jita"}

        summary = sync_system_cost_indices(force_refresh=True)

        self.assertEqual(summary["systems"], 1)
        self.assertEqual(summary["created"], 7)
        self.assertEqual(
            IndustrySystemCostIndex.objects.filter(solar_system_id=30000142).count(),
            7,
        )
        invention = IndustrySystemCostIndex.objects.get(
            solar_system_id=30000142,
            activity_id=IndustryActivityMixin.ACTIVITY_INVENTION,
        )
        self.assertEqual(invention.solar_system_name, "Jita")
        self.assertEqual(
            invention.cost_index_percent, Decimal("0.14000") + Decimal("0.41000")
        )

    @patch("indy_hub.services.system_cost_indices._resolve_solar_system_names")
    @patch("indy_hub.services.system_cost_indices.shared_client.fetch_industry_systems")
    def test_sync_updates_existing_cost_index_rows(
        self,
        mock_fetch_industry_systems,
        mock_resolve_names,
    ) -> None:
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Old Jita",
            activity_id=IndustryActivityMixin.ACTIVITY_MANUFACTURING,
            cost_index_percent=Decimal("1.00000"),
        )
        mock_fetch_industry_systems.return_value = [
            {
                "solar_system_id": 30000142,
                "cost_indices": [
                    {"activity": "manufacturing", "cost_index": 0.0014},
                ],
            }
        ]
        mock_resolve_names.return_value = {30000142: "Jita"}

        summary = sync_system_cost_indices(force_refresh=True)

        self.assertEqual(summary["updated"], 1)
        manufacturing = IndustrySystemCostIndex.objects.get(
            solar_system_id=30000142,
            activity_id=IndustryActivityMixin.ACTIVITY_MANUFACTURING,
        )
        self.assertEqual(manufacturing.solar_system_name, "Jita")
        self.assertEqual(manufacturing.cost_index_percent, Decimal("0.14000"))
