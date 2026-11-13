"""Tests for dogma-backed industry structure bonus and tax calculations."""

# Standard Library
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# Django
from django.core.exceptions import ValidationError
from django.test import TestCase

# AA Example App
from indy_hub.models import (
    IndustryActivityMixin,
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
    round_security_status,
)


class IndustryStructureCalculationTests(TestCase):
    def test_round_security_status_applies_special_positive_floor(self) -> None:
        self.assertEqual(round_security_status(Decimal("0")), Decimal("0.0"))
        self.assertEqual(round_security_status(Decimal("0.000001")), Decimal("0.1"))
        self.assertEqual(round_security_status(Decimal("0.049999")), Decimal("0.1"))
        self.assertEqual(round_security_status(Decimal("0.05")), Decimal("0.1"))
        self.assertEqual(round_security_status(Decimal("0.44")), Decimal("0.4"))
        self.assertEqual(round_security_status(Decimal("0.45")), Decimal("0.5"))

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
                    2355: Decimal("1"),
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
                    2355: Decimal("1"),
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

    @patch(
        "indy_hub.services.industry_structures.resolve_structure_type_bonuses",
        return_value=[],
    )
    def test_copying_installation_cost_uses_jcb_based_tax_and_scc(
        self, hull_bonuses
    ) -> None:
        self.structure.research_tax_percent = Decimal("2.500")
        self.structure.save(update_fields=["research_tax_percent"])
        IndustrySystemCostIndex.objects.create(
            solar_system_id=self.structure.solar_system_id,
            solar_system_name=self.structure.solar_system_name,
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
            cost_index_percent=Decimal("5.00000"),
        )

        breakdown = calculate_installation_cost(
            structure=self.structure,
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
            estimated_item_value=Decimal("50000"),
        )

        self.assertEqual(breakdown.base_job_cost, Decimal("50"))
        self.assertEqual(breakdown.adjusted_job_cost, Decimal("50"))
        self.assertEqual(breakdown.facility_tax, Decimal("25"))
        self.assertEqual(breakdown.scc_surcharge, Decimal("40"))
        self.assertEqual(breakdown.total_installation_cost, Decimal("115"))

    @patch(
        "indy_hub.services.industry_structures.resolve_structure_type_bonuses",
        return_value=[],
    )
    def test_copying_installation_cost_exposes_alpha_clone_tax_without_total_impact(
        self, hull_bonuses
    ) -> None:
        # Alpha clone tax is fixed at 0.25% of the job cost base. The breakdown
        # always exposes the would-be charge so that callers can decide whether
        # to apply it (per the producer's clone state) without recomputing.
        self.structure.research_tax_percent = Decimal("2.500")
        self.structure.save(update_fields=["research_tax_percent"])
        IndustrySystemCostIndex.objects.create(
            solar_system_id=self.structure.solar_system_id,
            solar_system_name=self.structure.solar_system_name,
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
            cost_index_percent=Decimal("5.00000"),
        )

        breakdown = calculate_installation_cost(
            structure=self.structure,
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
            estimated_item_value=Decimal("50000"),
        )

        self.assertEqual(breakdown.alpha_clone_tax_percent, Decimal("0.25"))
        # 0.25% of job cost base (1000) rounds to 3 ISK.
        self.assertEqual(breakdown.alpha_clone_tax, Decimal("3"))
        # Total stays unchanged: callers add the alpha tax themselves.
        self.assertEqual(breakdown.total_installation_cost, Decimal("115"))

    def _resolve_rig(self, type_id, effects, *, attributes=None, security="nullsec"):
        # AA Example App
        from indy_hub.services.industry_rules import (
            get_industry_rig_security_attribute_ids,
            get_industry_rule,
            get_industry_rules_for_rig,
        )

        if type_id == 999999 and len(effects) == 1:
            rule = get_industry_rule(effects[0])
            if rule is not None:
                type_id = {
                    "rigAdvSmshipManufactureMaterialBonus": 43854,
                    "rigAdvSmshipManufactureTimeBonus": 43856,
                    "rigSmallshipManufactureMaterialBonus": 37154,
                    "rigMediumshipManufactureMaterialBonus": 37146,
                    "rigMediumshipsManufactureTimeBonus": 37153,
                    "rigAdvMedShipManufactureMaterialBonus": 43859,
                    "rigLargeshipManufactureMaterialBonus": 37152,
                    "rigAdvLarShipManufactureMaterialBonus": 43862,
                    "rigAdvLarShipManufactureTimeBonus": 43864,
                    "rigCapShipManufactureMaterialBonus": 37172,
                    "rigAllShipManufactureMaterialBonus": 37180,
                    "rigEquipmentManufactureMaterialBonus": 37170,
                    "rigDroneManufactureMaterialBonus": 37156,
                    "rigStructureManufactureMaterialBonus": 43704,
                    "rigBasCapCompManufactureMaterialBonus": 43704,
                    "rigAdvComponentManufactureMaterialBonus": 37174,
                    "rigAdvCapComponentManufactureMaterialBonus": 37174,
                    "rigReactionHybMatBonus": 46490,
                    "rigReactionBioMatBonus": 46494,
                    "rigReactionCompMatBonus": 46486,
                    "rigThukkerBasCapCompManufactureMaterialBonus": 45544,
                    "rigThukkerAdvCapCompManufactureMaterialBonus": 45640,
                }[rule.effect_name]
        requested_effects = {
            rule.effect_name
            for effect in effects
            for rule in [get_industry_rule(effect, rig_type_id=type_id)]
            if rule is not None
        }
        # Individual filter tests inspect selected effects, but the SDE source
        # still needs the rig's complete verified effect set.
        complete_effects = dict.fromkeys(
            [
                *effects,
                *(rule.effect_name for rule in get_industry_rules_for_rig(type_id)),
            ]
        )
        resolved_attributes = dict(
            attributes
            if attributes is not None
            else {
                2355: Decimal("1"),
                2356: Decimal("1.9"),
                2357: Decimal("2.1"),
                2593: Decimal("-20"),
                2594: Decimal("-2.4"),
                2595: Decimal("-10"),
                2653: Decimal("-3.7"),
                2713: Decimal("-20"),
                2714: Decimal("-2.4"),
            }
        )
        for attribute_id in get_industry_rig_security_attribute_ids(type_id):
            # These filter tests vary a selected modifier, not security data.
            resolved_attributes.setdefault(attribute_id, Decimal("1"))
        snapshot = SDETypeSnapshot(
            type_id=type_id,
            name=f"Rig {type_id}",
            dogma_attributes=resolved_attributes,
            dogma_effect_names=tuple(complete_effects),
        )
        with patch(
            "indy_hub.services.industry_structures.get_type_snapshot",
            return_value=snapshot,
        ):
            return [
                row
                for row in resolve_rig_type_bonuses(type_id, security_band=security)
                if row.dogma_effect_name in requested_effects
            ]

    def test_actual_eight_missing_dogma_effects_resolve_with_provenance(self):
        effects = {
            "rigAdvSmshipManufactureMaterialBonus": (6, 2594),
            "rigAdvSmshipManufactureTimeBonus": (6, 2593),
            "rigMediumshipManufactureMaterialBonus": (7, 2594),
            "rigMediumshipsManufactureTimeBonus": (7, 2593),
            "rigAdvLarShipManufactureMaterialBonus": (10, 2594),
            "rigAdvLarShipManufactureTimeBonus": (10, 2593),
            "rigThukkerBasCapCompManufactureMaterialBonus": (13, 2653),
            "rigThukkerAdvCapCompManufactureMaterialBonus": (15, 2653),
        }
        for effect, (filter_id, attribute_id) in effects.items():
            with self.subTest(effect=effect):
                (bonus,) = self._resolve_rig(999999, [effect])
                self.assertEqual(bonus.target_filter_id, filter_id)
                self.assertEqual(bonus.modifying_attribute_id, attribute_id)
                self.assertEqual(bonus.dogma_effect_name, effect)
                self.assertEqual(bonus.activity_id, 1)

    def test_historical_aliases_keep_current_numeric_targets(self):
        for historical, actual in (
            (
                "rigAdvSmallshipManufactureMaterialBonus",
                "rigAdvSmshipManufactureMaterialBonus",
            ),
            (
                "rigAdvLargeshipManufactureMaterialBonus",
                "rigAdvLarShipManufactureMaterialBonus",
            ),
            ("rigMedShipManufactureTimeBonus", "rigMediumshipsManufactureTimeBonus"),
        ):
            with self.subTest(effect=historical):
                self.assertEqual(
                    self._resolve_rig(999999, [historical]),
                    self._resolve_rig(999999, [actual]),
                )

    def test_numeric_ship_filters_cover_positive_negative_and_overlapping_targets(self):
        # AA Example App
        from indy_hub.services.craft_structures import _item_supported_by_bonus

        cases = (
            ("rigSmallshipManufactureMaterialBonus", 25, 6, True),
            ("rigSmallshipManufactureMaterialBonus", 1305, 6, False),
            ("rigAdvSmshipManufactureMaterialBonus", 1305, 6, True),
            ("rigAdvSmshipManufactureMaterialBonus", 1534, 6, True),
            ("rigAdvSmshipManufactureMaterialBonus", 25, 6, False),
            ("rigMediumshipManufactureMaterialBonus", 26, 6, True),
            ("rigMediumshipManufactureMaterialBonus", 4902, 6, True),
            ("rigMediumshipManufactureMaterialBonus", 963, 6, False),
            ("rigAdvMedShipManufactureMaterialBonus", 26, 6, False),
            ("rigAdvMedShipManufactureMaterialBonus", 1527, 6, False),
            ("rigAdvMedShipManufactureMaterialBonus", 1202, 6, True),
            ("rigAdvMedShipManufactureMaterialBonus", 380, 6, True),
            ("rigAdvMedShipManufactureMaterialBonus", 543, 6, True),
            ("rigAdvMedShipManufactureMaterialBonus", 4902, 6, True),
            ("rigAdvMedShipManufactureMaterialBonus", 954, 32, True),
            ("rigLargeshipManufactureMaterialBonus", 513, 6, True),
            ("rigLargeshipManufactureMaterialBonus", 463, 6, False),
            ("rigLargeshipManufactureMaterialBonus", 900, 6, False),
            ("rigAdvLarShipManufactureMaterialBonus", 902, 6, True),
            ("rigAdvLarShipManufactureMaterialBonus", 27, 6, False),
            ("rigCapShipManufactureMaterialBonus", 485, 6, True),
            ("rigCapShipManufactureMaterialBonus", 5120, 6, True),
            ("rigCapShipManufactureMaterialBonus", 513, 6, False),
            ("rigAllShipManufactureMaterialBonus", 954, 32, True),
            ("rigAllShipManufactureMaterialBonus", 25, 6, True),
            ("rigAllShipManufactureMaterialBonus", 25, 7, False),
        )
        for effect, group_id, category_id, expected in cases:
            with self.subTest(effect=effect, group=group_id, category=category_id):
                (bonus,) = self._resolve_rig(999999, [effect])
                self.assertEqual(
                    _item_supported_by_bonus(
                        bonus,
                        {"misleading english name"},
                        group_id=group_id,
                        category_id=category_id,
                    ),
                    expected,
                )

    def test_numeric_equipment_drone_structure_and_component_filters(self):
        # AA Example App
        from indy_hub.services.craft_structures import _item_supported_by_bonus

        cases = (
            ("rigEquipmentManufactureMaterialBonus", 12, 2, True),
            ("rigEquipmentManufactureMaterialBonus", 340, 2, True),
            ("rigEquipmentManufactureMaterialBonus", 448, 2, True),
            ("rigEquipmentManufactureMaterialBonus", 649, 2, True),
            ("rigEquipmentManufactureMaterialBonus", 999, 2, False),
            ("rigEquipmentManufactureMaterialBonus", 999, 7, True),
            ("rigEquipmentManufactureMaterialBonus", 999, 20, True),
            ("rigEquipmentManufactureMaterialBonus", 999, 22, True),
            ("rigEquipmentManufactureMaterialBonus", 954, 32, False),
            ("rigDroneManufactureMaterialBonus", 549, 18, True),
            ("rigDroneManufactureMaterialBonus", 1652, 87, True),
            ("rigDroneManufactureMaterialBonus", 1652, 2, False),
            ("rigStructureManufactureMaterialBonus", 999, 23, True),
            ("rigStructureManufactureMaterialBonus", 999, 39, True),
            ("rigStructureManufactureMaterialBonus", 999, 40, True),
            ("rigStructureManufactureMaterialBonus", 999, 65, True),
            ("rigStructureManufactureMaterialBonus", 999, 66, True),
            ("rigStructureManufactureMaterialBonus", 536, 17, True),
            ("rigStructureManufactureMaterialBonus", 1136, 4, True),
            ("rigStructureManufactureMaterialBonus", 4736, 2, True),
            ("rigStructureManufactureMaterialBonus", 999, 4, False),
            ("rigBasCapCompManufactureMaterialBonus", 873, 17, True),
            ("rigBasCapCompManufactureMaterialBonus", 334, 17, False),
            ("rigAdvComponentManufactureMaterialBonus", 334, 17, True),
            ("rigAdvComponentManufactureMaterialBonus", 873, 17, False),
            ("rigAdvCapComponentManufactureMaterialBonus", 913, 17, True),
            ("rigAdvCapComponentManufactureMaterialBonus", 873, 17, False),
        )
        for effect, group_id, category_id, expected in cases:
            with self.subTest(effect=effect, group=group_id, category=category_id):
                (bonus,) = self._resolve_rig(999999, [effect])
                self.assertEqual(
                    _item_supported_by_bonus(
                        bonus,
                        {"Structure", "Subsystem", "Fighter"},
                        group_id=group_id,
                        category_id=category_id,
                    ),
                    expected,
                )

    def test_molecular_forged_uses_biochemical_reaction_filter(self):
        # AA Example App
        from indy_hub.services.craft_structures import _item_supported_by_bonus

        for effect, positive, negative in (
            ("rigReactionHybMatBonus", (974,), (712, 4096, 428, 429, 4932)),
            ("rigReactionBioMatBonus", (712, 4096), (974, 428, 429, 4932)),
            ("rigReactionCompMatBonus", (428, 429, 4932), (974, 712, 4096)),
        ):
            bonuses = self._resolve_rig(
                999999,
                [effect],
                attributes={2714: Decimal("-2.4"), 2357: Decimal("1.1")},
            )
            bonus = next(row for row in bonuses if row.activity_id == 9)
            self.assertEqual(bonus.material_efficiency_percent, Decimal("2.64"))
            for group_id in positive + negative:
                with self.subTest(effect=effect, group=group_id):
                    self.assertEqual(
                        _item_supported_by_bonus(
                            bonus, {"Hybrid Polymers"}, group_id=group_id, category_id=4
                        ),
                        group_id in positive,
                    )
        self.assertEqual(
            _supported_type_names_for_effect("rigPolymerReactionTimeBonus"),
            ("Hybrid Polymers",),
        )

    def test_thukker_selects_effect_attribute_and_each_security_multiplier(self):
        # AA Example App
        from indy_hub.services.craft_structures import _item_supported_by_bonus

        attrs = {
            2653: Decimal("-3.7"),
            2594: Decimal("-2"),
            2593: Decimal("-20"),
            2355: Decimal("0.1"),
            2356: Decimal("1.9"),
            2357: Decimal("0.1"),
        }
        effects = (
            "rigThukkerBasCapCompManufactureMaterialBonus",
            "rigThukkerAdvCapCompManufactureMaterialBonus",
            "rigAdvComponentManufactureMaterialBonus",
            "rigAdvComponentManufactureTimeBonus",
        )
        for security, enhanced, ordinary, time in (
            ("highsec", "0.37", "0.2", "2"),
            ("lowsec", "7.03", "3.8", "38"),
            ("nullsec", "0.37", "0.2", "2"),
        ):
            with self.subTest(security=security):
                (bonus,) = self._resolve_rig(
                    45544, effects[:1], attributes=attrs, security=security
                )
                self.assertEqual(bonus.material_efficiency_percent, Decimal(enhanced))
                for rig_id in (45548, 45640):
                    rows = self._resolve_rig(
                        rig_id,
                        effects[1:] if rig_id == 45548 else effects[1:3],
                        attributes=attrs,
                        security=security,
                    )
                    enhanced_row = next(
                        row for row in rows if row.target_filter_id == 15
                    )
                    ordinary_me = next(
                        row
                        for row in rows
                        if row.material_efficiency_percent
                        and row.target_filter_id == 14
                    )
                    self.assertEqual(
                        enhanced_row.material_efficiency_percent, Decimal(enhanced)
                    )
                    self.assertEqual(
                        ordinary_me.material_efficiency_percent, Decimal(ordinary)
                    )
                    if rig_id == 45548:
                        ordinary_te = next(
                            row for row in rows if row.time_efficiency_percent
                        )
                        self.assertEqual(
                            ordinary_te.time_efficiency_percent, Decimal(time)
                        )
                    self.assertFalse(
                        _item_supported_by_bonus(
                            enhanced_row, set(), group_id=334, category_id=17
                        )
                    )

    def test_xl_component_rigs_do_not_multiply_disjoint_filters(self):
        # AA Example App
        from indy_hub.services.craft_structures import (
            _combine_bonus_percentages,
            _item_supported_by_bonus,
        )

        effects = tuple(
            f"rig{family}Manufacture{metric}Bonus"
            for family in ("AdvComponent", "BasCapComp", "AdvCapComponent", "Structure")
            for metric in ("Material", "Time")
        )
        for rig_id, base_me, base_te, expected_me, expected_te in (
            (43704, "-2", "-20", "4.2", "42"),
            (43705, "-2.4", "-24", "5.04", "50.4"),
        ):
            with self.subTest(rig=rig_id):
                rows = self._resolve_rig(
                    rig_id,
                    effects,
                    attributes={
                        2594: Decimal(base_me),
                        2593: Decimal(base_te),
                        2357: Decimal("2.1"),
                    },
                )
                matching = [
                    row
                    for row in rows
                    if _item_supported_by_bonus(
                        row, set(), group_id=334, category_id=17
                    )
                ]
                self.assertEqual(len(matching), 2)
                self.assertEqual(
                    _combine_bonus_percentages(matching, "material_efficiency_percent"),
                    Decimal(expected_me),
                )
                self.assertEqual(
                    _combine_bonus_percentages(matching, "time_efficiency_percent"),
                    Decimal(expected_te),
                )

    def test_confessor_hull_and_rigs_keep_multiplicative_aggregation(self):
        # AA Example App
        from indy_hub.services.craft_structures import (
            _combine_bonus_percentages,
            _item_supported_by_bonus,
        )
        from indy_hub.services.industry_structures import IndustryStructureResolvedBonus

        rows = self._resolve_rig(43854, ["rigAdvSmshipManufactureMaterialBonus"])
        rows += self._resolve_rig(43856, ["rigAdvSmshipManufactureTimeBonus"])
        matching = [
            row
            for row in rows
            if _item_supported_by_bonus(row, set(), group_id=1305, category_id=6)
        ]
        matching.append(
            IndustryStructureResolvedBonus(
                source="structure",
                label="Raitaru",
                activity_id=1,
                material_efficiency_percent=Decimal("1"),
                time_efficiency_percent=Decimal("15"),
            )
        )
        self.assertEqual(
            _combine_bonus_percentages(matching, "material_efficiency_percent"),
            Decimal("5.9896"),
        )
        self.assertEqual(
            _combine_bonus_percentages(matching, "time_efficiency_percent"),
            Decimal("50.70"),
        )
        self.assertFalse(
            any(
                _item_supported_by_bonus(row, set(), group_id=25, category_id=6)
                for row in rows
            )
        )

    def test_unknown_industry_effect_and_missing_attribute_fail_explicitly(self):
        with self.assertRaisesMessage(ValidationError, "Unknown industry effect"):
            self._resolve_rig(999999, ["rigFutureManufactureMaterialBonus"])
        with self.assertRaisesMessage(ValidationError, "2653"):
            self._resolve_rig(
                45544,
                ["rigThukkerBasCapCompManufactureMaterialBonus"],
                attributes={2594: Decimal("-2")},
            )
        self.assertEqual(
            self._resolve_rig(46640, []),
            [],
        )

    @patch("indy_hub.services.industry_structures.get_type_snapshot", return_value=None)
    def test_missing_selected_rig_does_not_masquerade_as_zero_bonus(self, snapshot):
        with self.assertRaisesMessage(ValidationError, "Cannot resolve selected rig"):
            resolve_rig_type_bonuses(999999, security_band="nullsec")
        self.assertEqual(resolve_rig_type_bonuses(None, security_band="nullsec"), [])

    def test_all_official_security_attributes_are_required_without_inventing_defaults(
        self,
    ):
        # AA Example App
        from indy_hub.services.industry_rules import (
            get_industry_rig_security_attribute_ids,
        )

        fixtures = Path(__file__).with_name("fixtures") / "ccp_industry"
        dogma = {
            row["_key"]: row
            for row in map(
                json.loads, (fixtures / "typeDogma.jsonl").read_text().splitlines()
            )
        }
        effects = {
            row["_key"]: row
            for row in map(
                json.loads, (fixtures / "dogmaEffects.jsonl").read_text().splitlines()
            )
        }
        for rig_id, source in dogma.items():
            attributes = {
                row["attributeID"]: Decimal(str(row["value"]))
                for row in source["dogmaAttributes"]
            }
            required = tuple(sorted(set(attributes) & {2355, 2356, 2357}))
            self.assertEqual(get_industry_rig_security_attribute_ids(rig_id), required)
            effect_names = tuple(
                effects[row["effectID"]]["name"] for row in source["dogmaEffects"]
            )
            complete = SDETypeSnapshot(
                type_id=rig_id,
                name=f"Official rig {rig_id}",
                dogma_attributes=attributes,
                dogma_effect_names=effect_names,
            )
            with patch(
                "indy_hub.services.industry_structures.get_type_snapshot",
                return_value=complete,
            ):
                # In particular, official reaction rigs omit highsec attribute
                # 2355. The mathematical highsec resolver still defaults to one.
                resolve_rig_type_bonuses(rig_id, security_band="highsec")
            for missing_id in required:
                with self.subTest(rig_id=rig_id, missing_id=missing_id):
                    incomplete = SDETypeSnapshot(
                        type_id=rig_id,
                        name=complete.name,
                        dogma_attributes={
                            key: value
                            for key, value in attributes.items()
                            if key != missing_id
                        },
                        dogma_effect_names=effect_names,
                    )
                    with patch(
                        "indy_hub.services.industry_structures.get_type_snapshot",
                        return_value=incomplete,
                    ):
                        with self.assertRaisesMessage(ValidationError, str(missing_id)):
                            resolve_rig_type_bonuses(rig_id, security_band="highsec")

    def test_null_dogma_values_are_unavailable_while_zero_remains_valid(self):
        # Alliance Auth (External Libs)
        from eve_sde.models import (
            DogmaAttribute,
            DogmaEffect,
            EveSDESection,
            ItemGroup,
            ItemType,
            TypeDogma,
            TypeEffect,
        )

        # AA Example App
        from indy_hub.services.industry_structures import (
            _rig_percent_for_field,
            clear_industry_sde_caches,
            get_type_snapshot,
        )

        group = ItemGroup.objects.create(id=1839, name="Structure rig")
        rig = ItemType.objects.create(
            id=45544, name="Thukker rig", published=True, group=group
        )
        for section_name in (
            "TypeDogma",
            "TypeEffect",
            "DogmaEffect",
            "DogmaAttribute",
        ):
            # Django
            from django.utils import timezone

            EveSDESection.objects.create(
                sde_section=section_name,
                build_number=3670638,
                last_update=timezone.now(),
                total_lines=1,
                total_rows=1,
            )
        effect = DogmaEffect.objects.create(
            id=6887, name="rigThukkerBasCapCompManufactureMaterialBonus"
        )
        TypeEffect.objects.create(item_type=rig, dogma_effect=effect, is_default=False)
        for effect_id, name in (
            (2663, "rigSlot"),
            (6842, "structureEngineeringRigSecurityModification"),
        ):
            passive = DogmaEffect.objects.create(id=effect_id, name=name)
            TypeEffect.objects.create(item_type=rig, dogma_effect=passive)
        values = {
            2653: Decimal("-3.7"),
            2355: Decimal("0.1"),
            2356: Decimal("1.9"),
            2357: Decimal("0.1"),
            1298: Decimal("1657"),
            1299: Decimal("1404"),
            1300: Decimal("1406"),
            1547: Decimal("2"),
        }
        for attribute_id, value in values.items():
            attribute = DogmaAttribute.objects.create(id=attribute_id)
            TypeDogma.objects.create(
                item_type=rig, dogma_attribute=attribute, value=value
            )
        for unavailable_id in (2355, 2653):
            with self.subTest(unavailable_id=unavailable_id):
                TypeDogma.objects.filter(
                    item_type=rig, dogma_attribute_id=unavailable_id
                ).update(value=None)
                clear_industry_sde_caches()
                self.assertNotIn(
                    unavailable_id, get_type_snapshot(rig.id).dogma_attributes
                )
                with self.assertRaisesMessage(ValidationError, str(unavailable_id)):
                    resolve_rig_type_bonuses(rig.id, security_band="highsec")
                TypeDogma.objects.filter(
                    item_type=rig, dogma_attribute_id=unavailable_id
                ).update(value=values[unavailable_id])
        TypeDogma.objects.filter(item_type=rig, dogma_attribute_id=2653).update(value=0)
        clear_industry_sde_caches()
        snapshot = get_type_snapshot(rig.id)
        self.assertEqual(snapshot.dogma_attributes[2653], Decimal("0"))
        self.assertEqual(
            _rig_percent_for_field(
                snapshot.dogma_attributes,
                modifying_attribute_id=2653,
                security_band="highsec",
            ),
            Decimal("0"),
        )
        # Zero is a real parsed value, but changing the qualified -3.7 source
        # value locally is not a qualified CCP update.
        with self.assertRaisesMessage(ValidationError, "differs from the qualified"):
            resolve_rig_type_bonuses(rig.id, security_band="highsec")

    @patch(
        "indy_hub.services.industry_structures.resolve_structure_type_bonuses",
        return_value=[],
    )
    def test_incomplete_rig_effect_rows_cannot_write_a_current_bonus_cache(
        self, hull_bonuses
    ):
        # Alliance Auth (External Libs)
        from eve_sde.models import (
            DogmaAttribute,
            DogmaEffect,
            ItemType,
            TypeDogma,
            TypeEffect,
        )

        # AA Example App
        from indy_hub.services.industry_structures import clear_industry_sde_caches

        rig = ItemType.objects.create(id=37181, name="XL ship rig II", published=True)
        material_effect = DogmaEffect.objects.create(
            id=6840, name="rigAllShipManufactureMaterialBonus"
        )
        for attribute_id, value in ((2593, "-24"), (2594, "-2.4")):
            attribute = DogmaAttribute.objects.create(id=attribute_id)
            TypeDogma.objects.create(
                item_type=rig, dogma_attribute=attribute, value=Decimal(value)
            )
        IndustryStructureRig.objects.create(
            structure=self.structure,
            slot_index=1,
            rig_type_id=rig.id,
            rig_type_name=rig.name,
        )
        before = (
            self.structure.resolved_bonuses_cache,
            self.structure.resolved_bonuses_cache_signature,
            self.structure.resolved_bonuses_cache_updated_at,
        )
        for has_material_effect in (False, True):
            with self.subTest(has_material_effect=has_material_effect):
                if has_material_effect:
                    TypeEffect.objects.create(
                        item_type=rig, dogma_effect=material_effect, is_default=False
                    )
                clear_industry_sde_caches()
                with self.assertRaisesMessage(
                    ValidationError, "missing its CCP industry effects"
                ):
                    self.structure.get_resolved_bonuses()
                self.structure.refresh_from_db()
                self.assertEqual(
                    (
                        self.structure.resolved_bonuses_cache,
                        self.structure.resolved_bonuses_cache_signature,
                        self.structure.resolved_bonuses_cache_updated_at,
                    ),
                    before,
                )

    @patch(
        "indy_hub.services.industry_structures.resolve_structure_type_bonuses",
        return_value=[],
    )
    def test_pre_guard_v3_bonus_cache_cannot_hide_incomplete_rig_effects(
        self, hull_bonuses
    ):
        # Alliance Auth (External Libs)
        from eve_sde.models import ItemType

        # AA Example App
        from indy_hub.services.industry_structures import (
            INDUSTRY_RESOLVER_VERSION,
            IndustryStructureResolvedBonus,
            clear_industry_sde_caches,
            get_industry_calculation_cache_token,
        )

        ItemType.objects.create(id=37181, name="Incomplete XL rig", published=True)
        IndustryStructureRig.objects.create(
            structure=self.structure,
            slot_index=1,
            rig_type_id=37181,
            rig_type_name="Incomplete XL rig",
        )
        clear_industry_sde_caches()
        current_token = get_industry_calculation_cache_token()
        self.assertTrue(current_token.endswith(f":{INDUSTRY_RESOLVER_VERSION}"))
        pre_guard_token = current_token.rsplit(":", 1)[0]
        partial_bonus = IndustryStructureResolvedBonus(
            source="rig",
            label="Incomplete XL rig",
            activity_id=1,
            material_efficiency_percent=Decimal("2.4"),
            target_filter_id=3,
            target_category_ids=(6, 32),
            dogma_effect_name="rigAllShipManufactureMaterialBonus",
            modifying_attribute_id=2594,
        )
        for stale_rows in ([], [partial_bonus]):
            with self.subTest(stale_row_count=len(stale_rows)):
                self.structure.resolved_bonuses_cache_signature = ""
                with (
                    patch(
                        "indy_hub.services.industry_structures.get_industry_calculation_cache_token",
                        return_value=pre_guard_token,
                    ),
                    patch(
                        "indy_hub.services.industry_structures.resolve_structure_bonuses",
                        return_value=stale_rows,
                    ),
                ):
                    self.assertEqual(self.structure.get_resolved_bonuses(), stale_rows)
                self.structure.refresh_from_db()
                before = (
                    self.structure.resolved_bonuses_cache,
                    self.structure.resolved_bonuses_cache_signature,
                    self.structure.resolved_bonuses_cache_updated_at,
                )
                self.assertTrue(before[1].startswith(f"v3|{pre_guard_token}|"))
                with self.assertRaisesMessage(
                    ValidationError, "missing its CCP industry effects"
                ):
                    self.structure.get_resolved_bonuses()
                self.structure.refresh_from_db()
                self.assertEqual(
                    (
                        self.structure.resolved_bonuses_cache,
                        self.structure.resolved_bonuses_cache_signature,
                        self.structure.resolved_bonuses_cache_updated_at,
                    ),
                    before,
                )

    @patch("indy_hub.services.industry_structures.resolve_structure_bonuses")
    def test_bonus_cache_replaces_old_empty_result_then_reuses_numeric_targets(
        self, resolver
    ):
        # AA Example App
        from indy_hub.services.industry_structures import IndustryStructureResolvedBonus

        expected = IndustryStructureResolvedBonus(
            source="rig",
            label="Advanced Small",
            activity_id=1,
            material_efficiency_percent=Decimal("5.04"),
            target_filter_id=6,
            target_group_ids=(1305, 1534),
            dogma_effect_name="rigAdvSmshipManufactureMaterialBonus",
            modifying_attribute_id=2594,
        )
        self.structure.resolved_bonuses_cache = []
        self.structure.resolved_bonuses_cache_signature = "v2|35825|highsec|"
        resolver.return_value = [expected]
        self.assertEqual(self.structure.get_resolved_bonuses(), [expected])
        resolver.assert_called_once_with(self.structure)
        signature = self.structure.resolved_bonuses_cache_signature
        self.structure.refresh_from_db()
        self.assertEqual(self.structure.get_resolved_bonuses(), [expected])
        resolver.assert_called_once()
        self.assertTrue(signature.startswith("v3|ccp-"))
        self.assertEqual(self.structure.manufacturing_tax_percent, Decimal("0.500"))

    @patch("indy_hub.services.industry_structures.resolve_structure_bonuses")
    def test_current_empty_bonus_cache_is_valid_and_rule_change_recalculates(
        self, resolver
    ):
        resolver.return_value = []
        self.assertEqual(self.structure.get_resolved_bonuses(), [])
        self.assertEqual(self.structure.get_resolved_bonuses(), [])
        resolver.assert_called_once()
        with patch(
            "indy_hub.services.industry_structures.INDUSTRY_RULES_VERSION",
            "changed-rules",
        ):
            self.assertEqual(self.structure.get_resolved_bonuses(), [])
        self.assertEqual(resolver.call_count, 2)
        with patch(
            "indy_hub.services.industry_structures.INDUSTRY_RESOLVER_VERSION",
            "changed-resolver",
        ):
            self.assertEqual(self.structure.get_resolved_bonuses(), [])
        self.assertEqual(resolver.call_count, 3)

    def test_sde_reload_invalidates_positive_and_negative_process_caches(self):
        # Django
        from django.utils import timezone

        # Alliance Auth (External Libs)
        from eve_sde.models import EveSDESection, ItemType

        # AA Example App
        from indy_hub.services.industry_structures import (
            get_industry_calculation_cache_token,
            get_type_snapshot,
        )

        before = get_industry_calculation_cache_token()
        self.assertIsNone(get_type_snapshot(987654321))
        self.assertGreater(get_type_snapshot.cache_info().currsize, 0)
        item = ItemType.objects.create(
            id=987654321, name="Before reload", published=True
        )
        section = EveSDESection.objects.create(
            sde_section="TypeDogma",
            build_number=3693310,
            last_update=timezone.now(),
            total_lines=1,
            total_rows=1,
        )
        self.assertNotEqual(get_industry_calculation_cache_token(), before)
        self.assertEqual(get_type_snapshot.cache_info().currsize, 0)
        self.assertEqual(get_type_snapshot(item.id).name, "Before reload")
        ItemType.objects.filter(pk=item.id).update(name="After reload")
        section.last_update = timezone.now()
        section.save(update_fields=["last_update"])
        # A direct warm lookup checks the generation before returning its entry.
        self.assertEqual(get_type_snapshot(item.id).name, "After reload")

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
                    dogma_attributes={
                        2355: Decimal("1"),
                        2356: Decimal("1.9"),
                        2357: Decimal("2.1"),
                        2593: Decimal("-20.0"),
                        2594: Decimal("-2.0"),
                    },
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
                    dogma_attributes={
                        2355: Decimal("1"),
                        2356: Decimal("1.9"),
                        2357: Decimal("2.1"),
                        2593: Decimal("-20.0"),
                        2594: Decimal("-2.0"),
                    },
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
        self.assertIn("Ship", supported_type_rows)
        self.assertIn("Subsystem", supported_type_rows)
        self.assertIn("Module", supported_type_rows)
        self.assertIn("Charge", supported_type_rows)
        self.assertIn("Drone", supported_type_rows)
        self.assertEqual(
            supported_type_rows["Ship"].material_efficiency_percent,
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
