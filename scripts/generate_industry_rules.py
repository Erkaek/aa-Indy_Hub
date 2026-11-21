"""Build an offline CCP rule candidate and diff; never replace oracle snapshots."""

# Standard Library
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# AA Example App
from indy_hub.services.industry_rule_projection import (  # noqa: E402
    FILES,
    build_bundle,
    difference_report,
    fingerprint,
    qualify_projection,
    read_archive,
)
from indy_hub.services.industry_rules import (  # noqa: E402
    IndustryRulesError,
    read_industry_json,
    validate_industry_bundle,
)

__all__ = [
    "FILES",
    "build_bundle",
    "difference_report",
    "fingerprint",
    "generate",
    "qualify_projection",
    "read_archive",
]


def generate(archive, comparison_archive, output_dir, *, reference=None):
    output_dir = Path(output_dir).resolve()
    if output_dir == REPOSITORY_ROOT or REPOSITORY_ROOT in output_dir.parents:
        raise IndustryRulesError(
            "Candidate output must be outside the checkout; historical references are never overwritten"
        )
    targets = (output_dir / "ccp_rules.json", output_dir / "industry_rules_diff.json")
    if any(target.exists() for target in targets):
        raise IndustryRulesError(
            "Candidate outputs already exist; choose a new output directory"
        )
    build, datasets, hashes = read_archive(Path(archive))
    content, evidence = build_bundle(datasets, build)
    del datasets
    qualifications = {str(build): evidence}
    comparison_diff = {}
    if comparison_archive:
        other_build, other_data, _ = read_archive(Path(comparison_archive))
        other_content, other_evidence = build_bundle(other_data, other_build)
        del other_data
        other_evidence["status"] = qualify_projection(evidence, other_evidence)
        if other_evidence["status"] == "compatible_conditional":
            other_evidence["source_attribute_values"] = {
                source_id: source["attribute_values"]
                for source_id, source in other_content["sources"].items()
                if source["attribute_values"]
                != content["sources"][source_id]["attribute_values"]
            }
        qualifications[str(other_build)] = other_evidence
        comparison_diff = difference_report(content, other_content)
    content["qualifications"] = qualifications
    content["compatible_builds"] = sorted(
        int(key)
        for key, row in qualifications.items()
        if row["status"] in ("compatible_qualified", "compatible_conditional")
    )
    content["rules_version"] = f"ccp-{build}-{fingerprint(content)[:12]}"
    content["provenance"] = {
        "documentation": "https://developers.eveonline.com/docs/services/static-data/",
        "archives": [
            f"https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-{key}-jsonl.zip"
            for key in sorted(qualifications)
        ],
        "entry_sha256": hashes,
        "archive_sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
    }
    validate_industry_bundle(content)
    baseline_path = (
        Path(reference)
        if reference
        else REPOSITORY_ROOT / "indy_hub/data/industry/ccp_rules.json"
    )
    baseline = (
        read_industry_json(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else {}
    )
    report = {
        "source_build": build,
        "candidate_version": content["rules_version"],
        "qualifications": qualifications,
        "reference": str(baseline_path),
        "reference_diff": difference_report(baseline, content),
        "comparison_diff": comparison_diff,
        "rig_count": sum(bool(rules) for rules in content["rig_rules"].values()),
        "known_rig_count": len(content["rig_rules"]),
        "hull_count": len(content["hull_rules"]),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for target, value in zip(targets, (content, report)):
        with target.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True, indent=4) + "\n")
    print(
        json.dumps(
            {
                "version": content["rules_version"],
                "output_dir": str(output_dir),
                "rigs": report["rig_count"],
                "hulls": report["hull_count"],
                "qualifications": {
                    key: row["status"] for key, row in qualifications.items()
                },
            }
        )
    )
    return content, report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--comparison-archive", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    try:
        generate(
            args.archive,
            args.comparison_archive,
            args.output_dir,
            reference=args.reference,
        )
    except (
        IndustryRulesError,
        OSError,
        KeyError,
        TypeError,
        zipfile.BadZipFile,
    ) as error:
        parser.exit(2, f"Industry rule candidate rejected: {error}\n")
