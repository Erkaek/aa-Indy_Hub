#!/usr/bin/env python3

from __future__ import annotations

# Standard Library
import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT_ROOT = ROOT.parent.parent / "myauth"
DJANGO_PROJECT_ROOT = Path(
    os.environ.get("INDY_HUB_PROJECT_ROOT") or DEFAULT_PROJECT_ROOT
).resolve()

get_user_model = None
Sum = None
Client = None
reverse = None
timezone = None
CachedCharacterAsset = None
IndustryStructure = None
MaterialExchangeBuyOrder = None
MaterialExchangeConfig = None
MaterialExchangeSellOrder = None
MaterialExchangeStock = None
ProductionProject = None
ensure_task_submodules_imported = None
setup_periodic_tasks = None
_get_allowed_type_ids_for_config = None
_get_material_exchange_location_ids = None

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if DJANGO_PROJECT_ROOT.exists() and str(DJANGO_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(DJANGO_PROJECT_ROOT))

default_settings_module = (
    "myauth.settings.local"
    if (DJANGO_PROJECT_ROOT / "manage.py").exists()
    else "testauth.settings.local"
)
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.environ.get("INDY_HUB_SETTINGS_MODULE") or default_settings_module,
)


def bootstrap_django() -> None:
    global get_user_model
    global Sum
    global Client
    global reverse
    global timezone
    global CachedCharacterAsset
    global IndustryStructure
    global MaterialExchangeBuyOrder
    global MaterialExchangeConfig
    global MaterialExchangeSellOrder
    global MaterialExchangeStock
    global ProductionProject
    global ensure_task_submodules_imported
    global setup_periodic_tasks
    global _get_allowed_type_ids_for_config
    global _get_material_exchange_location_ids

    if get_user_model is not None:
        return

    # Django
    import django

    django.setup()

    # Django
    from django.contrib.auth import get_user_model as django_get_user_model
    from django.db.models import Sum as django_sum
    from django.test import Client as django_client
    from django.urls import reverse as django_reverse
    from django.utils import timezone as django_timezone

    # AA Example App
    from indy_hub.models import CachedCharacterAsset as cached_character_asset_model
    from indy_hub.models import IndustryStructure as industry_structure_model
    from indy_hub.models import (
        MaterialExchangeBuyOrder as material_exchange_buy_order_model,
    )
    from indy_hub.models import MaterialExchangeConfig as material_exchange_config_model
    from indy_hub.models import (
        MaterialExchangeSellOrder as material_exchange_sell_order_model,
    )
    from indy_hub.models import MaterialExchangeStock as material_exchange_stock_model
    from indy_hub.models import ProductionProject as production_project_model
    from indy_hub.tasks import (
        ensure_task_submodules_imported as ensure_task_submodules_imported_func,
    )
    from indy_hub.tasks import setup_periodic_tasks as setup_periodic_tasks_func
    from indy_hub.views.material_exchange import (
        _get_allowed_type_ids_for_config as get_allowed_type_ids_for_config_func,
    )
    from indy_hub.views.material_exchange import (
        _get_material_exchange_location_ids as get_material_exchange_location_ids_func,
    )

    get_user_model = django_get_user_model
    Sum = django_sum
    Client = django_client
    reverse = django_reverse
    timezone = django_timezone
    CachedCharacterAsset = cached_character_asset_model
    IndustryStructure = industry_structure_model
    MaterialExchangeBuyOrder = material_exchange_buy_order_model
    MaterialExchangeConfig = material_exchange_config_model
    MaterialExchangeSellOrder = material_exchange_sell_order_model
    MaterialExchangeStock = material_exchange_stock_model
    ProductionProject = production_project_model
    ensure_task_submodules_imported = ensure_task_submodules_imported_func
    setup_periodic_tasks = setup_periodic_tasks_func
    _get_allowed_type_ids_for_config = get_allowed_type_ids_for_config_func
    _get_material_exchange_location_ids = get_material_exchange_location_ids_func


class SmokeFailure(RuntimeError):
    pass


@dataclass
class SmokeArtifacts:
    structure_ids: list[int]
    sell_order_ids: list[int]
    buy_order_ids: list[int]


def log(message: str) -> None:
    print(message)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def response_text(response) -> str:
    return response.content.decode("utf-8", errors="replace")


def assert_ok(response, label: str, *, contains: str | None = None) -> str:
    expect(
        response.status_code == 200,
        f"{label}: expected HTTP 200, got {response.status_code}",
    )
    text = response_text(response)
    if contains:
        expect(contains in text, f"{label}: missing expected text: {contains!r}")
    log(f"[ok] {label}")
    return text


