"""Tests for the Material Exchange sell paste-import flow."""

# Standard Library
import json
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeSellOrder
from indy_hub.views.material_exchange import material_exchange_sell


def assign_main_character(user: User, *, character_id: int) -> EveCharacter:
    character, _ = EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={
            "character_name": f"Pilot {character_id}",
            "corporation_id": 2_000_000,
            "corporation_name": "Test Corp",
            "corporation_ticker": "TEST",
        },
    )
    CharacterOwnership.objects.update_or_create(
        user=user,
        character=character,
        defaults={"owner_hash": f"hash-{character_id}-{user.id}"},
    )
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.main_character = character
    profile.save(update_fields=["main_character"])
    return character


def grant_indy_permissions(user: User, *codenames: str) -> None:
    required = {"can_access_indy_hub"}
    required.update(codenames)
    permissions = Permission.objects.filter(codename__in=required)
    found = {perm.codename: perm for perm in permissions}
    missing = required - found.keys()
    if missing:
        raise AssertionError(f"Missing permissions: {sorted(missing)}")
    user.user_permissions.add(*found.values())


class MaterialExchangeSellPasteTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("sellpaste", password="secret123")
        self.character = assign_main_character(self.user, character_id=7_021_001)
        grant_indy_permissions(self.user)
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=self.character.corporation_id,
            structure_id=60_003_760,
            structure_name="C-N4OD - Fountain of Life",
            sell_markup_percent=Decimal("5.00"),
            sell_markup_base="buy",
            is_active=True,
            last_stock_sync=timezone.now(),
        )

        self.view = material_exchange_sell
        while hasattr(self.view, "__wrapped__"):
            self.view = self.view.__wrapped__

    def _prepare_request(self, request):
        request.user = self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_post_creates_sell_order_from_paste_quantities_json(self) -> None:
        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:material_exchange_sell"),
                {
                    "sell_input_mode": "paste",
                    "paste_quantities_json": json.dumps({"34": 4}),
                    "order_reference": "INDY-PASTE-0001",
                },
            )
        )

        with (
            patch("indy_hub.views.material_exchange.emit_view_analytics_event"),
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_config",
                return_value=self.config,
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure",
                return_value=({34: 10}, False),
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={34: {"buy": Decimal("5.00"), "sell": Decimal("6.00")}},
            ),
            patch(
                "indy_hub.views.material_exchange.get_type_name",
                return_value="Tritanium",
            ),
        ):
            response = self.view(request, tokens=[])

        self.assertEqual(response.status_code, 302)

        order = MaterialExchangeSellOrder.objects.get(order_reference="INDY-PASTE-0001")
        self.assertEqual(order.status, MaterialExchangeSellOrder.Status.DRAFT)
        self.assertEqual(order.rounded_total_price, Decimal("21"))
        self.assertEqual(order.items.count(), 1)

        order_item = order.items.get()
        self.assertEqual(order_item.type_id, 34)
        self.assertEqual(order_item.type_name, "Tritanium")
        self.assertEqual(order_item.quantity, 4)
        self.assertEqual(order_item.unit_price, Decimal("5.25"))
        self.assertEqual(order_item.total_price, Decimal("21.00"))
        self.assertEqual(
            response.headers["Location"],
            reverse("indy_hub:sell_order_detail", args=[order.id]),
        )

    def test_post_shows_specific_error_when_paste_mode_has_no_accepted_items(
        self,
    ) -> None:
        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:material_exchange_sell"),
                {
                    "sell_input_mode": "paste",
                    "paste_quantities_json": "{}",
                },
            )
        )

        with (
            patch("indy_hub.views.material_exchange.emit_view_analytics_event"),
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_config",
                return_value=self.config,
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure",
                return_value=({34: 10}, False),
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34},
            ),
        ):
            response = self.view(request, tokens=[])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"], reverse("indy_hub:material_exchange_sell")
        )
        self.assertFalse(MaterialExchangeSellOrder.objects.exists())
        self.assertIn(
            "Paste a list containing at least one accepted item.",
            [str(message) for message in get_messages(request)],
        )

    def test_get_builds_paste_catalog_with_accepted_and_rejected_items(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:material_exchange_sell"))
        )
        captured: dict[str, object] = {}

        def fake_render(_request, _template_name, context):
            captured["context"] = context
            return HttpResponse("ok")

        with (
            patch("indy_hub.views.material_exchange.emit_view_analytics_event"),
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_config",
                return_value=self.config,
            ),
            patch(
                "indy_hub.views.material_exchange._ensure_sell_assets_refresh_started",
                return_value={"running": False, "finished": True, "error": None},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure_data",
                return_value=(
                    {34: 10, 35: 5},
                    {self.character.character_id: {34: 10, 35: 5}},
                    False,
                ),
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={34: {"buy": Decimal("5.00"), "sell": Decimal("6.00")}},
            ),
            patch(
                "indy_hub.views.material_exchange.get_type_name",
                side_effect=lambda type_id: {34: "Tritanium", 35: "Unrefined Goo"}[
                    type_id
                ],
            ),
            patch(
                "indy_hub.views.material_exchange._get_group_map",
                return_value={34: "Minerals", 35: "Gas Clouds"},
            ),
            patch("indy_hub.views.material_exchange.batch_cache_type_names"),
            patch(
                "indy_hub.views.material_exchange.render",
                side_effect=fake_render,
            ),
        ):
            response = self.view(request, tokens=[])

        self.assertEqual(response.status_code, 200)
        context = captured["context"]
        self.assertEqual(len(context["materials"]), 1)
        self.assertEqual(context["materials"][0]["type_name"], "Tritanium")

        catalog = {entry["type_name"]: entry for entry in context["sell_paste_catalog"]}
        self.assertEqual(catalog["Tritanium"]["status"], "accepted")
        self.assertEqual(catalog["Tritanium"]["available_qty"], 10)
        self.assertEqual(catalog["Tritanium"]["unit_price"], "5.25")
        self.assertEqual(catalog["Unrefined Goo"]["status"], "rejected")
        self.assertEqual(catalog["Unrefined Goo"]["reason"], "not_bought")

    def test_get_marks_known_item_on_other_character_as_unavailable(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:material_exchange_sell"),
                {"character": str(self.character.character_id)},
            )
        )
        captured: dict[str, object] = {}

        other_character_id = self.character.character_id + 1

        def fake_render(_request, _template_name, context):
            captured["context"] = context
            return HttpResponse("ok")

        with (
            patch("indy_hub.views.material_exchange.emit_view_analytics_event"),
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_config",
                return_value=self.config,
            ),
            patch(
                "indy_hub.views.material_exchange._ensure_sell_assets_refresh_started",
                return_value={"running": False, "finished": True, "error": None},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure_data",
                return_value=(
                    {34: 10, 36: 3, 35: 5},
                    {
                        self.character.character_id: {34: 10, 35: 5},
                        other_character_id: {36: 3},
                    },
                    False,
                ),
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34, 36},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={
                    34: {"buy": Decimal("5.00"), "sell": Decimal("6.00")},
                    36: {"buy": Decimal("7.00"), "sell": Decimal("8.00")},
                },
            ),
            patch(
                "indy_hub.views.material_exchange.get_type_name",
                side_effect=lambda type_id: {
                    34: "Tritanium",
                    35: "Unrefined Goo",
                    36: "Large Skill Injector",
                }[type_id],
            ),
            patch(
                "indy_hub.views.material_exchange._get_group_map",
                return_value={34: "Minerals", 35: "Gas Clouds", 36: "Skill Injectors"},
            ),
            patch(
                "indy_hub.views.material_exchange._resolve_user_character_names_map",
                return_value={
                    self.character.character_id: self.character.character_name,
                    other_character_id: f"Pilot {other_character_id}",
                },
            ),
            patch("indy_hub.views.material_exchange.batch_cache_type_names"),
            patch(
                "indy_hub.views.material_exchange.render",
                side_effect=fake_render,
            ),
        ):
            response = self.view(request, tokens=[])

        self.assertEqual(response.status_code, 200)
        context = captured["context"]
        catalog = {entry["type_name"]: entry for entry in context["sell_paste_catalog"]}

        self.assertEqual(catalog["Tritanium"]["status"], "accepted")
        self.assertEqual(catalog["Large Skill Injector"]["status"], "unavailable")
        self.assertEqual(catalog["Large Skill Injector"]["reason"], "not_on_character")
        self.assertEqual(catalog["Large Skill Injector"]["available_qty"], 0)
        self.assertEqual(catalog["Large Skill Injector"]["total_available_qty"], 3)
        self.assertEqual(catalog["Unrefined Goo"]["status"], "rejected")
        self.assertEqual(catalog["Unrefined Goo"]["reason"], "not_bought")

    def test_get_ajax_character_switch_returns_fragment_html(self) -> None:
        other_character = assign_main_character(
            self.user, character_id=self.character.character_id + 1
        )
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:material_exchange_sell"),
                {"character": str(other_character.character_id)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        )

        with (
            patch("indy_hub.views.material_exchange.emit_view_analytics_event"),
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_config",
                return_value=self.config,
            ),
            patch(
                "indy_hub.views.material_exchange._ensure_sell_assets_refresh_started",
                return_value={"running": False, "finished": True, "error": None},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure_data",
                return_value=(
                    {34: 10, 35: 2},
                    {
                        self.character.character_id: {34: 10},
                        other_character.character_id: {35: 2},
                    },
                    False,
                ),
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34, 35},
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={
                    34: {"buy": Decimal("5.00"), "sell": Decimal("6.00")},
                    35: {"buy": Decimal("7.00"), "sell": Decimal("8.00")},
                },
            ),
            patch(
                "indy_hub.views.material_exchange.get_type_name",
                side_effect=lambda type_id: {34: "Tritanium", 35: "Pyerite"}[type_id],
            ),
            patch(
                "indy_hub.views.material_exchange._get_group_map",
                return_value={34: "Minerals", 35: "Minerals"},
            ),
            patch("indy_hub.views.material_exchange.batch_cache_type_names"),
            patch(
                "indy_hub.views.material_exchange._get_corp_name_for_hub",
                return_value="Test Corp",
            ),
            patch(
                "indy_hub.views.material_exchange._build_nav_context",
                return_value={},
            ),
            patch(
                "indy_hub.views.material_exchange.build_nav_context",
                return_value={},
            ),
        ):
            response = self.view(request, tokens=[])

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertIn('id="sellCharacterSelect"', payload["html"])
        self.assertIn("Pyerite", payload["html"])
        self.assertNotIn('<div class="page-header mb-4">', payload["html"])
