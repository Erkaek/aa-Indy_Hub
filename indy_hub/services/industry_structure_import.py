"""Helpers for importing structure registry entries from pasted Indy listings."""

from __future__ import annotations

# Standard Library
import re
from dataclasses import dataclass

# Django
from django.db import transaction

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.models import IndustryStructure, IndustryStructureRig
from indy_hub.services.industry_structures import (
    get_industry_rig_catalog,
    get_structure_type_options,
    is_rig_compatible_with_structure_type,
    resolve_item_type_reference,
    resolve_solar_system_location_reference,
    resolve_solar_system_reference,
    structure_type_supports_rigs,
)

logger = get_extension_logger(__name__)

PASTE_ACTIVITY_FLAG_MAP = {
    "material efficiency research": "enable_research",
    "blueprint copying": "enable_research",
    "time efficiency research": "enable_research",
    "composite reactions": "enable_composite_reactions",
    "biochemical reactions": "enable_biochemical_reactions",
    "hybrid reactions": "enable_hybrid_reactions",
    "invention": "enable_invention",
    "manufacturing (standard)": "enable_manufacturing",
    "manufacturing (capitals)": "enable_manufacturing_capitals",
    "manufacturing (super capitals)": "enable_manufacturing_super_capitals",
}

UNSUPPORTED_SERVICE_LABELS = (
    "reprocessing",
    "compression",
)

DEFAULT_DISABLED_ACTIVITY_FLAGS = {
    "enable_manufacturing": False,
    "enable_manufacturing_capitals": False,
    "enable_manufacturing_super_capitals": False,
    "enable_research": False,
    "enable_invention": False,
    "enable_biochemical_reactions": False,
    "enable_hybrid_reactions": False,
    "enable_composite_reactions": False,
}

STRUCTURE_SCAN_MAX_LENGTH = 20000

STRUCTURE_SCAN_SECTION_ALIASES = {
    "rig slot": "rigs",
    "rig slots": "rigs",
    "rigs": "rigs",
    "service slot": "services",
    "service slots": "services",
    "services": "services",
    "high power slots": "ignored",
    "medium power slots": "ignored",
    "low power slots": "ignored",
    "subsystem slots": "ignored",
    "drone bay": "ignored",
    "fighters": "ignored",
    "cargo": "ignored",
}

STRUCTURE_SCAN_SERVICE_ACTIVITY_MAP = (
    ("standup manufacturing plant", ("enable_manufacturing",)),
    ("standup capital shipyard", ("enable_manufacturing_capitals",)),
    ("standup supercapital shipyard", ("enable_manufacturing_super_capitals",)),
    ("standup research lab", ("enable_research", "enable_invention")),
    ("standup hyasyoda research lab", ("enable_research", "enable_invention")),
    ("standup invention lab", ("enable_invention",)),
    ("standup biochemical reactor", ("enable_biochemical_reactions",)),
    ("standup hybrid reactor", ("enable_hybrid_reactions",)),
    ("standup composite reactor", ("enable_composite_reactions",)),
)


@dataclass(frozen=True)
class ParsedIndyPasteStructure:
    structure_type_name: str
    structure_name: str
    service_text: str
    rig_names: tuple[str, ...]
    solar_system_name: str


@dataclass(frozen=True)
class ParsedStructureScanLoadout:
    service_names: tuple[str, ...]
    rig_names: tuple[str, ...]
    has_service_section: bool = False
    has_rig_section: bool = False


