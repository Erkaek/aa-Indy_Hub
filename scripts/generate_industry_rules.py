"""Build an offline CCP rule candidate and diff; never replace oracle snapshots."""

# Standard Library
import argparse
import collections
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# AA Example App
from indy_hub.services.industry_rules import (  # noqa: E402
    _ACTIVITIES,
    _FIELDS,
    FITTING_ATTRIBUTE_IDS,
    SECURITY_ATTRIBUTE_IDS,
    IndustryRulesError,
    _identifier,
    _number,
    _object,
    industry_projection_evidence,
    read_industry_json,
    validate_industry_bundle,
    validate_industry_filter,
)

FILES = (
    "industryModifierSources.jsonl",
    "industryTargetFilters.jsonl",
    "dogmaEffects.jsonl",
    "dogmaAttributes.jsonl",
    "typeDogma.jsonl",
    "types.jsonl",
    "groups.jsonl",
    "categories.jsonl",
    "blueprints.jsonl",
)
DESCRIPTIVE_SOURCE_FIELDS = ("name", "description", "displayName")
MODIFIER_FIELDS = (
    "domain",
    "func",
    "modifiedAttributeID",
    "modifyingAttributeID",
    "operation",
)


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_archive(archive):
    match = re.search(r"(\d+)-jsonl\.zip$", archive.name)
    if not match:
        raise IndustryRulesError(
            "Use the original CCP archive filename including its build"
        )
    datasets, hashes = {}, {}
    with zipfile.ZipFile(archive) as source:
        for name in FILES:
            blob = source.read(name)
            hashes[name] = hashlib.sha256(blob).hexdigest()
            records = {}
            for line_number, line in enumerate(blob.splitlines(), 1):
                record = read_industry_json(line.decode("utf-8"))
                path = f"{name}:{line_number}"
                if not isinstance(record, dict) or "_key" not in record:
                    raise IndustryRulesError("Missing record identifier", path=path)
                identifier = record["_key"]
                # The complete types archive contains CCP's zero sentinel;
                # references used by industry rules remain strictly positive.
                if type(identifier) is not int or identifier < 0:
                    raise IndustryRulesError(
                        "Invalid official record identifier", path=path
                    )
                if identifier in records:
                    raise IndustryRulesError("Duplicate official record", path=path)
                records[identifier] = record
            datasets[name] = records
    return int(match[1]), datasets, hashes


def _source_rows(source, filters):
    for activity, metrics in source.items():
        if activity == "_key" or activity in DESCRIPTIVE_SOURCE_FIELDS:
            continue
        path = f"source.{source['_key']}.{activity}"
        if activity not in _ACTIVITIES or not isinstance(metrics, dict):
            raise IndustryRulesError("Unsupported source activity", path=path)
        for metric, modifiers in metrics.items():
            if (
                metric not in _FIELDS
                or not isinstance(modifiers, list)
                or not modifiers
            ):
                raise IndustryRulesError(
                    "Unsupported or empty source metric", path=path
                )
            seen = set()
            for modifier in modifiers:
                _object(
                    modifier,
                    path,
                    required=("dogmaAttributeID",),
                    optional=("filterID",),
                )
                attribute_id = _identifier(modifier["dogmaAttributeID"], path)
                filter_id = modifier.get("filterID")
                if filter_id is not None:
                    _identifier(filter_id, path)
                    if filter_id not in filters:
                        raise IndustryRulesError(
                            f"Missing referenced filter {filter_id}", path=path
                        )
                association = (attribute_id, filter_id)
                if association in seen:
                    raise IndustryRulesError(
                        "Colliding official source association", path=path
                    )
                seen.add(association)
                yield activity, metric, modifier


def _effect_metadata(effect):
    return {"name": effect["name"], "modifier_info": effect.get("modifierInfo", [])}


def _attribute_definition(attribute):
    # Description/displayName are descriptive and cannot change the calculation.
    _object(
        attribute,
        f"dogmaAttributes.{attribute.get('_key')}",
        required=("_key", "name"),
        optional=(
            "attributeCategoryID",
            "dataType",
            "defaultValue",
            "description",
            "displayName",
            "displayWhenZero",
            "highIsGood",
            "published",
            "stackable",
            "unitID",
            "iconID",
        ),
    )
    return {
        key: attribute[key]
        for key in (
            "_key",
            "name",
            "unitID",
            "defaultValue",
            "dataType",
            "highIsGood",
            "stackable",
        )
        if key in attribute
    }


