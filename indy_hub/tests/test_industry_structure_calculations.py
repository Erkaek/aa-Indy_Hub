"""Tests for dogma-backed industry structure bonus and tax calculations."""

# Standard Library
from decimal import Decimal
from unittest.mock import patch

# Django
from django.core.exceptions import ValidationError
from django.test import TestCase

# AA Example App
from indy_hub.models import (
    IndustryStructure,
    IndustryStructureRig,
    IndustrySystemCostIndex,
)
from indy_hub.services.industry_structures import (
    SDETypeSnapshot,
    _supported_type_names_for_effect,
    build_structure_activity_previews,
    calculate_installation_cost,
    resolve_rig_type_bonuses,
)


class IndustryStructureCalculationTests(TestCase):
    def setUp(self) -> None:
        self.structure = IndustryStructure.objects.create(
            name="Raitaru Prime",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band=IndustryStructure.SecurityBand.HIGHSEC,
            solar_system_id=30000142,
            solar_system_name="Jita",
            manufacturing_tax_percent=Decimal("0.500"),
            invention_tax_percent=Decimal("0.500"),
        )
        self.cost_index = IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Jita",
            activity_id=8,
            cost_index_percent=Decimal("5.00000"),
        )

    def _mock_snapshot(self, item_type_id: int):
        if item_type_id == 35825:
            return SDETypeSnapshot(
                type_id=35825,
                name="Raitaru",
                dogma_attributes={
                    2600: Decimal("0.99"),
                    2601: Decimal("0.97"),
                    2602: Decimal("0.85"),
                },
                dogma_effect_names=("engComplexServiceFuelBonus",),
            )
        if item_type_id == 37181:
            return SDETypeSnapshot(
                type_id=37181,
                name="Standup XL-Set Ship Manufacturing Efficiency II",
                dogma_attributes={
                    2356: Decimal("1.9"),
                    2357: Decimal("2.1"),
                    2593: Decimal("-24.0"),
                    2594: Decimal("-2.4"),
                },
                dogma_effect_names=(
                    "rigAllShipManufactureMaterialBonus",
                    "rigAllShipManufactureTimeBonus",
                    "structureEngineeringRigSecurityModification",
                ),
            )
        if item_type_id == 43879:
            return SDETypeSnapshot(
                type_id=43879,
                name="Standup M-Set Invention Cost Optimization I",
                dogma_attributes={
                    2356: Decimal("1.9"),
                    2357: Decimal("2.1"),
                    2595: Decimal("-10.0"),
                },
                dogma_effect_names=(
                    "rigInventionCostBonus",
                    "structureEngineeringRigSecurityModification",
                ),
            )
        return None

    @patch("indy_hub.services.industry_structures.get_type_snapshot")
    def test_total_bonus_percent_is_derived_from_hull_and_rig_dogma(
        self, mock_snapshot
    ) -> None:
        mock_snapshot.side_effect = self._mock_snapshot
        IndustryStructureRig.objects.create(
            structure=self.structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Standup XL-Set Ship Manufacturing Efficiency II",
        )

        total_material_bonus = self.structure.get_total_bonus_percent(
            1,
            "material_efficiency_percent",
        )
        total_time_bonus = self.structure.get_total_bonus_percent(
            1,
            "time_efficiency_percent",
        )

        self.assertEqual(
            total_material_bonus.quantize(Decimal("0.001")),
            Decimal("3.376"),
        )
        self.assertEqual(
            total_time_bonus.quantize(Decimal("0.001")),
            Decimal("35.400"),
        )

    @patch("indy_hub.services.industry_structures.get_type_snapshot")
    def test_installation_cost_breakdown_uses_dogma_derived_cost_bonus(
        self, mock_snapshot
    ) -> None:
        mock_snapshot.side_effect = self._mock_snapshot
        IndustryStructureRig.objects.create(
            structure=self.structure,
            slot_index=1,
            rig_type_id=43879,
            rig_type_name="Standup M-Set Invention Cost Optimization I",
        )

        breakdown = calculate_installation_cost(
            structure=self.structure,
            activity_id=8,
            estimated_item_value=Decimal("1000000"),
        )

        self.assertEqual(breakdown.system_cost_index_percent, Decimal("5.00000"))
        self.assertEqual(breakdown.base_job_cost, Decimal("50000"))
        self.assertEqual(breakdown.adjusted_job_cost, Decimal("43650"))
        self.assertEqual(breakdown.facility_tax, Decimal("5000"))
        self.assertEqual(breakdown.scc_surcharge, Decimal("40000"))
        self.assertEqual(breakdown.total_installation_cost, Decimal("88650"))
        self.assertEqual(
            breakdown.total_job_cost_bonus_percent.quantize(Decimal("0.001")),
            Decimal("12.700"),
        )

    def test_missing_system_cost_index_raises_validation_error(self) -> None:
        other_structure = IndustryStructure.objects.create(
            name="Remote Athanor",
            structure_type_id=35835,
            structure_type_name="Athanor",
            solar_system_id=30002510,
            solar_system_name="Somewhere",
        )

        with self.assertRaises(ValidationError):
            calculate_installation_cost(
                structure=other_structure,
                activity_id=1,
                estimated_item_value=Decimal("250000"),
            )

    def test_basic_component_rigs_cover_construction_components(self) -> None:
        supported_types = _supported_type_names_for_effect(
            "rigcomponentmanufacturematerialbonus"
        )

        self.assertIn("Capital Component", supported_types)
        self.assertIn("Capital Construction Component", supported_types)
        self.assertIn("Construction Component", supported_types)
        self.assertIn("Structure Component", supported_types)

    def test_advanced_component_rigs_cover_live_component_aliases(self) -> None:
        supported_types = _supported_type_names_for_effect(
            "rigadvcomponentmanufacturematerialbonus"
        )

        self.assertIn("Advanced Component", supported_types)
        self.assertIn("Hybrid Tech Component", supported_types)

    def test_advanced_capital_component_rigs_cover_live_component_aliases(self) -> None:
        supported_types = _supported_type_names_for_effect(
            "rigadvcapcomponentmanufacturematerialbonus"
        )

        self.assertIn("Advanced Capital Component", supported_types)
        self.assertIn("Advanced Capital Construction Component", supported_types)

    @patch("indy_hub.services.industry_structures._get_blueprint_output_name_rows")
    def test_component_rigs_prefer_live_sde_group_labels(
        self, mock_output_rows
    ) -> None:
        mock_output_rows.return_value = (
            ("Construction Components", "Commodity"),
            ("Capital Construction Components", "Commodity"),
            ("Structure Components", "Commodity"),
        )

        supported_types = _supported_type_names_for_effect(
            "rigcomponentmanufacturematerialbonus"
        )

        self.assertIn("Construction Components", supported_types)
        self.assertIn("Capital Construction Components", supported_types)
        self.assertIn("Structure Components", supported_types)
        self.assertNotIn("Construction Component", supported_types)

    @patch("indy_hub.services.industry_structures._get_blueprint_output_name_rows")
    def test_composite_reaction_rigs_prefer_live_sde_group_labels(
        self, mock_output_rows
    ) -> None:
        mock_output_rows.return_value = (
            ("Intermediate Materials", "Material"),
            ("Composite", "Material"),
            ("Unrefined Mineral", "Material"),
        )

        supported_types = _supported_type_names_for_effect(
            "rigcompositereactiontimebonus"
        )

        self.assertIn("Intermediate Materials", supported_types)
        self.assertIn("Composite", supported_types)
        self.assertIn("Unrefined Mineral", supported_types)
        self.assertNotIn("Intermediate Material", supported_types)

    @patch("indy_hub.services.industry_structures._get_blueprint_output_name_rows")
    def test_legacy_reaction_rig_effect_names_cover_intermediate_materials(
        self,
        mock_output_rows,
    ) -> None:
        mock_output_rows.return_value = (
            ("Composite", "Material"),
            ("Intermediate Materials", "Material"),
            ("Unrefined Mineral", "Material"),
        )

        supported_types = _supported_type_names_for_effect("rigReactionCompMatBonus")

        self.assertIn("Intermediate Materials", supported_types)
        self.assertIn("Composite", supported_types)

    @patch("indy_hub.services.industry_structures._get_blueprint_output_name_rows")
    @patch("indy_hub.services.industry_structures.get_type_snapshot")
    def test_legacy_reaction_rig_resolves_material_bonus_from_reaction_attributes(
        self,
        mock_snapshot,
        mock_output_rows,
    ) -> None:
        mock_output_rows.return_value = (
            ("Composite", "Material"),
            ("Intermediate Materials", "Material"),
            ("Unrefined Mineral", "Material"),
        )
        mock_snapshot.return_value = SDETypeSnapshot(
            type_id=46496,
            name="Standup L-Set Reactor Efficiency I",
            dogma_attributes={
                2357: Decimal("1.1"),
                2713: Decimal("-20.0"),
                2714: Decimal("-2.0"),
            },
            dogma_effect_names=(
                "rigReactionCompMatBonus",
                "rigReactionCompTimeBonus",
                "structureReactionRigSecurityModification",
            ),
            group_id=773,
        )

        bonuses = resolve_rig_type_bonuses(
            46496,
            rig_type_name="Standup L-Set Reactor Efficiency I",
            security_band=IndustryStructure.SecurityBand.NULLSEC,
        )

        composite_material_bonus = next(
            bonus
            for bonus in bonuses
            if bonus.activity_id == 9
            and bonus.material_efficiency_percent > 0
            and "Intermediate Materials" in bonus.supported_type_names
        )
        composite_time_bonus = next(
            bonus
            for bonus in bonuses
            if bonus.activity_id == 9 and bonus.time_efficiency_percent > 0
        )

        self.assertEqual(
            composite_material_bonus.material_efficiency_percent,
            Decimal("2.20"),
        )
        self.assertEqual(
            composite_time_bonus.time_efficiency_percent,
            Decimal("22.00"),
        )

    def test_polymer_reaction_rigs_cover_live_reaction_output_groups(self) -> None:
        supported_types = _supported_type_names_for_effect(
            "rigpolymerreactiontimebonus"
        )

        self.assertIn("Polymer Reaction", supported_types)
        self.assertIn("Hybrid Polymer", supported_types)
        self.assertIn("Molecular-Forged Material", supported_types)

    @patch("indy_hub.services.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structures.resolve_item_type_reference")
    @patch("indy_hub.services.industry_structures.get_type_snapshot")
    def test_activity_preview_keeps_manufacturing_rigs_scoped_per_profile(
        self,
        mock_snapshot,
        mock_resolve_item,
        mock_resolve_system,
    ) -> None:
        def snapshot(item_type_id: int):
            if item_type_id == 35827:
                return SDETypeSnapshot(
                    type_id=35827,
                    name="Sotiyo",
                    dogma_attributes={
                        2600: Decimal("0.99"),
                        2601: Decimal("0.95"),
                        2602: Decimal("0.70"),
                    },
                    dogma_effect_names=("engComplexServiceFuelBonus",),
                    group_id=1404,
                )
            if item_type_id == 37180:
                return SDETypeSnapshot(
                    type_id=37180,
                    name="Standup XL-Set Ship Manufacturing Efficiency I",
                    dogma_attributes={2593: Decimal("-20.0"), 2594: Decimal("-2.0")},
                    dogma_effect_names=(
                        "rigAllShipManufactureMaterialBonus",
                        "rigAllShipManufactureTimeBonus",
                    ),
                    group_id=773,
                )
            if item_type_id == 37178:
                return SDETypeSnapshot(
                    type_id=37178,
                    name="Standup XL-Set Equipment and Consumable Manufacturing Efficiency I",
                    dogma_attributes={2593: Decimal("-20.0"), 2594: Decimal("-2.0")},
                    dogma_effect_names=(
                        "rigEquipmentManufactureMaterialBonus",
                        "rigEquipmentManufactureTimeBonus",
                        "rigAmmoManufactureMaterialBonus",
                        "rigAmmoManufactureTimeBonus",
                        "rigDroneManufactureMaterialBonus",
                        "rigDroneManufactureTimeBonus",
                    ),
                    group_id=773,
                )
            return None

        mock_snapshot.side_effect = snapshot
        mock_resolve_item.side_effect = lambda *, item_type_id=None, item_type_name=None: {
            37180: (37180, "Standup XL-Set Ship Manufacturing Efficiency I"),
            37178: (
                37178,
                "Standup XL-Set Equipment and Consumable Manufacturing Efficiency I",
            ),
        }.get(
            item_type_id
        )
        mock_resolve_system.return_value = (
            30000142,
            "Jita",
            IndustryStructure.SecurityBand.HIGHSEC,
        )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Jita",
            activity_id=IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
            cost_index_percent=Decimal("8.81000"),
        )

        previews = build_structure_activity_previews(
            structure_type_id=35827,
            solar_system_name="Jita",
            rig_type_ids=[37180, 37178],
            enabled_activity_flags={
                "enable_manufacturing": True,
                "enable_te_research": False,
                "enable_me_research": False,
                "enable_copying": False,
                "enable_invention": False,
                "enable_reactions": False,
            },
        )

        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0].activity_label, "Manufacturing")
        self.assertEqual(previews[0].system_cost_index_percent, Decimal("8.81000"))
        self.assertEqual(
            previews[0].structure_role_bonus.time_efficiency_percent, Decimal("30.0")
        )
        supported_type_rows = {
            row.type_name: row for row in previews[0].supported_type_rows
        }
        self.assertIn("Assault Frigate", supported_type_rows)
        self.assertIn("Battleship", supported_type_rows)
        self.assertIn("Module", supported_type_rows)
        self.assertIn("Charge", supported_type_rows)
        self.assertIn("Drone", supported_type_rows)
        self.assertEqual(
            supported_type_rows["Assault Frigate"].material_efficiency_percent,
            Decimal("2.0"),
        )
        self.assertEqual(
            supported_type_rows["Module"].time_efficiency_percent,
            Decimal("20.0"),
        )

    @patch("indy_hub.services.industry_structures.resolve_solar_system_reference")
    @patch("indy_hub.services.industry_structures.get_type_snapshot")
    def test_activity_preview_only_returns_explicitly_enabled_activities(
        self,
        mock_snapshot,
        mock_resolve_system,
    ) -> None:
        mock_snapshot.return_value = SDETypeSnapshot(
            type_id=35827,
            name="Sotiyo",
            dogma_attributes={
                2600: Decimal("0.99"),
                2601: Decimal("0.95"),
                2602: Decimal("0.70"),
            },
            dogma_effect_names=("engComplexServiceFuelBonus",),
            group_id=1404,
        )
        mock_resolve_system.return_value = (
            30000142,
            "Jita",
            IndustryStructure.SecurityBand.HIGHSEC,
        )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Jita",
            activity_id=IndustrySystemCostIndex.ACTIVITY_MANUFACTURING,
            cost_index_percent=Decimal("8.81000"),
        )

        previews = build_structure_activity_previews(
            structure_type_id=35827,
            solar_system_name="Jita",
            rig_type_ids=[],
            enabled_activity_flags={
                "enable_manufacturing": True,
                "enable_te_research": False,
                "enable_me_research": False,
                "enable_copying": False,
                "enable_invention": False,
                "enable_reactions": False,
            },
        )

        self.assertEqual([preview.activity_id for preview in previews], [1])
        self.assertEqual(
            [preview.activity_label for preview in previews], ["Manufacturing"]
        )
