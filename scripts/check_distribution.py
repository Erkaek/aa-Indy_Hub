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
INDUSTRY_FILES = (
    "indy_hub/data/industry/ccp_rules.json",
    INDUSTRY_DOCUMENT,
    "indy_hub/tests/craft/fixtures/ccp_browser_sde.json",
    "indy_hub/tests/industry/fixtures/ccp_industry/oracle.json",
    "indy_hub/tests/industry/fixtures/ccp_industry/industryModifierSources.jsonl",
    "indy_hub/tests/industry/fixtures/ccp_industry/industryTargetFilters.jsonl",
    "indy_hub/tests/industry/fixtures/ccp_industry/typeDogma.jsonl",
    "indy_hub/tests/industry/fixtures/ccp_hull_references.json",
    "indy_hub/tests/industry/fixtures/ccp_industry/dogmaEffects.jsonl",
)
INDUSTRY_SOURCE_FILES = (
    "docs/INDUSTRY_RULES.md",
    "scripts/check_distribution.py",
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


def check_industry_files(names, read, files, prefix=""):
    """Require exact source bytes, including offline fixtures, in each archive."""
    for filename in files:
        archived_name = prefix + filename
        assert archived_name in names, f"Missing distribution file: {filename}"
        assert (
            read(archived_name) == (SOURCE_ROOT / filename).read_bytes()
        ), f"Stale distribution file: {filename}"


def main(directory):
    directory = Path(directory)
    (wheel,) = directory.glob("*.whl")
    (sdist,) = directory.glob("*.tar.gz")
    assert (SOURCE_ROOT / INDUSTRY_DOCUMENT).read_bytes() == (
        SOURCE_ROOT / "docs/INDUSTRY_RULES.md"
    ).read_bytes(), "Packaged industry documentation must match docs/INDUSTRY_RULES.md"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        check_industry_files(names, archive.read, INDUSTRY_FILES)
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
        check_industry_files(
            names,
            lambda name: archive.extractfile(name).read(),
            INDUSTRY_FILES + INDUSTRY_SOURCE_FILES,
            prefix=prefix + "/",
        )
        assert not any("temporary_prompt" in name for name in names)
        assert not any("/docs/codex/" in name for name in names)
    print(
        "PASS: Python floor, Discord extra, Chart.js/license, "
        "CCP rules/fixtures/documentation and reproducible source tools"
    )


if __name__ == "__main__":
    main(sys.argv[1])
