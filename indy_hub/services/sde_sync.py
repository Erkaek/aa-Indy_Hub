"""Synchronization helpers for SDE compatibility tables used by Indy Hub."""

from __future__ import annotations

# Standard Library
import json
from pathlib import Path

# Django
from django.db import transaction

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Alliance Auth (External Libs)
from eve_sde.models import ItemType

# AA Example App
from indy_hub.models import (
    SDEBlueprintActivity,
    SDEBlueprintActivityMaterial,
    SDEBlueprintActivityProduct,
    SDEIndustryActivity,
    SDEMarketGroup,
)

logger = get_extension_logger(__name__)

_ACTIVITY_NAME_BY_ID = {
    1: "Manufacturing",
    3: "TE Research",
    4: "ME Research",
    5: "Copying",
    8: "Invention",
    9: "Reactions",
    11: "Reactions",
}

_ACTIVITY_IDS_BY_KEY = {
    "manufacturing": [1],
    "research_time": [3],
    "research_material": [4],
    "copying": [5],
    "invention": [8],
    "reaction": [9, 11],
}


def _load_market_groups(folder: Path) -> int:
    file_path = folder / "marketGroups.jsonl"
    if not file_path.exists():
        logger.warning("SDE market group file missing: %s", file_path)
        return 0

    rows: list[dict] = []
    with file_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            rows.append(json.loads(line))

    creates: list[SDEMarketGroup] = []
    parent_map: dict[int, int | None] = {}

    for entry in rows:
        group_id = int(entry.get("_key"))
        parent_id = entry.get("parentGroupID")
        creates.append(
            SDEMarketGroup(
                id=group_id,
                name=(entry.get("name", {}) or {}).get("en") or str(group_id),
                description=(entry.get("description", {}) or {}).get("en") or "",
                has_types=bool(entry.get("hasTypes", False)),
                icon_id=entry.get("iconID"),
                parent_market_group_id=None,
            )
        )
        parent_map[group_id] = int(parent_id) if parent_id else None

    SDEMarketGroup.objects.all().delete()
    SDEMarketGroup.objects.bulk_create(creates, batch_size=5000)

    updates = []
    valid_ids = set(parent_map.keys())
    for group_id, parent_id in parent_map.items():
        if parent_id and parent_id in valid_ids:
            updates.append(
                SDEMarketGroup(id=group_id, parent_market_group_id=parent_id)
            )

    if updates:
        SDEMarketGroup.objects.bulk_update(
            updates,
            ["parent_market_group"],
            batch_size=5000,
        )

    return len(creates)


def _load_industry_rows(folder: Path) -> tuple[int, int, int, int]:
    file_path = folder / "blueprints.jsonl"
    if not file_path.exists():
        logger.warning("SDE blueprints file missing: %s", file_path)
        return 0, 0, 0, 0

    activities_to_create = [
        SDEIndustryActivity(id=activity_id, name=activity_name)
        for activity_id, activity_name in _ACTIVITY_NAME_BY_ID.items()
    ]

    valid_item_type_ids = set(ItemType.objects.values_list("id", flat=True))
    if not valid_item_type_ids:
        raise RuntimeError(
            "eve_sde ItemType is empty. Run esde_load_sde before sync_sde_compat."
        )

    products: list[SDEBlueprintActivityProduct] = []
    materials: list[SDEBlueprintActivityMaterial] = []
    blueprint_activities: list[SDEBlueprintActivity] = []

    with file_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            blueprint = json.loads(line)
            blueprint_type_id = blueprint.get("_key")
            if not blueprint_type_id:
                continue

            blueprint_type_id = int(blueprint_type_id)
            if blueprint_type_id not in valid_item_type_ids:
                continue
            activities = blueprint.get("activities") or {}
            if not isinstance(activities, dict):
                continue

            for key, payload in activities.items():
                activity_ids = _ACTIVITY_IDS_BY_KEY.get(str(key))
                if not activity_ids or not isinstance(payload, dict):
                    continue

                product_entries = payload.get("products") or []
                material_entries = payload.get("materials") or []

                for activity_id in activity_ids:
                    try:
                        activity_time = int(payload.get("time") or 0)
                    except (TypeError, ValueError):
                        activity_time = 0

                    blueprint_activities.append(
                        SDEBlueprintActivity(
                            eve_type_id=blueprint_type_id,
                            activity_id=activity_id,
                            time=max(activity_time, 0),
                        )
                    )

                    for product in product_entries:
                        try:
                            product_type_id = int(product.get("typeID"))
                            quantity = int(product.get("quantity") or 1)
                        except (TypeError, ValueError):
                            continue
                        if product_type_id not in valid_item_type_ids:
                            continue

                        products.append(
                            SDEBlueprintActivityProduct(
                                eve_type_id=blueprint_type_id,
                                activity_id=activity_id,
                                product_eve_type_id=product_type_id,
                                quantity=quantity,
                            )
                        )

                    for material in material_entries:
                        try:
                            material_type_id = int(material.get("typeID"))
                            quantity = int(material.get("quantity") or 0)
                        except (TypeError, ValueError):
                            continue
                        if material_type_id not in valid_item_type_ids:
                            continue

                        materials.append(
                            SDEBlueprintActivityMaterial(
                                eve_type_id=blueprint_type_id,
                                activity_id=activity_id,
                                material_eve_type_id=material_type_id,
                                quantity=quantity,
                            )
                        )

    SDEIndustryActivity.objects.all().delete()
    SDEIndustryActivity.objects.bulk_create(activities_to_create, batch_size=1000)

    SDEBlueprintActivityProduct.objects.all().delete()
    for index in range(0, len(products), 10000):
        SDEBlueprintActivityProduct.objects.bulk_create(
            products[index : index + 10000],
            batch_size=5000,
        )

    SDEBlueprintActivity.objects.all().delete()
    for index in range(0, len(blueprint_activities), 10000):
        SDEBlueprintActivity.objects.bulk_create(
            blueprint_activities[index : index + 10000],
            batch_size=5000,
        )

    SDEBlueprintActivityMaterial.objects.all().delete()
    for index in range(0, len(materials), 10000):
        SDEBlueprintActivityMaterial.objects.bulk_create(
            materials[index : index + 10000],
            batch_size=5000,
        )

    return (
        len(activities_to_create),
        len(products),
        len(materials),
        len(blueprint_activities),
    )


def sync_sde_compat_tables(*, sde_folder: str) -> dict[str, int]:
    folder = Path(sde_folder)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"SDE folder does not exist: {folder}")

    with transaction.atomic():
        market_groups_count = _load_market_groups(folder)
        (
            activities_count,
            products_count,
            materials_count,
            blueprint_activities_count,
        ) = _load_industry_rows(folder)

    summary = {
        "market_groups": market_groups_count,
        "activities": activities_count,
        "products": products_count,
        "materials": materials_count,
        "blueprint_activities": blueprint_activities_count,
    }
    logger.info("SDE compatibility sync finished: %s", summary)
    return summary
