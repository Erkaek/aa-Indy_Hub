"""Smoke tests for the industry structure add and registry views."""

# Standard Library
import json
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest
from django.test import TestCase
from django.test.client import RequestFactory
from django.urls import reverse

# AA Example App
from indy_hub.models import (
    IndustryStructure,
    IndustryStructureRig,
    IndustrySystemCostIndex,
)
from indy_hub.services.industry_structures import NPC_STATION_STRUCTURE_TYPE_ID
from indy_hub.views.industry import (
    industry_structure_add,
    industry_structure_bonus_preview,
    industry_structure_bulk_import,
    industry_structure_bulk_update,
    industry_structure_bulk_update_preview,
    industry_structure_delete,
    industry_structure_duplicate,
    industry_structure_edit,
    industry_structure_existing_system_structures,
    industry_structure_registry,
    industry_structure_rig_advisor,
    industry_structure_solar_system_cost_indices,
    industry_structure_solar_system_search,
)


def grant_indy_access(user: User) -> None:
    permission = Permission.objects.get(codename="can_access_indy_hub")
    user.user_permissions.add(permission)


class IndustryStructureRegistryViewTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser(
            "structures",
            email="structures@example.com",
            password="secret123",
        )
        grant_indy_access(self.user)

    def _prepare_request(
        self, request: HttpRequest, user: User | None = None
    ) -> HttpRequest:
        request.user = user or self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    @property
    def _view(self):
        return industry_structure_registry.__wrapped__.__wrapped__

    @property
    def _add_view(self):
        return industry_structure_add.__wrapped__.__wrapped__

    @property
    def _edit_view(self):
        return industry_structure_edit.__wrapped__.__wrapped__

    @property
    def _duplicate_view(self):
        return industry_structure_duplicate.__wrapped__.__wrapped__

    @property
    def _delete_view(self):
        return industry_structure_delete.__wrapped__.__wrapped__

    @property
    def _bulk_update_view(self):
        return industry_structure_bulk_update.__wrapped__.__wrapped__.__wrapped__

    @property
    def _bulk_update_preview_view(self):
        return industry_structure_bulk_update_preview.__wrapped__.__wrapped__

    @property
    def _bulk_import_view(self):
        return industry_structure_bulk_import.__wrapped__.__wrapped__.__wrapped__

    @property
    def _solar_search_view(self):
        return industry_structure_solar_system_search.__wrapped__.__wrapped__

    @property
    def _existing_system_structures_view(self):
        return industry_structure_existing_system_structures.__wrapped__.__wrapped__

    @property
    def _solar_cost_indices_view(self):
        return industry_structure_solar_system_cost_indices.__wrapped__.__wrapped__

    @property
    def _bonus_preview_view(self):
        return industry_structure_bonus_preview.__wrapped__.__wrapped__

    @property
    def _rig_advisor_view(self):
        return industry_structure_rig_advisor.__wrapped__.__wrapped__

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_lists_existing_structures(self, _mock_sde_loaded) -> None:
        structure = IndustryStructure.objects.create(
            name="Raitaru Prime",
            solar_system_name="Jita",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            manufacturing_tax_percent=Decimal("0.500"),
            invention_tax_percent=Decimal("0.250"),
            enable_manufacturing=True,
            enable_invention=False,
        )
        IndustryStructureRig.objects.create(
            structure=structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Standup XL-Set Ship Manufacturing Efficiency II",
        )

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry"))
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Raitaru Prime", response.content.decode())
        self.assertIn(
            "Standup XL-Set Ship Manufacturing Efficiency II",
            response.content.decode(),
        )
        self.assertIn("Edit", response.content.decode())
        self.assertIn("Duplicate", response.content.decode())
        self.assertIn("Delete", response.content.decode())
        self.assertIn("View", response.content.decode())
        self.assertIn("structureDetailModal1", response.content.decode())
        self.assertIn("Manufacturing", response.content.decode())
        self.assertNotIn("Invention</td>", response.content.decode())
        self.assertRegex(
            response.content.decode(),
            r'(?s)name="update_existing_manual".*checked',
        )

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_registry_aggregates_bonus_rows_by_source_and_label(
        self,
        mock_resolve_structure_bonuses,
        _mock_sde_loaded,
    ) -> None:
        # AA Example App
        from indy_hub.services.industry_structures import IndustryStructureResolvedBonus

        IndustryStructure.objects.create(
            name="Sotiyo Prime",
            solar_system_name="Jita",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
            manufacturing_tax_percent=Decimal("2.500"),
        )
        mock_resolve_structure_bonuses.return_value = [
            IndustryStructureResolvedBonus(
                source="rig",
                label="Standup XL-Set Ship Manufacturing Efficiency I",
                activity_id=1,
                material_efficiency_percent=Decimal("4.200"),
            ),
            IndustryStructureResolvedBonus(
                source="rig",
                label="Standup XL-Set Ship Manufacturing Efficiency I",
                activity_id=1,
                time_efficiency_percent=Decimal("42.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=1,
                time_efficiency_percent=Decimal("30.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=1,
                job_cost_percent=Decimal("5.000"),
            ),
        ]

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry"))
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertEqual(
            content.count("Standup XL-Set Ship Manufacturing Efficiency I"), 1
        )
        self.assertIn("-4.200%", content)
        self.assertIn("-42.000%", content)
        self.assertIn("-30.000%", content)
        self.assertIn("-5.000%", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_registry_shows_bonus_activity_section_labels(
        self,
        mock_resolve_structure_bonuses,
        _mock_sde_loaded,
    ) -> None:
        # AA Example App
        from indy_hub.services.industry_structures import IndustryStructureResolvedBonus

        IndustryStructure.objects.create(
            name="Azbel Prime",
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_te_research=True,
            enable_copying=True,
        )
        mock_resolve_structure_bonuses.return_value = [
            IndustryStructureResolvedBonus(
                source="structure",
                label="Azbel",
                activity_id=3,
                time_efficiency_percent=Decimal("20.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Azbel",
                activity_id=5,
                time_efficiency_percent=Decimal("20.000"),
            ),
        ]

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry"))
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("TE Research", content)
        self.assertIn("Copying", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_filters_by_activity_with_active_badges(
        self, _mock_sde_loaded
    ) -> None:
        IndustryStructure.objects.create(
            name="Manual Invention",
            solar_system_name="Jita",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=False,
            enable_te_research=False,
            enable_me_research=False,
            enable_copying=False,
            enable_invention=True,
            enable_reactions=False,
        )
        IndustryStructure.objects.create(
            name="Synced Manufacturing",
            solar_system_name="XJP-Y7",
            structure_type_id=35826,
            structure_type_name="Azbel",
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            external_structure_id=1020000000001,
            enable_manufacturing=True,
            enable_te_research=False,
            enable_me_research=False,
            enable_copying=False,
            enable_invention=False,
            enable_reactions=False,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_registry"),
                {
                    "activity": "enable_invention",
                },
            )
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Manual Invention", content)
        self.assertNotIn("Synced Manufacturing", content)
        self.assertIn('value="enable_invention" selected', content)
        self.assertIn("Active Filters", content)
        self.assertIn("Activity:</strong> Invention", content)
        self.assertIn("Clear All", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_shows_capital_and_supercapital_manufacturing_labels(
        self, _mock_sde_loaded
    ) -> None:
        IndustryStructure.objects.create(
            name="Raitaru Line",
            solar_system_name="Jita",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
        )
        IndustryStructure.objects.create(
            name="Azbel Line",
            solar_system_name="Perimeter",
            structure_type_id=35826,
            structure_type_name="Azbel",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
        )
        IndustryStructure.objects.create(
            name="Sotiyo Line",
            solar_system_name="1DQ1-A",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            enable_manufacturing=True,
        )

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry"))
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Manufacturing (Capitals)", content)
        self.assertIn("Manufacturing (Super-Capitals)", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_filters_by_capital_manufacturing_activity(
        self, _mock_sde_loaded
    ) -> None:
        IndustryStructure.objects.create(
            name="Raitaru Line",
            solar_system_name="Jita",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
        )
        IndustryStructure.objects.create(
            name="Azbel Line",
            solar_system_name="Perimeter",
            structure_type_id=35826,
            structure_type_name="Azbel",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
        )
        IndustryStructure.objects.create(
            name="Sotiyo Line",
            solar_system_name="1DQ1-A",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            enable_manufacturing=True,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_registry"),
                {"activity": "manufacturing_capitals"},
            )
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Raitaru Line", content)
        self.assertIn("Azbel Line", content)
        self.assertIn("Sotiyo Line", content)
        self.assertIn("Activity:</strong> Manufacturing (Capitals)", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_filters_by_search_and_completion(self, _mock_sde_loaded) -> None:
        complete_structure = IndustryStructure.objects.create(
            name="Sotiyo Forge",
            solar_system_name="1DQ1-A",
            constellation_name="1DQ",
            region_name="Delve",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            solar_system_id=30004758,
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            enable_manufacturing=True,
            manufacturing_tax_percent=Decimal("1.000"),
        )
        IndustryStructureRig.objects.create(
            structure=complete_structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Standup XL-Set Ship Manufacturing Efficiency II",
        )
        IndustryStructure.objects.create(
            name="Sotiyo Empty",
            solar_system_name="1DQ1-A",
            constellation_name="1DQ",
            region_name="Delve",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            enable_manufacturing=True,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_registry"),
                {
                    "search": "forge",
                    "completion": "complete",
                },
            )
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Sotiyo Forge", content)
        self.assertNotIn("Sotiyo Empty", content)
        self.assertIn("2 overall", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_filters_by_region_and_constellation(
        self, _mock_sde_loaded
    ) -> None:
        IndustryStructure.objects.create(
            name="Forge Hub",
            solar_system_name="Jita",
            constellation_name="Kimotoro",
            region_name="The Forge",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            solar_system_id=30000142,
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
        )
        IndustryStructure.objects.create(
            name="Delve Hub",
            solar_system_name="1DQ1-A",
            constellation_name="1DQ",
            region_name="Delve",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            solar_system_id=30004758,
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            enable_manufacturing=True,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_registry"),
                {
                    "region_name": "The Forge",
                    "constellation_name": "Kimotoro",
                },
            )
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Forge Hub", content)
        self.assertNotIn("Delve Hub", content)
        self.assertIn("Region:</strong> The Forge", content)
        self.assertIn("Constellation:</strong> Kimotoro", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_registry_bonus_sections_hide_disabled_activities(
        self,
        mock_resolve_structure_bonuses,
        _mock_sde_loaded,
    ) -> None:
        # AA Example App
        from indy_hub.services.industry_structures import IndustryStructureResolvedBonus

        IndustryStructure.objects.create(
            name="Sotiyo Manufacturing",
            solar_system_name="C-N4OD",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            system_security_band=IndustryStructure.SecurityBand.NULLSEC,
            enable_manufacturing=True,
            enable_te_research=False,
            enable_me_research=False,
            enable_copying=False,
            enable_invention=False,
        )
        mock_resolve_structure_bonuses.return_value = [
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=1,
                time_efficiency_percent=Decimal("30.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=3,
                time_efficiency_percent=Decimal("30.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=4,
                time_efficiency_percent=Decimal("30.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=5,
                time_efficiency_percent=Decimal("30.000"),
            ),
            IndustryStructureResolvedBonus(
                source="structure",
                label="Sotiyo",
                activity_id=8,
                time_efficiency_percent=Decimal("30.000"),
            ),
        ]

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry"))
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Manufacturing", content)
        self.assertNotIn('<h4 class="h6 mb-0">TE Research</h4>', content)
        self.assertNotIn('<h4 class="h6 mb-0">ME Research</h4>', content)
        self.assertNotIn('<h4 class="h6 mb-0">Copying</h4>', content)
        self.assertNotIn('<h4 class="h6 mb-0">Invention</h4>', content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_add_page_renders_dedicated_structure_form(self, _mock_sde_loaded) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_add"))
        )

        response = self._add_view(request)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Register Structure", content)
        self.assertIn("Back to Registry", content)
        self.assertIn("Deduce Rigs", content)
        self.assertIn("Select one activity", content)
        self.assertIn("NPC Station", content)
        self.assertIn('"supports_rigs": false', content)
        self.assertIn('name="rigs-0-rig_type_id"', content)
        self.assertIn('name="rigs-1-rig_type_id"', content)
        self.assertIn('name="rigs-2-rig_type_id"', content)
        self.assertNotIn('name="rigs-3-rig_type_id"', content)

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    def test_registry_can_create_npc_station_without_rigs(
        self,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
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

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    "name": "Jita Research Station",
                    "structure_type_id": str(NPC_STATION_STRUCTURE_TYPE_ID),
                    "solar_system_name": "Jita",
                    "enable_manufacturing": "1",
                    "enable_research": "1",
                    "enable_invention": "1",
                    "manufacturing_tax_percent": "0.250",
                    "research_tax_percent": "0.500",
                    "invention_tax_percent": "0.750",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "",
                    "rigs-0-rig_type_id": "",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )

        response = self._add_view(request)

        self.assertEqual(response.status_code, 302)
        structure = IndustryStructure.objects.get(name="Jita Research Station")
        self.assertEqual(structure.structure_type_id, NPC_STATION_STRUCTURE_TYPE_ID)
        self.assertEqual(structure.structure_type_name, "NPC Station")
        self.assertEqual(structure.rigs.count(), 0)

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    def test_registry_can_create_npc_station_with_capital_manufacturing(
        self,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
        """Regression for #70: NPC Station with capital manufacturing flag must save."""

        mock_resolve_solar_system_reference.return_value = (
            30002780,
            "Iralaja",
            IndustryStructure.SecurityBand.HIGHSEC,
        )
        mock_resolve_solar_system_location_reference.return_value = {
            "solar_system_id": 30002780,
            "solar_system_name": "Iralaja",
            "system_security_band": IndustryStructure.SecurityBand.HIGHSEC,
            "constellation_id": 20000406,
            "constellation_name": "Subhatoun",
            "region_id": 10000033,
            "region_name": "The Citadel",
        }

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    "name": "Iralaja IX - Home Guard Testing Facilities",
                    "structure_type_id": str(NPC_STATION_STRUCTURE_TYPE_ID),
                    "solar_system_name": "Iralaja",
                    "enable_manufacturing": "1",
                    "enable_manufacturing_capitals": "1",
                    "manufacturing_tax_percent": "0.250",
                    "manufacturing_capitals_tax_percent": "0.250",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "",
                    "rigs-0-rig_type_id": "",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )

        response = self._add_view(request)

        self.assertEqual(response.status_code, 302)
        structure = IndustryStructure.objects.get(
            name="Iralaja IX - Home Guard Testing Facilities"
        )
        self.assertEqual(structure.structure_type_id, NPC_STATION_STRUCTURE_TYPE_ID)
        self.assertTrue(structure.enable_manufacturing)
        self.assertTrue(structure.enable_manufacturing_capitals)

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    def test_registry_add_view_surfaces_form_errors_as_messages(
        self,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
        """Regression for #70: invalid POST must surface validation errors via messages."""

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

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    # Missing required ``name`` and no activity flag enabled.
                    "structure_type_id": str(NPC_STATION_STRUCTURE_TYPE_ID),
                    "solar_system_name": "Jita",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "",
                    "rigs-0-rig_type_id": "",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )

        response = self._add_view(request)

        self.assertEqual(response.status_code, 200)
        emitted_messages = [str(message) for message in request._messages]
        self.assertTrue(
            any("Could not save the structure" in m for m in emitted_messages),
            f"Expected an error message about save failure, got: {emitted_messages}",
        )

    def test_npc_station_does_not_report_missing_rigs_section(self) -> None:
        """Regression for #70: NPC stations have no rig sockets so the registry
        list must not flag them as ``Setup needed`` because of missing rigs."""

        structure = IndustryStructure.objects.create(
            name="Iralaja IX - Test NPC",
            structure_type_id=NPC_STATION_STRUCTURE_TYPE_ID,
            structure_type_name="NPC Station",
            solar_system_id=30002780,
            solar_system_name="Iralaja",
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
            enable_manufacturing=True,
            manufacturing_tax_percent=Decimal("0.25"),
        )

        missing = structure.get_missing_profile_sections()
        self.assertNotIn("Rigs", missing)
        self.assertFalse(structure.is_profile_incomplete(), missing)

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.forms.industry_structures.resolve_item_type_reference")
    def test_registry_ignores_posted_rigs_for_npc_station(
        self,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
        def resolver(*, item_type_id=None, item_type_name=None):
            if (
                item_type_id == NPC_STATION_STRUCTURE_TYPE_ID
                or item_type_name == "NPC Station"
            ):
                return (NPC_STATION_STRUCTURE_TYPE_ID, "NPC Station")
            if item_type_id == 43879:
                return (43879, "Standup M-Set Invention Cost Optimization I")
            return None

        mock_resolve_item_type_reference.side_effect = resolver
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

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    "name": "Jita Factory Station",
                    "structure_type_id": str(NPC_STATION_STRUCTURE_TYPE_ID),
                    "solar_system_name": "Jita",
                    "enable_manufacturing": "1",
                    "manufacturing_tax_percent": "0.250",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "1",
                    "rigs-0-rig_type_id": "43879",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )

        response = self._add_view(request)

        self.assertEqual(response.status_code, 302)
        structure = IndustryStructure.objects.get(name="Jita Factory Station")
        self.assertEqual(structure.rigs.count(), 0)

    @patch("indy_hub.views.industry.build_structure_rig_advisor_rows")
    def test_rig_advisor_returns_compatible_categories_and_metrics(
        self, mock_advisor
    ) -> None:
        # AA Example App
        from indy_hub.services.industry_structures import IndustryStructureRigAdvisorRow

        mock_advisor.return_value = [
            IndustryStructureRigAdvisorRow(
                rig_type_id=37181,
                label="Standup XL-Set Ship Manufacturing Efficiency II",
                family="Manufacturing",
                activity_id=1,
                activity_label="Manufacturing",
                supported_types_label="Types supported",
                supported_type_names=("Assault Frigate", "Battleship"),
                material_efficiency_percent=Decimal("4.200"),
                time_efficiency_percent=Decimal("2.100"),
            )
        ]

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_rig_advisor"),
                {
                    "structure_type_id": "35827",
                    "solar_system_name": "Jita",
                    "enable_manufacturing": "1",
                },
            )
        )
        response = self._rig_advisor_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["activities"][0]["activity_label"], "Manufacturing")
        self.assertEqual(
            payload["activities"][0]["categories"], ["Assault Frigate", "Battleship"]
        )
        self.assertEqual(
            payload["activities"][0]["rig_options"][0]["label"],
            "Standup XL-Set Ship Manufacturing Efficiency II",
        )
        self.assertEqual(
            payload["activities"][0]["rig_options"][0]["metrics"][0]["label"], "ME"
        )

    @patch("indy_hub.views.industry.search_solar_system_options")
    def test_solar_system_search_returns_json_results(self, mock_search) -> None:
        mock_search.return_value = [
            {
                "id": 30000142,
                "name": "Jita",
                "security_band": IndustryStructure.SecurityBand.HIGHSEC,
            }
        ]

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_solar_system_search"),
                {"q": "Ji"},
            )
        )
        response = self._solar_search_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["results"][0]["name"], "Jita")

    def test_existing_system_structures_returns_known_structures(self) -> None:
        alpha = IndustryStructure.objects.create(
            name="Jita Alpha",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            sync_source=IndustryStructure.SyncSource.MANUAL,
        )
        beta = IndustryStructure.objects.create(
            name="Jita Beta",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
        )
        IndustryStructure.objects.create(
            name="Perimeter Gamma",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            solar_system_name="Perimeter",
            sync_source=IndustryStructure.SyncSource.MANUAL,
        )
        IndustryStructure.objects.create(
            name="Jita Raitaru",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            solar_system_name="Jita",
            sync_source=IndustryStructure.SyncSource.MANUAL,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_existing_system_structures"),
                {
                    "name": "Jita",
                    "structure_type_id": "35826",
                },
            )
        )
        response = self._existing_system_structures_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["solar_system_name"], "Jita")
        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["same_type_count"], 2)
        self.assertEqual(payload["structures"][0]["id"], alpha.pk)
        self.assertEqual(payload["structures"][0]["name"], "Jita Alpha")
        self.assertFalse(payload["structures"][0]["is_synced"])
        self.assertIn(
            f"/industry/structures/{alpha.pk}/edit/",
            payload["structures"][0]["edit_url"],
        )
        self.assertIn(
            f"/industry/structures/{alpha.pk}/duplicate/",
            payload["structures"][0]["duplicate_url"],
        )
        self.assertEqual(payload["structures"][1]["id"], beta.pk)
        self.assertTrue(payload["structures"][1]["is_synced"])

    def test_existing_system_structures_excludes_current_structure(self) -> None:
        current_structure = IndustryStructure.objects.create(
            name="Jita Alpha",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            sync_source=IndustryStructure.SyncSource.MANUAL,
        )
        IndustryStructure.objects.create(
            name="Jita Beta",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_existing_system_structures"),
                {
                    "name": "Jita",
                    "structure_type_id": "35826",
                    "exclude_structure_id": str(current_structure.pk),
                },
            )
        )
        response = self._existing_system_structures_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["structures"][0]["name"], "Jita Beta")

    def test_existing_system_structures_requires_structure_type(self) -> None:
        IndustryStructure.objects.create(
            name="Jita Alpha",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            sync_source=IndustryStructure.SyncSource.MANUAL,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_existing_system_structures"),
                {
                    "name": "Jita",
                },
            )
        )
        response = self._existing_system_structures_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["solar_system_name"], "")
        self.assertEqual(payload["total_count"], 0)
        self.assertEqual(payload["structures"], [])

    @patch("indy_hub.views.industry.resolve_solar_system_reference")
    def test_solar_system_cost_indices_returns_json_preview(
        self, mock_resolve_system
    ) -> None:
        mock_resolve_system.return_value = (
            30000142,
            "Jita",
            IndustryStructure.SecurityBand.HIGHSEC,
        )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Jita",
            activity_id=IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
            cost_index_percent=Decimal("5.00000"),
        )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Jita",
            activity_id=IndustrySystemCostIndex.ACTIVITY_INVENTION,
            cost_index_percent=Decimal("7.00000"),
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_solar_system_cost_indices"),
                {"name": "Jita"},
            )
        )
        response = self._solar_cost_indices_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertTrue(payload["found"])
        self.assertEqual(payload["solar_system_name"], "Jita")
        self.assertEqual(
            payload["security_band"], IndustryStructure.SecurityBand.HIGHSEC
        )
        self.assertEqual(payload["cost_indices"][0]["activity_label"], "Manufacturing")
        self.assertEqual(payload["cost_indices"][0]["cost_index_percent"], "5.00000")

    @patch("indy_hub.views.industry.build_structure_activity_previews")
    def test_bonus_preview_returns_selected_structure_bonus_summary(
        self, mock_previews
    ) -> None:
        # AA Example App
        from indy_hub.services.industry_structures import (
            IndustryStructureActivityPreview,
            IndustryStructureResolvedBonus,
            IndustryStructureRigBonusProfile,
            IndustryStructureSupportedTypeBonusRow,
        )

        mock_previews.return_value = [
            IndustryStructureActivityPreview(
                activity_id=1,
                activity_label="Manufacturing",
                system_cost_index_percent=Decimal("8.81000"),
                structure_role_bonus=IndustryStructureResolvedBonus(
                    source="structure",
                    label="Sotiyo",
                    activity_id=1,
                    material_efficiency_percent=Decimal("1.000"),
                    time_efficiency_percent=Decimal("30.000"),
                    job_cost_percent=Decimal("5.000"),
                ),
                supported_type_rows=(
                    IndustryStructureSupportedTypeBonusRow(
                        type_name="Assault Frigate",
                        material_efficiency_percent=Decimal("4.200"),
                        time_efficiency_percent=Decimal("42.000"),
                    ),
                    IndustryStructureSupportedTypeBonusRow(
                        type_name="Battleship",
                        material_efficiency_percent=Decimal("4.200"),
                        time_efficiency_percent=Decimal("42.000"),
                    ),
                ),
                rig_profiles=(
                    IndustryStructureRigBonusProfile(
                        label="Standup XL-Set Ship Manufacturing Efficiency II",
                        supported_types_label="Types supported",
                        supported_type_names=("Assault Frigate", "Battleship"),
                        material_efficiency_percent=Decimal("42.000"),
                        time_efficiency_percent=Decimal("21.000"),
                        job_cost_percent=Decimal("10.000"),
                    ),
                ),
            )
        ]

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_bonus_preview"),
                {
                    "structure_type_id": "35827",
                    "solar_system_name": "Jita",
                    "enable_manufacturing": "1",
                    "rig_type_id": ["37181"],
                },
            )
        )
        response = self._bonus_preview_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["rows"][0]["activity_label"], "Manufacturing")
        self.assertEqual(payload["rows"][0]["system_cost_index_percent"], "8.81")
        self.assertEqual(payload["rows"][0]["structure_role_metrics"][0]["label"], "ME")
        self.assertEqual(
            payload["rows"][0]["supported_type_rows"][0]["type_name"],
            "Assault Frigate",
        )
        self.assertEqual(
            payload["rows"][0]["supported_type_rows"][0]["metrics"][0]["value"], "4.200"
        )

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.forms.industry_structures.resolve_item_type_reference")
    @patch("indy_hub.forms.industry_structures.is_rig_compatible_with_structure_type")
    def test_registry_can_create_structure_with_rigs(
        self,
        mock_is_rig_compatible,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
        mock_is_rig_compatible.return_value = True

        def resolver(*, item_type_id=None, item_type_name=None):
            mapping = {
                35826: (35826, "Azbel"),
                43879: (43879, "Standup M-Set Invention Cost Optimization I"),
            }
            if item_type_id in mapping:
                return mapping[item_type_id]
            if item_type_name == "Azbel":
                return mapping[35826]
            return None

        mock_resolve_item_type_reference.side_effect = resolver
        mock_resolve_solar_system_reference.return_value = (
            30002187,
            "Perimeter",
            IndustryStructure.SecurityBand.HIGHSEC,
        )
        mock_resolve_solar_system_location_reference.return_value = {
            "solar_system_id": 30002187,
            "solar_system_name": "Perimeter",
            "system_security_band": IndustryStructure.SecurityBand.HIGHSEC,
            "constellation_id": 20000020,
            "constellation_name": "Kimotoro",
            "region_id": 10000002,
            "region_name": "The Forge",
        }

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    "name": "Azbel Forge",
                    "structure_type_id": "35826",
                    "solar_system_name": "Perimeter",
                    "enable_manufacturing": "1",
                    "enable_te_research": "1",
                    "enable_me_research": "1",
                    "enable_copying": "1",
                    "enable_invention": "1",
                    "manufacturing_tax_percent": "0.250",
                    "te_research_tax_percent": "0.500",
                    "me_research_tax_percent": "0.500",
                    "copying_tax_percent": "0.750",
                    "invention_tax_percent": "1.250",
                    "reactions_tax_percent": "2.500",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "1",
                    "rigs-0-rig_type_id": "43879",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )
        response = self._add_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            reverse("indy_hub:industry_structure_registry"),
        )
        structure = IndustryStructure.objects.get(name="Azbel Forge")
        self.assertEqual(structure.structure_type_name, "Azbel")
        self.assertEqual(structure.solar_system_id, 30002187)
        self.assertEqual(structure.solar_system_name, "Perimeter")
        self.assertEqual(structure.constellation_name, "Kimotoro")
        self.assertEqual(structure.region_name, "The Forge")
        self.assertTrue(structure.enable_manufacturing)
        self.assertTrue(structure.enable_invention)
        self.assertFalse(structure.enable_reactions)
        self.assertEqual(structure.invention_tax_percent, Decimal("1.250"))
        rig = structure.rigs.get(slot_index=1)
        self.assertEqual(rig.rig_type_id, 43879)
        self.assertEqual(
            rig.rig_type_name, "Standup M-Set Invention Cost Optimization I"
        )

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.forms.industry_structures.resolve_item_type_reference")
    def test_add_view_rejects_duplicate_name_when_whitespace_differs(
        self,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
        IndustryStructure.objects.create(
            name="C-N4OD - Kuat Drive Yards",
            solar_system_id=30000142,
            solar_system_name="Jita",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
        )

        mock_resolve_item_type_reference.return_value = (35827, "Sotiyo")
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

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    "name": "C-N4OD  -   Kuat Drive Yards",
                    "structure_type_id": "35827",
                    "solar_system_name": "Jita",
                    "enable_manufacturing": "1",
                    "manufacturing_tax_percent": "0.250",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "",
                    "rigs-0-rig_type_id": "",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )

        response = self._add_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "A shared structure with this registry name already exists, even when whitespace is ignored.",
            response.content.decode(),
        )
        self.assertEqual(
            IndustryStructure.objects.filter(
                name__icontains="Kuat Drive Yards"
            ).count(),
            1,
        )

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_location_reference")
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.forms.industry_structures.resolve_item_type_reference")
    @patch("indy_hub.forms.industry_structures.is_rig_compatible_with_structure_type")
    def test_registry_rejects_incompatible_rig_for_structure(
        self,
        mock_is_rig_compatible,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_view_sde_loaded,
        _mock_form_sde_loaded,
    ) -> None:
        def resolver(*, item_type_id=None, item_type_name=None):
            mapping = {
                35835: (35835, "Athanor"),
                37181: (37181, "Standup XL-Set Ship Manufacturing Efficiency II"),
            }
            if item_type_id in mapping:
                return mapping[item_type_id]
            return None

        mock_resolve_item_type_reference.side_effect = resolver
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
        mock_is_rig_compatible.return_value = False

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_add"),
                {
                    "name": "Athanor Ore Hub",
                    "structure_type_id": "35835",
                    "solar_system_name": "Jita",
                    "enable_reactions": "1",
                    "manufacturing_tax_percent": "0.250",
                    "te_research_tax_percent": "0.000",
                    "me_research_tax_percent": "0.000",
                    "copying_tax_percent": "0.000",
                    "invention_tax_percent": "0.000",
                    "reactions_tax_percent": "0.000",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "0",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "1",
                    "rigs-0-rig_type_id": "37181",
                    "rigs-1-slot_index": "",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "",
                    "rigs-2-rig_type_id": "",
                },
            )
        )
        response = self._add_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "This rig cannot be fitted on the selected structure type.",
            response.content.decode(),
        )
        self.assertFalse(
            IndustryStructure.objects.filter(name="Athanor Ore Hub").exists()
        )

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.forms.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.forms.industry_structures.resolve_item_type_reference")
    @patch("indy_hub.forms.industry_structures.is_rig_compatible_with_structure_type")
    def test_edit_view_updates_structure_and_rigs(
        self,
        mock_is_rig_compatible,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        _mock_form_sde_loaded,
    ) -> None:
        structure = IndustryStructure.objects.create(
            name="Raitaru Prime",
            solar_system_id=30000142,
            solar_system_name="Jita",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            manufacturing_tax_percent=Decimal("0.500"),
            invention_tax_percent=Decimal("0.250"),
        )
        IndustryStructureRig.objects.create(
            structure=structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Standup XL-Set Ship Manufacturing Efficiency II",
        )

        mock_is_rig_compatible.return_value = True
        mock_resolve_item_type_reference.side_effect = (
            lambda *, item_type_id=None, item_type_name=None: {
                35826: (35826, "Azbel"),
                43879: (43879, "Standup M-Set Invention Cost Optimization I"),
            }.get(item_type_id)
        )
        mock_resolve_solar_system_reference.return_value = (
            30002187,
            "Perimeter",
            IndustryStructure.SecurityBand.HIGHSEC,
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_edit", args=[structure.id]),
                {
                    "name": "Azbel Prime",
                    "structure_type_id": "35826",
                    "solar_system_name": "Perimeter",
                    "enable_manufacturing": "1",
                    "enable_te_research": "1",
                    "enable_me_research": "1",
                    "enable_copying": "1",
                    "enable_invention": "1",
                    "manufacturing_tax_percent": "1.000",
                    "te_research_tax_percent": "0.500",
                    "me_research_tax_percent": "0.500",
                    "copying_tax_percent": "0.750",
                    "invention_tax_percent": "1.500",
                    "reactions_tax_percent": "0.000",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "3",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "1",
                    "rigs-0-rig_type_id": "43879",
                    "rigs-1-slot_index": "2",
                    "rigs-1-rig_type_id": "",
                    "rigs-2-slot_index": "3",
                    "rigs-2-rig_type_id": "",
                },
            )
        )
        response = self._edit_view(request, structure.id)

        self.assertEqual(response.status_code, 302)
        structure.refresh_from_db()
        self.assertEqual(structure.name, "Azbel Prime")
        self.assertEqual(structure.structure_type_name, "Azbel")
        self.assertEqual(structure.solar_system_name, "Perimeter")
        self.assertEqual(structure.manufacturing_tax_percent, Decimal("1.000"))
        self.assertEqual(structure.invention_tax_percent, Decimal("1.500"))
        self.assertEqual(structure.rigs.count(), 1)
        self.assertEqual(structure.rigs.get(slot_index=1).rig_type_id, 43879)

    @patch(
        "indy_hub.forms.industry_structures.sde_item_types_loaded", return_value=True
    )
    @patch("indy_hub.forms.industry_structures.resolve_item_type_reference")
    @patch("indy_hub.forms.industry_structures.is_rig_compatible_with_structure_type")
    def test_edit_view_for_synced_structure_only_updates_rigs(
        self,
        mock_is_rig_compatible,
        mock_resolve_item_type_reference,
        _mock_form_sde_loaded,
    ) -> None:
        structure = IndustryStructure.objects.create(
            name="Auto Azbel",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            external_structure_id=1020000000001,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            solar_system_id=30000142,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
            enable_invention=False,
            manufacturing_tax_percent=Decimal("0.500"),
        )
        IndustryStructureRig.objects.create(
            structure=structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Standup XL-Set Ship Manufacturing Efficiency II",
        )

        mock_is_rig_compatible.return_value = True
        mock_resolve_item_type_reference.side_effect = (
            lambda *, item_type_id=None, item_type_name=None: {
                43879: (43879, "Standup M-Set Invention Cost Optimization I"),
            }.get(item_type_id)
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_edit", args=[structure.id]),
                {
                    "name": "Should Be Ignored",
                    "structure_type_id": "35826",
                    "solar_system_name": "Perimeter",
                    "enable_invention": "1",
                    "manufacturing_tax_percent": "9.999",
                    "rigs-TOTAL_FORMS": "3",
                    "rigs-INITIAL_FORMS": "3",
                    "rigs-MIN_NUM_FORMS": "0",
                    "rigs-MAX_NUM_FORMS": "1000",
                    "rigs-0-slot_index": "1",
                    "rigs-0-rig_type_id": "",
                    "rigs-1-slot_index": "2",
                    "rigs-1-rig_type_id": "43879",
                    "rigs-2-slot_index": "3",
                    "rigs-2-rig_type_id": "",
                },
            )
        )
        response = self._edit_view(request, structure.id)

        self.assertEqual(response.status_code, 302)
        messages = [message.message for message in request._messages]
        self.assertIn(
            "Synchronized structure updated successfully. Rigs saved: 1.",
            messages,
        )
        structure.refresh_from_db()
        self.assertEqual(structure.name, "Auto Azbel")
        self.assertEqual(structure.structure_type_id, 35826)
        self.assertEqual(structure.solar_system_name, "Jita")
        self.assertFalse(structure.enable_invention)
        self.assertEqual(structure.manufacturing_tax_percent, Decimal("0.500"))
        self.assertEqual(structure.rigs.count(), 1)
        self.assertEqual(structure.rigs.get(slot_index=2).rig_type_id, 43879)

    def test_duplicate_view_creates_copy_with_only_tax_changes(self) -> None:
        structure = IndustryStructure.objects.create(
            name="Sotiyo Profile",
            solar_system_id=30000142,
            solar_system_name="Jita",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
            enable_te_research=True,
            enable_me_research=True,
            enable_copying=True,
            enable_invention=True,
            enable_reactions=False,
            manufacturing_tax_percent=Decimal("0.500"),
            invention_tax_percent=Decimal("0.750"),
        )
        IndustryStructureRig.objects.create(
            structure=structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Standup XL-Set Ship Manufacturing Efficiency II",
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_duplicate", args=[structure.id]),
                {
                    "personal_tag": "structures - 1",
                    "manufacturing_tax_percent": "2.500",
                    "te_research_tax_percent": "1.000",
                    "me_research_tax_percent": "1.000",
                    "copying_tax_percent": "1.500",
                    "invention_tax_percent": "3.000",
                    "reactions_tax_percent": "0.000",
                },
            )
        )
        response = self._duplicate_view(request, structure.id)

        self.assertEqual(response.status_code, 302)
        duplicated_structure = IndustryStructure.objects.exclude(id=structure.id).get()
        self.assertEqual(duplicated_structure.name, structure.name)
        self.assertEqual(duplicated_structure.personal_tag, f"{self.user.username} - 1")
        self.assertEqual(
            duplicated_structure.visibility_scope,
            IndustryStructure.VisibilityScope.PERSONAL,
        )
        self.assertEqual(duplicated_structure.owner_user, self.user)
        self.assertEqual(duplicated_structure.source_structure, structure)
        self.assertEqual(
            duplicated_structure.structure_type_id, structure.structure_type_id
        )
        self.assertEqual(
            duplicated_structure.solar_system_name, structure.solar_system_name
        )
        self.assertEqual(
            duplicated_structure.enable_invention, structure.enable_invention
        )
        self.assertEqual(
            duplicated_structure.manufacturing_tax_percent, Decimal("2.500")
        )
        self.assertEqual(duplicated_structure.invention_tax_percent, Decimal("3.000"))
        self.assertEqual(duplicated_structure.rigs.count(), 1)
        self.assertEqual(duplicated_structure.rigs.get(slot_index=1).rig_type_id, 37181)

    def test_duplicate_view_prefills_first_personal_tag(self) -> None:
        structure = IndustryStructure.objects.create(
            name="Azbel Profile",
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_duplicate", args=[structure.id])
            )
        )
        response = self._duplicate_view(request, structure.id)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(f'value="{self.user.username} - 1"', content)
        self.assertIn("<strong>Structure:</strong> Azbel Profile", content)
        self.assertNotIn("Locked Structure Name", content)

    def test_duplicate_view_prefills_incremented_personal_tag_for_same_structure(
        self,
    ) -> None:
        structure = IndustryStructure.objects.create(
            name="Azbel Profile",
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )
        IndustryStructure.objects.create(
            name=structure.name,
            personal_tag=f"{self.user.username} - 1",
            owner_user=self.user,
            source_structure=structure,
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_duplicate", args=[structure.id])
            )
        )
        response = self._duplicate_view(request, structure.id)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'value="{self.user.username} - 2"', response.content.decode())

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_hides_personal_copies_from_other_users(
        self, _mock_sde_loaded
    ) -> None:
        other_user = User.objects.create_user(
            "other-structures",
            email="other@example.com",
            password="secret123",
        )
        grant_indy_access(other_user)
        public_structure = IndustryStructure.objects.create(
            name="Shared Azbel",
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
        )
        IndustryStructure.objects.create(
            name=public_structure.name,
            personal_tag="structures",
            owner_user=self.user,
            source_structure=public_structure,
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry")),
            user=other_user,
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Shared Azbel", content)
        self.assertNotIn("[structures]", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_scope_personal_shows_only_owned_personal_copies(
        self, _mock_sde_loaded
    ) -> None:
        other_user = User.objects.create_user(
            "other-scope",
            email="other-scope@example.com",
            password="secret123",
        )
        grant_indy_access(other_user)
        source_one = IndustryStructure.objects.create(
            name="Shared Azbel",
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )
        source_two = IndustryStructure.objects.create(
            name="Shared Sotiyo",
            solar_system_name="Perimeter",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
        )
        IndustryStructure.objects.create(
            name=source_one.name,
            personal_tag="mine",
            owner_user=self.user,
            source_structure=source_one,
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )
        IndustryStructure.objects.create(
            name=source_two.name,
            personal_tag="other",
            owner_user=other_user,
            source_structure=source_two,
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
            solar_system_name="Perimeter",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_registry"),
                {"scope": "personal"},
            )
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Shared Azbel [mine]", content)
        self.assertNotIn("Shared Sotiyo [other]", content)

    def test_personal_copy_edit_is_forbidden_to_other_users(self) -> None:
        other_user = User.objects.create_user(
            "other-edit",
            email="other-edit@example.com",
            password="secret123",
        )
        grant_indy_access(other_user)
        source = IndustryStructure.objects.create(
            name="Shared Azbel",
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )
        personal_copy = IndustryStructure.objects.create(
            name=source.name,
            personal_tag="mine",
            owner_user=self.user,
            source_structure=source,
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_edit", args=[personal_copy.id])
            ),
            user=other_user,
        )

        with self.assertRaises(Http404):
            self._edit_view(request, personal_copy.id)

    def test_delete_view_removes_structure(self) -> None:
        structure = IndustryStructure.objects.create(
            name="Delete Me",
            solar_system_id=30000142,
            solar_system_name="Jita",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_delete", args=[structure.id]),
            )
        )
        response = self._delete_view(request, structure.id)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(IndustryStructure.objects.filter(id=structure.id).exists())

    def test_model_save_rejects_duplicate_public_name_without_partial_constraint(
        self,
    ) -> None:
        IndustryStructure.objects.create(
            name="C-N4OD - Kuat Drive Yards",
            solar_system_name="Jita",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
        )

        with self.assertRaises(ValidationError) as caught:
            IndustryStructure.objects.create(
                name="C-N4OD  -   Kuat Drive Yards",
                solar_system_name="Perimeter",
                structure_type_id=35827,
                structure_type_name="Sotiyo",
                visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
            )

        self.assertIn(
            "A shared structure with this registry name already exists, even when whitespace is ignored.",
            caught.exception.message_dict["name"],
        )

    def test_model_save_rejects_duplicate_personal_tag_without_partial_constraint(
        self,
    ) -> None:
        IndustryStructure.objects.create(
            name="Azbel Profile",
            personal_tag="mine",
            owner_user=self.user,
            visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
        )

        with self.assertRaises(ValidationError) as caught:
            IndustryStructure.objects.create(
                name="Azbel Profile",
                personal_tag="Mine",
                owner_user=self.user,
                visibility_scope=IndustryStructure.VisibilityScope.PERSONAL,
                solar_system_name="Perimeter",
                structure_type_id=35826,
                structure_type_name="Azbel",
            )

        self.assertIn(
            "You already have a personal copy with this tag for this structure.",
            caught.exception.message_dict["personal_tag"],
        )

    def test_bulk_update_view_applies_tax_only_to_matching_structures(self) -> None:
        matched_structure = IndustryStructure.objects.create(
            name="Synced Highsec",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_id=30000142,
            solar_system_name="Jita",
            constellation_name="Kimotoro",
            region_name="The Forge",
            invention_tax_percent=Decimal("0.000"),
        )
        IndustryStructure.objects.create(
            name="Manual Highsec",
            sync_source=IndustryStructure.SyncSource.MANUAL,
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_id=30000142,
            solar_system_name="Jita",
            constellation_name="Kimotoro",
            region_name="The Forge",
            invention_tax_percent=Decimal("0.000"),
        )
        IndustryStructure.objects.create(
            name="Synced Lowsec",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            system_security_band=IndustryStructure.SecurityBand.LOWSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_id=30002187,
            solar_system_name="Perimeter",
            constellation_name="Kimotoro",
            region_name="Black Rise",
            invention_tax_percent=Decimal("0.000"),
        )

        preview_request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_bulk_update"),
                {
                    "source_scope": "synced",
                    "solar_system_name": "Jita",
                    "constellation_name": "Kimotoro",
                    "region_name": "The Forge",
                    "system_security_band": IndustryStructure.SecurityBand.HIGHSEC,
                    "structure_type_id": "35826",
                    "owner_corporation_id": "99000001",
                    "only_when_zero": "on",
                    "invention_tax_percent": "1.250",
                },
            )
        )
        preview_response = self._bulk_update_view(preview_request)

        self.assertEqual(preview_response.status_code, 200)
        self.assertIn(
            "currently have zero tax",
            preview_response.content.decode().lower(),
        )

        confirm_request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_bulk_update"),
                {
                    "source_scope": "synced",
                    "solar_system_name": "Jita",
                    "constellation_name": "Kimotoro",
                    "region_name": "The Forge",
                    "system_security_band": IndustryStructure.SecurityBand.HIGHSEC,
                    "structure_type_id": "35826",
                    "owner_corporation_id": "99000001",
                    "only_when_zero": "on",
                    "confirm_apply": "1",
                    "invention_tax_percent": "1.250",
                },
            )
        )
        response = self._bulk_update_view(confirm_request)

        self.assertEqual(response.status_code, 302)
        matched_structure.refresh_from_db()
        self.assertEqual(matched_structure.invention_tax_percent, Decimal("1.250"))
        self.assertEqual(
            IndustryStructure.objects.get(name="Manual Highsec").invention_tax_percent,
            Decimal("0.000"),
        )
        self.assertEqual(
            IndustryStructure.objects.get(name="Synced Lowsec").invention_tax_percent,
            Decimal("0.000"),
        )

    def test_bulk_update_preview_returns_matching_structure_names(self) -> None:
        IndustryStructure.objects.create(
            name="Jita Alpha",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            constellation_name="Kimotoro",
            region_name="The Forge",
            invention_tax_percent=Decimal("0.000"),
        )
        IndustryStructure.objects.create(
            name="Jita Beta",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            constellation_name="Kimotoro",
            region_name="The Forge",
            invention_tax_percent=Decimal("2.500"),
        )
        IndustryStructure.objects.create(
            name="Perimeter Gamma",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Perimeter",
            constellation_name="Kimotoro",
            region_name="The Forge",
            invention_tax_percent=Decimal("0.000"),
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_bulk_update_preview"),
                {
                    "source_scope": "synced",
                    "solar_system_name": "Jita",
                    "constellation_name": "Kimotoro",
                    "region_name": "The Forge",
                    "system_security_band": IndustryStructure.SecurityBand.HIGHSEC,
                    "structure_type_id": "35826",
                    "owner_corporation_id": "99000001",
                    "only_when_zero": "on",
                    "invention_tax_percent": "1.250",
                },
            )
        )
        response = self._bulk_update_preview_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["matched_count"], 2)
        self.assertEqual(payload["eligible_count"], 1)
        self.assertEqual(payload["structure_names"], ["Jita Alpha"])

    def test_bulk_update_preview_requires_tax_values_for_non_empty_preview(
        self,
    ) -> None:
        IndustryStructure.objects.create(
            name="Jita Alpha",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_name="Jita",
            invention_tax_percent=Decimal("0.000"),
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:industry_structure_bulk_update_preview"),
                {
                    "solar_system_name": "Jita",
                },
            )
        )
        response = self._bulk_update_preview_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["matched_count"], 1)
        self.assertEqual(payload["eligible_count"], 0)
        self.assertFalse(payload["has_tax_updates"])

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch(
        "indy_hub.services.industry_structure_import.resolve_solar_system_location_reference"
    )
    @patch("indy_hub.services.industry_structure_import.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structure_import.resolve_item_type_reference")
    def test_bulk_import_view_creates_structures_from_pasted_indy_data(
        self,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_sde_loaded,
    ) -> None:
        pasted_data = """Athanor\tYRNJ-8 - Om nom. Nom.\tReprocessing\nStandup M-Set Asteroid Ore Grading Processor II\n\nYRNJ-8\tTaurus\tFountain\nAzbel\tXJP-Y7 - Capital Ships and Components\tManufacturing (Standard) Manufacturing (Capitals)\nStandup L-Set Basic Capital Component Manufacturing Efficiency I\nStandup L-Set Capital Ship Manufacturing Efficiency I\n\nXJP-Y7\tChimera\tFountain\n"""

        def resolve_item_type_reference(*, item_type_id=None, item_type_name=None):
            mapping = {
                "Azbel": (35826, "Azbel"),
                "Standup L-Set Basic Capital Component Manufacturing Efficiency I": (
                    37172,
                    "Standup L-Set Basic Capital Component Manufacturing Efficiency I",
                ),
                "Standup L-Set Capital Ship Manufacturing Efficiency I": (
                    37174,
                    "Standup L-Set Capital Ship Manufacturing Efficiency I",
                ),
            }
            return mapping.get(item_type_name)

        mock_resolve_item_type_reference.side_effect = resolve_item_type_reference
        mock_resolve_solar_system_reference.side_effect = (
            lambda *, solar_system_id=None, solar_system_name=None: {
                "XJP-Y7": (
                    30004759,
                    "XJP-Y7",
                    IndustryStructure.SecurityBand.NULLSEC,
                )
            }.get(solar_system_name)
        )
        mock_resolve_solar_system_location_reference.side_effect = (
            lambda *, solar_system_id=None, solar_system_name=None: {
                "XJP-Y7": {
                    "solar_system_id": 30004759,
                    "solar_system_name": "XJP-Y7",
                    "system_security_band": IndustryStructure.SecurityBand.NULLSEC,
                    "constellation_id": 20000748,
                    "constellation_name": "Pegasus",
                    "region_id": 10000058,
                    "region_name": "Fountain",
                }
            }.get(solar_system_name)
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_bulk_import"),
                {
                    "raw_text": pasted_data,
                },
            )
        )
        response = self._bulk_import_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(IndustryStructure.objects.count(), 1)
        structure = IndustryStructure.objects.get(
            name="XJP-Y7 - Capital Ships and Components"
        )
        self.assertEqual(structure.sync_source, IndustryStructure.SyncSource.MANUAL)
        self.assertEqual(structure.structure_type_name, "Azbel")
        self.assertEqual(structure.solar_system_name, "XJP-Y7")
        self.assertEqual(structure.constellation_name, "Pegasus")
        self.assertEqual(structure.region_name, "Fountain")
        self.assertTrue(structure.enable_manufacturing)
        self.assertFalse(structure.enable_reactions)
        self.assertEqual(structure.rigs.count(), 2)
        self.assertEqual(structure.rigs.get(slot_index=1).rig_type_id, 37172)
        self.assertEqual(structure.rigs.get(slot_index=2).rig_type_id, 37174)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch(
        "indy_hub.services.industry_structure_import.resolve_solar_system_location_reference"
    )
    @patch("indy_hub.services.industry_structure_import.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structure_import.resolve_item_type_reference")
    def test_bulk_import_view_updates_existing_manual_when_enabled(
        self,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_sde_loaded,
    ) -> None:
        structure = IndustryStructure.objects.create(
            name="V6-NY1 - Structures, Invention",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            solar_system_id=30000142,
            solar_system_name="Jita",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            enable_manufacturing=True,
            invention_tax_percent=Decimal("1.500"),
        )
        IndustryStructureRig.objects.create(
            structure=structure,
            slot_index=1,
            rig_type_id=99999,
            rig_type_name="Old Rig",
        )
        pasted_data = """Raitaru\tV6-NY1 - Structures, Invention\tInvention Manufacturing (Standard)\nStandup M-Set Structure Manufacturing Material Efficiency II\nStandup M-Set Structure Manufacturing Time Efficiency II\nStandup M-Set Invention Accelerator I\n\nV6-NY1\tMinotaur\tFountain\n"""

        def resolve_item_type_reference(*, item_type_id=None, item_type_name=None):
            mapping = {
                "Raitaru": (35825, "Raitaru"),
                "Standup M-Set Structure Manufacturing Material Efficiency II": (
                    37163,
                    "Standup M-Set Structure Manufacturing Material Efficiency II",
                ),
                "Standup M-Set Structure Manufacturing Time Efficiency II": (
                    37164,
                    "Standup M-Set Structure Manufacturing Time Efficiency II",
                ),
                "Standup M-Set Invention Accelerator I": (
                    43879,
                    "Standup M-Set Invention Accelerator I",
                ),
            }
            return mapping.get(item_type_name)

        mock_resolve_item_type_reference.side_effect = resolve_item_type_reference
        mock_resolve_solar_system_reference.side_effect = (
            lambda *, solar_system_id=None, solar_system_name=None: {
                "V6-NY1": (
                    30004760,
                    "V6-NY1",
                    IndustryStructure.SecurityBand.NULLSEC,
                )
            }.get(solar_system_name)
        )
        mock_resolve_solar_system_location_reference.side_effect = (
            lambda *, solar_system_id=None, solar_system_name=None: {
                "V6-NY1": {
                    "solar_system_id": 30004760,
                    "solar_system_name": "V6-NY1",
                    "system_security_band": IndustryStructure.SecurityBand.NULLSEC,
                    "constellation_id": 20000748,
                    "constellation_name": "Minotaur",
                    "region_id": 10000058,
                    "region_name": "Fountain",
                }
            }.get(solar_system_name)
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_bulk_import"),
                {
                    "raw_text": pasted_data,
                    "update_existing_manual": "on",
                },
            )
        )
        response = self._bulk_import_view(request)

        self.assertEqual(response.status_code, 302)
        structure.refresh_from_db()
        self.assertEqual(structure.solar_system_name, "V6-NY1")
        self.assertEqual(structure.constellation_name, "Minotaur")
        self.assertEqual(structure.region_name, "Fountain")
        self.assertEqual(structure.invention_tax_percent, Decimal("1.500"))
        self.assertTrue(structure.enable_invention)
        self.assertEqual(structure.rigs.count(), 3)
        self.assertEqual(structure.rigs.get(slot_index=3).rig_type_id, 43879)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    @patch(
        "indy_hub.services.industry_structure_import.resolve_solar_system_location_reference"
    )
    @patch("indy_hub.services.industry_structure_import.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structure_import.resolve_item_type_reference")
    def test_bulk_import_view_keeps_supported_activities_when_reprocessing_is_also_present(
        self,
        mock_resolve_item_type_reference,
        mock_resolve_solar_system_reference,
        mock_resolve_solar_system_location_reference,
        _mock_sde_loaded,
    ) -> None:
        pasted_data = """Athanor\tNOL-M9 - Mixed Service\tReprocessing Manufacturing (Standard)\n\nNOL-M9\tPegasus\tFountain\n"""

        def resolve_item_type_reference(*, item_type_id=None, item_type_name=None):
            mapping = {
                "Athanor": (35835, "Athanor"),
            }
            return mapping.get(item_type_name)

        mock_resolve_item_type_reference.side_effect = resolve_item_type_reference
        mock_resolve_solar_system_reference.side_effect = (
            lambda *, solar_system_id=None, solar_system_name=None: {
                "NOL-M9": (
                    30004761,
                    "NOL-M9",
                    IndustryStructure.SecurityBand.NULLSEC,
                )
            }.get(solar_system_name)
        )
        mock_resolve_solar_system_location_reference.side_effect = (
            lambda *, solar_system_id=None, solar_system_name=None: {
                "NOL-M9": {
                    "solar_system_id": 30004761,
                    "solar_system_name": "NOL-M9",
                    "system_security_band": IndustryStructure.SecurityBand.NULLSEC,
                    "constellation_id": 20000748,
                    "constellation_name": "Pegasus",
                    "region_id": 10000058,
                    "region_name": "Fountain",
                }
            }.get(solar_system_name)
        )

        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:industry_structure_bulk_import"),
                {
                    "raw_text": pasted_data,
                },
            )
        )
        response = self._bulk_import_view(request)

        self.assertEqual(response.status_code, 302)
        structure = IndustryStructure.objects.get(name="NOL-M9 - Mixed Service")
        self.assertEqual(structure.structure_type_name, "Athanor")
        self.assertEqual(structure.solar_system_name, "NOL-M9")
        self.assertTrue(structure.enable_manufacturing)
        self.assertFalse(structure.enable_reactions)

    @patch(
        "indy_hub.views.industry.get_available_structure_sync_targets", return_value=[]
    )
    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_highlights_incomplete_synced_structures(
        self,
        _mock_sde_loaded,
        _mock_sync_targets,
    ) -> None:
        IndustryStructure.objects.create(
            name="Imported Empty Azbel",
            sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
            external_structure_id=1020000000002,
            owner_corporation_id=99000001,
            owner_corporation_name="Indy Corp",
            solar_system_id=30000142,
            solar_system_name="Jita",
            structure_type_id=35826,
            structure_type_name="Azbel",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            manufacturing_tax_percent=Decimal("0.000"),
            te_research_tax_percent=Decimal("0.000"),
            me_research_tax_percent=Decimal("0.000"),
            copying_tax_percent=Decimal("0.000"),
            invention_tax_percent=Decimal("0.000"),
            reactions_tax_percent=Decimal("0.000"),
        )

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry"))
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Enabled Activities", content)
        self.assertIn("System Type", content)
        self.assertNotIn("Missing complementary information", content)

    @patch("indy_hub.views.industry.sde_item_types_loaded", return_value=True)
    def test_registry_hides_sync_controls_for_non_admin(self, _mock_sde_loaded) -> None:
        user = User.objects.create_user(
            "structures-viewer",
            email="viewer@example.com",
            password="secret123",
        )
        grant_indy_access(user)

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:industry_structure_registry")),
            user=user,
        )
        response = self._view(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Sync Available Structures", content)
        self.assertNotIn("Authorize Corporation ESI", content)
        self.assertIn("Add Structure", content)
