"""REST normalization retains the existing strict industrial validator."""

# Standard Library
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

# Django
from django.test import SimpleTestCase

# AA Example App
from indy_hub.services import industry_rest_projection as rest
from indy_hub.services.industry_rule_projection import FILES, read_runtime_archive
from indy_hub.services.industry_rules import IndustryRulesError
from indy_hub.tests.industry.test_industry_rules_schema import official_sample


class RestProjectionTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "projection.zip"
        self.release = "20261120T165826Z"
        self.index = {"release": self.release, "buildNumber": 456, "tables": {}}
        self.responses = {}
        sample = official_sample()
        for i, name in enumerate(FILES[:8], 1):
            version = f"{i:064x}"
            self.index["tables"][name[:-6]] = {"version": version}
            base = f"https://sde.evetech.net/blobs/{version}"
            self.responses[base + ".schema.json"] = json.dumps(
                {
                    "type": "object",
                    "x-sde-key": "id",
                    "properties": {"id": {"type": "integer"}},
                }
            ).encode()
            self.responses[base + ".jsonl"] = "".join(
                json.dumps(
                    {"id": row["_key"], **{k: v for k, v in row.items() if k != "_key"}}
                )
                + "\n"
                for row in sample[name].values()
            ).encode()

    def fake_download(self, url, target, budget):
        data = (
            json.dumps(self.index).encode()
            if url.endswith("/")
            else self.responses[url]
        )
        Path(target).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_pinned_rest_records_pass_the_existing_archive_reader(self):
        with patch.object(rest, "download", side_effect=self.fake_download) as get:
            digest, provenance = rest.download_projection(456, self.release, self.path)
        data, hashes = read_runtime_archive(self.path, 456)
        self.assertEqual(data["groups.jsonl"], official_sample()["groups.jsonl"])
        self.assertEqual(len(hashes), 8)
        self.assertEqual(len(digest), 64)
        self.assertEqual(provenance["release"], self.release)
        self.assertEqual(len(provenance["tables"]), 8)
        self.assertEqual(get.call_count, 17)
        self.assertTrue(
            all("latest" not in call.args[0] for call in get.call_args_list)
        )

    def test_known_empty_modifiers_match_zip_omission(self):
        version = self.index["tables"]["industryModifierSources"]["version"]
        url = f"https://sde.evetech.net/blobs/{version}.jsonl"
        rows = [json.loads(line) for line in self.responses[url].splitlines()]
        for row in rows:
            for key, value in row.items():
                if key != "id" and isinstance(value, dict):
                    value.setdefault("cost", [])
        self.responses[url] = "".join(json.dumps(row) + "\n" for row in rows).encode()
        with patch.object(rest, "download", side_effect=self.fake_download):
            rest.download_projection(456, self.release, self.path)
        data, _ = read_runtime_archive(self.path, 456)
        self.assertEqual(
            data["industryModifierSources.jsonl"],
            official_sample()["industryModifierSources.jsonl"],
        )

    def test_empty_filter_lists_match_omitted_zip_fields(self):
        version = self.index["tables"]["industryTargetFilters"]["version"]
        url = f"https://sde.evetech.net/blobs/{version}.jsonl"
        rows = [json.loads(line) for line in self.responses[url].splitlines()]
        for row in rows:
            row.setdefault("groupIDs", [])
            row.setdefault("categoryIDs", [])
        self.responses[url] = "".join(json.dumps(row) + "\n" for row in rows).encode()
        with patch.object(rest, "download", side_effect=self.fake_download):
            rest.download_projection(456, self.release, self.path)
        data, _ = read_runtime_archive(self.path, 456)
        self.assertEqual(
            data["industryTargetFilters.jsonl"],
            official_sample()["industryTargetFilters.jsonl"],
        )

    def test_wrong_build_missing_table_and_invalid_release_fail_closed(self):
        with patch.object(rest, "download", side_effect=self.fake_download):
            with self.assertRaises(IndustryRulesError):
                rest.download_projection(999, self.release, self.path)
            self.index["tables"].pop("types")
            with self.assertRaises(IndustryRulesError):
                rest.download_projection(456, self.release, self.path)
            with self.assertRaises(IndustryRulesError):
                rest.download_projection(456, "latest", self.path)

    def test_normalization_preserves_unknown_semantics_for_validator_rejection(self):
        # AA Example App
        from indy_hub.services.industry_qualification import _archive_evidence

        version = self.index["tables"]["industryTargetFilters"]["version"]
        url = f"https://sde.evetech.net/blobs/{version}.jsonl"
        rows = [json.loads(line) for line in self.responses[url].splitlines()]
        rows[0]["futureSemantics"] = True
        self.responses[url] = "".join(json.dumps(row) + "\n" for row in rows).encode()
        with patch.object(rest, "download", side_effect=self.fake_download):
            rest.download_projection(456, self.release, self.path)
        with self.assertRaises(IndustryRulesError):
            _archive_evidence(self.path, 456)

    def test_redirects_and_stream_limits_are_not_bypassed(self):
        response = Mock(status_code=302)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(rest.requests, "get", return_value=response) as get:
            with self.assertRaises(IndustryRulesError):
                rest.download(
                    "https://sde.evetech.net/test",
                    self.path,
                    [0, rest.time.monotonic()],
                )
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            response.status_code = 200
            response.iter_content.return_value = [b"too large"]
            with (
                patch.object(rest, "MAX_DATA_BYTES", 1),
                self.assertRaises(IndustryRulesError),
            ):
                rest.download(
                    "https://sde.evetech.net/test",
                    self.path,
                    [0, rest.time.monotonic()],
                )