def choose_user(username: str | None):
    user_model = get_user_model()
    if username:
        user = user_model.objects.filter(username=username).first()
        expect(user is not None, f"User not found: {username}")
        return user

    sell_users = {
        username
        for username in MaterialExchangeSellOrder.objects.values_list(
            "seller__username", flat=True
        )
        if username
    }
    buy_users = {
        username
        for username in MaterialExchangeBuyOrder.objects.values_list(
            "buyer__username", flat=True
        )
        if username
    }
    preferred_usernames = sorted(sell_users & buy_users)
    if preferred_usernames:
        user = user_model.objects.filter(username=preferred_usernames[0]).first()
        if user:
            return user

    latest_sell = (
        MaterialExchangeSellOrder.objects.select_related("seller")
        .order_by("-id")
        .first()
    )
    if latest_sell:
        return latest_sell.seller

    latest_buy = (
        MaterialExchangeBuyOrder.objects.select_related("buyer").order_by("-id").first()
    )
    if latest_buy:
        return latest_buy.buyer

    user = user_model.objects.filter(is_superuser=True).order_by("id").first()
    if user:
        return user

    user = user_model.objects.filter(is_active=True).order_by("id").first()
    expect(user is not None, "No usable Django user found for smoke run.")
    return user


def run_task_health_check() -> None:
    ensure_task_submodules_imported()
    result = setup_periodic_tasks()
    expect(isinstance(result, dict), "Periodic task setup did not return a dict.")
    expect("unchanged" in result, "Periodic task setup result is incomplete.")
    log(f"[ok] periodic tasks {result}")


def check_core_pages(client: Client) -> None:
    assert_ok(
        client.get("/indy_hub/", follow=True), "overview page", contains="Indy Hub"
    )
    assert_ok(
        client.get("/indy_hub/bp-copy/fulfill/", follow=True), "bp-copy fulfill page"
    )
    assert_ok(
        client.get("/indy_hub/bp-copy/history/", follow=True),
        "bp-copy history page",
        contains="History",
    )
    assert_ok(
        client.get("/indy_hub/bp-copy/my-requests/", follow=True),
        "bp-copy my requests page",
        contains="My Requests",
    )
    assert_ok(
        client.get(reverse("indy_hub:material_exchange_index"), follow=True),
        "material exchange index",
        contains="Material Exchange",
    )
    assert_ok(
        client.get(reverse("indy_hub:my_orders"), follow=True),
        "material exchange my orders",
        contains="My Orders",
    )
    assert_ok(
        client.get(reverse("indy_hub:industry_structure_registry"), follow=True),
        "structure registry",
        contains="Structure Registry",
    )

    project_ref = (
        ProductionProject.objects.order_by("-updated_at")
        .values_list("project_ref", flat=True)
        .first()
    )
    if project_ref:
        assert_ok(
            client.get(
                reverse("indy_hub:craft_project", args=[project_ref]), follow=True
            ),
            "craft project workspace",
        )
    else:
        log("[skip] craft project workspace (no project found)")


def structure_post_data(name: str, manufacturing_tax: str) -> dict[str, str]:
    return {
        "name": name,
        "structure_type_id": "35825",
        "solar_system_name": "Jita",
        "enable_manufacturing": "1",
        "enable_research": "1",
        "enable_invention": "1",
        "manufacturing_tax_percent": manufacturing_tax,
        "manufacturing_capitals_tax_percent": "0.00",
        "manufacturing_super_capitals_tax_percent": "0.00",
        "research_tax_percent": "0.00",
        "invention_tax_percent": "0.00",
        "biochemical_reactions_tax_percent": "0.00",
        "hybrid_reactions_tax_percent": "0.00",
        "composite_reactions_tax_percent": "0.00",
        "rigs-TOTAL_FORMS": "3",
        "rigs-INITIAL_FORMS": "0",
        "rigs-MIN_NUM_FORMS": "0",
        "rigs-MAX_NUM_FORMS": "1000",
        "rigs-0-slot_index": "",
        "rigs-0-rig_type_id": "",
        "rigs-1-slot_index": "",
        "rigs-1-rig_type_id": "",
        "rigs-2-slot_index": "",
        "rigs-2-rig_type_id": "",
    }


