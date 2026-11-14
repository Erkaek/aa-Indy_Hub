"""Serve a disposable authenticated Craft Planner with pinned CCP fixtures.

Run with the repository's test interpreter. Only /tmp is written, external
connections are blocked before Django setup, and no active settings are loaded.
The printed manifest supplies the URL and synthetic cookie for Playwright.
"""

from __future__ import annotations

# Standard Library
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))


def serve(directory: Path, redis_socket: str) -> None:
    os.environ["DJANGO_SETTINGS_MODULE"] = "testauth.settings.local"
    os.environ["INDY_HUB_TEST_REDIS_URL"] = f"unix://{redis_socket}?db=1"
    # Django
    from django.conf import settings

    settings.DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(directory / "fixture.sqlite3"),
        }
    }
    settings.DEBUG = True
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    settings.STATIC_URL = "/static/"

    # Django
    import django

    django.setup()
    # Django
    from django.contrib.auth.models import User
    from django.contrib.staticfiles import finders
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.test import Client
    from django.utils import timezone

    # Alliance Auth (External Libs)
    from eve_sde import models as sde

    # AA Example App
    from indy_hub.models import (
        CachedCharacterAsset,
        IndustryStructure,
        IndustryStructureRig,
    )
    from indy_hub.services.production_projects import (
        create_project_from_single_blueprint,
    )
    from indy_hub.tests.test_smoke import assign_main_character

    call_command("migrate", interactive=False, verbosity=0)
    fixture = json.loads(
        (REPOSITORY / "indy_hub/tests/craft/fixtures/ccp_browser_sde.json").read_text()
    )
    for row in fixture["groups"]:
        category, _ = sde.ItemCategory.objects.get_or_create(
            id=row["categoryID"], defaults={"name": f"Category {row['categoryID']}"}
        )
        sde.ItemGroup.objects.create(
            id=row["_key"], name=row["name"]["en"], category=category, published=True
        )
    for row in fixture["types"]:
        market_id = row.get("marketGroupID")
        if market_id:
            sde.ItemMarketGroup.objects.get_or_create(
                id=market_id, defaults={"name": f"Fixture market group {market_id}"}
            )
        sde.ItemType.objects.create(
            id=row["_key"],
            name=row["name"]["en"],
            group_id=row["groupID"],
            published=row.get("published", True),
            volume=row.get("volume", 0),
            portion_size=row.get("portionSize", 1),
            market_group_id=market_id,
        )
    for row in fixture["blueprints"]:
        for activity, data in row["activities"].items():
            blueprint_activity = sde.BlueprintActivity.objects.create(
                id=f"{row['_key']}:{activity}",
                blueprint_item_type_id=row["_key"],
                activity=activity,
                time=data.get("time", 1),
            )
            for material in data.get("materials", []):
                sde.BlueprintActivityMaterial.objects.create(
                    blueprint_activity=blueprint_activity,
                    item_type_id=material["typeID"],
                    quantity=material["quantity"],
                )
            for product in data.get("products", []):
                sde.BlueprintActivityProduct.objects.create(
                    blueprint_activity=blueprint_activity,
                    item_type_id=product["typeID"],
                    quantity=product["quantity"],
                )
    for row in fixture["dogmaEffects"]:
        sde.DogmaEffect.objects.create(id=row["_key"], name=row["name"])
    for row in fixture["typeDogma"]:
        for attribute in row.get("dogmaAttributes", []):
            attribute_id = attribute["attributeID"]
            sde.DogmaAttribute.objects.get_or_create(
                id=attribute_id, defaults={"name": f"Attribute {attribute_id}"}
            )
            sde.TypeDogma.objects.create(
                item_type_id=row["_key"],
                dogma_attribute_id=attribute_id,
                value=attribute["value"],
            )
        for effect in row.get("dogmaEffects", []):
            sde.TypeEffect.objects.create(
                item_type_id=row["_key"],
                dogma_effect_id=effect["effectID"],
                is_default=effect.get("isDefault", False),
            )
    sde_state = sde.EveSDE.get_solo()
    sde_state.build_number = fixture["sde_build"]
    sde_state.save()
    for section, rows in (
        ("TypeDogma", fixture["typeDogma"]),
        ("TypeEffect", fixture["typeDogma"]),
        ("DogmaEffect", fixture["dogmaEffects"]),
        ("DogmaAttribute", fixture["typeDogma"]),
    ):
        sde.EveSDESection.objects.create(
            sde_section=section,
            build_number=fixture["sde_build"],
            last_update=timezone.now(),
            total_lines=len(rows),
            total_rows=len(rows),
        )

    # Additional official hull snapshots are test references; the unknown
    # product family below is deliberately synthetic and never added to them.
    hull_fixture = json.loads(
        (
            REPOSITORY / "indy_hub/tests/industry/fixtures/ccp_hull_references.json"
        ).read_text()
    )
    refinery_group, _ = sde.ItemGroup.objects.get_or_create(
        id=1406, defaults={"name": "Refinery", "category_id": 65, "published": True}
    )
    for row in hull_fixture["types"]:
        if row["_key"] in {35835, 35836}:
            sde.ItemType.objects.create(
                id=row["_key"],
                name=row["name"]["en"],
                group=refinery_group,
                published=True,
            )
    hull_effects = {row["_key"]: row["name"] for row in hull_fixture["dogmaEffects"]}
    for row in hull_fixture["typeDogma"]:
        if row["_key"] not in {35835, 35836}:
            continue
        for attribute in row["dogmaAttributes"]:
            attribute_id = attribute["attributeID"]
            sde.DogmaAttribute.objects.get_or_create(
                id=attribute_id, defaults={"name": f"Attribute {attribute_id}"}
            )
            sde.TypeDogma.objects.create(
                item_type_id=row["_key"],
                dogma_attribute_id=attribute_id,
                value=attribute["value"],
            )
        for effect in row["dogmaEffects"]:
            effect_id = effect["effectID"]
            sde.DogmaEffect.objects.get_or_create(
                id=effect_id, defaults={"name": hull_effects[effect_id]}
            )
            sde.TypeEffect.objects.create(
                item_type_id=row["_key"],
                dogma_effect_id=effect_id,
                is_default=effect["isDefault"],
            )

    future_group_id, future_product_id, future_blueprint_id = (
        99009901,
        99009902,
        99009903,
    )
    sde.ItemGroup.objects.create(
        id=future_group_id,
        name="Synthetic future reaction family",
        category_id=4,
        published=True,
    )
    sde.ItemType.objects.create(
        id=future_product_id,
        name="Synthetic future reaction <fixture>",
        group_id=future_group_id,
        published=True,
    )
    sde.ItemType.objects.create(
        id=future_blueprint_id,
        name="Synthetic future reaction formula",
        group_id=4097,
        published=True,
    )
    future_activity = sde.BlueprintActivity.objects.create(
        id=f"{future_blueprint_id}:reaction",
        blueprint_item_type_id=future_blueprint_id,
        activity="reaction",
        time=600,
    )
    sde.BlueprintActivityMaterial.objects.create(
        blueprint_activity=future_activity, item_type_id=34, quantity=10
    )
    sde.BlueprintActivityProduct.objects.create(
        blueprint_activity=future_activity, item_type_id=future_product_id, quantity=1
    )

    user = User.objects.create_superuser(
        "fixture-builder", "fixture@example.invalid", "fixture-only"
    )
    assign_main_character(user, character_id=99000001)
    for type_id, quantity in ((29992, 50), (34317, 100), (future_product_id, 3)):
        CachedCharacterAsset.objects.create(
            user=user,
            character_id=99000001,
            type_id=type_id,
            quantity=quantity,
            location_id=60003760,
            location_flag="Hangar",
        )
    structures = []
    for name, hull_type_id, rigs in (
        ("Fixture Raitaru hull", 35825, []),
        ("Fixture Raitaru Advanced Small", 35825, [43854, 43856]),
        ("Fixture Raitaru Structures", 35825, [43874]),
        ("Fixture Astrahus neutral", 35832, []),
    ):
        structure = IndustryStructure.objects.create(
            name=name,
            structure_type_id=hull_type_id,
            structure_type_name=sde.ItemType.objects.get(pk=hull_type_id).name,
            system_security_band="nullsec",
            enable_manufacturing=True,
        )
        for slot, type_id in enumerate(rigs, start=1):
            IndustryStructureRig.objects.create(
                structure=structure,
                slot_index=slot,
                rig_type_id=type_id,
                rig_type_name=sde.ItemType.objects.get(pk=type_id).name,
            )
        structures.append({"id": structure.id, "name": name})

    for name, hull_type_id, flags in (
        ("Fixture biochemical refinery", 35835, {"enable_biochemical_reactions": True}),
        (
            "Fixture polyvalent refinery",
            35836,
            {
                "enable_biochemical_reactions": True,
                "enable_hybrid_reactions": True,
                "enable_composite_reactions": True,
            },
        ),
    ):
        structure = IndustryStructure.objects.create(
            name=name,
            structure_type_id=hull_type_id,
            structure_type_name=sde.ItemType.objects.get(pk=hull_type_id).name,
            system_security_band="nullsec",
            **flags,
        )
        structures.append({"id": structure.id, "name": name})

    projects = []
    for blueprint_id, runs in ((34318, 100), (4316, 10), (future_blueprint_id, 10)):
        project = create_project_from_single_blueprint(
            user=user,
            blueprint_type_id=blueprint_id,
            blueprint_name=sde.ItemType.objects.get(pk=blueprint_id).name,
            runs=runs,
        )
        projects.append({"ref": project.project_ref, "blueprint_id": blueprint_id})
    client = Client()
    client.force_login(user)
    cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
    server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))
    manifest = {
        "jquery_path": finders.find("admin/js/vendor/jquery/jquery.min.js"),
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "cookie": {
            "name": settings.SESSION_COOKIE_NAME,
            "value": cookie,
            "url": f"http://127.0.0.1:{server.server_port}",
        },
        "projects": projects,
        "structures": structures,
        "future_reaction": {
            "group_id": future_group_id,
            "product_type_id": future_product_id,
            "blueprint_type_id": future_blueprint_id,
        },
        "database": str(directory / "fixture.sqlite3"),
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"FIXTURE_MANIFEST={manifest_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="indyhub-craft-browser-") as temporary:
        directory = Path(temporary)
        redis_socket = str(directory / "redis.sock")
        with (directory / "redis.log").open("w") as log:
            redis = subprocess.Popen(
                [
                    "redis-server",
                    "--port",
                    "0",
                    "--unixsocket",
                    redis_socket,
                    "--unixsocketperm",
                    "700",
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                ],
                stdout=log,
                stderr=log,
            )
            original_connect = socket.socket.connect

            def isolated_connect(sock, address):
                if sock.family == socket.AF_UNIX and address == redis_socket:
                    return original_connect(sock, address)
                raise OSError("External connection blocked by browser fixture")

            try:
                for _ in range(100):
                    if Path(redis_socket).exists():
                        break
                    if redis.poll() is not None:
                        raise RuntimeError("Private fixture Redis failed to start")
                    time.sleep(0.02)
                with patch.object(socket.socket, "connect", isolated_connect):
                    serve(directory, redis_socket)
            finally:
                redis.terminate()
                redis.wait(timeout=10)


if __name__ == "__main__":
    main()
