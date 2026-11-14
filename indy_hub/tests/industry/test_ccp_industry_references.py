"""Fixed numeric references reviewed from CCP 3670638, independent of generators.

Expected percentages below are literals, never generated with the resolver or
the source/Dogma join used by the matrix. The approved rig IDs were separately
checked against all 220 official industry sources and their published item/group
inventory: engineering/reaction Standup rigs, excluding converted outpost rigs.
"""

# Standard Library
import copy
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# Django
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

# AA Example App
from indy_hub.services import craft_structures, industry_rules, industry_structures

FIXTURES = Path(__file__).with_name("fixtures") / "ccp_industry"
APPROVED_RIG_IDS = frozenset(
    set(range(37146, 37176))
    | set(range(37178, 37184))
    | (set(range(43704, 43735)) - {43706, 43710, 43728, 43731})
    | set(range(43854, 43894))
    | set(range(43919, 43922))
    | {45544, 45546, 45548, 45640, 45641}
    | set(range(46484, 46498))
)


class CCPIndustryReferenceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def rows(name):
            return {
                row["_key"]: row
                for row in map(json.loads, (FIXTURES / name).read_bytes().splitlines())
            }

        cls.dogma = rows("typeDogma.jsonl")
        cls.effects = rows("dogmaEffects.jsonl")
        manifest = json.loads((FIXTURES / "oracle.json").read_bytes())
        cls.rigs = {row["type_id"]: row for row in manifest["rigs"]}
        cls.snapshots = {
            key: industry_structures.SDETypeSnapshot(
                type_id=key,
                name=cls.rigs[key]["name"],
                group_id=cls.rigs[key]["group_id"],
                dogma_attributes={
                    a["attributeID"]: Decimal(str(a["value"]))
                    for a in row["dogmaAttributes"]
                },
                dogma_effect_names=tuple(
                    cls.effects[a["effectID"]]["name"] for a in row["dogmaEffects"]
                ),
                dogma_effect_ids=tuple(a["effectID"] for a in row["dogmaEffects"]),
            )
            for key, row in cls.dogma.items()
        }
        cls.hull_fixture = json.loads(
            (FIXTURES.parent / "ccp_hull_references.json").read_bytes()
        )
        hull_types = {row["_key"]: row for row in cls.hull_fixture["types"]}
        hull_effects = {
            row["_key"]: row["name"] for row in cls.hull_fixture["dogmaEffects"]
        }
        cls.hull_snapshots = {
            row["_key"]: industry_structures.SDETypeSnapshot(
                type_id=row["_key"],
                name=hull_types[row["_key"]]["name"]["en"],
                group_id=hull_types[row["_key"]]["groupID"],
                dogma_attributes={
                    a["attributeID"]: Decimal(str(a["value"]))
                    for a in row["dogmaAttributes"]
                },
                dogma_effect_names=tuple(
                    hull_effects[a["effectID"]] for a in row["dogmaEffects"]
                ),
                dogma_effect_ids=tuple(a["effectID"] for a in row["dogmaEffects"]),
            )
            for row in cls.hull_fixture["typeDogma"]
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
        self.snapshot_patch = patch.object(
            industry_structures, "get_type_snapshot", side_effect=self.snapshots.get
        )
        self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        token = patch.object(
            industry_structures,
            "get_industry_calculation_cache_token",
            return_value="fixed-ccp-reference",
        )
        token.start()
        self.addCleanup(token.stop)

    def _rig_totals(self, rig_ids, group, category, band="nullsec", activity=1):
        bonuses = [
            bonus
            for key in rig_ids
            for bonus in industry_structures.resolve_rig_type_bonuses(
                key, security_band=band
            )
            if bonus.activity_id == activity
            and craft_structures._item_supported_by_bonus(
                bonus, set(), group_id=group, category_id=category
            )
        ]
        return tuple(
            craft_structures._combine_bonus_percentages(bonuses, field)
            for field in (
                "material_efficiency_percent",
                "time_efficiency_percent",
                "job_cost_percent",
            )
        )

    def test_historical_corpus_matches_separately_reviewed_type_inventory(self):
        self.assertEqual(set(self.rigs), APPROVED_RIG_IDS)
        self.assertEqual(set(self.dogma), APPROVED_RIG_IDS)
        self.assertEqual(len(APPROVED_RIG_IDS), 125)

    def test_hull_reference_provenance_and_direct_official_sources(self):
        manifest = json.loads((FIXTURES / "oracle.json").read_bytes())
        provenance = self.hull_fixture["provenance"]
        self.assertEqual(
            provenance["archive_sha256"], manifest["provenance"]["archive_sha256"]
        )
        for name, digest in provenance["entry_sha256"].items():
            self.assertEqual(digest, manifest["provenance"]["entry_sha256"][name])
        self.assertEqual(set(self.hull_snapshots), {35825, 35826, 35827, 35835, 35836})
        source_rows = self.hull_fixture["industryModifierSources"]
        self.assertEqual(
            {row["_key"] for row in source_rows}, {35825, 35826, 35827, 35836}
        )
        expected = {
            (key, activity, metric, attribute)
            for key in (35825, 35826, 35827)
            for activity in (
                "manufacturing",
                "researchTime",
                "researchMaterial",
                "copying",
                "invention",
            )
            for metric, attribute in (("cost", 2601), ("time", 2602))
        }
        expected.update(
            (key, "manufacturing", "material", 2600) for key in (35825, 35826, 35827)
        )
        expected.add((35836, "reaction", "time", 2721))
        actual = set()
        for row in source_rows:
            for activity, metrics in row.items():
                if activity == "_key":
                    continue
                for metric, modifiers in metrics.items():
                    self.assertEqual(len(modifiers), 1)
                    self.assertEqual(set(modifiers[0]), {"dogmaAttributeID"})
                    actual.add(
                        (
                            row["_key"],
                            activity,
                            metric,
                            modifiers[0]["dogmaAttributeID"],
                        )
                    )
        self.assertEqual(actual, expected)

    def test_confessor_basic_advanced_and_noneligible_ship_references(self):
        for band, material, time in (
            ("highsec", "2.4", "20"),
            ("lowsec", "4.56", "38"),
            ("nullsec", "5.04", "42"),
        ):
            with self.subTest(band=band):
                self.assertEqual(
                    self._rig_totals((43854, 43856), 1305, 6, band),
                    (Decimal(material), Decimal(time), 0),
                )
                self.assertEqual(self._rig_totals((37155,), 1305, 6, band), (0, 0, 0))
                self.assertEqual(self._rig_totals((43854,), 25, 6, band), (0, 0, 0))
                self.assertEqual(
                    self._rig_totals((37155,), 25, 6, band), (Decimal(material), 0, 0)
                )

    def test_basic_advanced_medium_overlap_is_explicitly_official(self):
        # Expedition command ships (group 4902) deliberately occur in both filters.
        self.assertEqual(self._rig_totals((37146,), 4902, 6), (Decimal("4.2"), 0, 0))
        self.assertEqual(self._rig_totals((43859,), 4902, 6), (Decimal("5.04"), 0, 0))

    def test_xl_component_filters_do_not_apply_twice_to_group_334(self):
        self.assertEqual(
            self._rig_totals((43704,), 334, 17), (Decimal("4.2"), Decimal("42"), 0)
        )
        self.assertEqual(
            self._rig_totals((43705,), 334, 17), (Decimal("5.04"), Decimal("50.4"), 0)
        )
        self.assertEqual(
            self._rig_totals((37181,), 1305, 6), (Decimal("5.04"), Decimal("50.4"), 0)
        )
        self.assertEqual(self._rig_totals((37181,), 334, 17), (0, 0, 0))

    def test_thukker_uses_distinct_ordinary_and_enhanced_attributes(self):
        for band, enhanced, ordinary in (
            ("highsec", "0.37", "0.2"),
            ("lowsec", "7.03", "3.8"),
            ("nullsec", "0.37", "0.2"),
        ):
            with self.subTest(band=band):
                self.assertEqual(
                    self._rig_totals((45544,), 873, 17, band), (Decimal(enhanced), 0, 0)
                )
                self.assertEqual(
                    self._rig_totals((45640,), 913, 17, band), (Decimal(enhanced), 0, 0)
                )
                self.assertEqual(
                    self._rig_totals((45640,), 334, 17, band), (Decimal(ordinary), 0, 0)
                )

    def test_molecular_forged_biochemical_reference_and_official_highsec_omission(self):
        # Highsec here is a scalar reference, never a legal reaction facility.
        self.assertNotIn(2355, self.snapshots[46495].dogma_attributes)
        for band, material in (
            ("highsec", "2.4"),
            ("lowsec", "2.4"),
            ("nullsec", "2.64"),
        ):
            self.assertEqual(
                self._rig_totals((46495,), 4096, 4, band, activity=9),
                (Decimal(material), 0, 0),
            )
        self.assertEqual(self._rig_totals((46487,), 4096, 4, activity=9), (0, 0, 0))

    def test_hull_multiplier_references_are_distinct_from_rig_percentages(self):
        # Official source attributes 2600/2601/2602, or 2721 for Tatara.
        for key, name, attrs, expected in (
            (35825, "Raitaru", {2600: ".99", 2601: ".97", 2602: ".85"}, (1, 15, 3)),
            (35826, "Azbel", {2600: ".99", 2601: ".96", 2602: ".80"}, (1, 20, 4)),
            (35827, "Sotiyo", {2600: ".99", 2601: ".95", 2602: ".70"}, (1, 30, 5)),
            (35835, "Athanor", {}, None),
            (35836, "Tatara", {2721: ".75"}, (0, 25, 0)),
        ):
            snapshot = self.hull_snapshots[key]
            self.assertEqual(snapshot.name, name)
            self.assertEqual(
                {
                    k: v
                    for k, v in snapshot.dogma_attributes.items()
                    if k in (2600, 2601, 2602, 2721)
                },
                {k: Decimal(v) for k, v in attrs.items()},
            )
            with (
                self.subTest(hull=name),
                patch.object(
                    industry_structures, "get_type_snapshot", return_value=snapshot
                ),
            ):
                bonuses = industry_structures.resolve_structure_type_bonuses(key)
                if expected is None:
                    self.assertEqual(bonuses, [])
                    continue
                activities = (9, 11) if key == 35836 else (1, 3, 4, 5, 8)
                self.assertEqual({row.activity_id for row in bonuses}, set(activities))
                for activity in activities:
                    actual = tuple(
                        craft_structures._combine_bonus_percentages(
                            [row for row in bonuses if row.activity_id == activity],
                            field,
                        )
                        for field in (
                            "material_efficiency_percent",
                            "time_efficiency_percent",
                            "job_cost_percent",
                        )
                    )
                    self.assertEqual(
                        actual,
                        (
                            expected
                            if activity in (1, 9, 11)
                            else (0, expected[1], expected[2])
                        ),
                    )

    def test_real_raitaru_and_confessor_rigs_combine_multiplicatively(self):
        hull = self.hull_snapshots[35825]
        snapshots = {**self.snapshots, 35825: hull}
        for band, material, time in (
            ("highsec", "3.376", "32"),
            ("lowsec", "5.5144", "47.3"),
            ("nullsec", "5.9896", "50.7"),
        ):
            with (
                self.subTest(band=band),
                patch.object(
                    industry_structures, "get_type_snapshot", side_effect=snapshots.get
                ),
            ):
                bonuses = industry_structures.resolve_structure_type_bonuses(35825)
                for key in (43854, 43856):
                    bonuses.extend(
                        industry_structures.resolve_rig_type_bonuses(
                            key, security_band=band
                        )
                    )
                applicable = [
                    row
                    for row in bonuses
                    if row.activity_id == 1
                    and craft_structures._item_supported_by_bonus(
                        row, set(), group_id=1305, category_id=6
                    )
                ]
                self.assertEqual(
                    craft_structures._combine_bonus_percentages(
                        applicable, "material_efficiency_percent"
                    ),
                    Decimal(material),
                )
                self.assertEqual(
                    craft_structures._combine_bonus_percentages(
                        applicable, "time_efficiency_percent"
                    ),
                    Decimal(time),
                )

    def test_group_and_category_membership_mutations_break_fixed_references(self):
        self.assertEqual(self._rig_totals((43854,), 1305, 6)[0], Decimal("5.04"))
        for filter_id, field, value, rig in (
            ("6", "groupIDs", [25], 43854),
            ("3", "categoryIDs", [7], 37181),
        ):
            candidate = copy.deepcopy(industry_rules._DATA)
            candidate["filters"][filter_id][field] = value
            with (
                self.subTest(filter=filter_id),
                patch.object(industry_rules, "_DATA", candidate),
            ):
                try:
                    actual = self._rig_totals((rig,), 1305, 6)[0]
                except (ValidationError, industry_rules.IndustryRulesError):
                    continue
                self.assertNotEqual(actual, Decimal("5.04"))

    def test_dogma_value_and_security_multiplier_mutations_break_fixed_reference(self):
        self.assertEqual(self._rig_totals((43854,), 1305, 6)[0], Decimal("5.04"))
        for attribute, value in ((2594, "-1.9"), (2357, "1.0")):
            snapshot = self.snapshots[43854]
            mutated = replace(
                snapshot,
                dogma_attributes={
                    **snapshot.dogma_attributes,
                    attribute: Decimal(value),
                },
            )
            with (
                self.subTest(attribute=attribute),
                patch.object(
                    industry_structures, "get_type_snapshot", return_value=mutated
                ),
            ):
                try:
                    actual = self._rig_totals((43854,), 1305, 6)[0]
                except (ValidationError, industry_rules.IndustryRulesError):
                    continue
                self.assertNotEqual(actual, Decimal("5.04"))

    def test_missing_or_newly_named_expected_effect_is_rejected(self):
        snapshot = self.snapshots[43854]
        for replacement in (None, "newCCPIndustrialOptimizer"):
            effects = tuple(
                name
                for name in snapshot.dogma_effect_names
                if name != "rigAdvSmshipManufactureMaterialBonus"
            )
            if replacement:
                effects += (replacement,)
            mutated = replace(snapshot, dogma_effect_names=effects)
            with (
                self.subTest(replacement=replacement),
                patch.object(
                    industry_structures, "get_type_snapshot", return_value=mutated
                ),
            ):
                with self.assertRaises(ValidationError):
                    industry_structures.resolve_rig_type_bonuses(
                        43854, security_band="nullsec"
                    )
