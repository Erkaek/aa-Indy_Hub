"""Pinned CCP industry effects and numeric target filters, without network IO.

Generated from industryModifierSources, industryTargetFilters and Dogma. See
docs/INDUSTRY_RULES.md for provenance and the offline regeneration procedure.
"""

# Standard Library
import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path


class IndustryRulesError(ValueError):
    """An unsupported or incomplete CCP rule, without a Django dependency."""

    def __init__(self, message: str, *, code="invalid_industry_rules", path=""):
        self.code = code
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


def _fail(message, path, code="invalid_industry_rules"):
    raise IndustryRulesError(message, code=code, path=path)


def _object(value, path, *, required=(), optional=()):
    if not isinstance(value, dict):
        _fail("expected an object", path)
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing:
        _fail(f"missing fields {sorted(missing)}", path)
    if unknown:
        _fail(f"unsupported semantic fields {sorted(unknown)}", path)
    return value


def _identifier(value, path):
    if type(value) is not int or value <= 0:
        _fail("expected a positive integer identifier", path)
    return value


def _identifier_key(value, path):
    if not isinstance(value, str) or not value.isdigit():
        _fail("expected a canonical numeric identifier key", path)
    identifier = _identifier(int(value), path)
    if str(identifier) != value:
        _fail("expected a canonical numeric identifier key", path)
    return identifier


def _identifiers(value, path):
    if not isinstance(value, list):
        _fail("expected an identifier list", path)
    for entry in value:
        _identifier(entry, path)
    if len(value) != len(set(value)):
        _fail("duplicate identifiers", path)
    return value


def _number(value, path, *, minimum=None, maximum=None, strict_minimum=False):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail("expected a finite number", path)
    if minimum is not None and (value < minimum or strict_minimum and value == minimum):
        _fail(f"number outside supported range above {minimum}", path)
    if maximum is not None and value > maximum:
        _fail(f"number exceeds supported maximum {maximum}", path)


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON association {key!r}", "JSON")
        result[key] = value
    return result


def read_industry_json(text: str):
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda value: _fail(
                f"non-finite JSON number {value}", "JSON"
            ),
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise IndustryRulesError(f"Cannot decode CCP industry rules: {exc}") from exc


_BUNDLE_PATH = Path(__file__).resolve().parents[1] / "data/industry/ccp_rules.json"
try:
    _RAW = _BUNDLE_PATH.read_bytes()
    _DATA = read_industry_json(_RAW.decode("utf-8"))
    _LOAD_ERROR = None
except (OSError, UnicodeError, IndustryRulesError) as exc:
    _RAW = str(exc).encode()
    _DATA = {}
    _LOAD_ERROR = IndustryRulesError(f"Cannot load packaged CCP industry rules: {exc}")
# Importing the application must not disable unrelated features for bad rules.
# This stable token also prevents an invalid bundle from reusing valid caches.
INDUSTRY_RULES_VERSION = (
    _DATA.get("rules_version")
    if isinstance(_DATA, dict) and isinstance(_DATA.get("rules_version"), str)
    else f"invalid-{hashlib.sha256(_RAW).hexdigest()[:16]}"
)
INDUSTRY_RULES_SDE_BUILD = _DATA.get("sde_build") if isinstance(_DATA, dict) else None
_VALIDATED = False

