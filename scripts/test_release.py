"""Offline failure-path tests for the release publication gate."""

# Standard Library
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

# Third Party
# First Party
from check_release import check_archives, check_sources


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="indyhub-release-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "indy_hub").mkdir()
        (self.root / "indy_hub/__init__.py").write_text('__version__ = "1.19.0"\n')
        (self.root / "package.json").write_text(json.dumps({"version": "1.19.0"}))
        self.lock = {"version": "1.19.0", "packages": {"": {"version": "1.19.0"}}}
        self.write_lock()
        self.changelog = self.root / "CHANGELOG.md"
        self.changelog.write_text(
            "## [Unreleased]\n\n## [1.19.0] - 2026-11-24\n\n### Fixed\n\n- Corrected behavior.\n\n## [1.18.3] - 2026-08-27\n\n- Older notes.\n"
        )

    def write_lock(self):
        (self.root / "package-lock.json").write_text(json.dumps(self.lock))

    def archives(self, wheel_version="1.19.0", source_version="1.19.0"):
        with zipfile.ZipFile(self.root / "indy_hub.whl", "w") as archive:
            archive.writestr(
                "indy_hub.dist-info/METADATA",
                f"Name: indy-hub\nVersion: {wheel_version}\n",
            )
        content = f"Name: indy-hub\nVersion: {source_version}\n".encode()
        with tarfile.open(self.root / "indy_hub.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("indy_hub/PKG-INFO")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

    def test_matching_tags_and_notes_exclude_previous_release(self):
        for tag in (None, "1.19.0", "v1.19.0"):
            version, notes = check_sources(self.root, tag)
            self.assertEqual(version, "1.19.0")
            self.assertIn("Corrected behavior.", notes)
            self.assertNotIn("Older notes.", notes)

    def test_wrong_and_prerelease_tags_fail(self):
        for tag in ("1.18.3", "1.19.0rc1", "v1.19.0rc1", "1.19.0-extra"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                check_sources(self.root, tag)

    def test_node_package_version_mismatch_fails(self):
        (self.root / "package.json").write_text('{"version":"1.18.3"}')
        with self.assertRaises(ValueError):
            check_sources(self.root)

    def test_each_lock_version_must_match(self):
        for key in ("top", "root"):
            with self.subTest(key=key):
                if key == "top":
                    self.lock["version"] = "1.18.3"
                else:
                    self.lock["version"] = "1.19.0"
                    self.lock["packages"][""]["version"] = "1.18.3"
                self.write_lock()
                with self.assertRaises(ValueError):
                    check_sources(self.root)

    def test_invalid_or_duplicate_release_notes_fail(self):
        for content in (
            "## [Unreleased]\n\n- Not released.\n",
            "## [1.19.0]\n\n- Undated.\n",
            "## [1.19.0] - 2026-02-30\n\n- Invalid date.\n",
            "## [1.19.0] - 2026-11-24\n\n### Fixed\n",
            "## [1.19.0] - 2026-11-24\n\n- First.\n\n## [1.19.0] - 2026-11-24\n\n- Duplicate.\n",
            "## [1.20.0] - 2026-11-24\n\n- Newer.\n\n## [1.19.0] - 2026-11-24\n\n- Old.\n",
        ):
            with self.subTest(content=content):
                self.changelog.write_text(content)
                with self.assertRaises(ValueError):
                    check_sources(self.root)

    def test_prerelease_source_version_fails(self):
        (self.root / "indy_hub/__init__.py").write_text('__version__ = "1.19.0rc1"\n')
        with self.assertRaises(ValueError):
            check_sources(self.root)

    def test_both_built_versions_match(self):
        self.archives()
        check_archives(self.root, "1.19.0")

    def test_stale_wheel_or_source_archive_fails(self):
        for wheel, source in (("1.18.3", "1.19.0"), ("1.19.0", "1.18.3")):
            with self.subTest(wheel=wheel, source=source):
                self.archives(wheel, source)
                with self.assertRaises(ValueError):
                    check_archives(self.root, "1.19.0")

    def test_incomplete_or_ambiguous_archives_fail(self):
        with self.assertRaises(ValueError):
            check_archives(self.root, "1.19.0")
        self.archives()
        (self.root / "old.whl").write_bytes((self.root / "indy_hub.whl").read_bytes())
        with self.assertRaises(ValueError):
            check_archives(self.root, "1.19.0")


if __name__ == "__main__":
    unittest.main()