def exercise_structure_registry(
    client: Client, user, artifacts: SmokeArtifacts
) -> None:
    suffix = timezone.now().strftime("%Y%m%d%H%M%S")
    created_name = f"ZZ Smoke Script Structure {suffix}"
    edited_name = f"{created_name} Edit"
    personal_tag = f"smoke-{suffix}"

    create_response = client.post(
        reverse("indy_hub:industry_structure_add"),
        structure_post_data(created_name, "1.25"),
        follow=True,
    )
    create_text = assert_ok(
        create_response,
        "structure add flow",
        contains="Structure registry entry created successfully.",
    )
    expect(created_name in create_text, "Created structure name not shown after add.")

    structure = IndustryStructure.objects.get(
        name=created_name,
        visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
    )
    artifacts.structure_ids.append(structure.id)

    edit_response = client.post(
        reverse("indy_hub:industry_structure_edit", args=[structure.id]),
        structure_post_data(edited_name, "1.50"),
        follow=True,
    )
    edit_text = assert_ok(
        edit_response,
        "structure edit flow",
        contains="Structure updated successfully.",
    )
    expect(edited_name in edit_text, "Edited structure name not shown after update.")
    structure.refresh_from_db()
    expect(structure.name == edited_name, "Structure name was not updated.")

    duplicate_response = client.post(
        reverse("indy_hub:industry_structure_duplicate", args=[structure.id]),
        {
            "personal_tag": personal_tag,
            "manufacturing_tax_percent": "1.75",
            "manufacturing_capitals_tax_percent": "0.00",
            "manufacturing_super_capitals_tax_percent": "0.00",
            "research_tax_percent": "0.00",
            "invention_tax_percent": "0.00",
            "biochemical_reactions_tax_percent": "0.00",
            "hybrid_reactions_tax_percent": "0.00",
            "composite_reactions_tax_percent": "0.00",
        },
        follow=True,
    )
    duplicate_text = assert_ok(
        duplicate_response,
        "structure duplicate flow",
        contains="Personal structure copy created successfully",
    )
    expect(personal_tag in duplicate_text, "Personal tag not shown after duplication.")

    duplicate = IndustryStructure.objects.get(
        source_structure=structure,
        owner_user=user,
        personal_tag=personal_tag,
    )
    artifacts.structure_ids.append(duplicate.id)

    delete_copy_response = client.post(
        reverse("indy_hub:industry_structure_delete", args=[duplicate.id]),
        follow=True,
    )
    assert_ok(
        delete_copy_response,
        "structure personal copy delete flow",
        contains=f"Structure {duplicate.display_name} deleted successfully.",
    )
    artifacts.structure_ids.remove(duplicate.id)

    delete_structure_response = client.post(
        reverse("indy_hub:industry_structure_delete", args=[structure.id]),
        follow=True,
    )
    assert_ok(
        delete_structure_response,
        "structure delete flow",
        contains=f"Structure {edited_name} deleted successfully.",
    )
    artifacts.structure_ids.remove(structure.id)


def pick_sell_candidate(config: MaterialExchangeConfig, user) -> list[int]:
    location_ids = _get_material_exchange_location_ids(config) or [
        int(config.structure_id)
    ]
    allowed_type_ids = _get_allowed_type_ids_for_config(config, "sell")
    queryset = (
        CachedCharacterAsset.objects.filter(
            user=user,
            location_id__in=location_ids,
            quantity__gt=0,
        )
        .values("type_id")
        .annotate(total_qty=Sum("quantity"))
        .order_by("-total_qty", "type_id")
    )
    type_ids = [int(row["type_id"]) for row in queryset]
    if allowed_type_ids is not None:
        type_ids = [type_id for type_id in type_ids if type_id in allowed_type_ids]
    return type_ids


def pick_buy_candidate(config: MaterialExchangeConfig) -> list[int]:
    allowed_type_ids = _get_allowed_type_ids_for_config(config, "buy")
    queryset = MaterialExchangeStock.objects.filter(
        config=config,
        quantity__gt=0,
        jita_buy_price__gt=0,
    ).order_by("-quantity", "type_id")
    type_ids = [int(item.type_id) for item in queryset]
    if allowed_type_ids is not None:
        type_ids = [type_id for type_id in type_ids if type_id in allowed_type_ids]
    return type_ids