# Historical spellings emitted by earlier integrations and saved configurations.
# Their target filters are the current official numeric filters, never name tests.
_ALIASES = {
    "rigadvsmallshipmanufacturematerialbonus": "rigadvsmshipmanufacturematerialbonus",
    "rigadvsmallshipmanufacturetimebonus": "rigadvsmshipmanufacturetimebonus",
    "rigmedshipmanufacturematerialbonus": "rigmediumshipmanufacturematerialbonus",
    "rigmedshipmanufacturetimebonus": "rigmediumshipsmanufacturetimebonus",
    "rigadvlargeshipmanufacturematerialbonus": "rigadvlarshipmanufacturematerialbonus",
    "rigadvlargeshipmanufacturetimebonus": "rigadvlarshipmanufacturetimebonus",
    "rigadvallshipmanufacturematerialbonus": "rigallshipmanufacturematerialbonus",
    "rigadvallshipmanufacturetimebonus": "rigallshipmanufacturetimebonus",
    "rigcomponentmanufacturematerialbonus": "rigbascapcompmanufacturematerialbonus",
    "rigcomponentmanufacturetimebonus": "rigbascapcompmanufacturetimebonus",
    "rigbiochemicalreactioncostbonus": "rigreactionbiomatbonus",
    "rigbiochemicalreactiontimebonus": "rigreactionbiotimebonus",
    "rigcompositereactioncostbonus": "rigreactioncompmatbonus",
    "rigcompositereactiontimebonus": "rigreactioncomptimebonus",
    "righybridreactioncostbonus": "rigreactionhybmatbonus",
    "righybridreactiontimebonus": "rigreactionhybtimebonus",
    "rigpolymerreactioncostbonus": "rigreactionhybmatbonus",
    "rigpolymerreactiontimebonus": "rigreactionhybtimebonus",
}

_ACTIVITIES = {
    "manufacturing": (1,),
    "researchTime": (3,),
    "researchMaterial": (4,),
    "copying": (5,),
    "invention": (8,),
    "reaction": (9, 11),
}
_FIELDS = {
    "material": "material_efficiency_percent",
    "time": "time_efficiency_percent",
    "cost": "job_cost_percent",
}

SECURITY_ATTRIBUTE_IDS = (2355, 2356, 2357)
FITTING_ATTRIBUTE_IDS = (1547, 1298, 1299, 1300)
SUPPORTED_BUNDLE_SCHEMA_VERSION = 2
QUALIFICATION_STATUSES = {
    "compatible_qualified",
    "compatible_conditional",
    "unqualified",
    "incompatible",
}


