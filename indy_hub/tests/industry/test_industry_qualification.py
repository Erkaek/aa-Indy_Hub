"""Durable global qualification, archive boundaries and calculation invalidation."""

# Standard Library
import hashlib
import io
import json
import tempfile
import zipfile
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

# Third Party
import requests

# Django
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

# AA Example App
from indy_hub.models import IndustryDataQualification
from indy_hub.services import industry_qualification as service
from indy_hub.services import industry_rule_projection as projection
from indy_hub.services import industry_rules as rules
from indy_hub.services import industry_structures as resolver
from indy_hub.tests.industry.test_industry_rules_schema import (
    completed_bundle,
    official_sample,
)


def make_archive(path, data=None, build=456, mutate=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "_sde.jsonl", json.dumps({"_key": "sde", "buildNumber": build})
        )
        for name, records in (official_sample() if data is None else data).items():
            blob = "".join(json.dumps(row) + "\n" for row in records.values())
            archive.writestr(name, mutate(name, blob) if mutate else blob)


class RuntimeArchiveTests(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "eve-online-static-data-456-jsonl.zip"

    def test_stream_reader_matches_independent_complete_reader(self):
        data = official_sample()
        data["types.jsonl"][999] = {"_key": 999, "groupID": 1000, "published": True}
        data["groups.jsonl"][1000] = {
            "_key": 1000,
            "categoryID": 7,
            "name": {"en": "Unrelated"},
        }
        data["typeDogma.jsonl"][999] = {"_key": 999, "dogmaAttributes": []}
        make_archive(self.path, data)
        _, complete, hashes = projection.read_archive(self.path)
        streamed, streamed_hashes = projection.read_runtime_archive(self.path, 456)
        self.assertNotIn(999, streamed["typeDogma.jsonl"])
        self.assertEqual(
            projection.build_bundle(complete, 456),
            projection.build_bundle(streamed, 456),
        )
        self.assertEqual(
            streamed_hashes, {name: hashes[name] for name in projection.FILES[:8]}
        )

    def test_wrong_build_missing_entry_and_corrupt_zip_fail_closed(self):
        make_archive(self.path)
        with self.assertRaisesMessage(rules.IndustryRulesError, "build does not match"):
            projection.read_runtime_archive(self.path, 999)
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("_sde.jsonl", "{}")
        with self.assertRaisesMessage(rules.IndustryRulesError, "Missing or duplicate"):
            projection.read_runtime_archive(self.path, 456)
        self.path.write_bytes(b"broken")
        with self.assertRaises(zipfile.BadZipFile):
            projection.read_runtime_archive(self.path, 456)

    def test_duplicate_ids_even_unrelated_records_are_rejected(self):
        make_archive(
            self.path,
            mutate=lambda name, blob: (
                blob + '{"_key":999}\n{"_key":999}\n'
                if name == "typeDogma.jsonl"
                else blob
            ),
        )
        with self.assertRaisesMessage(rules.IndustryRulesError, "duplicate official"):
            projection.read_runtime_archive(self.path, 456)

    def test_compressed_uncompressed_and_line_limits(self):
        make_archive(self.path)
        for constant in ("MAX_ARCHIVE_BYTES", "MAX_DATA_BYTES", "MAX_LINE_BYTES"):
            with self.subTest(limit=constant), patch.object(projection, constant, 1):
                with self.assertRaises((rules.IndustryRulesError, ValueError)):
                    projection.read_runtime_archive(self.path, 456)


class GlobalQualificationTests(TestCase):
    def setUp(self):
        self.reference = completed_bundle()
        self.rows = [
            (name, 456, "import-one") for name in sorted(service.CONTEXT_SECTIONS)
        ]
        self.sections = patch.object(
            resolver, "_get_industry_sde_sections", side_effect=lambda: self.rows
        )
        self.sections.start()
        self.addCleanup(self.sections.stop)
        for name in ("_DATA", "_VALIDATED"):
            item = patch.object(
                rules, name, self.reference if name == "_DATA" else False
            )
            item.start()
            self.addCleanup(item.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "eve-online-static-data-456-jsonl.zip"
        make_archive(self.path)

    def check(self, **kwargs):
        return service.check_installed_industry_rules(archive=self.path, **kwargs)

    def test_same_content_new_build_persists_and_invalidates_warm_caches(self):
        with patch.object(service.requests, "get") as http:
            self.assertEqual(
                resolver.get_industry_sde_qualification()["status"], "unqualified"
            )
            before = resolver.get_industry_calculation_cache_token()
            self.check()
            after = resolver.get_industry_calculation_cache_token()
            self.assertNotEqual(before, after)
            cache.clear()  # A Redis flush must not erase the qualification.
            self.assertEqual(
                resolver.get_industry_sde_qualification()["status"],
                "compatible_qualified",
            )
            self.assertEqual(resolver.get_industry_calculation_cache_token(), after)
            http.assert_not_called()
        record = IndustryDataQualification.objects.get()
        self.assertEqual(
            record.archive_sha256, hashlib.sha256(self.path.read_bytes()).hexdigest()
        )
        self.assertEqual(len(record.entry_sha256), 8)
        with patch.object(service, "_archive_evidence") as parser:
            self.check()
            parser.assert_not_called()

    def test_numeric_change_uses_attested_values_and_keeps_other_sources_qualified(
        self,
    ):
        data = official_sample()
        data["typeDogma.jsonl"][101]["dogmaAttributes"][0]["value"] = -3
        make_archive(self.path, data)
        self.check()
        result = resolver.get_industry_sde_qualification(
            source_type_id=101, include_evidence=True
        )
        self.assertEqual(result["status"], "compatible_conditional")
        values = rules.get_industry_source_attribute_values(
            101, [456], extra_qualifications=result["extra_qualifications"]
        )
        self.assertEqual(values["600"], -3)
        self.assertEqual(
            resolver.get_industry_sde_qualification(source_type_id=35825)["status"],
            "compatible_qualified",
        )
        with self.assertRaisesMessage(
            rules.IndustryRulesError, "conflicting numerical"
        ):
            rules.get_industry_source_attribute_values(
                101, [123, 456], extra_qualifications=result["extra_qualifications"]
            )

    def test_changed_filter_blocks_affected_source_only(self):
        data = official_sample()
        data["industryTargetFilters.jsonl"][1]["categoryIDs"] = [65]
        data["categories.jsonl"][65] = {"_key": 65, "name": {"en": "Structure"}}
        make_archive(self.path, data)
        self.check()
        self.assertEqual(
            resolver.get_industry_sde_qualification()["status"], "incompatible"
        )
        self.assertEqual(
            resolver.get_industry_sde_qualification(source_type_id=101)["status"],
            "incompatible",
        )
        self.assertEqual(
            resolver.get_industry_sde_qualification(source_type_id=35825)["status"],
            "compatible_qualified",
        )

    def test_local_numerical_mismatch_still_rejected_after_global_qualification(self):
        self.check()
        metadata = rules.get_industry_source_metadata(35825)
        snapshot = resolver.SDETypeSnapshot(
            type_id=35825,
            name="Raitaru",
            group_id=1404,
            dogma_attributes={2600: 0.98},
            dogma_effect_ids=(),
            dogma_effect_names=(),
        )
        with self.assertRaisesMessage(
            ValidationError, "differs from the qualified CCP projection"
        ):
            resolver._validate_source_effects(snapshot, metadata)

    def test_reimport_and_new_bundle_require_new_evidence_without_deleting_history(
        self,
    ):
        self.check()
        original = resolver.get_industry_calculation_cache_token()
        self.rows = [(name, build, "import-two") for name, build, _ in self.rows]
        self.assertEqual(
            resolver.get_industry_sde_qualification()["status"], "unqualified"
        )
        self.assertNotEqual(original, resolver.get_industry_calculation_cache_token())
        self.check()
        self.assertEqual(IndustryDataQualification.objects.count(), 2)
        with patch.object(service, "INDUSTRY_RULES_VERSION", "next-bundle"):
            self.assertEqual(
                resolver.get_industry_sde_qualification()["status"], "unqualified"
            )

    def test_navigation_metadata_does_not_trigger_new_download(self):
        self.check()
        self.rows.append(("SolarSystem", 999, "second"))
        with patch.object(service, "_archive_evidence") as parser:
            self.check()
            parser.assert_not_called()
        self.assertEqual(
            resolver.get_industry_sde_qualification()["status"], "compatible_qualified"
        )

    def test_failed_download_backoff_force_and_expired_lease(self):
        with patch.object(
            service, "_download_archive", side_effect=requests.Timeout("temporary")
        ) as download:
            service.check_installed_industry_rules()
            service.check_installed_industry_rules()
            self.assertEqual(download.call_count, 1)
            self.assertEqual(
                resolver.get_industry_sde_qualification()["status"], "unqualified"
            )
        record = IndustryDataQualification.objects.get()
        self.assertEqual(record.status, "error")
        record.status, record.lease_token = "checking", "previous-worker"
        record.next_check_at = timezone.now() + timedelta(minutes=1)
        record.save()
        with patch.object(service, "_archive_evidence") as parser:
            self.check(force=True)
            parser.assert_not_called()
        record.next_check_at = timezone.now() - timedelta(seconds=1)
        record.save()
        self.check()
        record.refresh_from_db()
        self.assertEqual(record.status, "compatible_qualified")
        self.assertEqual(record.lease_token, "")

    def test_duplicate_delivery_during_download_reuses_lease(self):
        original = service._archive_evidence
        nested = []

        def checking(path, build):
            nested.append(self.check(force=True))
            return original(path, build)

        with patch.object(service, "_archive_evidence", side_effect=checking) as parser:
            self.check()
            self.assertEqual(parser.call_count, 1)
        self.assertEqual(nested[0]["builds"], {"456": "checking"})

    def test_result_discarded_when_sde_changes_during_check(self):
        original = service._archive_evidence

        def changed(path, build):
            self.rows = [(name, value, "new-import") for name, value, _ in self.rows]
            return original(path, build)

        with patch.object(service, "_archive_evidence", side_effect=changed):
            self.check()
        record = IndustryDataQualification.objects.get()
        self.assertEqual(record.status, "error")
        self.assertEqual(record.evidence, {})
        self.assertEqual(
            resolver.get_industry_sde_qualification()["status"], "unqualified"
        )

    def test_wrong_archive_and_partial_import_do_not_qualify(self):
        make_archive(self.path, build=999)
        self.check()
        self.assertEqual(IndustryDataQualification.objects.get().status, "error")
        self.rows = self.rows[:-1]
        with patch.object(service, "_download_archive") as http:
            self.assertEqual(
                service.check_installed_industry_rules()["status"], "unqualified"
            )
            http.assert_not_called()

    def test_mixed_builds_each_need_matching_evidence(self):
        self.rows = [
            (name, 789 if name == "TypeEffect" else build, updated)
            for name, build, updated in self.rows
        ]
        with self.assertRaises(rules.IndustryRulesError):
            self.check()
        evidence = deepcopy(self.reference["qualifications"]["123"])
        with (
            patch.object(service, "_download_archive", return_value="a" * 64),
            patch.object(service, "_archive_evidence", return_value=(evidence, {})),
        ):
            service.check_installed_industry_rules()
        self.assertEqual(
            resolver.get_industry_sde_qualification()["status"], "compatible_qualified"
        )
        self.assertEqual(IndustryDataQualification.objects.count(), 2)

    def test_missing_table_or_corrupted_evidence_fails_closed(self):
        with patch.object(
            IndustryDataQualification.objects,
            "filter",
            side_effect=DatabaseError("offline"),
        ):
            self.assertEqual(
                resolver.get_industry_sde_qualification()["status"], "unqualified"
            )
        self.check()
        IndustryDataQualification.objects.update(evidence_hash="corrupt")
        self.assertEqual(
            resolver.get_industry_sde_qualification()["status"], "unqualified"
        )

    def test_command_status_has_no_network_and_offline_command_qualifies(self):
        with patch.object(service.requests, "get") as http:
            out = io.StringIO()
            call_command("check_industry_rules", status=True, stdout=out)
            self.assertEqual(
                json.loads(out.getvalue())["qualification"]["status"], "unqualified"
            )
            call_command(
                "check_industry_rules", archive=str(self.path), stdout=io.StringIO()
            )
            http.assert_not_called()
        self.rows = [(name, 999, updated) for name, _, updated in self.rows]
        with self.assertRaises(CommandError):
            call_command(
                "check_industry_rules", archive=str(self.path), stdout=io.StringIO()
            )


class ArchiveDownloadTests(SimpleTestCase):
    def test_trusted_url_stream_limits_redirects_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "download.zip"
            response = Mock(status_code=200, headers={})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.iter_content.return_value = [b"archive"]
            with patch.object(service.requests, "get", return_value=response) as http:
                digest = service._download_archive(456, target)
                self.assertEqual(digest, hashlib.sha256(b"archive").hexdigest())
                self.assertEqual(
                    http.call_args.args[0], service.ARCHIVE_URL.format(build=456)
                )
                self.assertFalse(http.call_args.kwargs["allow_redirects"])
                for status in (302, 403, 404, 429, 500):
                    response.status_code = status
                    with self.assertRaises(rules.IndustryRulesError):
                        service._download_archive(456, target)
                response.status_code = 200
                response.headers = {"Content-Length": "100"}
                with patch.object(service, "MAX_ARCHIVE_BYTES", 1):
                    with self.assertRaises(rules.IndustryRulesError):
                        service._download_archive(456, target)
                response.headers = {}
                with patch.object(service, "MAX_ARCHIVE_BYTES", 1):
                    with self.assertRaises(rules.IndustryRulesError):
                        service._download_archive(456, target)
