"""Check the published package contracts against actual built archives."""

# Standard Library
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

# Third Party
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

SOURCE_ROOT = Path(__file__).resolve().parents[1]
INDUSTRY_DOCUMENT = "indy_hub/data/industry/INDUSTRY_RULES.md"
INDUSTRY_RUNTIME_FILES = (
    "indy_hub/data/industry/ccp_rules.json",
    INDUSTRY_DOCUMENT,
)
INDUSTRY_TEST_FILES = (
    "indy_hub/tests/craft/fixtures/ccp_browser_sde.json",
    "indy_hub/tests/industry/fixtures/ccp_industry/oracle.json",
    "indy_hub/tests/industry/fixtures/ccp_industry/industryModifierSources.jsonl",
    "indy_hub/tests/industry/fixtures/ccp_industry/industryTargetFilters.jsonl",
    "indy_hub/tests/industry/fixtures/ccp_industry/typeDogma.jsonl",
    "indy_hub/tests/industry/fixtures/ccp_hull_references.json",
    "indy_hub/tests/industry/fixtures/ccp_industry/dogmaEffects.jsonl",
)
INDUSTRY_SOURCE_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "docs/INDUSTRY_RULES.md",
    "docs/UPGRADE_NOTES.md",
    "docs/PROCESS_RESOLUTION_INSTALL.md",
    "docs/deployment/README.md",
    "docs/deployment/RELEASING.md",
    "docs/deployment/indy_hub-worker.conf",
    "scripts/check_distribution.py",
    "scripts/check_release.py",
    "scripts/test_release.py",
    "scripts/generate_industry_rules.py",
    "scripts/generate_craft_browser_fixture.py",
    "scripts/sync_industry_documentation.py",
    "scripts/serve_craft_industry_fixture.py",
    "scripts/test_craft_demand.js",
    "scripts/test_craft_industry_browser.cjs",
    "scripts/test_craft_industry_workflow.cjs",
    "runtests.py",
    "testauth/runner.py",
    "testauth/settings/local.py",
)
INSTALLATION_DOCS = {
    "docs/UPGRADE_NOTES.md",
    "docs/PROCESS_RESOLUTION_INSTALL.md",
    "docs/INDUSTRY_RULES.md",
}


def check_local_notes_excluded(names, prefix=""):
    """Local prompts and audit reports are not published package resources."""
    for name in names:
        relative = name.removeprefix(prefix)
        assert relative != "PROMPTS-CODEX.md", f"Local plan packaged: {name}"
        if relative.startswith("docs/") and relative.endswith(".md"):
            assert relative in INSTALLATION_DOCS or relative.startswith(
                "docs/deployment/"
            ), f"Local report packaged: {name}"


def check_industry_files(names, read, files, prefix=""):
    """Require exact source bytes, including offline fixtures, in each archive."""
    for filename in files:
        archived_name = prefix + filename
        assert archived_name in names, f"Missing distribution file: {filename}"
        assert (
            read(archived_name) == (SOURCE_ROOT / filename).read_bytes()
        ), f"Stale distribution file: {filename}"


def module_files(*, include_tests):
    """Inventory source resources so wheel filtering cannot lose runtime files."""
    files = []
    for path in (SOURCE_ROOT / "indy_hub").rglob("*"):
        relative = path.relative_to(SOURCE_ROOT)
        if not path.is_file() or "__pycache__" in relative.parts:
            continue
        if path.suffix in {".pyc", ".pyo", ".pyd"}:
            continue
        if "temporary_prompt" in relative.parts:
            continue
        if not include_tests and relative.parts[:2] == ("indy_hub", "tests"):
            continue
        files.append(relative.as_posix())
    return files


def main(directory):
    directory = Path(directory)
    (wheel,) = directory.glob("*.whl")
    (sdist,) = directory.glob("*.tar.gz")
    assert (SOURCE_ROOT / INDUSTRY_DOCUMENT).read_bytes() == (
        SOURCE_ROOT / "docs/INDUSTRY_RULES.md"
    ).read_bytes(), "Packaged industry documentation must match docs/INDUSTRY_RULES.md"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        check_local_notes_excluded(names)
        check_industry_files(names, archive.read, module_files(include_tests=False))
        check_industry_files(names, archive.read, INDUSTRY_RUNTIME_FILES)
        assert not any(name.startswith("indy_hub/tests/") for name in names)
        assert any(
            ".dist-info/" in name and name.endswith("/LICENSE") for name in names
        )
        (metadata_name,) = (
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        supported = SpecifierSet(metadata["Requires-Python"])
        assert all(v not in supported for v in ["3.10", "3.11"])
        assert all(v in supported for v in ["3.12", "3.13", "3.14"])
        classifiers = metadata.get_all("Classifier", [])
        assert all(
            f"Programming Language :: Python :: {version}" in classifiers
            for version in ["3.12", "3.13", "3.14"]
        )
        assert not any(
            f"Programming Language :: Python :: {version}" in classifiers
            for version in ["3.10", "3.11"]
        )
        requirements = [
            Requirement(value) for value in metadata.get_all("Requires-Dist")
        ]
        discord = [
            req.name
            for req in requirements
            if req.marker and req.marker.evaluate({"extra": "aadiscordbot"})
        ]
        assert "allianceauth-discordbot" in discord and "aadiscordbot" not in discord
        assert not any("temporary_prompt" in name for name in names)
        assert any(name.endswith("/chartjs/4.6.1/chart.umd.min.js") for name in names)
        assert any(name.endswith("/chartjs/4.6.1/LICENSE.md") for name in names)
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        (prefix,) = {name.split("/", 1)[0] for name in names}
        check_local_notes_excluded(names, prefix=prefix + "/")
        check_industry_files(
            names,
            lambda name: archive.extractfile(name).read(),
            module_files(include_tests=True)
            + list(
                INDUSTRY_RUNTIME_FILES + INDUSTRY_TEST_FILES + INDUSTRY_SOURCE_FILES
            ),
            prefix=prefix + "/",
        )
        assert not any("temporary_prompt" in name for name in names)
        assert not any("/docs/codex/" in name for name in names)
    print(
        "PASS: Python floor, Discord extra, Chart.js/license, "
        "complete runtime wheel without tests; source tests/fixtures, "
        "installation guides and reproducible tools"
    )


if __name__ == "__main__":
    main(sys.argv[1])
