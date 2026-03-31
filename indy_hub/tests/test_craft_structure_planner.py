"""Tests for craft structure planner recommendations."""

# Standard Library
from decimal import Decimal
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.models import IndustryStructure, IndustrySystemCostIndex
from indy_hub.services.craft_structures import (
    _fetch_craftable_item_rows,
    _service_category_for_item,
    build_craft_structure_planner,
)
from indy_hub.services.industry_structures import IndustryStructureResolvedBonus


class _CursorStub:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.last_query = sql
        self.last_params = params

    def fetchall(self):
        return self._rows


class CraftStructurePlannerTests(TestCase):
    def setUp(self) -> None:
        self.nearby_structure = IndustryStructure.objects.create(
            name="Jita Capital Hub",
            structure_type_id=35826,
            structure_type_name="Azbel",
            solar_system_id=30000142,
            solar_system_name="Jita",
            constellation_id=20000020,
            constellation_name="Kimotoro",
            region_id=10000002,
            region_name="The Forge",
            enable_manufacturing=True,
            enable_manufacturing_capitals=True,
            manufacturing_tax_percent=Decimal("0.400"),
            manufacturing_capitals_tax_percent=Decimal("0.400"),
        )
        self.remote_structure = IndustryStructure.objects.create(
            name="Delve Component Yard",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            solar_system_id=30004759,
            solar_system_name="1DQ1-A",
            constellation_id=20000702,
            constellation_name="1P-VL2",
            region_id=10000060,
            region_name="Delve",
            enable_manufacturing=True,
            enable_manufacturing_capitals=True,
            manufacturing_tax_percent=Decimal("0.400"),
            manufacturing_capitals_tax_percent=Decimal("0.400"),
        )
        self.supercap_structure = IndustryStructure.objects.create(
            name="Supercapital Forge",
            structure_type_id=35827,
            structure_type_name="Sotiyo",
            solar_system_id=30002187,
            solar_system_name="Amarr",
            constellation_id=20000322,
            constellation_name="Throne Worlds",
            region_id=10000043,
            region_name="Domain",
            enable_manufacturing=True,
            enable_manufacturing_super_capitals=True,
            manufacturing_tax_percent=Decimal("0.300"),
            manufacturing_super_capitals_tax_percent=Decimal("0.300"),
        )
        self.reaction_structure = IndustryStructure.objects.create(
            name="Reaction Athanor",
            structure_type_id=35835,
            structure_type_name="Athanor",
            solar_system_id=30003001,
            solar_system_name="Nourvukaiken",
            constellation_id=20000401,
            constellation_name="Kimotoro Fringe",
            region_id=10000002,
            region_name="The Forge",
            enable_composite_reactions=True,
            composite_reactions_tax_percent=Decimal("0.500"),
        )
        self.unrigged_reaction_structure = IndustryStructure.objects.create(
            name="C1XD-X - Not Ready for Use",
            structure_type_id=35835,
            structure_type_name="Athanor",
            solar_system_id=30003002,
            solar_system_name="C1XD-X",
            constellation_id=20000402,
            constellation_name="Outer Ring Test",
            region_id=10000002,
            region_name="The Forge",
            enable_composite_reactions=True,
            composite_reactions_tax_percent=Decimal("0.100"),
        )

        for solar_system_id in (30000142, 30004759, 30002187):
            IndustrySystemCostIndex.objects.create(
                solar_system_id=solar_system_id,
                solar_system_name="System",
                activity_id=IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
                cost_index_percent=Decimal("3.00000"),
            )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30003001,
            solar_system_name="Nourvukaiken",
            activity_id=IndustrySystemCostIndex.ACTIVITY_REACTIONS,
            cost_index_percent=Decimal("4.00000"),
        )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30003002,
            solar_system_name="C1XD-X",
            activity_id=IndustrySystemCostIndex.ACTIVITY_REACTIONS,
            cost_index_percent=Decimal("1.00000"),
        )

    def _resolved_bonuses(self, structure: IndustryStructure):
        bonuses = {
            self.nearby_structure.id: [
                IndustryStructureResolvedBonus(
                    source="structure",
                    label="Structure role bonus",
                    activity_id=1,
                    material_efficiency_percent=Decimal("1.0"),
                    job_cost_percent=Decimal("2.0"),
                ),
                IndustryStructureResolvedBonus(
                    source="rig",
                    label="Advanced Component Rig",
                    activity_id=1,
                    supported_types_label="Types supported",
                    supported_type_names=("Advanced Component",),
                    material_efficiency_percent=Decimal("2.1"),
                ),
            ],
            self.remote_structure.id: [
                IndustryStructureResolvedBonus(
                    source="structure",
                    label="Structure role bonus",
                    activity_id=1,
                    material_efficiency_percent=Decimal("1.0"),
                    job_cost_percent=Decimal("2.0"),
                ),
                IndustryStructureResolvedBonus(
                    source="rig",
                    label="Advanced Component Rig",
                    activity_id=1,
                    supported_types_label="Types supported",
                    supported_type_names=("Advanced Component",),
                    material_efficiency_percent=Decimal("2.4"),
                ),
            ],
            self.supercap_structure.id: [
                IndustryStructureResolvedBonus(
                    source="structure",
                    label="Structure role bonus",
                    activity_id=1,
                    material_efficiency_percent=Decimal("1.0"),
                    job_cost_percent=Decimal("2.0"),
                ),
            ],
            self.reaction_structure.id: [
                IndustryStructureResolvedBonus(
                    source="structure",
                    label="Reaction role bonus",
                    activity_id=9,
                    job_cost_percent=Decimal("2.2"),
                ),
                IndustryStructureResolvedBonus(
                    source="rig",
                    label="Composite Reaction Rig",
                    activity_id=9,
                    supported_types_label="Types supported",
                    supported_type_names=("Composite Reaction",),
                    material_efficiency_percent=Decimal("2.4"),
                    time_efficiency_percent=Decimal("20.0"),
                ),
            ],
        }
        return bonuses.get(structure.id, [])

    @patch("indy_hub.services.craft_structures.connection.cursor")
    def test_fetch_craftable_item_rows_maps_reaction_activity_ids(
        self, mock_cursor
    ) -> None:
        mock_cursor.return_value = _CursorStub(
            [
                (
                    500,
                    "Ferrofluid",
                    200,
                    123456.78,
                    11,
                    "Composite Reactions",
                    "Material",
                ),
            ]
        )

        rows = _fetch_craftable_item_rows([500])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["base_price"], 123456.78)
        self.assertEqual(rows[0]["activity_id"], 9)
        self.assertEqual(rows[0]["activity_label"], "Reactions")

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_planner_prefers_exact_best_component_bonus_even_when_remote(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 100,
                "type_name": "Thanatos",
                "produced_per_cycle": 1,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Carrier",
                "category_name": "Ship",
            },
            {
                "type_id": 200,
                "type_name": "Capital Core Temperature Regulator",
                "produced_per_cycle": 1,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Advanced Component",
                "category_name": "Commodity",
            },
        ]
        mock_get_resolved_bonuses.side_effect = self._resolved_bonuses

        planner = build_craft_structure_planner(
            product_type_id=100,
            product_type_name="Thanatos",
            product_output_per_cycle=1,
            craft_cycles_summary={
                200: {
                    "type_id": 200,
                    "type_name": "Capital Core Temperature Regulator",
                    "total_needed": 8,
                    "produced_per_cycle": 1,
                }
            },
        )

        items_by_type_id = {item["type_id"]: item for item in planner["items"]}
        self.assertEqual(
            items_by_type_id[100]["recommended_structure_id"],
            self.remote_structure.id,
        )
        self.assertEqual(
            items_by_type_id[200]["recommended_structure_id"],
            self.remote_structure.id,
        )
        self.assertEqual(
            planner["summary"]["selected_structure_count"],
            1,
        )

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_planner_reuses_same_structure_when_economics_are_equal(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        shared_bonus = IndustryStructureResolvedBonus(
            source="structure",
            label="Structure role bonus",
            activity_id=1,
            material_efficiency_percent=Decimal("1.0"),
            job_cost_percent=Decimal("2.0"),
            time_efficiency_percent=Decimal("15.0"),
        )

        def equal_bonuses(structure: IndustryStructure):
            if structure.id in {self.nearby_structure.id, self.remote_structure.id}:
                return [shared_bonus]
            return []

        mock_get_resolved_bonuses.side_effect = equal_bonuses
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 100,
                "type_name": "Thanatos",
                "produced_per_cycle": 1,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Carrier",
                "category_name": "Ship",
            },
            {
                "type_id": 200,
                "type_name": "Capital Core Temperature Regulator",
                "produced_per_cycle": 1,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Advanced Component",
                "category_name": "Commodity",
            },
        ]

        planner = build_craft_structure_planner(
            product_type_id=100,
            product_type_name="Thanatos",
            product_output_per_cycle=1,
            craft_cycles_summary={
                200: {
                    "type_id": 200,
                    "type_name": "Capital Core Temperature Regulator",
                    "total_needed": 8,
                    "produced_per_cycle": 1,
                }
            },
        )

        items_by_type_id = {item["type_id"]: item for item in planner["items"]}
        self.assertEqual(
            items_by_type_id[100]["recommended_structure_id"],
            self.nearby_structure.id,
        )
        self.assertEqual(
            items_by_type_id[200]["recommended_structure_id"],
            self.nearby_structure.id,
        )
        self.assertEqual(
            planner["summary"]["selected_structure_count"],
            1,
        )

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_supercapital_items_only_offer_supercapital_services(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 300,
                "type_name": "Avatar",
                "produced_per_cycle": 1,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Titan",
                "category_name": "Ship",
            }
        ]
        mock_get_resolved_bonuses.side_effect = self._resolved_bonuses

        planner = build_craft_structure_planner(
            product_type_id=300,
            product_type_name="Avatar",
            product_output_per_cycle=1,
            craft_cycles_summary={},
        )

        self.assertEqual(len(planner["items"]), 1)
        item = planner["items"][0]
        self.assertEqual(item["service_category"], "manufacturing_super_capitals")
        self.assertEqual(len(item["options"]), 1)
        self.assertEqual(item["options"][0]["structure_id"], self.supercap_structure.id)

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_reaction_items_use_reaction_activity_and_structures(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 500,
                "type_name": "Ferrofluid",
                "produced_per_cycle": 200,
                "activity_id": 9,
                "activity_label": "Reactions",
                "group_name": "Composite Reactions",
                "category_name": "Material",
            }
        ]
        mock_get_resolved_bonuses.side_effect = self._resolved_bonuses

        planner = build_craft_structure_planner(
            product_type_id=500,
            product_type_name="Ferrofluid",
            product_output_per_cycle=200,
            craft_cycles_summary={},
        )

        self.assertEqual(len(planner["items"]), 1)
        item = planner["items"][0]
        self.assertEqual(item["activity_id"], 9)
        self.assertEqual(item["activity_label"], "Reactions")
        self.assertEqual(item["service_category"], "composite_reactions")
        self.assertEqual(len(item["options"]), 2)
        self.assertEqual(item["options"][0]["structure_id"], self.reaction_structure.id)

    def test_reaction_service_category_maps_live_sde_output_groups(self) -> None:
        self.assertEqual(
            _service_category_for_item(9, "Biochemical Material"),
            "biochemical_reactions",
        )
        self.assertEqual(
            _service_category_for_item(9, "Composite"),
            "composite_reactions",
        )
        self.assertEqual(
            _service_category_for_item(9, "Intermediate Materials"),
            "composite_reactions",
        )
        self.assertEqual(
            _service_category_for_item(9, "Unrefined Mineral"),
            "composite_reactions",
        )
        self.assertEqual(
            _service_category_for_item(9, "Hybrid Polymers"),
            "hybrid_reactions",
        )
        self.assertEqual(
            _service_category_for_item(9, "Molecular-Forged Materials"),
            "hybrid_reactions",
        )

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_composite_reaction_rig_bonus_beats_unrigged_lower_tax_structure(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 500,
                "type_name": "Ferrofluid",
                "produced_per_cycle": 200,
                "activity_id": 9,
                "activity_label": "Reactions",
                "group_name": "Composite Reactions",
                "category_name": "Material",
            }
        ]
        mock_get_resolved_bonuses.side_effect = self._resolved_bonuses

        planner = build_craft_structure_planner(
            product_type_id=500,
            product_type_name="Ferrofluid",
            product_output_per_cycle=200,
            craft_cycles_summary={},
        )

        item = planner["items"][0]
        self.assertEqual(item["recommended_structure_id"], self.reaction_structure.id)
        options_by_structure = {
            option["structure_id"]: option for option in item["options"]
        }
        self.assertGreater(
            options_by_structure[self.reaction_structure.id]["material_bonus_percent"],
            options_by_structure[self.unrigged_reaction_structure.id][
                "material_bonus_percent"
            ],
        )

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_planner_prefers_lower_install_cost_for_activity_when_bonuses_are_equal(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        self.nearby_structure.manufacturing_capitals_tax_percent = Decimal("0.900")
        self.nearby_structure.save(update_fields=["manufacturing_capitals_tax_percent"])
        self.remote_structure.manufacturing_capitals_tax_percent = Decimal("0.100")
        self.remote_structure.save(update_fields=["manufacturing_capitals_tax_percent"])

        IndustrySystemCostIndex.objects.filter(
            solar_system_id=self.nearby_structure.solar_system_id,
            activity_id=IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
        ).update(cost_index_percent=Decimal("5.00000"))
        IndustrySystemCostIndex.objects.filter(
            solar_system_id=self.remote_structure.solar_system_id,
            activity_id=IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
        ).update(cost_index_percent=Decimal("1.50000"))

        shared_bonus = IndustryStructureResolvedBonus(
            source="structure",
            label="Structure role bonus",
            activity_id=1,
            material_efficiency_percent=Decimal("1.0"),
            job_cost_percent=Decimal("2.0"),
            time_efficiency_percent=Decimal("15.0"),
        )

        def equal_bonuses(structure: IndustryStructure):
            if structure.id in {self.nearby_structure.id, self.remote_structure.id}:
                return [shared_bonus]
            return []

        mock_get_resolved_bonuses.side_effect = equal_bonuses
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 100,
                "type_name": "Thanatos",
                "produced_per_cycle": 1,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Carrier",
                "category_name": "Ship",
            }
        ]

        planner = build_craft_structure_planner(
            product_type_id=100,
            product_type_name="Thanatos",
            product_output_per_cycle=1,
            craft_cycles_summary={},
        )

        self.assertEqual(len(planner["items"]), 1)
        item = planner["items"][0]
        self.assertEqual(item["recommended_structure_id"], self.remote_structure.id)
        self.assertLess(
            item["options"][0]["total_installation_cost_percent"],
            item["options"][1]["total_installation_cost_percent"],
        )

    @patch("indy_hub.services.craft_structures._fetch_craftable_item_rows")
    @patch("indy_hub.models.IndustryStructure.get_resolved_bonuses")
    def test_planner_exposes_rig_me_te_separately_from_hull_bonus(
        self,
        mock_get_resolved_bonuses,
        mock_fetch_craftable_item_rows,
    ) -> None:
        mock_fetch_craftable_item_rows.return_value = [
            {
                "type_id": 700,
                "type_name": "Ishtar",
                "produced_per_cycle": 1,
                "base_price": 124209645,
                "activity_id": 1,
                "activity_label": "Manufacturing",
                "group_name": "Heavy Assault Cruiser",
                "category_name": "Ship",
            }
        ]

        def ship_bonuses(structure: IndustryStructure):
            if structure.id != self.remote_structure.id:
                return []
            return [
                IndustryStructureResolvedBonus(
                    source="structure",
                    label="Structure role bonus",
                    activity_id=1,
                    material_efficiency_percent=Decimal("1.0"),
                    time_efficiency_percent=Decimal("15.0"),
                ),
                IndustryStructureResolvedBonus(
                    source="rig",
                    label="Manufacturing rig bonus details",
                    activity_id=1,
                    supported_types_label="Types supported",
                    supported_type_names=("Heavy Assault Cruiser",),
                    material_efficiency_percent=Decimal("2.4"),
                    time_efficiency_percent=Decimal("20.0"),
                ),
            ]

        mock_get_resolved_bonuses.side_effect = ship_bonuses

        planner = build_craft_structure_planner(
            product_type_id=700,
            product_type_name="Ishtar",
            product_output_per_cycle=1,
            craft_cycles_summary={},
        )

        item = planner["items"][0]
        self.assertEqual(item["base_price"], 124209645)
        self.assertEqual(item["estimated_item_value"], 124209645)
        option = item["options"][0]
        self.assertEqual(option["rig_material_bonus_percent"], 2.4)
        self.assertEqual(option["rig_time_bonus_percent"], 20.0)
        self.assertEqual(option["structure_material_bonus_percent"], 1.0)
        self.assertEqual(option["structure_time_bonus_percent"], 15.0)
        self.assertGreater(
            option["material_bonus_percent"], option["rig_material_bonus_percent"]
        )
        self.assertGreater(
            option["time_bonus_percent"], option["rig_time_bonus_percent"]
        )