def _source_metadata(item, kind, dogma, effects):
    source_id, path = item["_key"], f"{kind}.{item['_key']}"
    type_dogma = dogma.get(source_id)
    if type_dogma is None:
        raise IndustryRulesError("Missing selected source Dogma", path=path)
    _object(
        type_dogma,
        path,
        required=("_key",),
        optional=("dogmaAttributes", "dogmaEffects"),
    )
    values, source_effects = {}, {}
    for entry in type_dogma.get("dogmaAttributes", []):
        _object(entry, path, required=("attributeID", "value"))
        attr_id = _identifier(entry["attributeID"], path)
        if attr_id in values:
            raise IndustryRulesError("Duplicate type Dogma attribute", path=path)
        _number(entry["value"], path)
        values[attr_id] = entry["value"]
    for entry in type_dogma.get("dogmaEffects", []):
        _object(entry, path, required=("effectID", "isDefault"))
        effect_id = _identifier(entry["effectID"], path)
        if effect_id not in effects or str(effect_id) in source_effects:
            raise IndustryRulesError("Missing or duplicate Dogma effect", path=path)
        source_effects[str(effect_id)] = _effect_metadata(effects[effect_id])
    names = [row["name"].casefold() for row in source_effects.values()]
    if len(names) != len(set(names)):
        raise IndustryRulesError("Colliding Dogma effect names", path=path)
    return values, source_effects


def _rig_rule(source_id, activity, metric, modifier, source_effects, effects):
    path = f"rig.{source_id}"
    matches = [
        (effects[int(effect_id)], info)
        for effect_id in source_effects
        for info in effects[int(effect_id)].get("modifierInfo", [])
        if info.get("modifiedAttributeID") == modifier["dogmaAttributeID"]
    ]
    if len(matches) != 1:
        raise IndustryRulesError(
            "Missing or ambiguous effect/source binding", path=path
        )
    effect, info = matches[0]
    _object(info, path, required=MODIFIER_FIELDS)
    if type(info["operation"]) is not int or (
        info["domain"],
        info["func"],
        info["operation"],
    ) != ("structureID", "ItemModifier", 6):
        raise IndustryRulesError(
            "Unsupported industrial Dogma operation/domain/function", path=path
        )
    return {
        "activity": activity,
        "metric": metric,
        "modifying_attribute_id": _identifier(info["modifyingAttributeID"], path),
        "target_filter_id": modifier.get("filterID"),
        "effect_id": effect["_key"],
        "effect_name": effect["name"],
        "modified_attribute_id": modifier["dogmaAttributeID"],
        "operation": info["operation"],
        "domain": info["domain"],
        "numeric_convention": "negative_percent",
    }


def _validate_attribute_values(kind, source_id, required, security, values, attributes):
    path = f"{kind}.{source_id}"
    for attribute_id in sorted(required):
        if attribute_id not in values or attribute_id not in attributes:
            raise IndustryRulesError(
                f"Missing expected attribute {attribute_id}", path=path
            )
        definition = attributes[attribute_id]
        if attribute_id in security:
            _number(values[attribute_id], path, minimum=0, strict_minimum=True)
            if definition.get("unitID") != 104:
                raise IndustryRulesError(
                    "Unsupported security multiplier unit", path=path
                )
        elif kind == "rig":
            _number(values[attribute_id], path, minimum=-100, maximum=0)
            if definition.get("unitID") != 105:
                raise IndustryRulesError("Unsupported rig percentage unit", path=path)
            if (
                abs(values[attribute_id])
                * max((values[s] for s in security), default=1)
                >= 100
            ):
                raise IndustryRulesError(
                    "Rig reduction must remain below 100 percent", path=path
                )
        else:
            _number(
                values[attribute_id], path, minimum=0, maximum=1, strict_minimum=True
            )
            if definition.get("unitID") not in (None, 104):
                raise IndustryRulesError("Unsupported hull multiplier unit", path=path)


