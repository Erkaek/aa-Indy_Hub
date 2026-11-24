"""Validate release versions and notes without importing Django or publishing."""

# Standard Library
import argparse
import ast
import json
import re
import tarfile
import zipfile
from datetime import date
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINAL_VERSION = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"


def release_version(root):
    """Read the Python version without executing application startup."""
    tree = ast.parse((root / "indy_hub/__init__.py").read_text())
    versions = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
    ]
    if len(versions) != 1 or not re.fullmatch(FINAL_VERSION, str(versions[0])):
        raise ValueError("Python version must be a single final X.Y.Z version")
    return versions[0]


def check_sources(root, tag=None):
    """Reject stale metadata, wrong tags and absent final changelog entries."""
    version = release_version(root)
    if tag is not None and tag not in {version, f"v{version}"}:
        raise ValueError(f"Tag {tag!r} does not match package version {version}")
    package = json.loads((root / "package.json").read_text())
    lock = json.loads((root / "package-lock.json").read_text())
    for label, actual in (
        ("package.json", package.get("version")),
        ("package-lock.json", lock.get("version")),
        (
            "package-lock.json root package",
            lock.get("packages", {}).get("", {}).get("version"),
        ),
    ):
        if actual != version:
            raise ValueError(f"{label}: {actual!r} differs from {version}")
    changelog = (root / "CHANGELOG.md").read_text()
    entries = list(re.finditer(r"^## \[([^\]]+)\]([^\n]*)$", changelog, re.M))
    matches = [entry for entry in entries if entry[1] == version]
    if len(matches) != 1:
        raise ValueError(f"Changelog needs exactly one entry for {version}")
    entry = matches[0]
    released = [item[1] for item in entries if item[1] != "Unreleased"]
    if not released or released[0] != version:
        raise ValueError("Package version must be the newest changelog entry")
    date_match = re.fullmatch(r" - (\d{4}-\d{2}-\d{2})", entry[2])
    if not date_match:
        raise ValueError("Release changelog heading must include an ISO date")
    date.fromisoformat(date_match[1])
    end = next(
        (item.start() for item in entries if item.start() > entry.start()),
        len(changelog),
    )
    notes = changelog[entry.end() : end].strip()
    if not re.search(r"^- \S", notes, re.M):
        raise ValueError("Release notes must contain at least one change")
    return version, notes


def check_archives(directory, version):
    """Check actual wheel and sdist metadata before any upload is attempted."""
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Release directory needs exactly one wheel and one sdist")
    metadata = []
    with zipfile.ZipFile(wheels[0]) as archive:
        names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            raise ValueError("Wheel needs exactly one package metadata record")
        metadata.append(archive.read(names[0]))
    with tarfile.open(sdists[0]) as archive:
        names = [
            name
            for name in archive.getnames()
            if name.count("/") == 1 and name.endswith("/PKG-INFO")
        ]
        if len(names) != 1:
            raise ValueError("Source archive needs one root PKG-INFO record")
        metadata.append(archive.extractfile(names[0]).read())
    for content in metadata:
        record = BytesParser().parsebytes(content)
        if record["Name"] != "indy-hub" or record["Version"] != version:
            raise ValueError("Built archive name/version differs from checked source")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag", help="Expected final Git tag, optionally prefixed with v"
    )
    parser.add_argument(
        "--dist", type=Path, help="Also verify built wheel/sdist versions"
    )
    parser.add_argument(
        "--notes-output", type=Path, help="Write the checked changelog section"
    )
    args = parser.parse_args()
    try:
        version, notes = check_sources(ROOT, args.tag)
        if args.dist:
            check_archives(args.dist, version)
        if args.notes_output:
            args.notes_output.write_text(notes + "\n")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"PASS: release {version}, synchronized versions and dated release notes")


if __name__ == "__main__":
    main()
