"""Offline fixture generation with reviewed CCP data, no runtime rule engine."""

# Standard Library
import copy
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

# AA Example App
from scripts import generate_craft_browser_fixture as generator


class BrowserFixtureGenerationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reference = json.loads(generator.REFERENCE.read_bytes())

    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory(
            prefix="indyhub-fixture-generator-"
        )
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def _archive(self, fixture=None):
        fixture = fixture or self.reference
        path = self.root / "eve-online-static-data-3670638-jsonl.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for section in (
                "types",
                "groups",
                "blueprints",
                "typeDogma",
                "dogmaEffects",
            ):
                archive.writestr(
                    f"{section}.jsonl",
                    b"\n".join(json.dumps(row).encode() for row in fixture[section])
                    + b"\n",
                )
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_reference_integrity_and_explicit_selection(self):
        data = copy.deepcopy(self.reference)
        provenance = data.pop("provenance")
        self.assertEqual(provenance["archive_sha256"], generator.PINNED_ARCHIVE_SHA256)
        self.assertEqual(provenance["selection_version"], 1)
        self.assertEqual(
            hashlib.sha256(generator._canonical(data)).hexdigest(),
            provenance["data_sha256"],
        )
        self.assertEqual(
            [row["_key"] for row in data["types"]],
            list(generator.TYPE_IDS + generator.ADDITIONAL_TYPE_IDS),
        )
        self.assertEqual(
            [row["_key"] for row in data["blueprints"]], list(generator.BLUEPRINT_IDS)
        )
        oracle_path = (
            Path(__file__).resolve().parents[1]
            / "industry/fixtures/ccp_industry/oracle.json"
        )
        oracle = json.loads(oracle_path.read_bytes())
        for name, fingerprint in provenance["entry_sha256"].items():
            self.assertEqual(fingerprint, oracle["provenance"]["entry_sha256"][name])

    def test_reconstruction_is_deterministic_and_preserves_all_reference_data(self):
        archive, fingerprint = self._archive()
        first = generator.build_fixture(archive, fingerprint)
        second = generator.build_fixture(archive, fingerprint)
        self.assertEqual(first, second)
        self.assertFalse(
            generator.compare_fixture(first, self.reference)["data_changed"]
        )
        for section in ("types", "groups", "blueprints", "typeDogma", "dogmaEffects"):
            self.assertEqual(first[section], self.reference[section])

    def test_bad_archive_fingerprint_is_rejected_before_projection(self):
        archive, _ = self._archive()
        with self.assertRaisesRegex(ValueError, "SHA-256 does not match"):
            generator.build_fixture(archive, "0" * 64)

    def test_other_build_requires_an_explicit_fingerprint(self):
        archive, _ = self._archive()
        renamed = archive.with_name("eve-online-static-data-9999999-jsonl.zip")
        archive.rename(renamed)
        with self.assertRaisesRegex(ValueError, "independently verified"):
            generator.build_fixture(renamed)

    def test_new_recipe_dependency_requires_selection_review(self):
        fixture = copy.deepcopy(self.reference)
        fixture["blueprints"][0]["activities"]["manufacturing"]["materials"].append(
            {"typeID": 99999999, "quantity": 1}
        )
        archive, fingerprint = self._archive(fixture)
        with self.assertRaisesRegex(
            ValueError, "dependencies require a selection review"
        ):
            generator.build_fixture(archive, fingerprint)

    def test_missing_or_duplicate_selected_record_is_rejected(self):
        for mutation in ("missing", "duplicate"):
            fixture = copy.deepcopy(self.reference)
            if mutation == "missing":
                fixture["types"].pop(0)
            else:
                fixture["types"].append(fixture["types"][0])
            archive, fingerprint = self._archive(fixture)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                generator.build_fixture(archive, fingerprint)

    def test_diff_report_identifies_changed_dogma_and_recipe_records(self):
        candidate = copy.deepcopy(self.reference)
        candidate["typeDogma"][0]["dogmaAttributes"][0]["value"] += 1
        candidate["blueprints"][0]["activities"]["manufacturing"]["materials"][0][
            "quantity"
        ] += 1
        report = generator.compare_fixture(candidate, self.reference)
        self.assertTrue(report["data_changed"])
        self.assertEqual(report["sections"]["typeDogma"]["changed"], [44])
        self.assertEqual(report["sections"]["blueprints"]["changed"], [34318])

    def test_cli_emits_candidate_and_report_without_overwriting_reference(self):
        archive, fingerprint = self._archive()
        reference_bytes = generator.REFERENCE.read_bytes()
        args = [
            "--archive",
            str(archive),
            "--expected-archive-sha256",
            fingerprint,
            "--output-dir",
            str(self.root / "candidate"),
        ]
        with patch("builtins.print"):
            generator.main(args)
        self.assertEqual(generator.REFERENCE.read_bytes(), reference_bytes)
        self.assertTrue(
            (self.root / "candidate/ccp_browser_sde.candidate.json").is_file()
        )
        report = json.loads(
            (self.root / "candidate/browser_fixture_diff.json").read_bytes()
        )
        self.assertFalse(report["data_changed"])
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            generator.main(args)
        self.assertEqual(generator.REFERENCE.read_bytes(), reference_bytes)
