"""Offline boundary mutations; expected validity does not use the calculator."""

# Standard Library
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

# AA Example App
from indy_hub.services import industry_rules
from scripts.generate_industry_rules import (
    FILES,
    build_bundle,
    fingerprint,
    generate,
    qualify_projection,
    read_archive,
)


def official_sample():
    """Small syntactically official inputs for boundary checks, not an oracle."""
    data = {filename: {} for filename in FILES}
    data["industryModifierSources.jsonl"] = {
        101: {
            "_key": 101,
            "manufacturing": {"material": [{"dogmaAttributeID": 500, "filterID": 1}]},
        },
        35825: {
            "_key": 35825,
            "manufacturing": {"material": [{"dogmaAttributeID": 2600}]},
        },
    }
    data["industryTargetFilters.jsonl"] = {
        1: {"_key": 1, "name": "Equipment", "categoryIDs": [7]}
    }
    data["dogmaEffects.jsonl"] = {
        2663: {"_key": 2663, "name": "rigSlot"},
        900: {
            "_key": 900,
            "name": "newNamingConvention",
            "modifierInfo": [
                {
                    "domain": "structureID",
                    "func": "ItemModifier",
                    "operation": 6,
                    "modifyingAttributeID": 600,
                    "modifiedAttributeID": 500,
                }
            ],
        },
    }
    data["dogmaAttributes.jsonl"] = {
        500: {"_key": 500, "name": "manufacturingMaterialMultiplier"},
        600: {"_key": 600, "name": "percentageReduction", "unitID": 105},
        2355: {
            "_key": 2355,
            "name": "hiSecModifier",
            "unitID": 104,
            "defaultValue": 1.0,
        },
        2600: {"_key": 2600, "name": "strEngMatBonus", "defaultValue": 1.0},
    }
    data["typeDogma.jsonl"] = {
        101: {
            "_key": 101,
            "dogmaAttributes": [
                {"attributeID": 600, "value": -2},
                {"attributeID": 2355, "value": 1},
            ],
            "dogmaEffects": [
                {"effectID": 2663, "isDefault": False},
                {"effectID": 900, "isDefault": False},
            ],
        },
        35825: {
            "_key": 35825,
            "dogmaAttributes": [{"attributeID": 2600, "value": 0.99}],
            "dogmaEffects": [],
        },
        35832: {"_key": 35832, "dogmaAttributes": [], "dogmaEffects": []},
    }
    data["types.jsonl"] = {
        101: {
            "_key": 101,
            "name": {"en": "New rig"},
            "published": True,
            "groupID": 100,
        },
        35825: {
            "_key": 35825,
            "name": {"en": "Raitaru"},
            "published": True,
            "groupID": 1404,
        },
        35832: {
            "_key": 35832,
            "name": {"en": "Astrahus"},
            "published": True,
            "groupID": 1657,
        },
    }
    data["groups.jsonl"] = {
        100: {"_key": 100, "categoryID": 66},
        1404: {"_key": 1404, "categoryID": 65},
        1657: {"_key": 1657, "categoryID": 65},
    }
    data["categories.jsonl"] = {7: {"_key": 7, "name": {"en": "Module"}}}
    return data


def completed_bundle():
    data, evidence = build_bundle(official_sample(), 123)
    data["qualifications"] = {"123": evidence}
    data["rules_version"] = f"ccp-123-{fingerprint(data)[:12]}"
    data["provenance"] = {
        "description": "synthetic schema input; no numerical qualification"
    }
    return data


