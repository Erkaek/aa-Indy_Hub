"""Local public-location names, rebuilt by the existing SDE operator/worker check."""

# Standard Library
import re
import tempfile
import time
import uuid
from pathlib import Path

# Django
from django.core.cache import cache
from django.db import connection, transaction

# Alliance Auth (External Libs)
from eve_sde import models as sde
from eve_sde.models.utils import to_roman_numeral

# AA Example App
from indy_hub.models import StaticLocationName
from indy_hub.services.industry_rest_projection import download
from indy_hub.services.industry_rule_projection import MAX_LINE_BYTES
from indy_hub.services.industry_rules import IndustryRulesError, read_industry_json


def is_static_location(identifier):
    return 10_000_000 <= int(identifier) < 70_000_000


def resolve_static_location_names(ids):
    release = sde.EveSDE.get_solo().release
    if not release:
        return {}
    return dict(
        StaticLocationName.objects.filter(
            release=f"{release}:1", location_id__in=ids
        ).values_list("location_id", "name")
    )


def _rows(model, *extra):
    translated = (
        ["name_en"] if any(f.name == "name_en" for f in model._meta.fields) else []
    )
    return model.objects.values("id", "name", *translated, *extra)


def _english(value):
    return str(value.get("en", "") if isinstance(value, dict) else value or "").strip()


def sync_static_location_names():
    release = sde.EveSDE.get_solo().release
    if (
        not release
        or StaticLocationName.objects.filter(release=f"{release}:1").exists()
    ):
        return
    key = f"indy_hub:static-location-sync:{release}"
    token = uuid.uuid4().hex
    if not cache.add(key, token, timeout=600):
        return
    try:
        _sync_static_location_names()
    finally:
        if cache.get(key) == token:
            cache.delete(key)


def _sync_static_location_names():
    """Five bounded reference-table downloads, once per installed REST release."""
    release = sde.EveSDE.get_solo().release
    if (
        not release
        or StaticLocationName.objects.filter(release=f"{release}:1").exists()
    ):
        return
    if not re.fullmatch(r"\d{8}T\d{6}Z", release):
        raise IndustryRulesError("Invalid SDE location release")
    with tempfile.TemporaryDirectory(prefix="indyhub-locations-") as directory:
        budget = [0, time.monotonic()]
        path = Path(directory) / "source"
        download(f"https://sde.evetech.net/tranquility/{release}/", path, budget)
        index = read_industry_json(path.read_text())
        if index.get("release") != release:
            raise IndustryRulesError("Location source release mismatch")
        refs = {}
        for table, key in (
            ("npcCorporations", "corporationID"),
            ("stationOperations", "operationID"),
            ("mapPlanets", "planetID"),
            ("mapMoons", "moonID"),
            ("npcStations", "stationID"),
        ):
            version = index["tables"][table]["version"]
            if not re.fullmatch(r"[a-f0-9]{64}", version):
                raise IndustryRulesError("Invalid location reference version")
            download(f"https://sde.evetech.net/blobs/{version}.jsonl", path, budget)
            rows, seen = {}, set()
            with path.open() as source:
                while line := source.readline(MAX_LINE_BYTES + 1):
                    if len(line) > MAX_LINE_BYTES:
                        raise IndustryRulesError(
                            "Location reference exceeds line limit"
                        )
                    row = read_industry_json(line)
                    identifier = row[key]
                    if (
                        type(identifier) is not int
                        or identifier < 0
                        or identifier in seen
                    ):
                        raise IndustryRulesError("Invalid/duplicate location reference")
                    seen.add(identifier)
                    field = (
                        "name"
                        if table == "npcCorporations"
                        else (
                            "operationName"
                            if table == "stationOperations"
                            else "uniqueName"
                        )
                    )
                    name = _english(row.get(field))
                    if name:
                        rows[identifier] = name
            refs[table] = rows
    names = {}
    for model in (
        sde.Region,
        sde.Constellation,
        sde.SolarSystem,
        sde.Stargate,
        sde.Landmark,
    ):
        for row in _rows(model):
            name = row.get("name_en") or row["name"]
            if name:
                names[row["id"]] = name
    systems = dict(names)
    for row in sde.Star.objects.values("id", "solar_system_id"):
        if row["solar_system_id"] in systems:
            names[row["id"]] = systems[row["solar_system_id"]]
    for row in _rows(sde.Planet, "solar_system_id", "celestial_index"):
        system = systems.get(row["solar_system_id"])
        name = refs["mapPlanets"].get(row["id"]) or row.get("name_en") or row["name"]
        if name or (system and row["celestial_index"]):
            names[row["id"]] = (
                name or f"{system} {to_roman_numeral(row['celestial_index'])}"
            )
    for row in _rows(sde.Moon, "orbit_id_raw", "orbit_index"):
        orbit = names.get(row["orbit_id_raw"])
        name = refs["mapMoons"].get(row["id"]) or row.get("name_en") or row["name"]
        if name or (orbit and row["orbit_index"]):
            names[row["id"]] = name or f"{orbit} - Moon {row['orbit_index']}"
    for row in _rows(
        sde.NPCStation,
        "orbit_id_raw",
        "owner_id_raw",
        "operation_id_raw",
        "use_operation_name",
    ):
        name = refs["npcStations"].get(row["id"]) or row.get("name_en") or row["name"]
        if not name:
            orbit = names.get(row["orbit_id_raw"])
            owner = refs["npcCorporations"].get(row["owner_id_raw"])
            operation = refs["stationOperations"].get(row["operation_id_raw"])
            if not orbit or not owner or (row["use_operation_name"] and not operation):
                raise IndustryRulesError(
                    f"Incomplete SDE station references: {row['id']}"
                )
            name = f"{orbit} - {owner}" + (
                f" {operation}" if row["use_operation_name"] else ""
            )
        names[row["id"]] = name
    if sde.EveSDE.get_solo().release != release:
        raise IndustryRulesError("Installed SDE changed during location generation")
    with transaction.atomic():
        StaticLocationName.objects.bulk_create(
            [
                StaticLocationName(location_id=key, name=value, release=f"{release}:1")
                for key, value in names.items()
            ],
            update_conflicts=True,
            update_fields=["name", "release"],
            batch_size=1000,
            **(
                {"unique_fields": ["location_id"]}
                if connection.features.supports_update_conflicts_with_target
                else {}
            ),
        )
        StaticLocationName.objects.exclude(release=f"{release}:1").delete()
