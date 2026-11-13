"""Small fixture helpers for the isolated, serial test databases."""

# Standard Library
import json
from pathlib import Path

# Django
from django.db.models import Max
from django.utils import timezone


def create_raw_fixture(instance):
    """Insert without save/signals and retain a usable integer primary key.

    MySQL does not populate generated keys after bulk_create(). Allocate the
    key explicitly in our private, serial test database so related fixtures
    work on SQLite, MySQL and MariaDB without token-link or task signals.
    This allocation is only suitable for isolated test fixtures.
    """
    manager = type(instance).objects
    if instance.pk is None:
        instance.pk = (manager.aggregate(last_pk=Max("pk"))["last_pk"] or 0) + 1
    manager.bulk_create([instance])
    return instance


def load_ccp_hull_fixture(type_id=35825):
    """Install a pinned official hull into the disposable test SDE database."""
    # Alliance Auth (External Libs)
    from eve_sde import models as sde

    fixture = json.loads(
        (
            Path(__file__).parent / "industry/fixtures/ccp_hull_references.json"
        ).read_text()
    )
    item = next(row for row in fixture["types"] if row["_key"] == type_id)
    dogma = next(row for row in fixture["typeDogma"] if row["_key"] == type_id)
    category, _ = sde.ItemCategory.objects.get_or_create(
        id=65, defaults={"name": "Structure"}
    )
    group, _ = sde.ItemGroup.objects.get_or_create(
        id=item["groupID"],
        defaults={"name": "Fixture hull group", "category": category},
    )
    sde.ItemType.objects.update_or_create(
        id=type_id,
        defaults={"name": item["name"]["en"], "group": group, "published": True},
    )
    for attribute in dogma.get("dogmaAttributes", []):
        definition, _ = sde.DogmaAttribute.objects.get_or_create(
            id=attribute["attributeID"],
            defaults={"name": f"Attribute {attribute['attributeID']}"},
        )
        sde.TypeDogma.objects.update_or_create(
            item_type_id=type_id,
            dogma_attribute=definition,
            defaults={"value": attribute["value"]},
        )
    effects = {row["_key"]: row["name"] for row in fixture["dogmaEffects"]}
    for effect in dogma.get("dogmaEffects", []):
        definition, _ = sde.DogmaEffect.objects.get_or_create(
            id=effect["effectID"], defaults={"name": effects[effect["effectID"]]}
        )
        sde.TypeEffect.objects.update_or_create(
            item_type_id=type_id,
            dogma_effect=definition,
            defaults={"is_default": effect.get("isDefault", False)},
        )
    for section in ("TypeDogma", "TypeEffect", "DogmaEffect", "DogmaAttribute"):
        sde.EveSDESection.objects.update_or_create(
            sde_section=section,
            defaults={
                "build_number": fixture["sde_build"],
                "last_update": timezone.now(),
                "total_lines": 1,
                "total_rows": 1,
            },
        )