def _normalize_scan_value(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_scan_key(value: str | None) -> str:
    return _normalize_scan_value(value).casefold()


def _normalize_scan_section_header(value: str | None) -> str:
    normalized = _normalize_scan_key(value)
    normalized = normalized.strip("[]")
    normalized = normalized.rstrip(":")
    return _normalize_scan_value(normalized)


def _scan_line_item_name(line: str) -> str:
    item_name = _normalize_scan_value(str(line or "").split("\t", 1)[0])
    item_name = re.sub(r"\s+x\d+$", "", item_name, flags=re.IGNORECASE)
    return item_name


def _is_empty_scan_slot(item_name: str) -> bool:
    normalized = _normalize_scan_key(item_name).strip("[]")
    return not normalized or normalized.startswith("empty ") or normalized == "offline"


def parse_structure_scan_loadout(raw_text: str) -> ParsedStructureScanLoadout:
    services: list[str] = []
    rigs: list[str] = []
    active_section: str | None = None
    has_service_section = False
    has_rig_section = False

    for raw_line in str(raw_text or "").splitlines():
        line = _normalize_scan_value(raw_line)
        if not line:
            continue

        section_name = _normalize_scan_section_header(line)
        if section_name in STRUCTURE_SCAN_SECTION_ALIASES:
            active_section = STRUCTURE_SCAN_SECTION_ALIASES[section_name]
            if active_section == "services":
                has_service_section = True
            elif active_section == "rigs":
                has_rig_section = True
            continue

        item_name = _scan_line_item_name(line)
        if _is_empty_scan_slot(item_name):
            continue

        if active_section == "services":
            services.append(item_name)
        elif active_section == "rigs":
            rigs.append(item_name)

    return ParsedStructureScanLoadout(
        service_names=tuple(services),
        rig_names=tuple(rigs),
        has_service_section=has_service_section,
        has_rig_section=has_rig_section,
    )


def _activity_flags_from_scan_service(service_name: str) -> tuple[str, ...]:
    normalized_service = _normalize_scan_key(service_name)
    for service_prefix, field_names in STRUCTURE_SCAN_SERVICE_ACTIVITY_MAP:
        if normalized_service.startswith(service_prefix):
            return field_names
    return ()


def resolve_structure_scan_loadout(
    raw_text: str,
    *,
    structure_type_id: int | None = None,
) -> dict[str, object]:
    scan_text = str(raw_text or "").strip()
    if not scan_text:
        return {
            "activity_flags": dict(DEFAULT_DISABLED_ACTIVITY_FLAGS),
            "has_service_section": False,
            "has_rig_section": False,
            "services": [],
            "rigs": [],
            "warnings": ["Paste a structure scan before importing."],
        }
    if len(scan_text) > STRUCTURE_SCAN_MAX_LENGTH:
        return {
            "activity_flags": dict(DEFAULT_DISABLED_ACTIVITY_FLAGS),
            "has_service_section": False,
            "has_rig_section": False,
            "services": [],
            "rigs": [],
            "warnings": ["Structure scan is too large to import from this screen."],
        }

    parsed_scan = parse_structure_scan_loadout(scan_text)
    activity_flags = dict(DEFAULT_DISABLED_ACTIVITY_FLAGS)
    matched_services: list[dict[str, object]] = []
    warnings: list[str] = []

    for service_name in parsed_scan.service_names:
        field_names = _activity_flags_from_scan_service(service_name)
        if not field_names:
            warnings.append(f"Ignored unsupported service module: {service_name}.")
            continue
        for field_name in field_names:
            activity_flags[field_name] = True
        matched_services.append(
            {
                "name": service_name,
                "fields": list(field_names),
            }
        )

    if parsed_scan.has_service_section:
        if parsed_scan.service_names and not matched_services:
            warnings.append(
                "No supported industry service was found in the Service Slots section."
            )
        elif not parsed_scan.service_names:
            warnings.append("No service module was found in the Service Slots section.")
    else:
        warnings.append("No Service Slots section was found in the pasted scan.")

    resolved_structure_type_id = None
    try:
        resolved_structure_type_id = (
            int(structure_type_id) if structure_type_id not in {None, ""} else None
        )
    except (TypeError, ValueError):
        resolved_structure_type_id = None

    supports_rigs = (
        True
        if resolved_structure_type_id is None
        else structure_type_supports_rigs(resolved_structure_type_id)
    )
    rig_catalog_by_name = {
        _normalize_scan_key(entry.get("name")): entry
        for entry in get_industry_rig_catalog()
    }
    matched_rigs: list[dict[str, object]] = []

    for rig_name in parsed_scan.rig_names[:3]:
        if not supports_rigs:
            warnings.append(
                f"Ignored rig '{rig_name}': the selected structure has no rig slots."
            )
            continue

        rig_entry = rig_catalog_by_name.get(_normalize_scan_key(rig_name))
        if rig_entry is None:
            warnings.append(f"Ignored unknown industrial rig: {rig_name}.")
            continue

        rig_type_id = int(rig_entry["type_id"])
        if (
            resolved_structure_type_id is not None
            and not is_rig_compatible_with_structure_type(
                rig_type_id=rig_type_id,
                structure_type_id=resolved_structure_type_id,
            )
        ):
            warnings.append(
                f"Ignored incompatible rig '{rig_entry['name']}' for the selected structure type."
            )
            continue

        matched_rigs.append(
            {
                "type_id": rig_type_id,
                "name": str(rig_entry["name"]),
            }
        )

    if len(parsed_scan.rig_names) > 3:
        warnings.append("Only the first three Rig Slots entries were imported.")
    if parsed_scan.has_rig_section and not parsed_scan.rig_names:
        warnings.append("No rig entry was found in the Rig Slots section.")
    elif not parsed_scan.has_rig_section:
        warnings.append("No Rig Slots section was found in the pasted scan.")

    return {
        "activity_flags": activity_flags,
        "has_service_section": parsed_scan.has_service_section,
        "has_rig_section": parsed_scan.has_rig_section,
        "services": matched_services,
        "rigs": matched_rigs,
        "warnings": warnings,
    }


def _split_tab_columns(line: str) -> list[str]:
    return [column.strip() for column in str(line or "").split("\t") if column.strip()]


def _get_supported_structure_type_names() -> set[str]:
    return {str(label) for _type_id, label in get_structure_type_options()}


def _is_structure_header(
    columns: list[str], supported_structure_names: set[str]
) -> bool:
    return bool(columns and columns[0] in supported_structure_names)


def _is_rig_line(columns: list[str]) -> bool:
    return bool(len(columns) == 1 and columns[0].lower().startswith("standup "))


def _derive_activity_flags(service_text: str) -> dict[str, bool]:
    resolved_flags = dict(DEFAULT_DISABLED_ACTIVITY_FLAGS)
    normalized_services = str(service_text or "").strip().lower()
    for service_name, field_name in PASTE_ACTIVITY_FLAG_MAP.items():
        if service_name in normalized_services:
            resolved_flags[field_name] = True
    return resolved_flags


def _has_only_unsupported_services(service_text: str) -> bool:
    normalized_services = str(service_text or "").strip().lower()
    if not normalized_services:
        return False
    if any(
        service_name in normalized_services for service_name in PASTE_ACTIVITY_FLAG_MAP
    ):
        return False
    return any(
        service_name in normalized_services
        for service_name in UNSUPPORTED_SERVICE_LABELS
    )


def parse_indy_structure_paste(
    raw_text: str,
) -> tuple[list[ParsedIndyPasteStructure], list[str]]:
    supported_structure_names = _get_supported_structure_type_names()
    parsed_entries: list[ParsedIndyPasteStructure] = []
    warnings: list[str] = []
    lines = str(raw_text or "").splitlines()
    index = 0

    while index < len(lines):
        current_line = lines[index].strip()
        if not current_line:
            index += 1
            continue

        header_columns = _split_tab_columns(current_line)
        if not _is_structure_header(header_columns, supported_structure_names):
            warnings.append(
                f"Skipped line {index + 1}: expected a structure header starting with a known structure type."
            )
            index += 1
            continue

        structure_type_name = header_columns[0]
        structure_name = header_columns[1] if len(header_columns) >= 2 else ""
        service_text = " ".join(header_columns[2:]).strip()
        if not structure_name:
            warnings.append(
                f"Skipped line {index + 1}: missing structure name for type '{structure_type_name}'."
            )
            index += 1
            continue

        rig_names: list[str] = []
        solar_system_name = ""
        index += 1

        while index < len(lines):
            detail_line = lines[index].strip()
            if not detail_line:
                index += 1
                continue

            detail_columns = _split_tab_columns(detail_line)
            if _is_rig_line(detail_columns):
                rig_names.append(detail_columns[0])
                index += 1
                continue

            if _is_structure_header(detail_columns, supported_structure_names):
                warnings.append(
                    f"Skipped '{structure_name}': missing solar system line after the structure block."
                )
                break

            solar_system_name = detail_columns[0] if detail_columns else ""
            index += 1
            break

        if not solar_system_name:
            continue

        parsed_entries.append(
            ParsedIndyPasteStructure(
                structure_type_name=structure_type_name,
                structure_name=structure_name,
                service_text=service_text,
                rig_names=tuple(rig_names[:3]),
                solar_system_name=solar_system_name,
            )
        )

    return parsed_entries, warnings


def import_indy_structure_paste(
    raw_text: str,
    *,
    update_existing_manual: bool = False,
) -> dict[str, object]:
    parsed_entries, warnings = parse_indy_structure_paste(raw_text)
    summary: dict[str, object] = {
        "processed": len(parsed_entries),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "warnings": warnings,
    }
    seen_structure_names: set[str] = set()

    for parsed_entry in parsed_entries:
        if parsed_entry.structure_name in seen_structure_names:
            summary["skipped"] += 1
            summary["warnings"].append(
                f"Skipped '{parsed_entry.structure_name}': duplicate structure name in the pasted batch."
            )
            continue
        seen_structure_names.add(parsed_entry.structure_name)

        structure_type_reference = resolve_item_type_reference(
            item_type_name=parsed_entry.structure_type_name
        )
        if structure_type_reference is None:
            summary["skipped"] += 1
            summary["warnings"].append(
                f"Skipped '{parsed_entry.structure_name}': unknown structure type '{parsed_entry.structure_type_name}'."
            )
            continue

        solar_system_reference = resolve_solar_system_reference(
            solar_system_name=parsed_entry.solar_system_name
        )
        if solar_system_reference is None:
            summary["skipped"] += 1
            summary["warnings"].append(
                f"Skipped '{parsed_entry.structure_name}': unknown solar system '{parsed_entry.solar_system_name}'."
            )
            continue

        solar_system_location_reference = resolve_solar_system_location_reference(
            solar_system_name=parsed_entry.solar_system_name
        )

        activity_flags = _derive_activity_flags(parsed_entry.service_text)
        if not any(activity_flags.values()):
            summary["skipped"] += 1
            if _has_only_unsupported_services(parsed_entry.service_text):
                continue
            else:
                summary["warnings"].append(
                    f"Skipped '{parsed_entry.structure_name}': no supported industry activity found in '{parsed_entry.service_text or 'empty service list'}'."
                )
            continue

        resolved_rigs: list[tuple[int, str]] = []
        for rig_name in parsed_entry.rig_names:
            rig_reference = resolve_item_type_reference(item_type_name=rig_name)
            if rig_reference is None:
                summary["warnings"].append(
                    f"Ignored rig '{rig_name}' on '{parsed_entry.structure_name}': rig type was not found in eve_sde."
                )
                continue
            resolved_rigs.append((int(rig_reference[0]), str(rig_reference[1])))

        structure_type_id, structure_type_name = structure_type_reference
        solar_system_id, solar_system_name, system_security_band = (
            solar_system_reference
        )
        existing_structure = IndustryStructure.objects.filter(
            name=parsed_entry.structure_name
        ).first()

        if existing_structure and existing_structure.is_synced_structure():
            summary["skipped"] += 1
            summary["warnings"].append(
                f"Skipped '{parsed_entry.structure_name}': a synchronized structure with the same name already exists."
            )
            continue

        if existing_structure and not update_existing_manual:
            summary["skipped"] += 1
            summary["warnings"].append(
                f"Skipped '{parsed_entry.structure_name}': a manual structure with the same name already exists. Enable manual updates to refresh it."
            )
            continue

        with transaction.atomic():
            if existing_structure is None:
                structure = IndustryStructure.objects.create(
                    name=parsed_entry.structure_name,
                    structure_type_id=structure_type_id,
                    structure_type_name=structure_type_name,
                    solar_system_id=solar_system_id,
                    solar_system_name=solar_system_name,
                    constellation_id=(
                        None
                        if solar_system_location_reference is None
                        else solar_system_location_reference["constellation_id"]
                    ),
                    constellation_name=(
                        ""
                        if solar_system_location_reference is None
                        else str(solar_system_location_reference["constellation_name"])
                    ),
                    region_id=(
                        None
                        if solar_system_location_reference is None
                        else solar_system_location_reference["region_id"]
                    ),
                    region_name=(
                        ""
                        if solar_system_location_reference is None
                        else str(solar_system_location_reference["region_name"])
                    ),
                    system_security_band=system_security_band,
                    sync_source=IndustryStructure.SyncSource.MANUAL,
                    **activity_flags,
                )
                summary["created"] += 1
            else:
                structure = existing_structure
                structure.structure_type_id = structure_type_id
                structure.structure_type_name = structure_type_name
                structure.solar_system_id = solar_system_id
                structure.solar_system_name = solar_system_name
                structure.constellation_id = (
                    None
                    if solar_system_location_reference is None
                    else solar_system_location_reference["constellation_id"]
                )
                structure.constellation_name = (
                    ""
                    if solar_system_location_reference is None
                    else str(solar_system_location_reference["constellation_name"])
                )
                structure.region_id = (
                    None
                    if solar_system_location_reference is None
                    else solar_system_location_reference["region_id"]
                )
                structure.region_name = (
                    ""
                    if solar_system_location_reference is None
                    else str(solar_system_location_reference["region_name"])
                )
                structure.system_security_band = system_security_band
                structure.sync_source = IndustryStructure.SyncSource.MANUAL
                for field_name, field_value in activity_flags.items():
                    setattr(structure, field_name, field_value)
                structure.save(
                    update_fields=[
                        "structure_type_id",
                        "structure_type_name",
                        "solar_system_id",
                        "solar_system_name",
                        "constellation_id",
                        "constellation_name",
                        "region_id",
                        "region_name",
                        "system_security_band",
                        "sync_source",
                        *activity_flags.keys(),
                        "updated_at",
                    ]
                )
                summary["updated"] += 1

            structure.rigs.all().delete()
            for slot_index, (rig_type_id, rig_type_name) in enumerate(
                resolved_rigs[:3], start=1
            ):
                IndustryStructureRig.objects.create(
                    structure=structure,
                    slot_index=slot_index,
                    rig_type_id=rig_type_id,
                    rig_type_name=rig_type_name,
                )

    return summary
