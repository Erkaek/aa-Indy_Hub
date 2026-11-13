"""Build a reviewable Craft browser fixture from a local official CCP ZIP.

The fixed selection covers the Confessor manufacturing tree, a fuel block,
basic/advanced ship counterexamples, fitted structures/rigs and a neutral
Astrahus. Blueprint activities are limited to manufacturing; item/group names
are projected to English. Dogma records remain intact and effects retain their
numeric IDs and names. Missing recipe dependencies require a selection review.

No historical reference is overwritten. --output-dir must receive new candidate
and diff files. A different build requires its independently obtained archive
SHA-256 via --expected-archive-sha256. No Django, SDE import or network is used.
"""

# Standard Library
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "indy_hub/tests/craft/fixtures/ccp_browser_sde.json"
)
PINNED_BUILD = 3670638
PINNED_ARCHIVE_SHA256 = (
    "18e864e143043e8f0f3b8e8fd06525c13b5818d819272643d2726c7cf15a3aa5"
)
TYPE_IDS = (
    34,
    35,
    36,
    37,
    38,
    44,
    603,
    621,
    3683,
    3689,
    4246,
    4316,
    9832,
    9848,
    11478,
    11890,
    16272,
    16273,
    16275,
    17889,
    20187,
    23055,
    23057,
    28848,
    29992,
    29993,
    29994,
    29995,
    30002,
    30003,
    30018,
    30019,
    30021,
    30022,
    30024,
    30248,
    30251,
    30252,
    30254,
    30258,
    30259,
    30268,
    30269,
    30270,
    30271,
    30303,
    30304,
    30305,
    30306,
    30307,
    30308,
    30309,
    30310,
    30311,
    30464,
    30465,
    30466,
    30467,
    30476,
    30477,
    30478,
    30479,
    34317,
    34318,
    34396,
    34397,
    35825,
    37457,
    43854,
    43856,
    43874,
    46495,
    57458,
    57495,
)
# Keep the historical order, including the later neutral-structure addition.
ADDITIONAL_TYPE_IDS = (35832,)
BLUEPRINT_IDS = (
    34318,
    4316,
    30467,
    30003,
    30477,
    30465,
    29993,
    30479,
    34397,
    29995,
    11890,
)


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _read_selected(archive, name, wanted, hashes):
    """Hash the entire source entry, retaining only the selected records."""
    digest = hashlib.sha256()
    selected = {}
    with archive.open(name) as stream:
        for line in stream:
            digest.update(line)
            row = json.loads(line)
            if row["_key"] in wanted:
                if row["_key"] in selected:
                    raise ValueError(f"Duplicate {name} key {row['_key']}")
                selected[row["_key"]] = row
    hashes[name] = digest.hexdigest()
    return selected


def _require(rows, wanted, name):
    missing = set(wanted) - rows.keys()
    if missing:
        raise ValueError(f"Missing {name} records: {sorted(missing)}")


