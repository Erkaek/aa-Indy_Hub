"""Bounded CCP REST projection, normalized for the existing industry validator."""

# Standard Library
import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

# Third Party
import requests

# Alliance Auth (External Libs)
from eve_sde.sde_rest import alias_sde_record

# AA Example App
from indy_hub.services.industry_rule_projection import (
    FILES,
    MAX_DATA_BYTES,
    MAX_LINE_BYTES,
)
from indy_hub.services.industry_rules import (
    _ACTIVITIES,
    _FIELDS,
    IndustryRulesError,
    read_industry_json,
)


def download(url, target, budget):
    """One request, no redirects/retries, shared byte and elapsed-time bounds."""
    with requests.get(
        url, stream=True, timeout=(10, 30), allow_redirects=False
    ) as response:
        if response.status_code != 200:
            raise IndustryRulesError(
                f"CCP REST source returned HTTP {response.status_code}"
            )
        digest = hashlib.sha256()
        with Path(target).open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                budget[0] += len(chunk)
                if budget[0] > MAX_DATA_BYTES or time.monotonic() - budget[1] > 180:
                    raise IndustryRulesError(
                        "CCP REST projection exceeded size/time limits"
                    )
                digest.update(chunk)
                output.write(chunk)
    return digest.hexdigest()


def normalize_record(value, node, root):
    """Remove only schema-declared key aliases after the SDE adapter adds _key."""
    if "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise IndustryRulesError("Unsupported REST schema reference")
        node = root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
    if isinstance(value, dict):
        key = node.get("x-sde-key")
        if key and key != "_key" and "_key" in value:
            if value.get(key) != value["_key"]:
                raise IndustryRulesError("Conflicting REST record key")
            value.pop(key, None)
        for name, item in value.items():
            child = node.get("properties", {}).get(name, {})
            normalize_record(item, child, root)
    elif isinstance(value, list):
        for item in value:
            normalize_record(item, node.get("items", {}), root)
    return value


def download_projection(build, release, target):
    """Pin all tables to the installed release; never use the moving latest URL."""
    if not re.fullmatch(r"\d{8}T\d{6}Z", release):
        raise IndustryRulesError("Invalid installed SDE REST release")
    folder = Path(target).parent
    budget = [0, time.monotonic()]
    index_path = folder / "rest-index.json"
    index_hash = download(
        f"https://sde.evetech.net/tranquility/{release}/", index_path, budget
    )
    index = read_industry_json(index_path.read_text())
    if (
        not isinstance(index, dict)
        or index.get("release") != release
        or index.get("buildNumber") != build
    ):
        raise IndustryRulesError("REST release does not match the installed SDE")
    provenance = {"release": release, "index_sha256": index_hash, "tables": {}}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "_sde.jsonl", json.dumps({"_key": "sde", "buildNumber": build})
        )
        for name in FILES[:8]:
            table = name.removesuffix(".jsonl")
            version = index.get("tables", {}).get(table, {}).get("version", "")
            if not isinstance(version, str) or not re.fullmatch(
                r"[a-f0-9]{64}", version
            ):
                raise IndustryRulesError(f"Missing/invalid REST table version: {table}")
            schema_path, data_path = folder / "schema.json", folder / "data.jsonl"
            base = f"https://sde.evetech.net/blobs/{version}"
            schema_hash = download(base + ".schema.json", schema_path, budget)
            if schema_path.stat().st_size > MAX_LINE_BYTES:
                raise IndustryRulesError("REST schema exceeds size limit")
            schema = read_industry_json(schema_path.read_text())
            if not isinstance(schema, dict):
                raise IndustryRulesError("Invalid REST schema")
            raw_hash = download(base + ".jsonl", data_path, budget)
            with data_path.open("rb") as source, archive.open(name, "w") as output:
                while line := source.readline(MAX_LINE_BYTES + 1):
                    if len(line) > MAX_LINE_BYTES or time.monotonic() - budget[1] > 180:
                        raise IndustryRulesError(
                            "REST normalization exceeded size/time limits"
                        )
                    record = read_industry_json(line.decode())
                    if not isinstance(record, dict):
                        raise IndustryRulesError("Invalid REST record")
                    record = normalize_record(
                        alias_sde_record(record, schema), schema, schema
                    )
                    # ZIP represents Dogma scalar values as floats; REST may
                    # encode mathematically whole values as JSON integers.
                    if table == "typeDogma":
                        for attribute in record.get("dogmaAttributes", []):
                            value = attribute.get("value")
                            if type(value) in (int, float):
                                attribute["value"] = float(value)
                    elif table == "dogmaAttributes":
                        value = record.get("defaultValue")
                        if type(value) in (int, float):
                            record["defaultValue"] = float(value)
                    if table == "industryTargetFilters":
                        for field in ("groupIDs", "categoryIDs"):
                            if record.get(field) == []:
                                record.pop(field)
                    # REST emits known empty modifier arrays that ZIP omits.
                    # Unknown activities/metrics remain for strict rejection.
                    if table == "industryModifierSources":
                        for activity in _ACTIVITIES:
                            metrics = record.get(activity)
                            if isinstance(metrics, dict):
                                for metric in _FIELDS:
                                    if metrics.get(metric) == []:
                                        metrics.pop(metric)
                                if not metrics:
                                    record.pop(activity)
                    # The validator independently rejects missing/duplicate IDs.
                    output.write((json.dumps(record) + "\n").encode())
            provenance["tables"][table] = {
                "version": version,
                "raw_sha256": raw_hash,
                "schema_sha256": schema_hash,
            }
    with Path(target).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest(), provenance