def exercise_material_exchange(client: Client, user, artifacts: SmokeArtifacts) -> None:
    config = MaterialExchangeConfig.objects.first()
    expect(config is not None, "No Material Exchange configuration found.")

    sell_order = None
    sell_candidates = pick_sell_candidate(config, user)
    expect(sell_candidates, "No sell candidate found in cached character assets.")
    for index, type_id in enumerate(sell_candidates[:10], start=1):
        reference = f"SMOKE-SELL-{timezone.now().strftime('%H%M%S')}-{index}"
        response = client.post(
            reverse("indy_hub:material_exchange_sell"),
            {
                "sell_input_mode": "manual",
                "order_reference": reference,
                f"qty_{type_id}": "1",
            },
            follow=True,
        )
        sell_order = MaterialExchangeSellOrder.objects.filter(
            order_reference=reference
        ).first()
        if sell_order:
            artifacts.sell_order_ids.append(sell_order.id)
            text = assert_ok(
                response,
                "material exchange sell create flow",
                contains="Sell order created.",
            )
            expect(reference in text, "Sell order reference missing after creation.")
            detail = client.get(
                reverse("indy_hub:sell_order_detail", args=[sell_order.id]),
                follow=True,
            )
            assert_ok(
                detail, "material exchange sell detail", contains="Sell Order Details"
            )
            delete_response = client.post(
                reverse("indy_hub:sell_order_delete", args=[sell_order.id]),
                follow=True,
            )
            assert_ok(
                delete_response,
                "material exchange sell delete flow",
                contains=f"Sell order {reference} has been deleted.",
            )
            artifacts.sell_order_ids.remove(sell_order.id)
            break
    expect(sell_order is not None, "Unable to create a disposable sell order.")

    buy_order = None
    buy_candidates = pick_buy_candidate(config)
    expect(buy_candidates, "No buy candidate found in material exchange stock.")
    for index, type_id in enumerate(buy_candidates[:10], start=1):
        reference = f"SMOKE-BUY-{timezone.now().strftime('%H%M%S')}-{index}"
        response = client.post(
            reverse("indy_hub:material_exchange_buy"),
            {
                "order_reference": reference,
                f"qty_{type_id}": "1",
            },
            follow=True,
        )
        buy_order = MaterialExchangeBuyOrder.objects.filter(
            order_reference=reference
        ).first()
        if buy_order:
            artifacts.buy_order_ids.append(buy_order.id)
            text = assert_ok(
                response,
                "material exchange buy create flow",
                contains="Created buy order",
            )
            expect(reference in text, "Buy order reference missing after creation.")
            detail = client.get(
                reverse("indy_hub:buy_order_detail", args=[buy_order.id]),
                follow=True,
            )
            assert_ok(
                detail, "material exchange buy detail", contains="Buy Order Details"
            )
            delete_response = client.post(
                reverse("indy_hub:buy_order_delete", args=[buy_order.id]),
                follow=True,
            )
            assert_ok(
                delete_response,
                "material exchange buy delete flow",
                contains=f"Buy order {reference} has been deleted.",
            )
            artifacts.buy_order_ids.remove(buy_order.id)
            break
    expect(buy_order is not None, "Unable to create a disposable buy order.")


def cleanup_artifacts(artifacts: SmokeArtifacts) -> None:
    if artifacts.buy_order_ids:
        MaterialExchangeBuyOrder.objects.filter(id__in=artifacts.buy_order_ids).delete()
        log(f"[cleanup] removed lingering buy orders: {artifacts.buy_order_ids}")
        artifacts.buy_order_ids.clear()

    if artifacts.sell_order_ids:
        MaterialExchangeSellOrder.objects.filter(
            id__in=artifacts.sell_order_ids
        ).delete()
        log(f"[cleanup] removed lingering sell orders: {artifacts.sell_order_ids}")
        artifacts.sell_order_ids.clear()

    if artifacts.structure_ids:
        IndustryStructure.objects.filter(id__in=artifacts.structure_ids).delete()
        log(f"[cleanup] removed lingering structures: {artifacts.structure_ids}")
        artifacts.structure_ids.clear()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Indy Hub smoke workflow.")
    parser.add_argument(
        "--username",
        help="Django username to force-login for the smoke run. Defaults to the latest material exchange user.",
    )
    return parser.parse_args()


def main() -> int:
    bootstrap_django()
    args = parse_args()
    artifacts = SmokeArtifacts(structure_ids=[], sell_order_ids=[], buy_order_ids=[])
    client = Client()

    try:
        user = choose_user(args.username)
        client.force_login(user)
        log(f"[info] using user {user.username}")
        log(f"[info] django project root {DJANGO_PROJECT_ROOT}")
        log(f"[info] settings module {os.environ['DJANGO_SETTINGS_MODULE']}")

        run_task_health_check()
        check_core_pages(client)
        exercise_structure_registry(client, user, artifacts)
        exercise_material_exchange(client, user, artifacts)
        log("[ok] smoke run completed")
        return 0
    except SmokeFailure as exc:
        log(f"[error] {exc}")
        return 1
    finally:
        cleanup_artifacts(artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
