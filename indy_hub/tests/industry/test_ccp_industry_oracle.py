"""Independent offline CCP source/Dogma oracle for the industry rig resolver.

Expected values traverse the official per-type modifier-source rows and bind
their target attributes to that type's Dogma modifiers. They never call the
runtime effect/rule resolver, numeric matcher or percentage aggregator.
"""

# Standard Library
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# Django
from django.test import SimpleTestCase

# AA Example App
from indy_hub.services import craft_structures, industry_structures
from indy_hub.services.industry_rules import get_industry_rule

FIXTURES = Path(__file__).with_name("fixtures") / "ccp_industry"
TOLERANCE = Decimal("0.000000001")  # absolute percentage points
ACTIVITIES = {
    "manufacturing": 1,
    "researchTime": 3,
    "researchMaterial": 4,
    "copying": 5,
    "invention": 8,
    "reaction": 9,
}
FIELDS = {
    "material": "material_efficiency_percent",
    "time": "time_efficiency_percent",
    "cost": "job_cost_percent",
}
BANDS = {"highsec": 2355, "lowsec": 2356, "nullsec": 2357}


def _rows(filename):
    return {
        row["_key"]: row
        for row in map(json.loads, (FIXTURES / filename).read_bytes().splitlines())
    }


class CCPIndustryOracleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.oracle = json.loads((FIXTURES / "oracle.json").read_text())
        cls.sources = _rows("industryModifierSources.jsonl")
        cls.filters = _rows("industryTargetFilters.jsonl")
        cls.effects = _rows("dogmaEffects.jsonl")
        cls.dogma = _rows("typeDogma.jsonl")
        cls.rigs = {rig["type_id"]: rig for rig in cls.oracle["rigs"]}
        cls.snapshots = {
            rig_id: industry_structures.SDETypeSnapshot(
                type_id=rig_id,
                name=rig["name"],
                group_id=rig["group_id"],
                dogma_attributes={
                    row["attributeID"]: Decimal(str(row["value"]))
                    for row in cls.dogma[rig_id].get("dogmaAttributes", [])
                },
                dogma_effect_names=tuple(
                    cls.effects[row["effectID"]]["name"]
                    for row in cls.dogma[rig_id].get("dogmaEffects", [])
                ),
                dogma_effect_ids=tuple(
                    row["effectID"] for row in cls.dogma[rig_id].get("dogmaEffects", [])
                ),
            )
            for rig_id, rig in cls.rigs.items()
        }

    def setUp(self):
        super().setUp()
        sections = patch.object(
            industry_structures,
            "_get_industry_sde_sections",
            return_value=[
                (section, 3670638, None)
                for section in (
                    "TypeDogma",
                    "TypeEffect",
                    "DogmaEffect",
                    "DogmaAttribute",
                )
            ],
        )
        sections.start()
        self.addCleanup(sections.stop)
        token = patch.object(
            industry_structures,
            "get_industry_calculation_cache_token",
            return_value="offline-ccp-oracle",
        )
        token.start()
        self.addCleanup(token.stop)

    def _official_percent(self, rig_id, activity, metric, band, target=None):
        """Evaluate raw source rows without the application's correspondence map."""
        modifiers = self.sources[rig_id].get(activity, {}).get(metric, [])
        attributes = {
            entry["attributeID"]: Decimal(str(entry["value"]))
            for entry in self.dogma[rig_id].get("dogmaAttributes", [])
        }
        rig_effects = [
            self.effects[row["effectID"]]
            for row in self.dogma[rig_id].get("dogmaEffects", [])
        ]
        multiplier = Decimal(1)
        matched_effects = []
        for modifier in modifiers:
            if "filterID" in modifier:
                self.assertIn(modifier["filterID"], self.filters)
                target_filter = self.filters[modifier["filterID"]]
                self.assertLessEqual(
                    target_filter.keys(), {"_key", "name", "groupIDs", "categoryIDs"}
                )
            else:
                target_filter = None
            if target_filter is not None:
                if target is None:
                    continue
                if not (
                    target["group_id"] in target_filter.get("groupIDs", [])
                    or target["category_id"] in target_filter.get("categoryIDs", [])
                ):
                    continue
            matches = [
                (effect, info)
                for effect in rig_effects
                for info in effect.get("modifierInfo", [])
                if info.get("domain") == "structureID"
                and info.get("modifiedAttributeID") == modifier["dogmaAttributeID"]
            ]
            self.assertEqual(len(matches), 1, (rig_id, modifier, matches))
            effect, info = matches[0]
            self.assertEqual(info["operation"], 6)
            value = attributes[info["modifyingAttributeID"]]
            security_multiplier = attributes.get(BANDS[band], Decimal(1))
            multiplier *= Decimal(1) - abs(value * security_multiplier) / 100
            matched_effects.append(effect["name"])
        return (Decimal(1) - multiplier) * 100, matched_effects

    def test_official_fixture_provenance_and_audit_scope(self):
        self.assertEqual(self.oracle["sde_build"], 3670638)
        self.assertEqual(len(self.rigs), 125)
        self.assertEqual(self.oracle["published_product_count"], 4173)
        self.assertEqual(len(self.oracle["group_activity_cases"]), 466)
        for filename, expected in self.oracle["fixture_sha256"].items():
            self.assertEqual(
                hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest(),
                expected,
                filename,
            )
        for filename in (
            "industryModifierSources.jsonl",
            "industryTargetFilters.jsonl",
        ):
            self.assertEqual(
                self.oracle["fixture_sha256"][filename],
                self.oracle["provenance"]["entry_sha256"][filename],
            )

    def test_runtime_rule_content_requires_matching_version_digest(self):
        bundle_path = (
            Path(industry_structures.__file__).resolve().parents[1]
            / "data/industry/ccp_rules.json"
        )
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        version = bundle.pop("rules_version")
        bundle.pop("provenance")
        digest = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(version, f"ccp-{bundle['sde_build']}-{digest[:12]}")

    def test_all_349500_manufacturing_reaction_rule_comparisons(self):
        differences = []
        compared = 0
        invalid_configuration_cases = 0
        with patch.object(
            industry_structures, "get_type_snapshot", side_effect=self.snapshots.get
        ):
            for rig_id, rig in self.rigs.items():
                for band in BANDS:
                    bonuses = industry_structures.resolve_rig_type_bonuses(
                        rig_id, security_band=band
                    )
                    for target in self.oracle["group_activity_cases"]:
                        applicable = [
                            bonus
                            for bonus in bonuses
                            if bonus.activity_id == ACTIVITIES[target["activity"]]
                            and craft_structures._item_supported_by_bonus(
                                bonus,
                                set(),
                                group_id=target["group_id"],
                                category_id=target["category_id"],
                            )
                        ]
                        for metric in ("material", "time"):
                            expected, effects = self._official_percent(
                                rig_id, target["activity"], metric, band, target
                            )
                            actual = craft_structures._combine_bonus_percentages(
                                applicable, FIELDS[metric]
                            )
                            compared += 1
                            # This is a rule-value matrix, not a statement that
                            # every hypothetical facility combination is legal.
                            if target["activity"] == "reaction" and band == "highsec":
                                invalid_configuration_cases += 1
                            if abs(expected - actual) > TOLERANCE:
                                differences.append(
                                    {
                                        "rig": rig_id,
                                        "name": rig["name"],
                                        "effects": effects,
                                        "target": target,
                                        "activity": target["activity"],
                                        "metric": metric,
                                        "security": band,
                                        "CCP": str(expected),
                                        "IndyHub": str(actual),
                                    }
                                )
        self.assertEqual(compared, 349500)
        self.assertEqual(invalid_configuration_cases, 1500)
        self.assertEqual(differences, [], json.dumps(differences[:30], indent=2))

    def test_all_144_research_copying_invention_scalar_comparisons(self):
        compared = 0
        with patch.object(
            industry_structures, "get_type_snapshot", side_effect=self.snapshots.get
        ):
            for rig_id in self.rigs:
                for band in BANDS:
                    bonuses = industry_structures.resolve_rig_type_bonuses(
                        rig_id, security_band=band
                    )
                    for activity in (
                        "researchTime",
                        "researchMaterial",
                        "copying",
                        "invention",
                    ):
                        for metric, sources in (
                            self.sources[rig_id].get(activity, {}).items()
                        ):
                            self.assertEqual(len(sources), 1)
                            expected, effects = self._official_percent(
                                rig_id, activity, metric, band
                            )
                            actual = craft_structures._combine_bonus_percentages(
                                [
                                    bonus
                                    for bonus in bonuses
                                    if bonus.activity_id == ACTIVITIES[activity]
                                ],
                                FIELDS[metric],
                            )
                            self.assertLessEqual(
                                abs(expected - actual),
                                TOLERANCE,
                                (
                                    rig_id,
                                    effects,
                                    activity,
                                    metric,
                                    band,
                                    expected,
                                    actual,
                                ),
                            )
                            compared += 1
        self.assertEqual(compared, 144)

    def test_all_46_actual_effect_bindings_have_official_source(self):
        effects = set()
        for rig_id in self.rigs:
            for activity, metrics in self.sources[rig_id].items():
                if activity == "_key":
                    continue
                for metric in metrics:
                    targets = (
                        [
                            row
                            for row in self.oracle["group_activity_cases"]
                            if row["activity"] == activity
                        ]
                        if activity in ("manufacturing", "reaction")
                        else [None]
                    )
                    for target in targets:
                        _, names = self._official_percent(
                            rig_id, activity, metric, "nullsec", target
                        )
                        effects.update(names)
            for row in self.dogma[rig_id].get("dogmaEffects", []):
                name = self.effects[row["effectID"]]["name"]
                rule = get_industry_rule(name, rig_type_id=rig_id)
                if rule is not None:
                    self.assertIn(name, effects)
                    self.assertEqual(rule.effect_id, row["effectID"])
        self.assertEqual(len(effects), 46)

    def test_aliases_keep_official_numeric_filters(self):
        self.assertEqual(
            get_industry_rule("rigAdvSmallshipManufactureMaterialBonus"),
            get_industry_rule("rigAdvSmshipManufactureMaterialBonus"),
        )
        self.assertEqual(
            get_industry_rule("rigAdvSmallshipManufactureMaterialBonus").group_ids,
            (324, 541, 830, 831, 834, 893, 1283, 1305, 1527, 1534),
        )
        self.assertIsNone(
            get_industry_rule("rigUnknownIndustryManufactureMaterialBonus")
        )

    def test_known_effect_does_not_authorize_an_unknown_or_wrong_source_type(self):
        effect = "rigAdvSmshipManufactureMaterialBonus"
        self.assertIsNotNone(get_industry_rule(effect, rig_type_id=43854))
        self.assertIsNone(get_industry_rule(effect, rig_type_id=99999999))
        self.assertIsNone(get_industry_rule(effect, rig_type_id=43859))
        self.assertIsNotNone(
            get_industry_rule(
                "rigAdvSmallshipManufactureMaterialBonus", rig_type_id=43854
            )
        )