def industry_projection_evidence(data):
    """Hash the numerical inputs separately from supported numerical values."""

    def digest(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    projection = {
        key: deepcopy(data[key])
        for key in (
            "rig_rules",
            "hull_rules",
            "sources",
            "industrial_effects",
            "attribute_definitions",
        )
    }
    projection["filters"] = {
        key: {
            field: (
                sorted(row[field])
                if field in ("groupIDs", "categoryIDs")
                else row[field]
            )
            for field in ("_key", "groupIDs", "categoryIDs")
            if field in row
        }
        for key, row in data["filters"].items()
    }
    semantics = deepcopy(projection)
    for source in semantics["sources"].values():
        source["attribute_values"] = sorted(source["attribute_values"])
    source_fingerprints = {}
    for key, source in data["sources"].items():
        rules = data["rig_rules"].get(key, data["hull_rules"].get(key))
        attribute_ids = set(source["attribute_values"])
        filter_ids = set()
        if source["kind"] == "rig":
            for rule in rules.values():
                attribute_ids.add(str(rule["modified_attribute_id"]))
                if rule["target_filter_id"] is not None:
                    filter_ids.add(str(rule["target_filter_id"]))
        source_fingerprints[key] = digest(
            {
                "source": source,
                "rules": rules,
                "definitions": {
                    a: data["attribute_definitions"][a] for a in sorted(attribute_ids)
                },
                "filters": {f: projection["filters"][f] for f in sorted(filter_ids)},
            }
        )
    return {
        "status": "compatible_qualified",
        "projection_sha256": digest(projection),
        "semantics_sha256": digest(semantics),
        "source_sha256": source_fingerprints,
    }


def validate_industry_filter(target, path="filter"):
    """Names are descriptive; every eligibility field must be understood."""
    _object(
        target,
        path,
        required=("_key", "name"),
        optional=("groupIDs", "categoryIDs", "description", "displayName"),
    )
    _identifier(target["_key"], path)
    if not isinstance(target["name"], str):
        _fail("expected a display name", path)
    members = []
    for field in ("groupIDs", "categoryIDs"):
        members.extend(_identifiers(target.get(field, []), f"{path}.{field}"))
    if not members:
        _fail("empty eligibility filter", path)


def _validate_rule(record, filters, path):
    fields = (
        "activity",
        "metric",
        "modifying_attribute_id",
        "target_filter_id",
        "effect_id",
        "effect_name",
        "modified_attribute_id",
        "operation",
        "domain",
        "numeric_convention",
    )
    _object(record, path, required=fields)
    if record["activity"] not in _ACTIVITIES or record["metric"] not in _FIELDS:
        _fail("unsupported activity or metric", path)
    if type(record["operation"]) is not int or (
        record["operation"],
        record["domain"],
        record["numeric_convention"],
    ) != (6, "structureID", "negative_percent"):
        _fail("unsupported Dogma operation, domain or numeric convention", path)
    for key in ("modifying_attribute_id", "modified_attribute_id", "effect_id"):
        _identifier(record[key], f"{path}.{key}")
    if not isinstance(record["effect_name"], str) or not record["effect_name"].strip():
        _fail("missing effect name", path)
    filter_id = record["target_filter_id"]
    if filter_id is not None:
        _identifier(filter_id, path)
        if str(filter_id) not in filters:
            _fail(f"missing referenced filter {filter_id}", path)
    elif record["activity"] in ("manufacturing", "reaction"):
        _fail("manufacturing/reaction rig requires an explicit target filter", path)


def _validate_industry_bundle(data):
    """Validate the complete boundary before any numerical rule is consumed."""
    required = (
        "schema_version",
        "sde_build",
        "compatible_builds",
        "rules",
        "rig_rules",
        "rig_security_attribute_ids",
        "filters",
        "group_names",
        "category_names",
        "rules_version",
        "provenance",
        "hull_rules",
        "sources",
        "industrial_effects",
        "qualifications",
        "attribute_definitions",
    )
    _object(data, "bundle", required=required)
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != SUPPORTED_BUNDLE_SCHEMA_VERSION
    ):
        _fail("unsupported schema version", "bundle.schema_version")
    _identifier(data["sde_build"], "bundle.sde_build")
    _identifiers(data["compatible_builds"], "bundle.compatible_builds")
    for key in (
        "filters",
        "rules",
        "rig_rules",
        "hull_rules",
        "sources",
        "rig_security_attribute_ids",
        "industrial_effects",
        "qualifications",
        "group_names",
        "category_names",
        "attribute_definitions",
    ):
        if not isinstance(data[key], dict):
            _fail("expected an object", f"bundle.{key}")
    if not isinstance(data["provenance"], dict):
        _fail("expected descriptive provenance object", "bundle.provenance")
    for names_key in ("group_names", "category_names"):
        if any(
            not isinstance(name, str) or not name for name in data[names_key].values()
        ):
            _fail("expected nonempty display labels", f"bundle.{names_key}")
    for key, target in data["filters"].items():
        validate_industry_filter(target, f"filters.{key}")
        if key != str(target["_key"]):
            _fail("filter key differs from its identifier", f"filters.{key}")
        for member_key, names_key in (
            ("groupIDs", "group_names"),
            ("categoryIDs", "category_names"),
        ):
            if any(
                str(member) not in data[names_key]
                for member in target.get(member_key, [])
            ):
                _fail("missing referenced group/category label", f"filters.{key}")
    for name, record in data["rules"].items():
        _validate_rule(record, data["filters"], f"rules.{name}")
        if name != record["effect_name"].casefold():
            _fail("effect name/key mismatch", f"rules.{name}")
        if not any(source.get(name) == record for source in data["rig_rules"].values()):
            _fail("generic rule has no verified source binding", f"rules.{name}")
    if set(data["rules"]) != {
        name for records in data["rig_rules"].values() for name in records
    }:
        _fail("generic rule inventory is incomplete", "rules")
    for source_id, records in data["rig_rules"].items():
        source = data["sources"].get(source_id)
        if (
            source is None
            or source.get("kind") != "rig"
            or not isinstance(records, dict)
        ):
            _fail("rig rules need a known industrial source", f"rig_rules.{source_id}")
        if bool(records) != source["has_industry_source"]:
            _fail("unexplained absence of rig rules", f"rig_rules.{source_id}")
        expected_effects = {
            effect["name"].casefold()
            for effect_id, effect in source["effects"].items()
            if effect_id in data["industrial_effects"]
        }
        if expected_effects != set(records):
            _fail(
                "incomplete industrial effect/source bindings", f"rig_rules.{source_id}"
            )
        for name, record in records.items():
            _validate_rule(record, data["filters"], f"rig_rules.{source_id}.{name}")
            if name != record["effect_name"].casefold():
                _fail("effect name/key mismatch", f"rig_rules.{source_id}.{name}")
            effect = source["effects"].get(str(record["effect_id"]))
            expected = {
                "domain": record["domain"],
                "func": "ItemModifier",
                "modifiedAttributeID": record["modified_attribute_id"],
                "modifyingAttributeID": record["modifying_attribute_id"],
                "operation": record["operation"],
            }
            if (
                effect is None
                or effect["name"] != record["effect_name"]
                or expected not in effect["modifier_info"]
            ):
                _fail(
                    "effect/source modifier association is incomplete",
                    f"rig_rules.{source_id}.{name}",
                )
            attr = source["attribute_values"].get(str(record["modifying_attribute_id"]))
            _number(
                attr, f"rig_rules.{source_id}.{name}.value", minimum=-100, maximum=0
            )
            if (
                data["attribute_definitions"]
                .get(str(record["modifying_attribute_id"]), {})
                .get("unitID")
                != 105
            ):
                _fail(
                    "unsupported rig percentage unit", f"rig_rules.{source_id}.{name}"
                )
            if (
                str(record["modified_attribute_id"])
                not in data["attribute_definitions"]
            ):
                _fail(
                    "missing target attribute definition",
                    f"rig_rules.{source_id}.{name}",
                )
            for security_id in source["security_attribute_ids"]:
                multiplier = source["attribute_values"].get(str(security_id))
                _number(
                    multiplier,
                    f"rig_rules.{source_id}.{name}.security",
                    minimum=0,
                    strict_minimum=True,
                )
                if abs(attr) * multiplier >= 100:
                    _fail(
                        "effective rig reduction must be below 100 percent",
                        f"rig_rules.{source_id}.{name}",
                    )
    for source_id, records in data["hull_rules"].items():
        source = data["sources"].get(source_id)
        if (
            source is None
            or source.get("kind") != "hull"
            or not isinstance(records, list)
        ):
            _fail("hull rules need a known source", f"hull_rules.{source_id}")
        associations = set()
        for record in records:
            _object(
                record,
                f"hull_rules.{source_id}",
                required=("activity", "metric", "attribute_id", "numeric_convention"),
            )
            if (
                record["activity"] not in _ACTIVITIES
                or record["metric"] not in _FIELDS
                or record["numeric_convention"] != "multiplier"
            ):
                _fail("unsupported hull rule", f"hull_rules.{source_id}")
            _identifier(record["attribute_id"], f"hull_rules.{source_id}")
            association = (record["activity"], record["metric"])
            if association in associations:
                _fail("colliding hull association", f"hull_rules.{source_id}")
            associations.add(association)
            _number(
                source["attribute_values"].get(str(record["attribute_id"])),
                f"hull_rules.{source_id}.value",
                minimum=0,
                maximum=1,
                strict_minimum=True,
            )
            if data["attribute_definitions"].get(str(record["attribute_id"]), {}).get(
                "unitID"
            ) not in (None, 104):
                _fail("unsupported hull multiplier unit", f"hull_rules.{source_id}")
        if bool(records) != source["has_industry_source"]:
            _fail("unexplained absence of hull rules", f"hull_rules.{source_id}")
    shared_effects = {}
    for source_id, source in data["sources"].items():
        _identifier_key(source_id, f"sources.{source_id}")
        _object(
            source,
            f"sources.{source_id}",
            required=(
                "kind",
                "has_industry_source",
                "effects",
                "attribute_values",
                "security_attribute_ids",
            ),
            optional=("group_id", "fitting_attribute_values"),
        )
        if (
            source["kind"] not in ("rig", "hull")
            or type(source["has_industry_source"]) is not bool
        ):
            _fail("unsupported source kind", f"sources.{source_id}")
        if not isinstance(source["effects"], dict) or not isinstance(
            source["attribute_values"], dict
        ):
            _fail("invalid source effects/attributes", f"sources.{source_id}")
        if "group_id" in source:
            _identifier(source["group_id"], f"sources.{source_id}.group_id")
        fitting = source.get("fitting_attribute_values", {})
        if not isinstance(fitting, dict):
            _fail(
                "expected fitting attribute object",
                f"sources.{source_id}.fitting_attribute_values",
            )
        for attribute_key, value in fitting.items():
            attribute_id = _identifier_key(
                attribute_key, f"sources.{source_id}.fitting_attribute_values"
            )
            if attribute_id not in FITTING_ATTRIBUTE_IDS:
                _fail(
                    "unsupported fitting attribute",
                    f"sources.{source_id}.{attribute_key}",
                )
            _number(
                value,
                f"sources.{source_id}.{attribute_key}",
                minimum=0,
                strict_minimum=True,
            )
            if int(value) != value:
                _fail(
                    "expected an integer fitting value",
                    f"sources.{source_id}.{attribute_key}",
                )
        if source["kind"] == "rig" and source_id not in data["rig_rules"]:
            _fail("industrial rig source has no bindings", f"sources.{source_id}")
        if source["kind"] == "hull" and source_id not in data["hull_rules"]:
            _fail("hull source has no explicit rule set", f"sources.{source_id}")
        security = _identifiers(
            source["security_attribute_ids"],
            f"sources.{source_id}.security_attribute_ids",
        )
        if set(security) - set(SECURITY_ATTRIBUTE_IDS):
            _fail("unsupported security multiplier", f"sources.{source_id}")
        if source["kind"] == "rig" and security != data[
            "rig_security_attribute_ids"
        ].get(source_id):
            _fail("security source/binding mismatch", f"sources.{source_id}")
        for attribute_id in security:
            _number(
                source["attribute_values"].get(str(attribute_id)),
                f"sources.{source_id}.security",
                minimum=0,
                strict_minimum=True,
            )
            if (
                data["attribute_definitions"].get(str(attribute_id), {}).get("unitID")
                != 104
            ):
                _fail("unsupported security multiplier unit", f"sources.{source_id}")
        for attribute_id, value in source["attribute_values"].items():
            if attribute_id not in data["attribute_definitions"]:
                _fail(
                    "missing attribute definition",
                    f"sources.{source_id}.{attribute_id}",
                )
            _number(value, f"sources.{source_id}.{attribute_id}")
        for effect_id, effect in source["effects"].items():
            _identifier_key(effect_id, f"sources.{source_id}.effects.{effect_id}")
            _object(
                effect,
                f"sources.{source_id}.effects.{effect_id}",
                required=("name", "modifier_info"),
            )
            if (
                not isinstance(effect["name"], str)
                or not effect["name"].strip()
                or not isinstance(effect["modifier_info"], list)
            ):
                _fail(
                    "invalid effect metadata",
                    f"sources.{source_id}.effects.{effect_id}",
                )
            if any(not isinstance(info, dict) for info in effect["modifier_info"]):
                _fail(
                    "expected modifier metadata objects",
                    f"sources.{source_id}.effects.{effect_id}",
                )
            if (
                effect_id in data["industrial_effects"]
                and effect != data["industrial_effects"][effect_id]
            ):
                _fail(
                    "industrial source/global effect metadata differs",
                    f"sources.{source_id}.effects.{effect_id}",
                )
            if effect_id in shared_effects and shared_effects[effect_id] != effect:
                _fail(
                    "shared Dogma effect metadata differs between sources",
                    f"sources.{source_id}.effects.{effect_id}",
                )
            shared_effects[effect_id] = effect
    bound_effects = {
        str(record["effect_id"])
        for records in data["rig_rules"].values()
        for record in records.values()
    }
    if set(data["industrial_effects"]) != bound_effects:
        _fail(
            "industrial effect inventory differs from its source bindings",
            "industrial_effects",
        )
    for effect_id, effect in data["industrial_effects"].items():
        _identifier_key(effect_id, f"industrial_effects.{effect_id}")
        _object(
            effect,
            f"industrial_effects.{effect_id}",
            required=("name", "modifier_info"),
        )
        if not isinstance(effect["name"], str) or not isinstance(
            effect["modifier_info"], list
        ):
            _fail(
                "invalid industrial effect metadata", f"industrial_effects.{effect_id}"
            )
        for info in effect["modifier_info"]:
            _object(
                info,
                f"industrial_effects.{effect_id}",
                required=(
                    "domain",
                    "func",
                    "modifiedAttributeID",
                    "modifyingAttributeID",
                    "operation",
                ),
            )
            if type(info["operation"]) is not int or (
                info["domain"],
                info["func"],
                info["operation"],
            ) != ("structureID", "ItemModifier", 6):
                _fail(
                    "unsupported industrial modifier", f"industrial_effects.{effect_id}"
                )
            _identifier(info["modifiedAttributeID"], f"industrial_effects.{effect_id}")
            _identifier(info["modifyingAttributeID"], f"industrial_effects.{effect_id}")
    for attribute_id, definition in data["attribute_definitions"].items():
        _object(
            definition,
            f"attribute_definitions.{attribute_id}",
            required=("_key", "name"),
            optional=("unitID", "defaultValue", "dataType", "highIsGood", "stackable"),
        )
        if (
            str(
                _identifier(definition["_key"], f"attribute_definitions.{attribute_id}")
            )
            != attribute_id
        ):
            _fail(
                "attribute definition key mismatch",
                f"attribute_definitions.{attribute_id}",
            )
        if "defaultValue" in definition:
            _number(
                definition["defaultValue"],
                f"attribute_definitions.{attribute_id}.defaultValue",
            )
    for build, qualification in data["qualifications"].items():
        _object(
            qualification,
            f"qualifications.{build}",
            required=(
                "status",
                "projection_sha256",
                "semantics_sha256",
                "source_sha256",
            ),
            optional=("source_attribute_values",),
        )
        if qualification["status"] not in QUALIFICATION_STATUSES:
            _fail("unsupported qualification state", f"qualifications.{build}")
        for field in ("projection_sha256", "semantics_sha256"):
            if (
                not isinstance(qualification[field], str)
                or len(qualification[field]) != 64
            ):
                _fail(
                    "invalid qualification fingerprint",
                    f"qualifications.{build}.{field}",
                )
        if not isinstance(qualification["source_sha256"], dict):
            _fail("invalid source fingerprints", f"qualifications.{build}")
        overrides = qualification.get("source_attribute_values", {})
        if not isinstance(overrides, dict):
            _fail(
                "expected numeric variant object",
                f"qualifications.{build}.source_attribute_values",
            )
        if overrides:
            if qualification["status"] != "compatible_conditional" or not isinstance(
                overrides, dict
            ):
                _fail(
                    "numeric variants require a conditional qualification",
                    f"qualifications.{build}",
                )
            variant = deepcopy(data)
            for source_id, values in overrides.items():
                source = variant["sources"].get(source_id)
                if (
                    source is None
                    or not isinstance(values, dict)
                    or values.keys() != source["attribute_values"].keys()
                ):
                    _fail(
                        "numeric variant changes the expected attribute set",
                        f"qualifications.{build}.{source_id}",
                    )
                for attribute_id, value in values.items():
                    if int(attribute_id) in source["security_attribute_ids"]:
                        _number(
                            value,
                            f"qualifications.{build}",
                            minimum=0,
                            strict_minimum=True,
                        )
                    elif source["kind"] == "rig":
                        _number(
                            value, f"qualifications.{build}", minimum=-100, maximum=0
                        )
                    else:
                        _number(
                            value,
                            f"qualifications.{build}",
                            minimum=0,
                            maximum=1,
                            strict_minimum=True,
                        )
                source["attribute_values"] = values
                if source["kind"] == "rig":
                    multiplier = max(
                        (values[str(a)] for a in source["security_attribute_ids"]),
                        default=1,
                    )
                    for rule in variant["rig_rules"][source_id].values():
                        if (
                            abs(values[str(rule["modifying_attribute_id"])])
                            * multiplier
                            >= 100
                        ):
                            _fail(
                                "numeric variant exceeds the supported reduction",
                                f"qualifications.{build}",
                            )
            variant_evidence = industry_projection_evidence(variant)
            if any(
                qualification[field] != variant_evidence[field]
                for field in ("projection_sha256", "semantics_sha256", "source_sha256")
            ):
                _fail(
                    "conditional qualification does not match its numeric variants",
                    f"qualifications.{build}",
                )
    expected_builds = sorted(
        int(build)
        for build, q in data["qualifications"].items()
        if q["status"] in ("compatible_qualified", "compatible_conditional")
    )
    if (
        sorted(data["compatible_builds"]) != expected_builds
        or data["sde_build"] not in expected_builds
    ):
        _fail("build list is not backed by qualification evidence", "compatible_builds")
    expected_evidence = industry_projection_evidence(data)
    if data["qualifications"].get(str(data["sde_build"])) != expected_evidence:
        _fail(
            "reference build qualification does not match its actual rule projection",
            "qualifications",
        )
    for build, qualification in data["qualifications"].items():
        if (
            qualification["status"] == "compatible_qualified"
            and qualification != expected_evidence
        ):
            _fail(
                "qualified build projection differs from the reference",
                f"qualifications.{build}",
            )
        if qualification["status"] == "compatible_conditional" and (
            not qualification.get("source_attribute_values")
            or qualification["semantics_sha256"]
            != expected_evidence["semantics_sha256"]
        ):
            _fail(
                "conditional build lacks verified numeric-only differences",
                f"qualifications.{build}",
            )
    version_content = {
        key: value
        for key, value in data.items()
        if key not in ("rules_version", "provenance")
    }
    digest = hashlib.sha256(
        json.dumps(version_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if data["rules_version"] != f"ccp-{data['sde_build']}-{digest[:12]}":
        _fail("rule version does not match its content fingerprint", "rules_version")
    return data


def validate_industry_bundle(data):
    try:
        return _validate_industry_bundle(data)
    except IndustryRulesError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IndustryRulesError(f"Malformed CCP industry bundle: {exc}") from exc


def _validated_data():
    global _VALIDATED
    if _LOAD_ERROR is not None:
        raise _LOAD_ERROR
    if not _VALIDATED:
        validate_industry_bundle(_DATA)
        _VALIDATED = True
    return _DATA


@dataclass(frozen=True)
class IndustryRule:
    activity_ids: tuple[int, ...]
    field_name: str
    modifying_attribute_id: int
    target_filter_id: int | None
    group_ids: tuple[int, ...]
    category_ids: tuple[int, ...]
    effect_id: int
    effect_name: str
    modified_attribute_id: int
    supported_type_names: tuple[str, ...]


def get_industry_rule(
    effect_name: str, *, rig_type_id: int | None = None
) -> IndustryRule | None:
    """Resolve an official effect/source binding, including legacy name aliases.

    Per-type bindings matter: CCP's laboratory rigs can use the same Dogma
    effect for different research activities. A provided source type must have
    a verified binding; unknown types/effects require a reviewed bundle update.
    Generic bindings are available only to callers omitting the source type.
    """
    data = _validated_data()
    name = str(effect_name).strip().lower()
    name = _ALIASES.get(name, name)
    if rig_type_id is not None:
        record = data["rig_rules"].get(str(rig_type_id), {}).get(name)
    else:
        record = data["rules"].get(name)
    if record is None:
        return None
    target_filter_id = record["target_filter_id"]
    target = (
        data["filters"][str(target_filter_id)] if target_filter_id is not None else {}
    )
    group_ids = tuple(target.get("groupIDs", ()))
    category_ids = tuple(target.get("categoryIDs", ()))
    labels = tuple(
        sorted(
            {data["group_names"][str(group)] for group in group_ids}
            | {data["category_names"][str(category)] for category in category_ids}
        )
    )
    return IndustryRule(
        activity_ids=_ACTIVITIES[record["activity"]],
        field_name=_FIELDS[record["metric"]],
        modifying_attribute_id=record["modifying_attribute_id"],
        target_filter_id=target_filter_id,
        group_ids=group_ids,
        category_ids=category_ids,
        effect_id=record["effect_id"],
        effect_name=record["effect_name"],
        modified_attribute_id=record["modified_attribute_id"],
        supported_type_names=labels,
    )


def get_industry_rules_for_rig(rig_type_id: int) -> tuple[IndustryRule, ...]:
    """Return the complete verified industrial effect set for a source rig."""
    return tuple(
        get_industry_rule(effect_name, rig_type_id=rig_type_id)
        for effect_name in _validated_data()["rig_rules"].get(str(rig_type_id), {})
    )


def get_industry_rig_security_attribute_ids(rig_type_id: int) -> tuple[int, ...]:
    """Return security attributes explicitly declared by CCP for this source."""
    return tuple(
        _validated_data()["rig_security_attribute_ids"].get(str(rig_type_id), ())
    )


@dataclass(frozen=True)
class IndustryHullRule:
    activity_ids: tuple[int, ...]
    field_name: str
    attribute_id: int
    numeric_convention: str = "multiplier"


def get_industry_rules_for_hull(structure_type_id: int) -> tuple[IndustryHullRule, ...]:
    """Hull sources refer directly to multipliers, not rig percentage effects."""
    return tuple(
        IndustryHullRule(
            _ACTIVITIES[row["activity"]], _FIELDS[row["metric"]], row["attribute_id"]
        )
        for row in _validated_data()["hull_rules"].get(str(structure_type_id), ())
    )


def get_industry_source_metadata(type_id: int) -> dict | None:
    source = _validated_data()["sources"].get(str(type_id))
    return deepcopy(source) if source is not None else None


def get_industry_source_attribute_values(type_id: int, build_numbers=()) -> dict:
    """Expected source values for the TypeDogma build(s), including reviewed variants."""
    data = _validated_data()
    source_id = str(type_id)
    source = data["sources"].get(source_id)
    if source is None:
        raise IndustryRulesError(
            f"Unknown industry source {type_id}", code="unknown_industry_source"
        )
    values = source["attribute_values"]
    variants = []
    for build in sorted(set(build_numbers)):
        qualification = data["qualifications"].get(str(build))
        status = get_industry_build_qualification([build], source_type_id=type_id)[
            "status"
        ]
        if qualification is None or status not in (
            "compatible_qualified",
            "compatible_conditional",
        ):
            raise IndustryRulesError(
                f"Unqualified industry source build {build}",
                code="unqualified_industry_build",
            )
        variants.append(
            qualification.get("source_attribute_values", {}).get(source_id, values)
        )
    if variants and any(variant != variants[0] for variant in variants[1:]):
        raise IndustryRulesError(
            "Mixed TypeDogma builds have conflicting numerical values",
            code="mixed_industry_builds",
        )
    return deepcopy(variants[0] if variants else values)


def is_known_industry_effect(effect_name: str, effect_id: int | None = None) -> bool:
    effects = _validated_data()["industrial_effects"]
    if effect_id is not None and str(effect_id) in effects:
        return True
    name = str(effect_name).strip().casefold()
    name = _ALIASES.get(name, name)
    return any(row["name"].casefold() == name for row in effects.values())


def get_industry_build_qualification(build_numbers, *, source_type_id=None) -> dict:
    """Qualification evidence concerns the industrial projection, not build age."""
    data = _validated_data()
    builds = sorted(set(build_numbers))
    records = [data["qualifications"].get(str(build)) for build in builds]
    statuses = []
    reference = data["qualifications"][str(data["sde_build"])]["source_sha256"]
    for record in records:
        if record is None:
            statuses.append("unqualified")
        elif record["status"] == "unqualified":
            statuses.append("unqualified")
        elif source_type_id is not None and str(source_type_id) not in reference:
            statuses.append("unqualified")
        elif (
            source_type_id is not None
            and record["source_sha256"].get(str(source_type_id))
            == reference[str(source_type_id)]
        ):
            statuses.append("compatible_qualified")
        else:
            statuses.append(record["status"])
    if not builds or any(record is None for record in records):
        status = "unqualified"
    elif "incompatible" in statuses:
        status = "incompatible"
    elif "unqualified" in statuses:
        status = "unqualified"
    elif "compatible_conditional" in statuses:
        status = "compatible_conditional"
    else:
        status = "compatible_qualified"
    return {
        "status": status,
        "build_numbers": builds,
        "reason": {
            "compatible_qualified": "Official industrial projections match the qualified reference.",
            "compatible_conditional": "Supported numerical values changed; source domains were verified.",
            "unqualified": "The installed build has no verified official industrial projection; qualify its archive offline.",
            "incompatible": "Official industrial semantics differ from this rule bundle.",
        }[status],
    }