def build_fixture(archive_path, expected_archive_sha256=None):
    """Return deterministic data and provenance without writing any file."""
    archive_path = Path(archive_path)
    match = re.fullmatch(r"eve-online-static-data-(\d+)-jsonl\.zip", archive_path.name)
    if match is None:
        raise ValueError("Use the original CCP archive filename including its build")
    build = int(match[1])
    expected = expected_archive_sha256
    if expected is None and build == PINNED_BUILD:
        expected = PINNED_ARCHIVE_SHA256
    if expected is None or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("Supply the independently verified archive SHA-256")
    with archive_path.open("rb") as stream:
        archive_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if archive_digest != expected:
        raise ValueError("Archive SHA-256 does not match the expected fingerprint")

    hashes = {}
    ids = TYPE_IDS + ADDITIONAL_TYPE_IDS
    with zipfile.ZipFile(archive_path) as archive:
        types = _read_selected(archive, "types.jsonl", set(ids), hashes)
        _require(types, ids, "type")
        main_groups = sorted({types[key]["groupID"] for key in TYPE_IDS})
        group_ids = main_groups + sorted(
            {types[key]["groupID"] for key in ADDITIONAL_TYPE_IDS} - set(main_groups)
        )
        groups = _read_selected(archive, "groups.jsonl", set(group_ids), hashes)
        _require(groups, group_ids, "group")
        blueprints = _read_selected(
            archive, "blueprints.jsonl", set(BLUEPRINT_IDS), hashes
        )
        _require(blueprints, BLUEPRINT_IDS, "blueprint")
        dogma = _read_selected(archive, "typeDogma.jsonl", set(ids), hashes)
        main_effects = {
            entry["effectID"]
            for key in TYPE_IDS
            for entry in dogma.get(key, {}).get("dogmaEffects", [])
        }
        extra_effects = {
            entry["effectID"]
            for key in ADDITIONAL_TYPE_IDS
            for entry in dogma.get(key, {}).get("dogmaEffects", [])
        }
        effect_ids = sorted(main_effects) + sorted(extra_effects - main_effects)
        effects = _read_selected(archive, "dogmaEffects.jsonl", set(effect_ids), hashes)
        _require(effects, effect_ids, "effect")

    selected_blueprints = []
    for key in BLUEPRINT_IDS:
        row = blueprints[key]
        manufacturing = row["activities"].get("manufacturing")
        if not manufacturing or not manufacturing.get("products"):
            raise ValueError(f"Blueprint {key} has no manufacturing product")
        dependencies = {
            item["typeID"]
            for field in ("materials", "products")
            for item in manufacturing.get(field, [])
        }
        if dependencies - set(ids):
            raise ValueError(f"Blueprint {key} dependencies require a selection review")
        selected_blueprints.append(
            {
                "_key": key,
                "activities": {"manufacturing": manufacturing},
                "blueprintTypeID": row["blueprintTypeID"],
                "maxProductionLimit": row["maxProductionLimit"],
            }
        )
    fixture = {
        "sde_build": build,
        "types": [
            {
                "_key": key,
                "groupID": types[key]["groupID"],
                **(
                    {"marketGroupID": types[key]["marketGroupID"]}
                    if "marketGroupID" in types[key]
                    else {}
                ),
                "name": {"en": types[key]["name"]["en"]},
                "portionSize": types[key].get("portionSize", 1),
                "published": types[key]["published"],
                "volume": types[key].get("volume", 0),
            }
            for key in ids
        ],
        "groups": [
            {
                "_key": key,
                "categoryID": groups[key]["categoryID"],
                "name": {"en": groups[key]["name"]["en"]},
            }
            for key in group_ids
        ],
        "blueprints": selected_blueprints,
        "typeDogma": [dogma[key] for key in ids if key in dogma],
        "dogmaEffects": [
            {"_key": key, "name": effects[key]["name"]} for key in effect_ids
        ],
    }
    fixture["provenance"] = {
        "selection_version": 1,
        "archive": f"https://developers.eveonline.com/static-data/tranquility/{archive_path.name}",
        "archive_sha256": archive_digest,
        "entry_sha256": hashes,
        "data_sha256": hashlib.sha256(_canonical(fixture)).hexdigest(),
    }
    return fixture


def compare_fixture(candidate, reference):
    """Describe every changed selected record, including recipe/Dogma changes."""
    sections = {}
    for section in ("types", "groups", "blueprints", "typeDogma", "dogmaEffects"):
        before = {row["_key"]: row for row in reference[section]}
        after = {row["_key"]: row for row in candidate[section]}
        sections[section] = {
            "added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(
                key for key in before.keys() & after.keys() if before[key] != after[key]
            ),
            "order_changed": [row["_key"] for row in reference[section]]
            != [row["_key"] for row in candidate[section]],
        }
    return {
        "reference_build": reference["sde_build"],
        "candidate_build": candidate["sde_build"],
        "data_changed": candidate["sde_build"] != reference["sde_build"]
        or any(
            values["added"]
            or values["removed"]
            or values["changed"]
            or values["order_changed"]
            for values in sections.values()
        ),
        "provenance_changed": candidate.get("provenance")
        != reference.get("provenance"),
        "sections": sections,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-archive-sha256")
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    candidate_path = args.output_dir / "ccp_browser_sde.candidate.json"
    report_path = args.output_dir / "browser_fixture_diff.json"
    for path in (candidate_path, report_path):
        if path.exists() or path.resolve() == args.reference.resolve():
            parser.error(f"Refusing to overwrite {path}")
    candidate = build_fixture(args.archive, args.expected_archive_sha256)
    reference_bytes = args.reference.read_bytes()
    report = compare_fixture(candidate, json.loads(reference_bytes))
    report["reference_sha256"] = hashlib.sha256(reference_bytes).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path, data in ((candidate_path, candidate), (report_path, report)):
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=4) + "\n")
    print(
        json.dumps(
            {
                "candidate": str(candidate_path),
                "report": str(report_path),
                "data_changed": report["data_changed"],
            }
        )
    )


if __name__ == "__main__":
    main()