class IndustryRulesSchemaTests(TestCase):
    def setUp(self):
        self.data = completed_bundle()

    def test_valid_official_omissions_and_new_effect_name(self):
        industry_rules.validate_industry_bundle(self.data)
        self.assertEqual(self.data["hull_rules"]["35832"], [])
        self.assertFalse(self.data["sources"]["35832"]["has_industry_source"])
        self.assertIn("newnamingconvention", self.data["rig_rules"]["101"])

    def test_schema_version_is_exact_and_typed(self):
        for version in (1, 3, "2", 2.0, True):
            with self.subTest(version=version):
                self.data["schema_version"] = version
                with self.assertRaises(industry_rules.IndustryRulesError):
                    industry_rules.validate_industry_bundle(self.data)

    def test_resealed_source_metadata_mutations_are_rejected(self):
        # Recompute both attestations so rejection proves schema/association
        # validation, independently of the existing version-digest check.
        for mutation in ("fitting_list", "invalid_effect_id", "modifier_extension"):
            with self.subTest(mutation=mutation):
                data = json.loads(json.dumps(self.data))
                source = data["sources"]["101"]
                if mutation == "fitting_list":
                    source["fitting_attribute_values"] = []
                elif mutation == "invalid_effect_id":
                    source["effects"]["not-an-id"] = {
                        "name": "unknownMetadataEffect",
                        "modifier_info": [],
                    }
                else:
                    source["effects"]["900"]["modifier_info"].append(
                        {"futureSemanticOperation": 999}
                    )
                    self.assertEqual(
                        len(data["industrial_effects"]["900"]["modifier_info"]), 1
                    )
                data["qualifications"]["123"] = (
                    industry_rules.industry_projection_evidence(data)
                )
                content = {
                    k: v
                    for k, v in data.items()
                    if k not in ("rules_version", "provenance")
                }
                data["rules_version"] = f"ccp-123-{fingerprint(content)[:12]}"
                with self.assertRaises(industry_rules.IndustryRulesError):
                    industry_rules.validate_industry_bundle(data)

    def test_nonindustrial_modifiers_keep_their_own_operations(self):
        sample = official_sample()
        sample["dogmaEffects.jsonl"][2663]["modifierInfo"] = [
            {
                "domain": "shipID",
                "func": "LocationGroupModifier",
                "operation": 3,
                "modifiedAttributeID": 1001,
                "modifyingAttributeID": 1002,
                "groupID": 1003,
            }
        ]
        data, evidence = build_bundle(sample, 123)
        data["qualifications"] = {"123": evidence}
        data["rules_version"] = f"ccp-123-{fingerprint(data)[:12]}"
        data["provenance"] = {"description": "nonindustrial metadata boundary"}
        industry_rules.validate_industry_bundle(data)

    def test_missing_and_unknown_filter_semantics_are_rejected(self):
        for target in (
            None,
            {"_key": 1, "name": "New", "typeIDs": [42]},
            {"_key": 1, "name": "Empty"},
        ):
            with self.subTest(target=target):
                data = deepcopy(self.data)
                if target is None:
                    data["filters"].pop("1")
                else:
                    data["filters"]["1"] = target
                with self.assertRaises(industry_rules.IndustryRulesError):
                    industry_rules.validate_industry_bundle(data)

    def test_unknown_activity_metric_operation_and_domain_are_typed_errors(self):
        for field, value in (
            ("activity", "futureIndustry"),
            ("metric", "waste"),
            ("operation", 7),
            ("domain", "shipID"),
        ):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data["rig_rules"]["101"]["newnamingconvention"][field] = value
                with self.assertRaises(industry_rules.IndustryRulesError):
                    industry_rules.validate_industry_bundle(data)

    def test_incomplete_association_missing_attribute_and_nonfinite_are_rejected(self):
        for section in ("rule", "effect", "attribute", "nonfinite"):
            with self.subTest(section=section):
                data = deepcopy(self.data)
                if section == "rule":
                    data["rig_rules"]["101"].clear()
                elif section == "effect":
                    data["sources"]["101"]["effects"].pop("900")
                elif section == "attribute":
                    data["sources"]["101"]["attribute_values"].pop("600")
                else:
                    data["sources"]["101"]["attribute_values"]["600"] = float("nan")
                with self.assertRaises(industry_rules.IndustryRulesError):
                    industry_rules.validate_industry_bundle(data)

    def test_content_and_qualification_fingerprints_are_verified(self):
        data = deepcopy(self.data)
        data["sources"]["101"]["attribute_values"]["600"] = -3
        with self.assertRaisesRegex(industry_rules.IndustryRulesError, "qualification"):
            industry_rules.validate_industry_bundle(data)
        data = deepcopy(self.data)
        data["rules_version"] = "stale-token"
        with self.assertRaisesRegex(industry_rules.IndustryRulesError, "fingerprint"):
            industry_rules.validate_industry_bundle(data)

    def test_duplicate_json_associations_are_rejected(self):
        with self.assertRaisesRegex(
            industry_rules.IndustryRulesError, "duplicate JSON"
        ):
            industry_rules.read_industry_json('{"rig_rules":{},"rig_rules":{}}')
        for number in ("NaN", "Infinity", "-Infinity"):
            with (
                self.subTest(number=number),
                self.assertRaisesRegex(industry_rules.IndustryRulesError, "non-finite"),
            ):
                industry_rules.read_industry_json('{"value":' + number + "}")

    def test_lazy_boundary_reports_invalid_rules_without_loading_django(self):
        with (
            patch.object(industry_rules, "_DATA", {"schema_version": 999}),
            patch.object(industry_rules, "_VALIDATED", False),
        ):
            with self.assertRaises(industry_rules.IndustryRulesError):
                industry_rules.get_industry_rules_for_rig(101)

    def test_conditional_build_uses_attested_values_and_rejects_mixed_imports(self):
        sample = official_sample()
        sample["typeDogma.jsonl"][101]["dogmaAttributes"][0]["value"] = -3
        candidate, evidence = build_bundle(sample, 456)
        evidence["status"] = "compatible_conditional"
        evidence["source_attribute_values"] = {
            "101": candidate["sources"]["101"]["attribute_values"]
        }
        self.data["qualifications"]["456"] = evidence
        self.data["compatible_builds"].append(456)
        content = {
            k: v
            for k, v in self.data.items()
            if k not in ("rules_version", "provenance")
        }
        self.data["rules_version"] = f"ccp-123-{fingerprint(content)[:12]}"
        industry_rules.validate_industry_bundle(self.data)
        with (
            patch.object(industry_rules, "_DATA", self.data),
            patch.object(industry_rules, "_VALIDATED", False),
        ):
            self.assertEqual(
                industry_rules.get_industry_source_attribute_values(101, [456])["600"],
                -3,
            )
            self.assertEqual(
                industry_rules.get_industry_source_attribute_values(101, [123])["600"],
                -2,
            )
            with self.assertRaisesRegex(industry_rules.IndustryRulesError, "Mixed"):
                industry_rules.get_industry_source_attribute_values(101, [123, 456])
            with self.assertRaisesRegex(
                industry_rules.IndustryRulesError, "Unqualified"
            ):
                industry_rules.get_industry_source_attribute_values(101, [999])

    def test_declared_compatibility_cannot_bypass_projection_evidence(self):
        self.data["qualifications"]["456"] = deepcopy(
            self.data["qualifications"]["123"]
        )
        self.data["qualifications"]["456"]["projection_sha256"] = "f" * 64
        self.data["compatible_builds"].append(456)
        with self.assertRaisesRegex(industry_rules.IndustryRulesError, "projection"):
            industry_rules.validate_industry_bundle(self.data)

    def test_incompatible_filter_only_blocks_affected_sources(self):
        sample = official_sample()
        sample["industryTargetFilters.jsonl"][1]["categoryIDs"] = [8]
        sample["categories.jsonl"][8] = {"_key": 8, "name": {"en": "Charge"}}
        _, evidence = build_bundle(sample, 456)
        evidence["status"] = "incompatible"
        self.data["qualifications"]["456"] = evidence
        content = {
            k: v
            for k, v in self.data.items()
            if k not in ("rules_version", "provenance")
        }
        self.data["rules_version"] = f"ccp-123-{fingerprint(content)[:12]}"
        industry_rules.validate_industry_bundle(self.data)
        with (
            patch.object(industry_rules, "_DATA", self.data),
            patch.object(industry_rules, "_VALIDATED", False),
        ):
            self.assertEqual(
                industry_rules.get_industry_build_qualification([456])["status"],
                "incompatible",
            )
            self.assertEqual(
                industry_rules.get_industry_build_qualification(
                    [456], source_type_id=101
                )["status"],
                "incompatible",
            )
            self.assertEqual(
                industry_rules.get_industry_build_qualification(
                    [456], source_type_id=35825
                )["status"],
                "compatible_qualified",
            )
            self.assertEqual(
                industry_rules.get_industry_source_attribute_values(35825, [456]),
                {"2600": 0.99},
            )


