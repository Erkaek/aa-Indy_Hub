"""Helpers for importing structure registry entries from pasted Indy listings."""

from __future__ import annotations

# Standard Library
from dataclasses import dataclass

# Django
from django.db import transaction

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.models import IndustryStructure, IndustryStructureRig
from indy_hub.services.industry_structures import (
    get_structure_type_options,
    resolve_item_type_reference,
    resolve_solar_system_location_reference,
    resolve_solar_system_reference,
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


@dataclass(frozen=True)
class ParsedIndyPasteStructure:
    structure_type_name: str
    structure_name: str
    service_text: str
    rig_names: tuple[str, ...]
    solar_system_name: str


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
