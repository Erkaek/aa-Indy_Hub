"""UI regression tests for Material Exchange bulk quantity actions."""

# Standard Library
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import MaterialExchangeConfig, MaterialExchangeStock
from indy_hub.views.material_exchange import (
    material_exchange_buy,
    material_exchange_sell,
)


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


class MaterialExchangeBulkActionsUiTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("bulkui", password="secret123")
        self.character = assign_main_character(self.user, character_id=7_031_001)
        grant_indy_permissions(self.user)
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=self.character.corporation_id,
            structure_id=60_003_760,
            structure_name="C-N4OD - Fountain of Life",
            hangar_division=1,
            sell_markup_percent=Decimal("5.00"),
            sell_markup_base="buy",
            buy_markup_percent=Decimal("5.00"),
            buy_markup_base="buy",
            is_active=True,
            last_stock_sync=timezone.now(),
            last_price_sync=timezone.now(),
        )

        self.sell_view = material_exchange_sell
        while hasattr(self.sell_view, "__wrapped__"):
            self.sell_view = self.sell_view.__wrapped__

        self.buy_view = material_exchange_buy
        while hasattr(self.buy_view, "__wrapped__"):
            self.buy_view = self.buy_view.__wrapped__

    def _prepare_request(self, request):
        request.user = self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_sell_page_renders_visible_bulk_buttons(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:material_exchange_sell"))
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
                return_value=({34: 10}, {self.character.character_id: {34: 10}}, False),
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
            patch(
                "indy_hub.views.material_exchange._get_group_map",
                return_value={34: "Minerals"},
            ),
            patch(
                "indy_hub.views.material_exchange._resolve_user_character_names_map",
                return_value={
                    self.character.character_id: self.character.character_name
                },
            ),
            patch("indy_hub.views.material_exchange.batch_cache_type_names"),
            patch(
                "indy_hub.views.material_exchange._get_corp_name_for_hub",
                return_value="Test Corp",
            ),
            patch(
                "indy_hub.views.material_exchange._build_nav_context", return_value={}
            ),
            patch(
                "indy_hub.views.material_exchange.build_nav_context", return_value={}
            ),
        ):
            response = self.sell_view(request, tokens=[])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="sellBulkClearVisible"')
        self.assertContains(response, 'id="sellBulkMaxVisible"')
        self.assertContains(response, 'data-action="clear-visible"')
        self.assertContains(response, 'data-action="max-visible"')

    def test_buy_page_renders_visible_bulk_buttons(self) -> None:
        MaterialExchangeStock.objects.create(
            config=self.config,
            type_id=34,
            type_name="Tritanium",
            quantity=120,
            jita_buy_price=Decimal("5.00"),
            jita_sell_price=Decimal("6.00"),
            last_stock_sync=timezone.now(),
            last_price_update=timezone.now(),
        )
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:material_exchange_buy"))
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
                "indy_hub.views.material_exchange._get_material_exchange_accepted_locations",
                return_value=[
                    {
                        "structure_name": self.config.structure_name,
                        "hangar_division": self.config.hangar_division,
                    }
                ],
            ),
            patch(
                "indy_hub.views.material_exchange._get_material_exchange_location_summary",
                return_value=self.config.structure_name,
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34},
            ),
            patch(
                "indy_hub.views.material_exchange._get_group_map",
                return_value={34: "Minerals"},
            ),
            patch("indy_hub.views.material_exchange._normalize_stock_type_names"),
            patch(
                "indy_hub.views.material_exchange._resolve_type_image_url",
                return_value="https://images.evetech.net/types/34/icon",
            ),
            patch(
                "indy_hub.views.material_exchange.get_corp_divisions_cached",
                return_value=({1: "Division 1"}, False),
            ),
            patch(
                "indy_hub.views.material_exchange._build_nav_context", return_value={}
            ),
            patch(
                "indy_hub.views.material_exchange.build_nav_context", return_value={}
            ),
        ):
            response = self.buy_view(request, tokens=[])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="buyBulkClearVisible"')
        self.assertContains(response, 'id="buyBulkMaxVisible"')
        self.assertContains(response, 'data-action="clear-visible"')
        self.assertContains(response, 'data-action="max-visible"')
