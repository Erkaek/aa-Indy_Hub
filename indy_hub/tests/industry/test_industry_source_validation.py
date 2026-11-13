"""Reject incomplete/unknown installed sources without changing the CCP oracle."""

# Standard Library
import copy
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

# AA Example App
from indy_hub.models import IndustryStructure
from indy_hub.services import industry_rules
from indy_hub.services import industry_structures as resolver
from indy_hub.services.industry_rules import (
    IndustryRulesError,
    get_industry_source_metadata,
)


class IndustrySourceValidationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures = Path(__file__).with_name("fixtures") / "ccp_industry"
        cls.dogma = {
            row["_key"]: row
            for row in map(
                json.loads, (fixtures / "typeDogma.jsonl").read_text().splitlines()
            )
        }
        cls.effects = {
            row["_key"]: row
            for row in map(
                json.loads, (fixtures / "dogmaEffects.jsonl").read_text().splitlines()
            )
        }
        cls.rig_groups = {
            row["type_id"]: row["group_id"]
            for row in json.loads((fixtures / "oracle.json").read_text())["rigs"]
        }

    def setUp(self):
        self.qualification = patch.object(
            resolver,
            "get_industry_sde_qualification",
            return_value={
                "status": "compatible_qualified",
                "build_numbers": [3670638],
                "reason": "Official fixture",
            },
        )
        self.qualification.start()
        self.addCleanup(self.qualification.stop)

    def snapshot(self, type_id=37181):
        row = self.dogma[type_id]
        ids = tuple(effect["effectID"] for effect in row["dogmaEffects"])
        return resolver.SDETypeSnapshot(
            type_id=type_id,
            name=str(type_id),
            group_id=self.rig_groups[type_id],
            dogma_attributes={
                item["attributeID"]: Decimal(str(item["value"]))
                for item in row["dogmaAttributes"]
            },
            dogma_effect_names=tuple(
                self.effects[effect_id]["name"] for effect_id in ids
            ),
            dogma_effect_ids=ids,
        )

    def resolve(self, snapshot):
        with patch.object(resolver, "get_type_snapshot", return_value=snapshot):
            return resolver.resolve_rig_type_bonuses(
                snapshot.type_id, security_band="nullsec"
            )

    def test_effect_identity_does_not_depend_on_the_historical_english_name(self):
        self.assertTrue(
            resolver._is_industry_bonus_effect("futureCCPProtocol", effect_id=6840)
        )
        self.assertFalse(resolver._is_industry_bonus_effect("rigSlot", effect_id=2663))
        original = self.snapshot()
        names = list(original.dogma_effect_names)
        names[original.dogma_effect_ids.index(6840)] = "futureCCPProtocol"
        with self.assertRaisesMessage(ValidationError, "Unknown industry effect"):
            self.resolve(replace(original, dogma_effect_names=tuple(names)))

    def test_unknown_effects_fail_even_when_their_name_has_no_industry_keyword(self):
        original = self.snapshot()
        for effect_name in ("rigFutureManufactureMaterialBonus", "futureCCPProtocol"):
            with self.subTest(effect=effect_name):
                mutated = replace(
                    original,
                    dogma_effect_names=original.dogma_effect_names + (effect_name,),
                    dogma_effect_ids=original.dogma_effect_ids + (999999,),
                )
                with self.assertRaisesMessage(
                    ValidationError, "Unknown industry effect"
                ):
                    self.resolve(mutated)

    def test_known_name_cannot_hide_a_changed_id_or_incomplete_source_effect_set(self):
        original = self.snapshot()
        for effect_ids, effect_names in (
            (
                tuple(
                    999999 if value == 6840 else value
                    for value in original.dogma_effect_ids
                ),
                original.dogma_effect_names,
            ),
            (original.dogma_effect_ids[1:], original.dogma_effect_names[1:]),
        ):
            with self.subTest(ids=effect_ids):
                with self.assertRaisesMessage(
                    ValidationError, "CCP Dogma effect set differs"
                ):
                    self.resolve(
                        replace(
                            original,
                            dogma_effect_ids=effect_ids,
                            dogma_effect_names=effect_names,
                        )
                    )

    def test_unknown_source_with_only_nonindustrial_effect_is_still_unqualified(self):
        original = self.snapshot()
        mutated = replace(
            original,
            type_id=999999,
            dogma_effect_names=("rigSlot",),
            dogma_effect_ids=(2663,),
        )
        with self.assertRaisesMessage(ValidationError, "Unknown industry source"):
            self.resolve(mutated)

    def test_official_nonindustrial_source_legitimately_has_no_industry_bonus(self):
        # This source is deliberately outside the 125 industrial-rig matrix.
        metadata = get_industry_source_metadata(46640)
        self.assertFalse(metadata["has_industry_source"])
        pairs = list(metadata["effects"].items())
        snapshot = resolver.SDETypeSnapshot(
            type_id=46640,
            name="Standup L-Set Reprocessing Monitor II",
            group_id=metadata["group_id"],
            dogma_attributes={
                int(key): Decimal(str(value))
                for key, value in {
                    **metadata["attribute_values"],
                    **metadata["fitting_attribute_values"],
                }.items()
            },
            dogma_effect_ids=tuple(int(key) for key, _ in pairs),
            dogma_effect_names=tuple(value["name"] for _, value in pairs),
        )
        self.assertEqual(self.resolve(snapshot), [])

    def test_missing_or_wrong_domain_attributes_are_configuration_errors(self):
        original = self.snapshot()
        for attribute_id, value in (
            (2594, None),
            (2594, Decimal("2.4")),
            (2594, Decimal("NaN")),
            (2594, Decimal("-101")),
            (2357, Decimal("0")),
            (2356, Decimal("Infinity")),
        ):
            with self.subTest(attribute=attribute_id, value=value):
                attrs = dict(original.dogma_attributes)
                if value is None:
                    del attrs[attribute_id]
                else:
                    attrs[attribute_id] = value
                with self.assertRaises(ValidationError):
                    self.resolve(replace(original, dogma_attributes=attrs))

    def test_unqualified_build_does_not_produce_a_reliable_source_result(self):
        with patch.object(
            resolver,
            "get_industry_sde_qualification",
            return_value={
                "status": "unqualified",
                "build_numbers": [9999999],
                "reason": "Qualify its archive offline.",
            },
        ):
            with self.assertRaisesMessage(
                ValidationError, "Qualify its archive offline"
            ):
                self.resolve(self.snapshot())

    def test_real_source_without_build_metadata_is_not_qualified(self):
        with patch.object(
            resolver,
            "get_industry_sde_qualification",
            return_value={
                "status": "unqualified",
                "build_numbers": [],
                "reason": "Missing SDE build metadata.",
            },
        ):
            with self.assertRaisesMessage(
                ValidationError, "Missing SDE build metadata"
            ):
                self.resolve(self.snapshot())

    def test_attested_conditional_values_use_the_installed_dogma_authority(self):
        original = self.snapshot()
        attrs = dict(original.dogma_attributes)
        attrs[2594] = Decimal("-2.3")
        expected = dict(
            get_industry_source_metadata(original.type_id)["attribute_values"]
        )
        expected["2594"] = -2.3
        with (
            patch.object(
                resolver,
                "get_industry_sde_qualification",
                return_value={
                    "status": "compatible_conditional",
                    "build_numbers": [3693311],
                    "attribute_build_numbers": [3693311],
                    "reason": "Verified numerical change.",
                },
            ),
            patch.object(
                resolver, "get_industry_source_attribute_values", return_value=expected
            ) as attested_values,
        ):
            rows = self.resolve(replace(original, dogma_attributes=attrs))
        self.assertEqual(
            next(
                row.material_efficiency_percent
                for row in rows
                if row.material_efficiency_percent
            ),
            Decimal("4.83"),
        )
        attested_values.assert_called_once_with(
            original.type_id, build_numbers=[3693311]
        )

    def test_changed_supported_value_is_not_silently_called_qualified(self):
        original = self.snapshot()
        for attribute_id, value in ((2594, "-2.3"), (2357, "1.9")):
            with self.subTest(attribute=attribute_id):
                attrs = dict(original.dogma_attributes)
                attrs[attribute_id] = Decimal(value)
                with self.assertRaisesMessage(
                    ValidationError, "differs from the qualified"
                ):
                    self.resolve(replace(original, dogma_attributes=attrs))

    def test_navigation_reload_changes_freshness_without_unqualifying_rules(self):
        rows = [
            (section, 3670638, "fixture")
            for section in ("TypeDogma", "TypeEffect", "DogmaEffect", "DogmaAttribute")
        ] + [("SolarSystem", 99999999, "first"), ("Stargate", 99999999, "fixture")]
        # Exercise the real qualification function, rather than the per-source
        # test patch used by mutation tests above.
        self.qualification.stop()
        with patch.object(resolver, "_get_industry_sde_sections", return_value=rows):
            self.assertEqual(
                resolver.get_industry_sde_qualification()["status"],
                "compatible_qualified",
            )
            before = resolver.get_industry_calculation_cache_token()
            rows[4] = ("SolarSystem", 99999999, "second")
            after = resolver.get_industry_calculation_cache_token()
        self.assertNotEqual(before, after)

    def test_missing_attribute_definition_build_is_explicitly_unqualified(self):
        rows = [
            (section, 3670638, "fixture")
            for section in ("TypeDogma", "TypeEffect", "DogmaEffect")
        ]
        self.qualification.stop()
        with patch.object(resolver, "_get_industry_sde_sections", return_value=rows):
            result = resolver.get_industry_sde_qualification()
        self.assertEqual(result["status"], "unqualified")
        self.assertIn("DogmaAttribute", result["reason"])

    def test_valid_renamed_effect_bundle_reports_missing_family_binding(self):
        # AA Example App
        from indy_hub.services.craft_structures import _service_category_for_item

        data = copy.deepcopy(industry_rules._DATA)
        old_name = "rigReactionHybMatBonus"
        new_name = "futureOfficialHybridBonus"
        for rules in (data["rules"], *data["rig_rules"].values()):
            record = rules.pop(old_name.casefold(), None)
            if record is not None:
                record["effect_name"] = new_name
                rules[new_name.casefold()] = record
        for effects in (
            data["industrial_effects"],
            *(source["effects"] for source in data["sources"].values()),
        ):
            for effect in effects.values():
                if effect["name"] == old_name:
                    effect["name"] = new_name
        evidence = industry_rules.industry_projection_evidence(data)
        data["qualifications"] = {
            build: copy.deepcopy(evidence) for build in data["qualifications"]
        }
        content = {
            key: value
            for key, value in data.items()
            if key not in ("rules_version", "provenance")
        }
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        data["rules_version"] = f"ccp-{data['sde_build']}-{digest[:12]}"
        industry_rules.validate_industry_bundle(data)
        with (
            patch.object(industry_rules, "_DATA", data),
            patch.object(industry_rules, "_VALIDATED", False),
        ):
            with self.assertRaisesMessage(
                ValidationError, "Missing CCP reaction family binding"
            ):
                _service_category_for_item(9, "Hybrid Polymers", group_id=974)
            self.assertEqual(
                _service_category_for_item(
                    9, "Molecular-Forged Materials", group_id=4096
                ),
                "biochemical_reactions",
            )

    def test_missing_capital_binding_is_an_actionable_configuration_error(self):
        # AA Example App
        from indy_hub.services.craft_structures import _service_category_for_item

        with patch(
            "indy_hub.services.craft_structures.get_industry_rule", return_value=None
        ):
            with self.assertRaisesMessage(
                ValidationError, "Missing CCP capital manufacturing"
            ):
                _service_category_for_item(1, "Dreadnought", group_id=485)
            self.assertEqual(
                _service_category_for_item(1, "Titan", group_id=30),
                "manufacturing_super_capitals",
            )

    def test_bad_bundle_has_nonraising_availability_token_for_independent_project_work(
        self,
    ):
        error = IndustryRulesError("unknown semantic filter", path="filters.1")
        with (
            patch.object(resolver, "_get_industry_sde_sections", return_value=[]),
            patch.object(
                resolver, "get_industry_build_qualification", side_effect=error
            ),
        ):
            self.assertIn(
                ":unavailable-", resolver.get_industry_calculation_cache_token()
            )

    def test_invalid_bundle_cannot_reuse_a_persistent_bonus_cache(self):
        error = IndustryRulesError("unknown semantic filter", path="filters.1")
        with patch(
            "indy_hub.services.industry_rules.get_industry_source_metadata",
            side_effect=error,
        ):
            with self.assertRaisesMessage(ValidationError, "unknown semantic filter"):
                IndustryStructure.get_resolved_bonuses(
                    SimpleNamespace(structure_type_id=35825)
                )

    def test_hull_attributes_must_exist_and_use_the_official_multiplier_convention(
        self,
    ):
        original = resolver.SDETypeSnapshot(
            type_id=35825,
            name="Raitaru",
            dogma_effect_names=(),
            dogma_attributes={
                2600: Decimal("0.99"),
                2601: Decimal("0.97"),
                2602: Decimal("0.85"),
            },
        )
        for value in (
            None,
            Decimal("0"),
            Decimal("1.01"),
            Decimal("NaN"),
            Decimal("Infinity"),
        ):
            with self.subTest(value=value):
                attrs = dict(original.dogma_attributes)
                if value is None:
                    del attrs[2602]
                else:
                    attrs[2602] = value
                with patch.object(
                    resolver,
                    "get_type_snapshot",
                    return_value=replace(original, dogma_attributes=attrs),
                ):
                    with self.assertRaises(ValidationError):
                        resolver.resolve_structure_type_bonuses(35825)