class IndustryRulesGeneratorTests(TestCase):
    def setUp(self):
        self.data = official_sample()

    def test_generator_rejects_new_filter_semantics_and_unknown_source_fields(self):
        for section in ("filter", "source"):
            with self.subTest(section=section):
                data = deepcopy(self.data)
                if section == "filter":
                    data["industryTargetFilters.jsonl"][1]["typeIDs"] = [42]
                else:
                    data["industryModifierSources.jsonl"][101][
                        "unknownEligibility"
                    ] = {}
                with self.assertRaises(industry_rules.IndustryRulesError):
                    build_bundle(data, 123)

    def test_description_is_not_treated_as_eligibility(self):
        self.data["industryModifierSources.jsonl"][101][
            "description"
        ] = "CCP editorial annotation"
        self.data["industryTargetFilters.jsonl"][1][
            "description"
        ] = "CCP editorial annotation"
        data, _ = build_bundle(self.data, 123)
        self.assertEqual(len(data["rig_rules"]["101"]), 1)

    def test_operation_domain_and_modifier_extensions_are_rejected(self):
        for key, value in (
            ("operation", 7),
            ("domain", "shipID"),
            ("newCondition", True),
        ):
            with self.subTest(key=key):
                data = deepcopy(self.data)
                data["dogmaEffects.jsonl"][900]["modifierInfo"][0][key] = value
                with self.assertRaises(industry_rules.IndustryRulesError):
                    build_bundle(data, 123)

    def test_uninterpreted_attribute_bounds_are_rejected(self):
        for field in ("minAttributeID", "maxAttributeID", "chargeRechargeTimeID"):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data["dogmaAttributes.jsonl"][600][field] = 500
                with self.assertRaisesRegex(
                    industry_rules.IndustryRulesError, "unsupported semantic fields"
                ):
                    build_bundle(data, 123)

    def test_same_effect_cannot_overwrite_another_activity(self):
        self.data["industryModifierSources.jsonl"][101]["reaction"] = {
            "time": [{"dogmaAttributeID": 500, "filterID": 1}]
        }
        with self.assertRaisesRegex(industry_rules.IndustryRulesError, "Colliding"):
            build_bundle(self.data, 123)

    def test_missing_modifying_attribute_and_invalid_numeric_domains_are_rejected(self):
        for value in (None, 2, -100, float("inf")):
            with self.subTest(value=value):
                data = deepcopy(self.data)
                rows = data["typeDogma.jsonl"][101]["dogmaAttributes"]
                if value is None:
                    rows.pop(0)
                else:
                    rows[0]["value"] = value
                with self.assertRaises(industry_rules.IndustryRulesError):
                    build_bundle(data, 123)

    def test_official_absence_of_security_attribute_is_allowed(self):
        self.data["typeDogma.jsonl"][101]["dogmaAttributes"].pop(1)
        data, _ = build_bundle(self.data, 123)
        self.assertEqual(data["rig_security_attribute_ids"]["101"], [])

    def test_qualification_ignores_products_and_editorial_changes(self):
        self.data["industryTargetFilters.jsonl"][1]["categoryIDs"].append(8)
        self.data["categories.jsonl"][8] = {"_key": 8, "name": {"en": "Charge"}}
        _, before = build_bundle(self.data, 123)
        self.data["types.jsonl"][999] = {
            "_key": 999,
            "published": True,
            "groupID": 100,
            "name": {"en": "New product"},
        }
        self.data["industryTargetFilters.jsonl"][1]["description"] = "New translation"
        self.data["industryTargetFilters.jsonl"][1]["categoryIDs"].reverse()
        _, after = build_bundle(self.data, 456)
        self.assertEqual(qualify_projection(before, after), "compatible_qualified")

    def test_numeric_change_is_conditional_and_semantic_change_incompatible(self):
        _, before = build_bundle(self.data, 123)
        self.data["typeDogma.jsonl"][101]["dogmaAttributes"][0]["value"] = -3
        _, after = build_bundle(self.data, 456)
        self.assertEqual(qualify_projection(before, after), "compatible_conditional")
        self.data["dogmaEffects.jsonl"][900]["name"] = "AnotherIndustrialName"
        _, after = build_bundle(self.data, 456)
        self.assertEqual(qualify_projection(before, after), "incompatible")

    def test_candidate_refuses_checkout_and_existing_outputs(self):
        with self.assertRaises(industry_rules.IndustryRulesError):
            generate(Path("absent.zip"), None, Path(__file__).resolve().parents[3])
        with tempfile.TemporaryDirectory(prefix="indy-rules-schema-") as directory:
            (Path(directory) / "ccp_rules.json").write_text("historic-candidate")
            with self.assertRaises(industry_rules.IndustryRulesError):
                generate(Path("absent.zip"), None, Path(directory))
            self.assertEqual(
                (Path(directory) / "ccp_rules.json").read_text(), "historic-candidate"
            )

    def test_archive_duplicate_records_are_rejected(self):
        # Standard Library
        import zipfile

        with tempfile.TemporaryDirectory(prefix="indy-rules-schema-") as directory:
            archive = Path(directory) / "eve-online-static-data-123-jsonl.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                for name, rows in self.data.items():
                    lines = [json.dumps(row) for row in rows.values()]
                    if name == FILES[0]:
                        lines.append(lines[0])
                    handle.writestr(name, "\n".join(lines))
            with self.assertRaisesRegex(industry_rules.IndustryRulesError, "Duplicate"):
                read_archive(archive)