def _build_bundle(datasets, build):
    """Return validated source data and its mathematical qualification evidence."""
    sources, filters, effects, attributes, dogma, types, groups, categories = (
        datasets[name] for name in FILES[:8]
    )
    for key, target in filters.items():
        validate_industry_filter(target, f"industryTargetFilters.{key}")
        for field, table in (("groupIDs", groups), ("categoryIDs", categories)):
            if any(identifier not in table for identifier in target.get(field, [])):
                raise IndustryRulesError(
                    "Unknown filter group/category", path=f"industryTargetFilters.{key}"
                )
    for key, source in sources.items():
        if source.get("_key") != key:
            raise IndustryRulesError("Source identifier mismatch")
        list(_source_rows(source, filters))
    rigs, hulls = [], []
    for item in types.values():
        if not item.get("published"):
            continue
        group = groups.get(item.get("groupID"))
        if group is None:
            raise IndustryRulesError(
                "Published type has no group", path=f"types.{item['_key']}"
            )
        if group.get("categoryID") == 65:
            hulls.append(item)
        if group.get("categoryID") == 66 and item["groupID"] != 1984:
            if any(
                row["effectID"] == 2663
                for row in dogma.get(item["_key"], {}).get("dogmaEffects", [])
            ):
                rigs.append(item)
    rigs.sort(key=lambda row: row["_key"])
    hulls.sort(key=lambda row: row["_key"])
    specific, hull_rules, metadata, industrial_effects, definitions = {}, {}, {}, {}, {}
    bindings = collections.defaultdict(list)
    for kind, items in (("rig", rigs), ("hull", hulls)):
        for item in items:
            source_id, path = item["_key"], f"{kind}.{item['_key']}"
            values, source_effects = _source_metadata(item, kind, dogma, effects)
            security = (
                sorted(set(SECURITY_ATTRIBUTE_IDS) & values.keys())
                if kind == "rig"
                else []
            )
            required_attributes = set(security)
            records = []
            if kind == "rig":
                specific[str(source_id)] = {}
            for activity, metric, modifier in _source_rows(
                sources.get(source_id, {"_key": source_id}), filters
            ):
                if kind == "hull":
                    if "filterID" in modifier:
                        raise IndustryRulesError(
                            "Unsupported conditional hull source", path=path
                        )
                    if any(
                        row["activity"] == activity and row["metric"] == metric
                        for row in records
                    ):
                        raise IndustryRulesError(
                            "Colliding hull association", path=path
                        )
                    required_attributes.add(modifier["dogmaAttributeID"])
                    records.append(
                        {
                            "activity": activity,
                            "metric": metric,
                            "attribute_id": modifier["dogmaAttributeID"],
                            "numeric_convention": "multiplier",
                        }
                    )
                else:
                    rule = _rig_rule(
                        source_id, activity, metric, modifier, source_effects, effects
                    )
                    name = rule["effect_name"].casefold()
                    if name in specific[str(source_id)]:
                        raise IndustryRulesError(
                            "Colliding rig/effect association", path=f"{path}.{name}"
                        )
                    specific[str(source_id)][name] = rule
                    required_attributes.add(rule["modifying_attribute_id"])
                    bindings[name].append(rule)
                    industrial_effects[str(rule["effect_id"])] = source_effects[
                        str(rule["effect_id"])
                    ]
                    definitions[str(modifier["dogmaAttributeID"])] = (
                        _attribute_definition(attributes[modifier["dogmaAttributeID"]])
                    )
            if kind == "hull":
                hull_rules[str(source_id)] = records
            _validate_attribute_values(
                kind, source_id, required_attributes, security, values, attributes
            )
            for attribute_id in required_attributes:
                definitions[str(attribute_id)] = _attribute_definition(
                    attributes[attribute_id]
                )
            metadata[str(source_id)] = {
                "kind": kind,
                "has_industry_source": source_id in sources,
                "effects": source_effects,
                "attribute_values": {
                    str(a): values[a] for a in sorted(required_attributes)
                },
                "security_attribute_ids": security,
                "group_id": item["groupID"],
                "fitting_attribute_values": {
                    str(a): values[a] for a in FITTING_ATTRIBUTE_IDS if a in values
                },
            }
    generic = {}
    for name, entries in sorted(bindings.items()):
        counts = collections.Counter(
            json.dumps(rule, sort_keys=True) for rule in entries
        )
        generic[name] = json.loads(
            sorted(counts, key=lambda entry: (-counts[entry], entry))[0]
        )
    data = {
        "schema_version": 2,
        "sde_build": build,
        "compatible_builds": [build],
        "rules": generic,
        "rig_rules": specific,
        "hull_rules": hull_rules,
        "sources": metadata,
        "industrial_effects": industrial_effects,
        "rig_security_attribute_ids": {
            str(item["_key"]): metadata[str(item["_key"])]["security_attribute_ids"]
            for item in rigs
        },
        "filters": {str(key): row for key, row in sorted(filters.items())},
        "group_names": {
            str(g): groups[g]["name"]["en"]
            for row in filters.values()
            for g in row.get("groupIDs", [])
        },
        "category_names": {
            str(c): categories[c]["name"]["en"]
            for row in filters.values()
            for c in row.get("categoryIDs", [])
        },
        "attribute_definitions": definitions,
    }
    return data, industry_projection_evidence(data)


def build_bundle(datasets, build):
    try:
        return _build_bundle(datasets, build)
    except IndustryRulesError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IndustryRulesError(f"Malformed official industry data: {exc}") from exc


def qualify_projection(reference, candidate):
    if reference["projection_sha256"] == candidate["projection_sha256"]:
        return "compatible_qualified"
    if reference["semantics_sha256"] == candidate["semantics_sha256"]:
        return "compatible_conditional"
    return "incompatible"


def difference_report(reference, candidate):
    changes = {}
    for section in sorted(set(reference) | set(candidate)):
        before, after = reference.get(section), candidate.get(section)
        if before == after:
            continue
        if isinstance(before, dict) and isinstance(after, dict):
            changes[section] = {
                "added": sorted(after.keys() - before.keys()),
                "removed": sorted(before.keys() - after.keys()),
                "changed": sorted(
                    key
                    for key in before.keys() & after.keys()
                    if before[key] != after[key]
                ),
            }
        else:
            changes[section] = {"before": before, "after": after}
    return changes


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
