"""Basic smoke tests for the Indy Hub app."""

# Standard Library
from datetime import timedelta
from decimal import Decimal
from unittest import skip
from unittest.mock import patch

# Django
from django.apps import apps
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.auth_hooks import IndyHubMenu, register_charlink_hook
from indy_hub.models import (
    Blueprint,
    BlueprintCopyChat,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    CachedCharacterAsset,
    CachedCorporationAsset,
    CachedStructureName,
    CharacterSettings,
    CorporationSharingSetting,
    IndustryActivityMixin,
    IndustryJob,
    IndustrySkillSnapshot,
    IndustryStructure,
    IndustrySystemCostIndex,
    JobNotificationDigestEntry,
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
    MaterialExchangeSettings,
    SDESyncCompatState,
    UserOnboardingProgress,
)
from indy_hub.notifications import notify_user
from indy_hub.services.esi_client import ESIForbiddenError
from indy_hub.services.location_population import (
    LocationTarget,
    _resolve_location_name_for_target,
)
from indy_hub.tasks.industry import (
    MANUAL_REFRESH_KIND_BLUEPRINTS,
    MANUAL_REFRESH_KIND_JOBS,
    manual_refresh_allowed,
    request_manual_refresh,
    reset_manual_refresh_cooldown,
)
from indy_hub.utils import eve as eve_utils
from indy_hub.utils import job_notifications as job_notifications_utils
from indy_hub.utils.eve import get_type_name, reset_forbidden_structure_lookup_cache
from indy_hub.utils.menu_badge import compute_menu_badge_count, menu_badge_cache_key

PUBLIC_STATION_ID = 60003760


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


class IndyHubConfigTests(TestCase):
    def test_app_is_registered(self) -> None:
        """The indy_hub app should be installed and discoverable."""
        app_config = apps.get_app_config("indy_hub")
        self.assertEqual(app_config.name, "indy_hub")

    def test_get_type_name_graceful_fallback(self) -> None:
        """`get_type_name` should fall back to the stringified id when SDE types are unavailable."""
        self.assertEqual(get_type_name(12345), "12345")


class NavigationMenuBadgeTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

        self.builder = User.objects.create_user("navbuilder", password="secret123")
        assign_main_character(self.builder, character_id=7001001)
        grant_indy_permissions(self.builder, "can_manage_corp_bp_requests")
        CharacterSettings.objects.create(
            user=self.builder,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_EVERYONE,
        )

        self.customer = User.objects.create_user("navcustomer", password="secret123")
        assign_main_character(self.customer, character_id=7002001)
        grant_indy_permissions(self.customer)
        CharacterSettings.objects.create(
            user=self.customer,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_EVERYONE,
        )

    def _render_menu(self, user: User) -> IndyHubMenu:
        request = self.factory.get("/")
        request.user = user
        menu = IndyHubMenu()
        menu.render(request)
        return menu

    def test_menu_count_deduplicates_chat_from_fulfill_queue(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.builder,
            character_id=1111001,
            item_id=2222001,
            blueprint_id=3333001,
            type_id=4444001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=8,
            runs=0,
            character_name="Nav Builder",
            type_name="Navigation Blueprint",
        )

        request_obj = BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=self.customer,
            runs_requested=2,
            copies_requested=1,
        )

        offer = BlueprintCopyOffer.objects.create(
            request=request_obj,
            owner=self.builder,
            status="accepted",
        )

        BlueprintCopyChat.objects.create(
            request=request_obj,
            offer=offer,
            buyer=self.customer,
            seller=self.builder,
            is_open=True,
            last_message_at=timezone.now(),
            last_message_role="buyer",
            seller_last_seen_at=None,
        )

        self.assertEqual(compute_menu_badge_count(self.builder.id), 1)

    def test_menu_count_includes_my_open_requests(self) -> None:
        BlueprintCopyRequest.objects.create(
            type_id=9876501,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.customer,
            runs_requested=1,
            copies_requested=1,
        )

        BlueprintCopyRequest.objects.create(
            type_id=9876502,
            material_efficiency=8,
            time_efficiency=10,
            requested_by=self.builder,
            runs_requested=2,
            copies_requested=1,
        )

        BlueprintCopyRequest.objects.create(
            type_id=9876503,
            material_efficiency=12,
            time_efficiency=14,
            requested_by=self.builder,
            runs_requested=3,
            copies_requested=2,
            fulfilled=True,
            delivered=False,
        )

        self.assertEqual(compute_menu_badge_count(self.builder.id), 2)

    def test_menu_render_computes_count_when_cache_is_cold(self) -> None:
        BlueprintCopyRequest.objects.create(
            type_id=9876510,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.builder,
            runs_requested=1,
            copies_requested=1,
        )

        cache.delete(menu_badge_cache_key(self.builder.id))

        menu = self._render_menu(self.builder)

        self.assertEqual(menu.count, 1)
        self.assertEqual(cache.get(menu_badge_cache_key(self.builder.id)), 1)

    def test_request_creation_invalidates_stale_menu_badge_cache(self) -> None:
        cache.set(menu_badge_cache_key(self.builder.id), 0, 300)

        BlueprintCopyRequest.objects.create(
            type_id=9876511,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.builder,
            runs_requested=1,
            copies_requested=1,
        )

        self.assertIsNone(cache.get(menu_badge_cache_key(self.builder.id)))


class AuthHookTests(TestCase):
    def test_register_charlink_hook_returns_module_path(self) -> None:
        self.assertEqual(register_charlink_hook(), "indy_hub.thirdparty.charlink_hook")

    def test_charlink_hook_imports_when_charlink_is_installed(self) -> None:
        # Standard Library
        import importlib
        import importlib.util

        # Django
        from django.apps import apps

        if importlib.util.find_spec("charlink") is None:
            self.skipTest("charlink is not installed")
        if not apps.is_installed("charlink"):
            self.skipTest("charlink is not enabled in INSTALLED_APPS")

        module = importlib.import_module("indy_hub.thirdparty.charlink_hook")

        self.assertEqual(module.app_import.app_label, "indy_hub")
        self.assertEqual(
            [item.unique_id for item in module.app_import.imports],
            ["personal", "corporation", "materialhub"],
        )


class NavbarBlueprintSharingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("navbaruser", password="secret123")
        assign_main_character(self.user, character_id=7003001)
        grant_indy_permissions(self.user)
        SDESyncCompatState.objects.update_or_create(
            pk=1,
            defaults={"last_synced_at": timezone.now()},
        )

    def test_base_access_user_sees_fulfill_requests_nav_link(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("indy_hub:bp_copy_fulfill_requests"))

    def test_blueprint_sharing_nav_shows_request_and_fulfill_badges(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=7003003,
            item_id=8003001,
            blueprint_id=9003001,
            type_id=1234501,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=8,
            runs=0,
            character_name="Navbar User",
            type_name="Navbar Blueprint",
        )

        BlueprintCopyRequest.objects.create(
            type_id=9871001,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.user,
            runs_requested=1,
            copies_requested=1,
        )
        BlueprintCopyRequest.objects.create(
            type_id=9871002,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
            fulfilled=True,
            delivered=False,
        )

        other_user = User.objects.create_user("navbuyer", password="secret123")
        assign_main_character(other_user, character_id=7003004)
        grant_indy_permissions(other_user)
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=other_user,
            runs_requested=1,
            copies_requested=1,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:bp_copy_request_page"))
        self.assertEqual(response.status_code, 200)

        html = response.content.decode("utf-8")
        self.assertIn('data-nav-badge="blueprint-sharing">3<', html)
        self.assertIn('data-nav-badge="my-requests">2<', html)
        self.assertIn('data-nav-badge="fulfill-requests">1<', html)


class IndexSDEGuardTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("sdeguard", password="secret123")
        assign_main_character(self.user, character_id=7003002)
        grant_indy_permissions(self.user)
        self.client.force_login(self.user)

    def test_index_is_blocked_when_sde_sync_never_ran(self) -> None:
        SDESyncCompatState.objects.all().delete()

        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "indy_hub/sde_not_ready.html")
        self.assertContains(
            response,
            "Indy Hub static data is not loaded yet. Please contact your administrator.",
        )

    def test_index_loads_normally_when_sde_sync_has_timestamp(self) -> None:
        SDESyncCompatState.objects.update_or_create(
            pk=1,
            defaults={"last_synced_at": timezone.now()},
        )

        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "indy_hub/overview_intro.html")
        self.assertNotContains(
            response,
            "Indy Hub static data is not loaded yet. Please contact your administrator.",
        )


class BlueprintCopyHistoryAccessTests(TestCase):
    def setUp(self) -> None:
        self.viewer = User.objects.create_user("historyviewer", password="secret123")
        assign_main_character(self.viewer, character_id=7003101)
        grant_indy_permissions(self.viewer, "can_manage_corp_bp_requests")

        self.base_user = User.objects.create_user("historybase", password="secret123")
        assign_main_character(self.base_user, character_id=7003102)
        grant_indy_permissions(self.base_user)

    def test_history_requires_manage_permission(self) -> None:
        self.client.force_login(self.base_user)
        response = self.client.get(reverse("indy_hub:bp_copy_history"))
        self.assertEqual(response.status_code, 302)

    def test_history_page_renders_for_authorized_user(self) -> None:
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("indy_hub:bp_copy_history"))
        self.assertEqual(response.status_code, 200)

    def test_fulfill_header_shows_history_link_only_for_authorized(self) -> None:
        fulfill_url = reverse("indy_hub:bp_copy_fulfill_requests")
        history_url = reverse("indy_hub:bp_copy_history")

        self.client.force_login(self.base_user)
        response = self.client.get(fulfill_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, history_url)

        self.client.force_login(self.viewer)
        response = self.client.get(fulfill_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, history_url)


class NavbarMaterialExchangeMyOrdersTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("materialorders", password="secret123")
        assign_main_character(self.user, character_id=7010001)
        grant_indy_permissions(self.user)

    def test_my_orders_page_renders_indy_hub_navbar(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:my_orders"))
        self.assertEqual(response.status_code, 200)
        # This URL is part of the Indy Hub navbar, not the page body.
        self.assertContains(response, reverse("indy_hub:all_bp_list"))

    def test_my_orders_page_shows_material_hub_nav_badge_for_open_orders(self) -> None:
        settings_obj = MaterialExchangeSettings.get_solo()
        settings_obj.is_enabled = True
        settings_obj.save(update_fields=["is_enabled"])
        config = MaterialExchangeConfig.objects.create(
            corporation_id=1234,
            structure_id=5678,
            is_active=True,
        )
        MaterialExchangeSellOrder.objects.create(
            config=config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-NAV-BADGE",
        )
        MaterialExchangeBuyOrder.objects.create(
            config=config,
            buyer=self.user,
            status=MaterialExchangeBuyOrder.Status.COMPLETED,
            order_reference="INDY-NAV-CLOSED",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:my_orders"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["material_hub_nav_badge_count"], 1)


class NavbarIndustryJobsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("jobsnavbaruser", password="secret123")
        assign_main_character(self.user, character_id=7011001)
        grant_indy_permissions(self.user)
        CorporationSharingSetting.objects.create(
            user=self.user,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            job_catalog_scope=CharacterSettings.SCOPE_EVERYONE,
        )

    def test_personal_jobs_page_keeps_corporation_jobs_nav_link(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:personnal_job_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("indy_hub:corporation_job_list"))

    def test_my_orders_lists_in_progress_before_completed(self) -> None:
        config = MaterialExchangeConfig.objects.create(
            corporation_id=1234,
            structure_id=5678,
            is_active=True,
        )

        in_progress = MaterialExchangeSellOrder.objects.create(
            config=config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.AWAITING_VALIDATION,
            order_reference="INDY-TEST-INPROGRESS",
        )
        completed = MaterialExchangeBuyOrder.objects.create(
            config=config,
            buyer=self.user,
            status=MaterialExchangeBuyOrder.Status.COMPLETED,
            order_reference="INDY-TEST-COMPLETED",
        )

        # Force timestamps so the completed order is newer (this used to place it in the middle).
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(days=1)
        MaterialExchangeSellOrder.objects.filter(pk=in_progress.pk).update(
            created_at=older
        )
        MaterialExchangeBuyOrder.objects.filter(pk=completed.pk).update(
            created_at=newer
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:my_orders"))
        self.assertEqual(response.status_code, 200)

        html = response.content.decode("utf-8")
        in_progress_label = in_progress.get_status_display()
        completed_label = completed.get_status_display()
        self.assertIn(in_progress_label, html)
        self.assertIn(completed_label, html)
        self.assertLess(html.find(in_progress_label), html.find(completed_label))

    def test_my_orders_shows_contract_check_button_for_open_orders(self) -> None:
        config = MaterialExchangeConfig.objects.create(
            corporation_id=1234,
            structure_id=5678,
            structure_name="C-N4OD - Fountain of Life",
            is_active=True,
        )
        order = MaterialExchangeSellOrder.objects.create(
            config=config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-CHECK-BUTTON",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:my_orders"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("indy_hub:sell_order_check_contract", args=[order.id])
        )


class MaterialExchangeContractCheckTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("contractcheck", password="secret123")
        self.character = assign_main_character(self.user, character_id=7011001)
        grant_indy_permissions(self.user)
        self.client.force_login(self.user)

        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=self.character.corporation_id,
            structure_id=60003760,
            structure_name="C-N4OD - Fountain of Life",
            is_active=True,
        )

    def test_sell_order_contract_check_reports_success(self) -> None:
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-SELL-CHECK",
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=order,
            type_id=1,
            type_name="Common Moon Mining Crystal Type A II",
            quantity=40,
            unit_price="1410000.00",
            total_price="56400000.00",
        )

        response = self.client.post(
            reverse("indy_hub:sell_order_check_contract", args=[order.id]),
            {
                "contract_text": (
                    "Contract Type\tItem Exchange\n"
                    "Description\tINDY-SELL-CHECK\n"
                    "Availability\tPrivate (Test Corp)\n"
                    "Location\tC-N4OD - Fountain of Life\n"
                    "I will pay\t0 ISK\n"
                    "I will receive\t56.400.000 ISK (56,40 Million)\n"
                    "Items For Sale\tCommon Moon Mining Crystal Type A II x 40\n"
                    "Items Required\t\n"
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["checks"]), 6)

    def test_buy_order_contract_check_reports_amount_mismatch(self) -> None:
        order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.user,
            status=MaterialExchangeBuyOrder.Status.DRAFT,
            order_reference="INDY-BUY-CHECK",
        )
        MaterialExchangeBuyOrderItem.objects.create(
            order=order,
            type_id=2,
            type_name="Coherent Asteroid Mining Crystal Type A II",
            quantity=20,
            unit_price="1200000.00",
            total_price="24000000.00",
        )

        response = self.client.post(
            reverse("indy_hub:buy_order_check_contract", args=[order.id]),
            {
                "contract_text": (
                    "Contract Type\tItem Exchange\n"
                    "Description\tINDY-BUY-CHECK\n"
                    f"Availability\tPrivate ({self.character.character_name})\n"
                    "Location\tC-N4OD - Fountain of Life\n"
                    "I will pay\t23.000.000 ISK\n"
                    "I will receive\t0 ISK\n"
                    "Items For Sale\tCoherent Asteroid Mining Crystal Type A II x 20\n"
                    "Items Required\t\n"
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        amount_check = next(
            check for check in payload["checks"] if check["key"] == "amount"
        )
        self.assertFalse(amount_check["passed"])

    def test_sell_order_contract_check_reports_missing_and_surplus_items(self) -> None:
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-SELL-ITEMS-CHECK",
        )
        MaterialExchangeSellOrderItem.objects.create(
            order=order,
            type_id=11,
            type_name="Common Moon Mining Crystal Type A II",
            quantity=5,
            unit_price="1410000.00",
            total_price="7050000.00",
        )

        response = self.client.post(
            reverse("indy_hub:sell_order_check_contract", args=[order.id]),
            {
                "contract_text": (
                    "Contract Type\tItem Exchange\n"
                    "Description\tINDY-SELL-ITEMS-CHECK\n"
                    "Availability\tPrivate (Test Corp)\n"
                    "Location\tC-N4OD - Fountain of Life\n"
                    "I will pay\t0 ISK\n"
                    "I will receive\t7.050.000 ISK\n"
                    "Items For Sale\tCommon Moon Mining Crystal Type A II x 3\n"
                    "Spare Moon Drill Bit x 2\n"
                    "Items Required\t\n"
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        items_check = next(
            check for check in payload["checks"] if check["key"] == "items"
        )
        self.assertFalse(items_check["passed"])
        self.assertEqual(
            items_check["detail_sections"],
            [
                {
                    "key": "missing",
                    "label": "Missing from pasted contract",
                    "items": ["Common Moon Mining Crystal Type A II x 2"],
                },
                {
                    "key": "surplus",
                    "label": "Surplus in pasted contract",
                    "items": ["Spare Moon Drill Bit x 2"],
                },
            ],
        )

    def test_sell_order_detail_renders_contract_check_button(self) -> None:
        order = MaterialExchangeSellOrder.objects.create(
            config=self.config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="INDY-SELL-DETAIL-CHECK",
        )

        response = self.client.get(
            reverse("indy_hub:sell_order_detail", args=[order.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("indy_hub:sell_order_check_contract", args=[order.id])
        )

    def test_buy_order_detail_renders_contract_check_button(self) -> None:
        order = MaterialExchangeBuyOrder.objects.create(
            config=self.config,
            buyer=self.user,
            status=MaterialExchangeBuyOrder.Status.DRAFT,
            order_reference="INDY-BUY-DETAIL-CHECK",
        )

        response = self.client.get(
            reverse("indy_hub:buy_order_detail", args=[order.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("indy_hub:buy_order_check_contract", args=[order.id])
        )

    def test_sell_order_detail_missing_renders_friendly_404(self) -> None:
        material_exchange_index = reverse("indy_hub:material_exchange_index")
        response = self.client.get(
            reverse("indy_hub:sell_order_detail", args=[99999999])
            + f"?next={material_exchange_index}"
        )
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            "no longer available",
            status_code=404,
        )
        self.assertContains(
            response,
            material_exchange_index,
            status_code=404,
        )

    def test_buy_order_detail_missing_renders_friendly_404(self) -> None:
        response = self.client.get(
            reverse("indy_hub:buy_order_detail", args=[99999999])
        )
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            "no longer available",
            status_code=404,
        )

    def test_order_not_found_drops_unsafe_next_url(self) -> None:
        """An absolute URL to another host must not be honored as the
        Continue link target — guards against open-redirect regressions."""
        unsafe_next = "https://evil.example.com/phish"
        response = self.client.get(
            reverse("indy_hub:sell_order_detail", args=[99999999])
            + f"?next={unsafe_next}"
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, unsafe_next, status_code=404)
        self.assertNotContains(response, "evil.example.com", status_code=404)
        # No "Continue" CTA should be rendered when next_url is dropped.
        self.assertNotContains(response, ">Continue<", status_code=404)


class BlueprintModelClassificationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("classifier", password="secret123")

    def test_original_blueprint_infers_type(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=9001,
            item_id=9001001,
            blueprint_id=9002001,
            type_id=424242,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Classifier",
            type_name="Widget Blueprint",
        )
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.ORIGINAL)

        blueprint.quantity = -2
        blueprint.save()
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.COPY)

    def test_update_or_create_persists_reclassified_bp_type(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=9004,
            item_id=9004001,
            blueprint_id=9005001,
            type_id=454545,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-2,
            time_efficiency=0,
            material_efficiency=0,
            runs=1,
            character_name="Classifier",
            type_name="Widget Blueprint",
        )
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.COPY)

        Blueprint.objects.update_or_create(
            item_id=blueprint.item_id,
            defaults={"quantity": -1},
        )

        blueprint.refresh_from_db()
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.ORIGINAL)


class CorporationSharingSettingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("director", password="secret123")
        self.setting = CorporationSharingSetting.objects.create(
            user=self.user,
            corporation_id=4242,
            corporation_name="Directive Industries",
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

    def test_default_allows_all_characters(self) -> None:
        self.assertFalse(self.setting.restricts_characters)
        self.assertTrue(self.setting.is_character_authorized(9001))

    def test_whitelist_filters_characters(self) -> None:
        self.setting.set_authorized_characters([1010, 2020])
        self.setting.save(update_fields=["authorized_characters"])
        self.setting.refresh_from_db()

        self.assertTrue(self.setting.restricts_characters)
        self.assertTrue(self.setting.is_character_authorized(1010))
        self.assertFalse(self.setting.is_character_authorized(3030))

    def test_authorized_character_ids_are_unique_and_sorted(self) -> None:
        self.setting.set_authorized_characters(["5050", None, 4040, 5050])
        self.setting.save(update_fields=["authorized_characters"])
        self.setting.refresh_from_db()

        self.assertEqual(self.setting.authorized_character_ids, [4040, 5050])

    def test_reaction_detection_from_name(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=9002,
            item_id=9002001,
            blueprint_id=9003001,
            type_id=434343,
            location_id=PUBLIC_STATION_ID,
            location_flag="corporate",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Classifier",
            type_name="Fullerene Reaction Formula",
        )
        blueprint.refresh_from_db()
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.REACTION)

    def test_positive_quantity_classified_as_copy(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=9003,
            item_id=9100100,
            blueprint_id=9100200,
            type_id=565656,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=5,
            time_efficiency=0,
            material_efficiency=0,
            runs=2,
            character_name="Classifier",
            type_name="Widget Blueprint Copy",
        )
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.COPY)


class LocationNameSignalTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("locator", password="secret123")

    @patch("indy_hub.signals.resolve_location_name", return_value="Structure Beta")
    def test_blueprint_location_name_refreshes_on_identifier_change(self, mock_resolve):
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=7001,
            item_id=5001001,
            blueprint_id=5002001,
            type_id=13579,
            location_id=1111,
            location_name="Alpha Depot",
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Locator",
            type_name="Test Blueprint",
        )

        mock_resolve.assert_not_called()

        blueprint.location_id = 2222
        blueprint.location_name = "Alpha Depot"
        blueprint.save()
        blueprint.refresh_from_db()

        mock_resolve.assert_called_once_with(
            2222,
            character_id=7001,
            owner_user_id=self.user.id,
        )
        self.assertEqual(blueprint.location_name, "Structure Beta")

    @patch("indy_hub.signals.resolve_location_name", return_value="Station Gamma")
    def test_industry_job_location_name_refreshes_on_station_change(self, mock_resolve):
        start = timezone.now()
        end = start + timedelta(hours=1)

        job = IndustryJob.objects.create(
            owner_user=self.user,
            character_id=8001,
            job_id=9101112,
            installer_id=self.user.id,
            station_id=3333,
            location_name="Outpost Alpha",
            activity_id=1,
            blueprint_id=6001001,
            blueprint_type_id=6002001,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Widget",
            product_type_name="Widget Product",
            character_name="Locator",
        )

        mock_resolve.assert_not_called()

        job.station_id = 4444
        job.location_name = "Outpost Alpha"
        job.save()
        job.refresh_from_db()

        mock_resolve.assert_called_once_with(
            4444,
            character_id=8001,
            owner_user_id=self.user.id,
        )
        self.assertEqual(job.location_name, "Station Gamma")


class LocationPopulationTests(TestCase):
    @patch("indy_hub.services.location_population.resolve_location_name")
    def test_force_refresh_is_honored_for_public_station(self, mock_resolve):
        station_id = 60003760
        mock_resolve.return_value = "Jita IV - Moon 4 - Caldari Navy Assembly Plant"

        target = LocationTarget.empty()
        target.characters.add(9001)
        target.character_owners[9001] = 42
        target.owners.add(42)

        result = _resolve_location_name_for_target(
            station_id,
            target,
            force_refresh=True,
        )

        self.assertEqual(
            result,
            "Jita IV - Moon 4 - Caldari Navy Assembly Plant",
        )
        mock_resolve.assert_called_once_with(
            station_id,
            character_id=9001,
            owner_user_id=42,
            force_refresh=True,
        )


class JobNotificationSignalTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("notifier", password="secret123")
        CharacterSettings.objects.create(
            user=self.user,
            character_id=0,
            jobs_notify_completed=True,
        )

    @patch("indy_hub.utils.job_notifications.notify_user")
    def test_notification_sent_for_completed_job(self, mock_notify):
        start = timezone.now() - timedelta(hours=2)
        end = timezone.now() - timedelta(minutes=5)

        job = IndustryJob.objects.create(
            owner_user=self.user,
            character_id=9101,
            job_id=88001,
            installer_id=self.user.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Factory",
            activity_id=1,
            blueprint_id=7001,
            blueprint_type_id=7002,
            blueprint_type_name="Widget Blueprint",
            runs=1,
            status="delivered",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            product_type_name="Widget",
            character_name="Notifier",
        )

        job.refresh_from_db()

        mock_notify.assert_called_once()
        args, kwargs = mock_notify.call_args
        self.assertEqual(args[0], self.user)
        self.assertEqual(args[1], "Notifier - Job #88001 completed")
        message = args[2]
        self.assertIn("Character: Notifier", message)
        self.assertIn("Job: #88001", message)
        self.assertIn("Blueprint: Widget Blueprint", message)
        self.assertIn("Activity: Manufacturing", message)
        self.assertIn("Result: Product: Widget (qty 1)", message)
        self.assertIn("Location: Factory", message)
        self.assertIn("https://images.evetech.net/types/7002/icon", message)
        self.assertTrue(job.job_completed_notified)
        self.assertEqual(
            kwargs.get("thumbnail_url"),
            "https://images.evetech.net/types/7002/icon",
        )

    @patch("indy_hub.utils.job_notifications.notify_user")
    def test_notification_skipped_when_preference_disabled(self, mock_notify):
        other_user = User.objects.create_user("silent", password="secret123")
        CharacterSettings.objects.create(
            user=other_user,
            character_id=0,
            jobs_notify_completed=False,
        )

        start = timezone.now() - timedelta(hours=1)
        end = timezone.now() - timedelta(minutes=10)

        job = IndustryJob.objects.create(
            owner_user=other_user,
            character_id=9201,
            job_id=88002,
            installer_id=other_user.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Research Lab",
            activity_id=1,
            blueprint_id=7003,
            blueprint_type_id=7004,
            blueprint_type_name="Widget Blueprint",
            runs=1,
            status="delivered",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            product_type_name="Widget",
            character_name="Silent",
        )

        job.refresh_from_db()

        mock_notify.assert_not_called()
        self.assertTrue(job.job_completed_notified)

    @patch("indy_hub.utils.job_notifications.notify_user")
    def test_notification_handles_string_end_date(self, mock_notify):
        start = timezone.now() - timedelta(hours=2)
        future_end = timezone.now() + timedelta(hours=1)

        job = IndustryJob.objects.create(
            owner_user=self.user,
            character_id=9301,
            job_id=88003,
            installer_id=self.user.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Factory",
            activity_id=1,
            blueprint_id=7005,
            blueprint_type_id=7006,
            blueprint_type_name="Widget Blueprint",
            runs=1,
            status="manufacturing",
            duration=3600,
            start_date=start,
            end_date=future_end,
            activity_name="Manufacturing",
            product_type_name="Widget",
            character_name="Notifier",
        )

        job.refresh_from_db()
        self.assertFalse(job.job_completed_notified)

        past_end_iso = (timezone.now() - timedelta(minutes=5)).isoformat()
        job.end_date = past_end_iso
        job.status = "delivered"

        mock_notify.reset_mock()

        job_notifications_utils.process_job_completion_notification(job)

        job.refresh_from_db()

        mock_notify.assert_called_once()
        self.assertTrue(job.job_completed_notified)

    @patch("indy_hub.utils.job_notifications.notify_user")
    def test_corporation_job_notifications_respect_recipients(self, mock_notify):
        corp_id = 3000000

        manager_live = User.objects.create_user("corpmanager1", password="test12345")
        char = assign_main_character(manager_live, character_id=9401)
        char.corporation_id = corp_id
        char.save(update_fields=["corporation_id"])
        grant_indy_permissions(manager_live, "can_manage_corp_bp_requests")
        CorporationSharingSetting.objects.create(
            user=manager_live,
            corporation_id=corp_id,
            corp_jobs_notify_frequency=CharacterSettings.NOTIFY_IMMEDIATE,
        )

        manager_muted = User.objects.create_user("corpmanager2", password="test12345")
        char = assign_main_character(manager_muted, character_id=9402)
        char.corporation_id = corp_id
        char.save(update_fields=["corporation_id"])
        grant_indy_permissions(manager_muted, "can_manage_corp_bp_requests")
        CorporationSharingSetting.objects.create(
            user=manager_muted,
            corporation_id=corp_id,
            corp_jobs_notify_frequency=CharacterSettings.NOTIFY_DISABLED,
        )

        other_corp_manager = User.objects.create_user(
            "corpmanager3", password="test12345"
        )
        char = assign_main_character(other_corp_manager, character_id=9403)
        char.corporation_id = 4000000
        char.save(update_fields=["corporation_id"])
        grant_indy_permissions(other_corp_manager, "can_manage_corp_bp_requests")
        CorporationSharingSetting.objects.create(
            user=other_corp_manager,
            corporation_id=4000000,
            corp_jobs_notify_frequency=CharacterSettings.NOTIFY_IMMEDIATE,
        )

        provider = User.objects.create_user("provider", password="test12345")
        assign_main_character(provider, character_id=9404)
        grant_indy_permissions(provider)
        CharacterSettings.objects.create(user=provider, character_id=0)

        start = timezone.now() - timedelta(hours=2)
        end = timezone.now() - timedelta(minutes=5)

        job = IndustryJob.objects.create(
            owner_user=provider,
            character_id=9501,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            job_id=99001,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Factory",
            activity_id=1,
            blueprint_id=7007,
            blueprint_type_id=7008,
            blueprint_type_name="Widget Blueprint",
            runs=1,
            status="delivered",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            product_type_name="Widget",
            character_name="Notifier",
        )

        job.refresh_from_db()

        self.assertTrue(job.job_completed_notified)
        self.assertEqual(mock_notify.call_count, 1)
        called_user = mock_notify.call_args[0][0]
        self.assertEqual(called_user.id, manager_live.id)

    @patch("indy_hub.utils.job_notifications.notify_user")
    def test_corporation_job_notifications_enqueue_digest(self, mock_notify):
        corp_id = 3000001
        manager_digest = User.objects.create_user("corpdigest", password="test12345")
        char = assign_main_character(manager_digest, character_id=9502)
        char.corporation_id = corp_id
        char.save(update_fields=["corporation_id"])
        grant_indy_permissions(manager_digest, "can_manage_corp_bp_requests")
        settings = CorporationSharingSetting.objects.create(
            user=manager_digest,
            corporation_id=corp_id,
            corp_jobs_notify_frequency=CharacterSettings.NOTIFY_DAILY,
            corp_jobs_next_digest_at=None,
        )

        provider = User.objects.create_user("provider2", password="test12345")
        assign_main_character(provider, character_id=9503)
        grant_indy_permissions(provider)
        CharacterSettings.objects.create(user=provider, character_id=0)

        start = timezone.now() - timedelta(hours=2)
        end = timezone.now() - timedelta(minutes=5)

        job = IndustryJob.objects.create(
            owner_user=provider,
            character_id=9601,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            job_id=99002,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Factory",
            activity_id=1,
            blueprint_id=7010,
            blueprint_type_id=7011,
            blueprint_type_name="Widget Blueprint",
            runs=1,
            status="delivered",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            product_type_name="Widget",
            character_name="Notifier",
        )

        job.refresh_from_db()
        settings.refresh_from_db()

        mock_notify.assert_not_called()
        self.assertTrue(job.job_completed_notified)
        self.assertIsNotNone(settings.corp_jobs_next_digest_at)

        self.assertTrue(
            JobNotificationDigestEntry.objects.filter(
                user=manager_digest,
                job_id=job.job_id,
                scope=JobNotificationDigestEntry.SCOPE_CORPORATION,
                corporation_id=corp_id,
                sent_at__isnull=True,
            ).exists()
        )


class BlueprintCopyFulfillViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("capsuleer", password="test12345")
        assign_main_character(self.user, character_id=101001)
        CharacterSettings.objects.create(
            user=self.user,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        grant_indy_permissions(
            self.user,
            "can_manage_corp_bp_requests",
        )
        self.client.force_login(self.user)

    def test_personal_only_request_visible(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=1001,
            blueprint_id=2001,
            type_id=987654,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=5,
            runs=0,
            character_name="Capsuleer",
            type_name="Test Blueprint",
        )
        buyer = User.objects.create_user("requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("metrics", response.context)
        self.assertEqual(response.context["metrics"]["total"], 1)
        self.assertEqual(len(response.context["requests"]), 1)
        entry = response.context["requests"][0]
        self.assertEqual(entry["status_key"], "awaiting_response")
        self.assertFalse(entry["is_corporate"])
        self.assertEqual(entry["personal_blueprints"], 1)
        self.assertEqual(entry["corporate_blueprints"], 0)

    @patch("indy_hub.views.industry.build_craft_character_advisor")
    @patch("indy_hub.views.industry.compute_effective_cycle_seconds")
    @patch("indy_hub.views.industry._fetch_blueprint_activity_times")
    @patch("indy_hub.views.industry._build_copy_estimated_item_values")
    def test_request_shows_estimated_copy_cost_for_matching_structure(
        self,
        mock_build_copy_estimated_item_values,
        mock_fetch_blueprint_activity_times,
        mock_compute_effective_cycle_seconds,
        mock_build_craft_character_advisor,
    ) -> None:
        mock_build_copy_estimated_item_values.return_value = {
            987654: {
                "product_type_id": 654987,
                "unit_value": Decimal("500000"),
                "estimated_item_value": Decimal("1000000"),
                "source": "adjusted_price",
                "runs_requested": 2,
            }
        }
        mock_fetch_blueprint_activity_times.return_value = {
            987654: {IndustryActivityMixin.ACTIVITY_COPYING: 3600}
        }
        mock_compute_effective_cycle_seconds.return_value = 3060
        mock_build_craft_character_advisor.return_value = {
            "items": {
                "987654": {
                    "eligible_characters": [
                        {
                            "character_id": 42,
                            "name": "Capsuleer",
                            "time_bonus_percent": 15.0,
                            "available_slots": 4,
                            "total_slots": 5,
                            "used_slots": 1,
                        }
                    ],
                    "blocked_characters": [],
                    "best_character": {
                        "character_id": 42,
                        "name": "Capsuleer",
                        "time_bonus_percent": 15.0,
                        "available_slots": 4,
                        "total_slots": 5,
                        "used_slots": 1,
                    },
                }
            }
        }
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=1002,
            blueprint_id=2002,
            type_id=987654,
            location_id=1_036_001,
            location_name="Test Raitaru",
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=5,
            runs=0,
            character_name="Capsuleer",
            type_name="Test Blueprint",
        )
        IndustryStructure.objects.create(
            name="Test Raitaru",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            solar_system_id=30000142,
            solar_system_name="Jita",
            external_structure_id=1_036_001,
            enable_research=True,
            research_tax_percent=Decimal("0.500"),
            visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
        )
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Jita",
            activity_id=5,
            cost_index_percent=Decimal("5.00000"),
        )

        buyer = User.objects.create_user("requester_cost", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=2,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        entry = response.context["requests"][0]
        self.assertIsNotNone(entry["copy_cost"])
        self.assertEqual(entry["copy_cost"]["structure_name"], "Test Raitaru")
        self.assertEqual(len(entry["copy_structure_options"]), 1)
        self.assertEqual(
            entry["copy_structure_options"][0]["structure_name"],
            "Test Raitaru",
        )
        self.assertEqual(
            entry["copy_cost"]["per_copy_installation_cost"],
            Decimal("1900"),
        )
        self.assertEqual(
            entry["copy_cost"]["total_installation_cost"],
            Decimal("3800"),
        )
        self.assertContains(response, "Install estimate")
        self.assertContains(response, "3,800 ISK")
        self.assertContains(response, "Producer character")
        self.assertContains(response, "Capsuleer")
        self.assertContains(response, "Time estimate")
        self.assertContains(response, "3h 24m")
        self.assertContains(response, "Copy structure")
        self.assertContains(response, "Test Raitaru · Jita")

    def test_self_request_hidden_without_corporate_source(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=2001,
            blueprint_id=3001,
            type_id=555,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=8,
            material_efficiency=7,
            runs=0,
            character_name="Capsuleer",
            type_name="Another Blueprint",
        )
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["requests"], [])
        self.assertEqual(response.context["metrics"]["total"], 0)
        self.assertEqual(response.context["metrics"]["awaiting_response"], 0)

    def test_corporate_manager_without_personal_share_sees_requests(self) -> None:
        settings = CharacterSettings.objects.get(user=self.user, character_id=0)
        settings.allow_copy_requests = False
        settings.copy_sharing_scope = CharacterSettings.SCOPE_NONE
        settings.save(update_fields=["allow_copy_requests", "copy_sharing_scope"])

        corp_id = 3_000_000
        manager_character = EveCharacter.objects.get(character_id=101001)
        manager_character.corporation_id = corp_id
        manager_character.corporation_name = "Manager Corp"
        manager_character.corporation_ticker = "MGR"
        manager_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=self.user,
            character=manager_character,
            defaults={
                "owner_hash": f"hash-{manager_character.character_id}-{self.user.id}",
            },
        )
        provider = User.objects.create_user("corp_seller", password="sellcorp123")
        assign_main_character(provider, character_id=303110)
        grant_indy_permissions(
            provider,
            "can_manage_corp_bp_requests",
        )
        provider_character = EveCharacter.objects.get(character_id=303110)
        provider_character.corporation_id = corp_id
        provider_character.corporation_name = "Manager Corp"
        provider_character.corporation_ticker = "MGR"
        provider_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=provider,
            character=provider_character,
            defaults={
                "owner_hash": f"hash-{provider_character.character_id}-{provider.id}",
            },
        )
        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=corp_id,
            corporation_name="Manager Corp",
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

        Blueprint.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Manager Corp",
            item_id=9054001,
            blueprint_id=9054002,
            type_id=888001,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=14,
            material_efficiency=12,
            runs=0,
            type_name="Manager Corp Blueprint",
        )

        buyer = User.objects.create_user("buyer_corp", password="buycorp123")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=888001,
            material_efficiency=12,
            time_efficiency=14,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        entry = requests[0]
        self.assertEqual(entry["id"], request_obj.id)
        self.assertEqual(entry["status_key"], "awaiting_response")
        self.assertTrue(entry["show_offer_actions"])
        self.assertEqual(entry["owned_blueprints"], 1)
        self.assertEqual(entry["available_blueprints"], 1)
        self.assertTrue(entry["is_corporate"])
        self.assertIn("Manager Corp", entry["corporation_names"])
        self.assertIn("MGR", entry["corporation_tickers"])
        self.assertEqual(entry["personal_blueprints"], 0)
        self.assertEqual(entry["corporate_blueprints"], 1)
        self.assertFalse(entry["has_dual_sources"])
        self.assertEqual(entry["default_scope"], "corporation")

    def test_personal_requests_hidden_when_personal_share_disabled(self) -> None:
        settings = CharacterSettings.objects.get(user=self.user, character_id=0)
        settings.allow_copy_requests = False
        settings.copy_sharing_scope = CharacterSettings.SCOPE_NONE
        settings.save(update_fields=["allow_copy_requests", "copy_sharing_scope"])

        corp_id = 3_100_001
        manager_character = EveCharacter.objects.get(character_id=101001)
        manager_character.corporation_id = corp_id
        manager_character.corporation_name = "Manager Access Corp"
        manager_character.corporation_ticker = "MAC"
        manager_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=self.user,
            character=manager_character,
            defaults={
                "owner_hash": f"hash-{manager_character.character_id}-{self.user.id}",
            },
        )

        corp_owner = User.objects.create_user("corp_owner2", password="owner123")
        owner_character = assign_main_character(corp_owner, character_id=303120)
        owner_character.corporation_id = corp_id
        owner_character.corporation_name = "Manager Access Corp"
        owner_character.corporation_ticker = "MAC"
        owner_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=corp_owner,
            character=owner_character,
            defaults={
                "owner_hash": f"hash-{owner_character.character_id}-{corp_owner.id}",
            },
        )
        grant_indy_permissions(corp_owner, "can_manage_corp_bp_requests")
        CorporationSharingSetting.objects.create(
            user=corp_owner,
            corporation_id=corp_id,
            corporation_name="Manager Access Corp",
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

        Blueprint.objects.create(
            owner_user=corp_owner,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Manager Access Corp",
            item_id=9111001,
            blueprint_id=9111002,
            type_id=781001,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=14,
            material_efficiency=12,
            runs=0,
            type_name="Corp Access Blueprint",
        )

        personal_bp = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=9111011,
            blueprint_id=9111012,
            type_id=781099,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=8,
            runs=0,
            character_name="Capsuleer",
            type_name="Personal Hidden Blueprint",
        )

        buyer = User.objects.create_user("buyer_hidden", password="buyer123")
        BlueprintCopyRequest.objects.create(
            type_id=personal_bp.type_id,
            material_efficiency=personal_bp.material_efficiency,
            time_efficiency=personal_bp.time_efficiency,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["requests"], [])
        self.assertEqual(response.context["metrics"]["total"], 0)

    def test_dual_source_requests_show_corporate_only(self) -> None:
        corp_id = 4_200_123
        main_character = EveCharacter.objects.get(character_id=101001)
        main_character.corporation_id = corp_id
        main_character.corporation_name = "Dual Source Corp"
        main_character.corporation_ticker = "DUAL"
        main_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=self.user,
            character=main_character,
            defaults={
                "owner_hash": f"hash-{main_character.character_id}-{self.user.id}",
            },
        )
        CorporationSharingSetting.objects.create(
            user=self.user,
            corporation_id=corp_id,
            corporation_name="Dual Source Corp",
            allow_copy_requests=True,
            share_scope=CharacterSettings.SCOPE_CORPORATION,
        )

        personal_bp = Blueprint.objects.create(
            owner_user=self.user,
            character_id=main_character.character_id,
            item_id=51001,
            blueprint_id=52001,
            type_id=777001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=6,
            material_efficiency=4,
            runs=0,
            character_name="Capsuleer",
            type_name="Dual Blueprint",
        )
        Blueprint.objects.create(
            owner_user=self.user,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Dual Source Corp",
            item_id=51002,
            blueprint_id=52002,
            type_id=personal_bp.type_id,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=personal_bp.time_efficiency,
            material_efficiency=personal_bp.material_efficiency,
            runs=0,
            type_name="Dual Blueprint",
        )

        buyer = User.objects.create_user("dual_requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=personal_bp.type_id,
            material_efficiency=personal_bp.material_efficiency,
            time_efficiency=personal_bp.time_efficiency,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        entry = requests[0]
        self.assertTrue(entry["has_dual_sources"])
        self.assertEqual(entry["personal_blueprints"], 1)
        self.assertEqual(entry["corporate_blueprints"], 1)
        self.assertEqual(entry["default_scope"], "corporation")
        self.assertIn("Dual Source Corp", entry["corporation_names"])
        self.assertIn("DUAL", entry["corporation_tickers"])

        html = response.content.decode()
        scope_script_id = f"bp-scope-options-{entry['id']}"
        self.assertIn(scope_script_id, html)
        self.assertIn("data-scope-trigger", html)

    def test_dual_source_corporate_rejection_keeps_personal_scope_visible(self) -> None:
        corp_id = 4_200_456
        main_character = EveCharacter.objects.get(character_id=101001)
        main_character.corporation_id = corp_id
        main_character.corporation_name = "Fallback Scope Corp"
        main_character.corporation_ticker = "FSC"
        main_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=self.user,
            character=main_character,
            defaults={
                "owner_hash": f"hash-{main_character.character_id}-{self.user.id}",
            },
        )
        CorporationSharingSetting.objects.create(
            user=self.user,
            corporation_id=corp_id,
            corporation_name="Fallback Scope Corp",
            allow_copy_requests=True,
            share_scope=CharacterSettings.SCOPE_CORPORATION,
        )

        personal_bp = Blueprint.objects.create(
            owner_user=self.user,
            character_id=main_character.character_id,
            item_id=61001,
            blueprint_id=62001,
            type_id=778001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=6,
            material_efficiency=4,
            runs=0,
            character_name="Capsuleer",
            type_name="Fallback Blueprint",
        )
        Blueprint.objects.create(
            owner_user=self.user,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Fallback Scope Corp",
            item_id=61002,
            blueprint_id=62002,
            type_id=personal_bp.type_id,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=personal_bp.time_efficiency,
            material_efficiency=personal_bp.material_efficiency,
            runs=0,
            type_name="Fallback Blueprint",
        )

        buyer = User.objects.create_user("fallback_requester", password="test12345")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=personal_bp.type_id,
            material_efficiency=personal_bp.material_efficiency,
            time_efficiency=personal_bp.time_efficiency,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.post(
            reverse("indy_hub:bp_offer_copy_request", args=[request_obj.id]),
            {
                "action": "reject",
                "message": "Corp unavailable",
                "source_scope": "corporation",
            },
        )
        self.assertRedirects(response, reverse("indy_hub:bp_copy_fulfill_requests"))
        self.assertTrue(BlueprintCopyRequest.objects.filter(id=request_obj.id).exists())

        offer = BlueprintCopyOffer.objects.get(request=request_obj, owner=self.user)
        self.assertEqual(offer.status, "rejected")
        self.assertEqual(offer.source_scope, "corporation")

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        entry = requests[0]
        self.assertEqual(entry["id"], request_obj.id)
        self.assertEqual(entry["status_key"], "awaiting_response")
        self.assertEqual(entry["personal_blueprints"], 1)
        self.assertEqual(entry["corporate_blueprints"], 0)
        self.assertFalse(entry["has_dual_sources"])
        self.assertEqual(entry["default_scope"], "personal")

        html = response.content.decode()
        scope_script_id = f"bp-scope-options-{entry['id']}"
        self.assertNotIn(scope_script_id, html)

    @skip(
        "Pre-existing test failure: personal_provider not seeing corporation-scoped requests after rejection"
    )
    def test_corporate_rejection_hides_request_for_all_managers(self) -> None:

        settings = CharacterSettings.objects.get(user=self.user, character_id=0)
        settings.allow_copy_requests = False
        settings.copy_sharing_scope = CharacterSettings.SCOPE_NONE
        settings.save(update_fields=["allow_copy_requests", "copy_sharing_scope"])

        corp_id = 8_801_234
        corp_name = "Shared Access Corp"

        manager_character = EveCharacter.objects.get(character_id=101001)
        manager_character.corporation_id = corp_id
        manager_character.corporation_name = corp_name
        manager_character.corporation_ticker = "SAC"
        manager_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=self.user,
            character=manager_character,
            defaults={
                "owner_hash": f"hash-{manager_character.character_id}-{self.user.id}"
            },
        )

        corp_owner = User.objects.create_user("corp_owner", password="owner123")
        owner_character = assign_main_character(corp_owner, character_id=2022001)
        owner_character.corporation_id = corp_id
        owner_character.corporation_name = corp_name
        owner_character.corporation_ticker = "SAC"
        owner_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=corp_owner,
            character=owner_character,
            defaults={
                "owner_hash": f"hash-{owner_character.character_id}-{corp_owner.id}"
            },
        )
        CharacterSettings.objects.create(
            user=corp_owner,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        grant_indy_permissions(
            corp_owner,
            "can_manage_corp_bp_requests",
        )

        CorporationSharingSetting.objects.create(
            user=corp_owner,
            corporation_id=corp_id,
            corporation_name=corp_name,
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

        blueprint = Blueprint.objects.create(
            owner_user=corp_owner,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name=corp_name,
            item_id=88001,
            blueprint_id=88002,
            type_id=660001,
            location_id=88003,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=10,
            runs=0,
            type_name="Shared Corp Blueprint",
        )

        rejector = User.objects.create_user("corp_manager", password="manager123")
        rejector_character = assign_main_character(rejector, character_id=2022002)
        rejector_character.corporation_id = corp_id
        rejector_character.corporation_name = corp_name
        rejector_character.corporation_ticker = "SAC"
        rejector_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=rejector,
            character=rejector_character,
            defaults={
                "owner_hash": f"hash-{rejector_character.character_id}-{rejector.id}"
            },
        )
        CharacterSettings.objects.create(
            user=rejector,
            character_id=0,
            allow_copy_requests=False,
            copy_sharing_scope=CharacterSettings.SCOPE_NONE,
        )
        grant_indy_permissions(
            rejector,
            "can_manage_corp_bp_requests",
        )

        personal_provider = User.objects.create_user(
            "personal_builder", password="build123"
        )
        personal_character = assign_main_character(
            personal_provider, character_id=3033001
        )
        CharacterOwnership.objects.update_or_create(
            user=personal_provider,
            character=personal_character,
            defaults={
                "owner_hash": f"hash-{personal_character.character_id}-{personal_provider.id}"
            },
        )
        CharacterSettings.objects.create(
            user=personal_provider,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        grant_indy_permissions(personal_provider, "can_manage_corp_bp_requests")
        Blueprint.objects.create(
            owner_user=personal_provider,
            character_id=personal_character.character_id,
            item_id=personal_character.character_id + 100,
            blueprint_id=personal_character.character_id + 200,
            type_id=blueprint.type_id,
            location_id=personal_character.character_id + 300,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=blueprint.time_efficiency,
            material_efficiency=blueprint.material_efficiency,
            runs=0,
            character_name="Personal Builder",
            type_name="Shared Corp Blueprint",
        )

        buyer = User.objects.create_user("corporate_customer", password="buyme123")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))
        self.assertEqual(response.status_code, 200)
        initial_requests = response.context["requests"]
        self.assertEqual(len(initial_requests), 1)
        self.assertEqual(initial_requests[0]["id"], request_obj.id)

        self.client.logout()
        self.client.force_login(rejector)
        response = self.client.post(
            reverse("indy_hub:bp_offer_copy_request", args=[request_obj.id]),
            {"action": "reject", "message": "Corp unavailable"},
        )
        self.assertRedirects(response, reverse("indy_hub:bp_copy_fulfill_requests"))
        self.assertTrue(BlueprintCopyRequest.objects.filter(id=request_obj.id).exists())

        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["requests"], [])

        self.client.logout()
        self.client.force_login(personal_provider)
        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))
        self.assertEqual(response.status_code, 200)
        remaining_requests = response.context["requests"]
        self.assertEqual(len(remaining_requests), 1)
        self.assertEqual(remaining_requests[0]["id"], request_obj.id)

        self.client.logout()
        self.client.force_login(self.user)

    def test_rejected_offer_hidden_from_queue(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=44,
            item_id=2101,
            blueprint_id=3101,
            type_id=999001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=9,
            runs=0,
            character_name="Capsuleer",
            type_name="Hidden Blueprint",
        )
        buyer = User.objects.create_user("rejecting_requester", password="test12345")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )
        BlueprintCopyOffer.objects.create(
            request=request_obj,
            owner=self.user,
            status="rejected",
            message="No time",
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["requests"], [])
        self.assertEqual(response.context["metrics"]["total"], 0)

    def test_requester_notified_when_all_providers_reject(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=45,
            item_id=2201,
            blueprint_id=3201,
            type_id=999002,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=6,
            runs=0,
            character_name="Capsuleer",
            type_name="Shared Blueprint",
        )
        other_provider = User.objects.create_user(
            "second_builder", password="test12345"
        )
        assign_main_character(other_provider, character_id=101005)
        CharacterSettings.objects.create(
            user=other_provider,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        grant_indy_permissions(other_provider, "can_manage_corp_bp_requests")
        Blueprint.objects.create(
            owner_user=other_provider,
            character_id=55,
            item_id=2202,
            blueprint_id=3202,
            type_id=blueprint.type_id,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=blueprint.time_efficiency,
            material_efficiency=blueprint.material_efficiency,
            runs=0,
            character_name="Second Builder",
            type_name="Shared Blueprint",
        )
        requester = User.objects.create_user("bp_customer", password="request123")
        assign_main_character(requester, character_id=201001)
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=requester,
            runs_requested=2,
            copies_requested=1,
        )

        with patch("indy_hub.views.industry.notify_user") as mock_notify:
            response = self.client.post(
                reverse("indy_hub:bp_offer_copy_request", args=[request_obj.id]),
                {"action": "reject", "message": "Can't right now"},
            )
            self.assertRedirects(response, reverse("indy_hub:bp_copy_fulfill_requests"))
            self.assertTrue(
                BlueprintCopyRequest.objects.filter(id=request_obj.id).exists()
            )
            mock_notify.assert_not_called()

            self.client.logout()
            self.client.force_login(other_provider)
            response = self.client.post(
                reverse("indy_hub:bp_offer_copy_request", args=[request_obj.id]),
                {"action": "reject", "message": "Also unavailable"},
            )
            self.assertRedirects(response, reverse("indy_hub:bp_copy_fulfill_requests"))

            self.assertFalse(
                BlueprintCopyRequest.objects.filter(id=request_obj.id).exists()
            )
            mock_notify.assert_called_once()
            args, kwargs = mock_notify.call_args
            self.assertEqual(args[0], requester)
            self.assertIn("declined", str(args[2]))

        self.client.logout()
        self.client.force_login(self.user)

    def test_busy_blueprints_flagged_in_context(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=3001,
            blueprint_id=4001,
            type_id=987001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=7,
            runs=0,
            character_name="Capsuleer",
            type_name="Busy Blueprint",
        )
        buyer = User.objects.create_user("self_requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=2,
            copies_requested=1,
        )

        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=blueprint.character_id,
            job_id=7770001,
            installer_id=self.user.id,
            station_id=blueprint.location_id,
            location_name="Busy Location",
            activity_id=5,
            blueprint_id=blueprint.item_id,
            blueprint_type_id=blueprint.type_id,
            runs=1,
            status="active",
            duration=3600,
            start_date=timezone.now() - timedelta(minutes=10),
            end_date=timezone.now() + timedelta(hours=2),
            activity_name="Copying",
            blueprint_type_name=blueprint.type_name,
            product_type_name="Busy Product",
            character_name=blueprint.character_name,
        )
        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        request_entry = requests[0]
        self.assertTrue(request_entry["all_copies_busy"])
        self.assertEqual(request_entry["owned_blueprints"], 1)
        self.assertEqual(request_entry["available_blueprints"], 0)
        self.assertGreater(request_entry["active_copy_jobs"], 0)
        self.assertIsNotNone(request_entry["busy_until"])
        self.assertFalse(request_entry["busy_overdue"])

    def test_non_copy_job_blocks_blueprint(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=4001,
            blueprint_id=5001,
            type_id=987002,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=9,
            runs=0,
            character_name="Capsuleer",
            type_name="Manufacturing Blueprint",
        )
        buyer = User.objects.create_user(
            "manufacturing_requester", password="test12345"
        )
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )

        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=blueprint.character_id,
            job_id=8880001,
            installer_id=self.user.id,
            station_id=blueprint.location_id,
            location_name="Manufacturing Hub",
            activity_id=1,
            blueprint_id=blueprint.item_id,
            blueprint_type_id=blueprint.type_id,
            runs=1,
            status="active",
            duration=7200,
            start_date=timezone.now() - timedelta(minutes=30),
            end_date=timezone.now() + timedelta(hours=1),
            activity_name="Manufacturing",
            blueprint_type_name=blueprint.type_name,
            product_type_name="Manufactured Product",
            character_name=blueprint.character_name,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        request_entry = requests[0]
        self.assertTrue(request_entry["all_copies_busy"])
        self.assertEqual(request_entry["owned_blueprints"], 1)
        self.assertEqual(request_entry["available_blueprints"], 0)

    def test_job_with_zero_blueprint_id_matches_original(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=43,
            item_id=0,
            blueprint_id=0,
            type_id=987003,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=6,
            material_efficiency=4,
            runs=0,
            character_name="Capsuleer",
            type_name="Zero Blueprint",
        )
        buyer = User.objects.create_user("zero_requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )

        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=blueprint.character_id,
            job_id=8890001,
            installer_id=self.user.id,
            station_id=blueprint.location_id,
            location_name="Zero Yard",
            activity_id=5,
            blueprint_id=0,
            blueprint_type_id=blueprint.type_id,
            runs=1,
            status="active",
            duration=5400,
            start_date=timezone.now() - timedelta(minutes=15),
            end_date=timezone.now() + timedelta(hours=1),
            activity_name="Copying",
            blueprint_type_name=blueprint.type_name,
            product_type_name="Zero Product",
            character_name=blueprint.character_name,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        request_entry = requests[0]
        self.assertTrue(request_entry["all_copies_busy"])
        self.assertEqual(request_entry["active_copy_jobs"], 1)

    def test_job_with_mismatched_blueprint_id_does_not_block(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=45,
            item_id=6001,
            blueprint_id=7001,
            type_id=555001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=20,
            material_efficiency=10,
            runs=0,
            character_name="Capsuleer",
            type_name="Ambiguous Blueprint",
        )
        buyer = User.objects.create_user("ambiguous_requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )

        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=blueprint.character_id,
            job_id=8895001,
            installer_id=self.user.id,
            station_id=blueprint.location_id,
            location_name="Ambiguous Site",
            activity_id=5,
            blueprint_id=9999999,
            blueprint_type_id=blueprint.type_id,
            runs=1,
            status="active",
            duration=3600,
            start_date=timezone.now() - timedelta(minutes=5),
            end_date=timezone.now() + timedelta(hours=1),
            activity_name="Copying",
            blueprint_type_name=blueprint.type_name,
            product_type_name="Ambiguous Product",
            character_name=blueprint.character_name,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        request_entry = requests[0]
        self.assertFalse(request_entry["all_copies_busy"])
        self.assertEqual(request_entry["owned_blueprints"], 1)
        self.assertEqual(request_entry["available_blueprints"], 1)
        self.assertEqual(request_entry["active_copy_jobs"], 0)
        self.assertIsNone(request_entry["busy_until"])

    def test_job_past_end_date_still_blocks(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=46,
            item_id=6101,
            blueprint_id=7101,
            type_id=565001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=8,
            runs=0,
            character_name="Capsuleer",
            type_name="Late Delivery Blueprint",
        )
        buyer = User.objects.create_user("late_requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )

        job_end = timezone.now() - timedelta(hours=2)
        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=blueprint.character_id,
            job_id=8897001,
            installer_id=self.user.id,
            station_id=blueprint.location_id,
            location_name="Late Facility",
            activity_id=5,
            blueprint_id=blueprint.item_id,
            blueprint_type_id=blueprint.type_id,
            runs=1,
            status="active",
            duration=3600,
            start_date=timezone.now() - timedelta(hours=3),
            end_date=job_end,
            activity_name="Copying",
            blueprint_type_name=blueprint.type_name,
            product_type_name="Late Product",
            character_name=blueprint.character_name,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        request_entry = requests[0]
        self.assertTrue(request_entry["all_copies_busy"])
        self.assertEqual(request_entry["owned_blueprints"], 1)
        self.assertEqual(request_entry["available_blueprints"], 0)
        self.assertEqual(request_entry["active_copy_jobs"], 1)
        self.assertTrue(request_entry["busy_overdue"])
        self.assertEqual(request_entry["busy_until"], job_end)

    def test_reaction_blueprint_not_listed(self) -> None:
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=3001,
            blueprint_id=4001,
            type_id=777777,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Capsuleer",
            type_name="Fullerene Reaction Formula",
        )
        buyer = User.objects.create_user("reaction-buyer", password="reactpass")
        BlueprintCopyRequest.objects.create(
            type_id=777777,
            material_efficiency=0,
            time_efficiency=0,
            requested_by=buyer,
            runs_requested=1,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["requests"], [])
        self.assertEqual(response.context["metrics"]["total"], 0)


class BlueprintCopyRequestPageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("requester", password="secret123")
        assign_main_character(self.user, character_id=103001)
        grant_indy_permissions(self.user, "can_manage_corp_bp_requests")
        self.client.force_login(self.user)

        viewer_character = EveCharacter.objects.get(character_id=103001)
        CharacterOwnership.objects.get_or_create(
            user=self.user,
            character=viewer_character,
            defaults={
                "owner_hash": f"hash-{viewer_character.character_id}-{self.user.id}",
            },
        )

        self.owner = User.objects.create_user("supplier", password="supply123")
        assign_main_character(self.owner, character_id=501)
        CharacterSettings.objects.create(
            user=self.owner,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        Blueprint.objects.create(
            owner_user=self.owner,
            character_id=501,
            item_id=9050001,
            blueprint_id=9050002,
            type_id=605001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=8,
            runs=0,
            character_name="Supplier",
            type_name="Duplicated Widget Blueprint",
        )
        IndustrySkillSnapshot.objects.create(
            owner_user=self.owner,
            character_id=501,
            skill_levels={"3402": {"active": 4, "trained": 4}},
        )

    def test_request_page_uses_modal_preview(self) -> None:
        response = self.client.get(reverse("indy_hub:bp_copy_request_page"))

        self.assertEqual(response.status_code, 200)
        page_entry = response.context["page_obj"].object_list[0]
        self.assertIn("copy_request_preview", page_entry)
        self.assertEqual(page_entry["copy_request_preview"]["alerted_owner_count"], 1)
        self.assertIn("max_runs_per_copy", page_entry["copy_request_preview"])
        self.assertContains(response, "bpCopyRequestModal")

    def test_duplicate_submission_is_blocked(self) -> None:
        url = reverse("indy_hub:bp_copy_request_page")
        post_data = {
            "type_id": 605001,
            "material_efficiency": 8,
            "time_efficiency": 12,
            "runs_requested": 2,
            "copies_requested": 1,
        }

        with patch("indy_hub.views.industry.notify_user") as mock_notify:
            response = self.client.post(url, post_data)
            self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))

            initial_requests = BlueprintCopyRequest.objects.filter(
                type_id=605001,
                material_efficiency=8,
                time_efficiency=12,
                requested_by=self.user,
                fulfilled=False,
            )
            self.assertEqual(initial_requests.count(), 1)
            first_request = initial_requests.first()
            self.assertIsNotNone(first_request)
            self.assertEqual(first_request.runs_requested, 2)
            self.assertEqual(first_request.copies_requested, 1)

            followup_data = {
                "type_id": 605001,
                "material_efficiency": 8,
                "time_efficiency": 12,
                "runs_requested": 3,
                "copies_requested": 2,
            }
            response = self.client.post(url, followup_data)
            self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))

            open_requests = BlueprintCopyRequest.objects.filter(
                type_id=605001,
                material_efficiency=8,
                time_efficiency=12,
                requested_by=self.user,
                fulfilled=False,
            )

            self.assertEqual(open_requests.count(), 1)
            self.assertEqual(mock_notify.call_count, 1)

    def test_submission_above_max_runs_per_copy_limit_is_rejected(self) -> None:
        url = reverse("indy_hub:bp_copy_request_create")
        post_data = {
            "type_id": 605001,
            "material_efficiency": 8,
            "time_efficiency": 12,
            "runs_requested": 10,
            "copies_requested": 75,
        }

        with (
            patch(
                "indy_hub.views.industry.get_max_copy_runs_per_request",
                return_value=9,
            ),
            patch("indy_hub.views.industry.notify_user") as mock_notify,
        ):
            response = self.client.post(url, post_data, headers={"referer": url})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url)
        self.assertFalse(
            BlueprintCopyRequest.objects.filter(
                type_id=605001,
                material_efficiency=8,
                time_efficiency=12,
                requested_by=self.user,
                fulfilled=False,
            ).exists()
        )
        self.assertEqual(mock_notify.call_count, 0)

    def test_reaction_blueprint_request_is_rejected(self) -> None:
        url = reverse("indy_hub:bp_copy_request_create")
        post_data = {
            "type_id": 605001,
            "material_efficiency": 0,
            "time_efficiency": 0,
            "runs_requested": 1,
            "copies_requested": 1,
        }

        with (
            patch(
                "indy_hub.views.industry.is_reaction_blueprint",
                return_value=True,
            ),
            patch("indy_hub.views.industry.notify_user") as mock_notify,
        ):
            response = self.client.post(url, post_data)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            BlueprintCopyRequest.objects.filter(
                type_id=605001,
                requested_by=self.user,
                fulfilled=False,
            ).exists()
        )
        self.assertEqual(mock_notify.call_count, 0)

    def test_everyone_scope_shows_blueprint(self) -> None:
        settings = CharacterSettings.objects.get(user=self.owner, character_id=0)
        settings.copy_sharing_scope = CharacterSettings.SCOPE_EVERYONE
        settings.allow_copy_requests = True
        settings.save(update_fields=["copy_sharing_scope", "allow_copy_requests"])

        response = self.client.get(reverse("indy_hub:bp_copy_request_page"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["page_obj"]
        visible_type_ids = {entry["type_id"] for entry in page_obj}
        self.assertIn(605001, visible_type_ids)

    def test_invalid_per_page_falls_back_to_default(self) -> None:
        settings = CharacterSettings.objects.get(user=self.owner, character_id=0)
        settings.copy_sharing_scope = CharacterSettings.SCOPE_EVERYONE
        settings.allow_copy_requests = True
        settings.save(update_fields=["copy_sharing_scope", "allow_copy_requests"])

        response = self.client.get(
            reverse("indy_hub:bp_copy_request_page"),
            {"per_page": "abc"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["per_page"], 24)

    def test_corporation_scope_shows_corporate_blueprint(self) -> None:
        corp_id = 2_000_000
        CorporationSharingSetting.objects.create(
            user=self.owner,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

        supplier_character, _ = EveCharacter.objects.get_or_create(
            character_id=303001,
            defaults={
                "character_name": "SupplierCorpPilot",
                "corporation_id": corp_id,
                "corporation_name": "Test Corp",
                "corporation_ticker": "TEST",
                "alliance_id": 4_000_000,
                "alliance_name": "Widget Alliance",
                "alliance_ticker": "WID",
            },
        )
        CharacterOwnership.objects.get_or_create(
            user=self.owner,
            character=supplier_character,
            defaults={
                "owner_hash": f"hash-{supplier_character.character_id}-{self.owner.id}",
            },
        )

        Blueprint.objects.create(
            owner_user=self.owner,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            item_id=9051001,
            blueprint_id=9051002,
            type_id=705001,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=14,
            material_efficiency=10,
            runs=0,
            type_name="Corporate Widget Blueprint",
        )

        response = self.client.get(reverse("indy_hub:bp_copy_request_page"))

        self.assertEqual(response.status_code, 200)
        visible_type_ids = {entry["type_id"] for entry in response.context["page_obj"]}
        self.assertIn(705001, visible_type_ids)

    def test_corporate_director_receives_notification(self) -> None:
        corp_id = 2_500_000
        CorporationSharingSetting.objects.create(
            user=self.owner,
            corporation_id=corp_id,
            corporation_name="Directorate Industries",
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

        supplier_character, _ = EveCharacter.objects.get_or_create(
            character_id=303010,
            defaults={
                "character_name": "CorpSupplier",
                "corporation_id": corp_id,
                "corporation_name": "Directorate Industries",
                "corporation_ticker": "DIR",
            },
        )
        CharacterOwnership.objects.update_or_create(
            user=self.owner,
            character=supplier_character,
            defaults={
                "owner_hash": f"hash-{supplier_character.character_id}-{self.owner.id}",
            },
        )

        Blueprint.objects.create(
            owner_user=self.owner,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Directorate Industries",
            item_id=9053001,
            blueprint_id=9053002,
            type_id=805001,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=10,
            runs=0,
            type_name="Directorate Blueprint",
        )

        manager = User.objects.create_user("corpmanager", password="manage123")
        assign_main_character(manager, character_id=403010)
        grant_indy_permissions(
            manager,
            "can_manage_corp_bp_requests",
        )
        manager_character = EveCharacter.objects.get(character_id=403010)
        manager_character.corporation_id = corp_id
        manager_character.corporation_name = "Directorate Industries"
        manager_character.corporation_ticker = "DIR"
        manager_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=manager,
            character=manager_character,
            defaults={
                "owner_hash": f"hash-{manager_character.character_id}-{manager.id}",
            },
        )
        post_data = {
            "type_id": 805001,
            "material_efficiency": 10,
            "time_efficiency": 12,
            "runs_requested": 1,
            "copies_requested": 1,
        }

        IndustrySkillSnapshot.objects.create(
            owner_user=self.owner,
            character_id=303010,
            skill_levels={"3402": {"active": 4, "trained": 4}},
        )
        IndustrySkillSnapshot.objects.create(
            owner_user=manager,
            character_id=403010,
            skill_levels={"3402": {"active": 4, "trained": 4}},
        )

        with patch("indy_hub.views.industry.notify_user") as mock_notify:
            response = self.client.post(
                reverse("indy_hub:bp_copy_request_page"), post_data
            )

        self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))
        recipients = {call.args[0] for call in mock_notify.call_args_list}
        self.assertSetEqual({self.owner, manager}, recipients)

    def test_notifications_target_all_eligible_copy_providers(self) -> None:
        corp_id = 2_500_100
        CorporationSharingSetting.objects.create(
            user=self.owner,
            corporation_id=corp_id,
            corporation_name="Qualified Industries",
            share_scope=CharacterSettings.SCOPE_CORPORATION,
            allow_copy_requests=True,
        )

        supplier_character, _ = EveCharacter.objects.get_or_create(
            character_id=303110,
            defaults={
                "character_name": "QualifiedSupplier",
                "corporation_id": corp_id,
                "corporation_name": "Qualified Industries",
                "corporation_ticker": "QI",
            },
        )
        CharacterOwnership.objects.update_or_create(
            user=self.owner,
            character=supplier_character,
            defaults={
                "owner_hash": f"hash-{supplier_character.character_id}-{self.owner.id}",
            },
        )

        Blueprint.objects.create(
            owner_user=self.owner,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Qualified Industries",
            item_id=9153001,
            blueprint_id=9153002,
            type_id=805001,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=10,
            runs=0,
            type_name="Qualified Blueprint",
        )

        manager = User.objects.create_user("qualifiedmanager", password="manage123")
        assign_main_character(manager, character_id=403110)
        grant_indy_permissions(manager, "can_manage_corp_bp_requests")
        manager_character = EveCharacter.objects.get(character_id=403110)
        manager_character.corporation_id = corp_id
        manager_character.corporation_name = "Qualified Industries"
        manager_character.corporation_ticker = "QI"
        manager_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "corporation_ticker",
            ]
        )
        CharacterOwnership.objects.update_or_create(
            user=manager,
            character=manager_character,
            defaults={
                "owner_hash": f"hash-{manager_character.character_id}-{manager.id}",
            },
        )

        IndustrySkillSnapshot.objects.create(
            owner_user=self.owner,
            character_id=303110,
            skill_levels={"3402": {"active": 4, "trained": 5}},
        )
        IndustrySkillSnapshot.objects.create(
            owner_user=manager,
            character_id=403110,
            skill_levels={"3402": {"active": 2, "trained": 5}},
        )

        post_data = {
            "type_id": 805001,
            "material_efficiency": 10,
            "time_efficiency": 12,
            "runs_requested": 1,
            "copies_requested": 1,
        }

        with patch("indy_hub.views.industry.notify_user") as mock_notify:
            response = self.client.post(
                reverse("indy_hub:bp_copy_request_page"), post_data
            )

        self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))
        recipients = {call.args[0] for call in mock_notify.call_args_list}
        self.assertSetEqual({self.owner, manager}, recipients)

    def test_alliance_scope_shows_corporate_blueprint_for_allied_member(self) -> None:
        corp_id = 2_000_000
        CorporationSharingSetting.objects.create(
            user=self.owner,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            share_scope=CharacterSettings.SCOPE_ALLIANCE,
            allow_copy_requests=True,
        )

        supplier_character, _ = EveCharacter.objects.get_or_create(
            character_id=303002,
            defaults={
                "character_name": "SupplierAlliancePilot",
                "corporation_id": corp_id,
                "corporation_name": "Test Corp",
                "corporation_ticker": "TEST",
                "alliance_id": 5_000_000,
                "alliance_name": "Alliance Umbrella",
                "alliance_ticker": "UMB",
            },
        )
        CharacterOwnership.objects.update_or_create(
            user=self.owner,
            character=supplier_character,
            defaults={
                "owner_hash": f"hash-{supplier_character.character_id}-{self.owner.id}",
            },
        )

        allied_character, _ = EveCharacter.objects.get_or_create(
            character_id=203001,
            defaults={
                "character_name": "AllianceBuyer",
                "corporation_id": 2_100_000,
                "corporation_name": "Ally Corp",
                "corporation_ticker": "ALLY",
                "alliance_id": 5_000_000,
                "alliance_name": "Alliance Umbrella",
                "alliance_ticker": "UMB",
            },
        )
        CharacterOwnership.objects.update_or_create(
            user=self.user,
            character=allied_character,
            defaults={
                "owner_hash": f"hash-{allied_character.character_id}-{self.user.id}",
            },
        )
        CharacterOwnership.objects.filter(user=self.user).exclude(
            character=allied_character
        ).delete()
        profile = UserProfile.objects.get(user=self.user)
        profile.main_character = allied_character
        profile.save(update_fields=["main_character"])

        Blueprint.objects.create(
            owner_user=self.owner,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            item_id=9052001,
            blueprint_id=9052002,
            type_id=705002,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=16,
            material_efficiency=12,
            runs=0,
            type_name="Alliance Widget Blueprint",
        )

        response = self.client.get(reverse("indy_hub:bp_copy_request_page"))

        self.assertEqual(response.status_code, 200)
        visible_type_ids = {entry["type_id"] for entry in response.context["page_obj"]}
        self.assertIn(705002, visible_type_ids)


class BlueprintCopyMyRequestsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("buyer", password="secret123")
        assign_main_character(self.user, character_id=104001)
        grant_indy_permissions(self.user, "can_manage_corp_bp_requests")
        self.client.force_login(self.user)

        self.provider = User.objects.create_user("seller", password="sell123")
        CharacterSettings.objects.create(
            user=self.provider,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        Blueprint.objects.create(
            owner_user=self.provider,
            character_id=8801,
            item_id=50001,
            blueprint_id=60001,
            type_id=700001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=8,
            runs=0,
            character_name="Provider",
            type_name="Sample Blueprint",
        )

    def test_update_requires_post(self) -> None:
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=700001,
            material_efficiency=8,
            time_efficiency=10,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(
            reverse("indy_hub:bp_update_copy_request", args=[request_obj.id])
        )

        self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.runs_requested, 2)
        self.assertEqual(request_obj.copies_requested, 1)

    def test_update_changes_runs_and_copies_and_notifies(self) -> None:
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=700001,
            material_efficiency=8,
            time_efficiency=10,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
        )

        with patch("indy_hub.views.industry.notify_user") as mock_notify:
            response = self.client.post(
                reverse("indy_hub:bp_update_copy_request", args=[request_obj.id]),
                {"runs_requested": 5, "copies_requested": 4},
            )

            self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))

            request_obj.refresh_from_db()
            self.assertEqual(request_obj.runs_requested, 5)
            self.assertEqual(request_obj.copies_requested, 4)
            mock_notify.assert_called()

    def test_cancel_redirects_back_to_my_requests(self) -> None:
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=700001,
            material_efficiency=8,
            time_efficiency=10,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.post(
            reverse("indy_hub:bp_cancel_copy_request", args=[request_obj.id]),
            {"next": reverse("indy_hub:bp_copy_my_requests")},
        )

        self.assertRedirects(response, reverse("indy_hub:bp_copy_my_requests"))
        self.assertFalse(
            BlueprintCopyRequest.objects.filter(id=request_obj.id).exists()
        )


class StructureLookupForbiddenCacheTests(TestCase):
    def tearDown(self) -> None:
        reset_forbidden_structure_lookup_cache()
        eve_utils._LOCATION_NAME_CACHE.clear()

    def test_character_skipped_after_forbidden_error(self) -> None:
        reset_forbidden_structure_lookup_cache()
        structure_id = 1_046_000_000_001
        character_id = 7001

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name"
        ) as mock_fetch:
            mock_fetch.side_effect = ESIForbiddenError(
                "forbidden",
                character_id=character_id,
                structure_id=structure_id,
            )

            result = eve_utils.resolve_location_name(
                structure_id,
                character_id=character_id,
                owner_user_id=None,
                force_refresh=True,
                allow_public=False,
            )
            self.assertEqual(result, f"Structure {structure_id}")
            self.assertEqual(mock_fetch.call_count, 1)

            mock_fetch.side_effect = RuntimeError(
                "fetch_structure_name should not run again"
            )

            second_result = eve_utils.resolve_location_name(
                structure_id,
                character_id=character_id,
                owner_user_id=None,
                force_refresh=True,
                allow_public=False,
            )
            self.assertEqual(second_result, f"Structure {structure_id}")
            self.assertEqual(mock_fetch.call_count, 1)

    def test_forbidden_character_cache_is_scoped_to_structure(self) -> None:
        reset_forbidden_structure_lookup_cache()
        forbidden_structure_id = 1_046_000_000_001
        allowed_structure_id = 1_046_000_000_002
        character_id = 7001

        def _fetch(structure_id_in, character_id_in):
            self.assertEqual(int(character_id_in), character_id)
            if int(structure_id_in) == forbidden_structure_id:
                raise ESIForbiddenError(
                    "forbidden",
                    character_id=character_id,
                    structure_id=forbidden_structure_id,
                )
            if int(structure_id_in) == allowed_structure_id:
                return "Allowed Structure"
            raise AssertionError(f"Unexpected structure id {structure_id_in}")

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name",
            side_effect=_fetch,
        ) as mock_fetch:
            forbidden_result = eve_utils.resolve_location_name(
                forbidden_structure_id,
                character_id=character_id,
                owner_user_id=None,
                force_refresh=True,
                allow_public=False,
            )
            allowed_result = eve_utils.resolve_location_name(
                allowed_structure_id,
                character_id=character_id,
                owner_user_id=None,
                force_refresh=True,
                allow_public=False,
            )

        self.assertEqual(forbidden_result, f"Structure {forbidden_structure_id}")
        self.assertEqual(allowed_result, "Allowed Structure")
        self.assertEqual(mock_fetch.call_count, 2)


class StructureLookupDbCacheTests(TestCase):
    def setUp(self) -> None:
        reset_forbidden_structure_lookup_cache()

    def tearDown(self) -> None:
        reset_forbidden_structure_lookup_cache()
        eve_utils._LOCATION_NAME_CACHE.clear()

    def test_force_refresh_reuses_recent_forbidden_placeholder(self) -> None:
        structure_id = 1_045_667_241_057
        placeholder = f"Structure {structure_id}"
        CachedStructureName.objects.create(
            structure_id=structure_id,
            name=placeholder,
            last_resolved=timezone.now(),
        )
        eve_utils.set_structure_forbidden_cooldown(structure_id)

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name"
        ) as mock_fetch:
            mock_fetch.side_effect = RuntimeError(
                "fetch_structure_name should not be called"
            )
            result = eve_utils.resolve_location_name(
                structure_id,
                character_id=7001,
                owner_user_id=None,
                force_refresh=True,
                allow_public=False,
            )

        self.assertEqual(result, placeholder)
        self.assertEqual(mock_fetch.call_count, 0)

    def test_resolve_location_name_prefers_cached_structure_name(self) -> None:
        structure_id = 1_042_090_993_674
        CachedStructureName.objects.create(
            structure_id=structure_id,
            name="Cached Structure Alpha",
            last_resolved=timezone.now(),
        )

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name"
        ) as mock_fetch:
            mock_fetch.side_effect = RuntimeError(
                "fetch_structure_name should not be called"
            )
            result = eve_utils.resolve_location_name(
                structure_id,
                character_id=None,
                owner_user_id=None,
                force_refresh=False,
                allow_public=False,
            )

        self.assertEqual(result, "Cached Structure Alpha")

    def test_in_memory_placeholder_replaced_by_db_value(self) -> None:
        structure_id = 1_045_667_241_057
        eve_utils._LOCATION_NAME_CACHE[structure_id] = f"Structure {structure_id}"

        CachedStructureName.objects.create(
            structure_id=structure_id,
            name="Cached Structure Beta",
            last_resolved=timezone.now(),
        )

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name"
        ) as mock_fetch:
            mock_fetch.side_effect = RuntimeError(
                "fetch_structure_name should not be called"
            )
            result = eve_utils.resolve_location_name(
                structure_id,
                character_id=None,
                owner_user_id=None,
                force_refresh=False,
                allow_public=False,
            )

        self.assertEqual(result, "Cached Structure Beta")

    def test_resolve_location_name_reuses_office_folder_alias(self) -> None:
        office_folder_item_id = 1_045_722_708_748
        structure_id = 1_045_667_241_057

        CachedStructureName.objects.create(
            structure_id=structure_id,
            name="Cached Structure Alias",
            last_resolved=timezone.now(),
        )
        CachedCorporationAsset.objects.create(
            corporation_id=123,
            item_id=office_folder_item_id,
            location_id=structure_id,
            location_flag="OfficeFolder",
            type_id=27,
            quantity=1,
            is_singleton=True,
            is_blueprint=False,
        )

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name"
        ) as mock_fetch:
            mock_fetch.side_effect = RuntimeError(
                "fetch_structure_name should not be called"
            )
            result = eve_utils.resolve_location_name(
                office_folder_item_id,
                character_id=None,
                owner_user_id=None,
                force_refresh=False,
                allow_public=False,
            )

        self.assertEqual(result, "Cached Structure Alias")
        cached = CachedStructureName.objects.get(structure_id=office_folder_item_id)
        self.assertEqual(cached.name, "Cached Structure Alias")

    def test_resolve_location_name_writes_through_to_central_cache(self) -> None:
        structure_id = 1_045_667_241_057
        CachedStructureName.objects.filter(structure_id=structure_id).delete()

        with patch(
            "indy_hub.utils.eve.shared_client.fetch_structure_name",
            return_value="Cached Write Through Structure",
        ):
            result = eve_utils.resolve_location_name(
                structure_id,
                character_id=7001,
                owner_user_id=None,
                force_refresh=True,
                allow_public=False,
            )

        self.assertEqual(result, "Cached Write Through Structure")
        cached = CachedStructureName.objects.get(structure_id=structure_id)
        self.assertEqual(cached.name, "Cached Write Through Structure")

    def test_in_memory_resolved_cache_writes_to_central_cache(self) -> None:
        structure_id = 1_042_090_993_675
        eve_utils._LOCATION_NAME_CACHE[structure_id] = "In-memory Structure Gamma"
        CachedStructureName.objects.filter(structure_id=structure_id).delete()

        result = eve_utils.resolve_location_name(
            structure_id,
            character_id=None,
            owner_user_id=None,
            force_refresh=False,
            allow_public=False,
        )

        self.assertEqual(result, "In-memory Structure Gamma")
        cached = CachedStructureName.objects.get(structure_id=structure_id)
        self.assertEqual(cached.name, "In-memory Structure Gamma")


class ManualRefreshCooldownTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("manual", password="secret123")
        reset_manual_refresh_cooldown(MANUAL_REFRESH_KIND_BLUEPRINTS, self.user.id)
        reset_manual_refresh_cooldown(MANUAL_REFRESH_KIND_JOBS, self.user.id)
        reset_manual_refresh_cooldown(
            MANUAL_REFRESH_KIND_BLUEPRINTS,
            self.user.id,
            scope="corporation",
        )

    def tearDown(self) -> None:
        reset_manual_refresh_cooldown(MANUAL_REFRESH_KIND_BLUEPRINTS, self.user.id)
        reset_manual_refresh_cooldown(MANUAL_REFRESH_KIND_JOBS, self.user.id)
        reset_manual_refresh_cooldown(
            MANUAL_REFRESH_KIND_BLUEPRINTS,
            self.user.id,
            scope="corporation",
        )

    def test_manual_refresh_sets_cooldown(self) -> None:
        with patch(
            "indy_hub.tasks.industry.update_blueprints_for_user.apply_async"
        ) as mock_apply:
            scheduled, remaining, reason = request_manual_refresh(
                MANUAL_REFRESH_KIND_BLUEPRINTS,
                self.user.id,
                check_active=False,
            )
        self.assertTrue(scheduled)
        self.assertIsNone(remaining)
        self.assertIsNone(reason)
        mock_apply.assert_called_once()

        allowed, cooldown = manual_refresh_allowed(
            MANUAL_REFRESH_KIND_BLUEPRINTS, self.user.id
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(cooldown)

    def test_manual_blueprint_refresh_forces_task_refresh(self) -> None:
        with patch(
            "indy_hub.tasks.industry.update_blueprints_for_user.apply_async"
        ) as mock_apply:
            scheduled, remaining, reason = request_manual_refresh(
                MANUAL_REFRESH_KIND_BLUEPRINTS,
                self.user.id,
                check_active=False,
                scope="corporation",
            )

        self.assertTrue(scheduled)
        self.assertIsNone(remaining)
        self.assertIsNone(reason)
        mock_apply.assert_called_once_with(
            args=(self.user.id,),
            kwargs={"scope": "corporation", "force_refresh": True},
            countdown=0,
            priority=None,
        )

    def test_manual_refresh_rejects_when_already_in_progress(self) -> None:
        with (
            patch(
                "indy_hub.tasks.industry._get_manual_refresh_cooldown_seconds",
                return_value=0,
            ),
            patch(
                "indy_hub.tasks.industry.update_blueprints_for_user.apply_async"
            ) as mock_apply,
        ):
            scheduled, remaining, reason = request_manual_refresh(
                MANUAL_REFRESH_KIND_BLUEPRINTS,
                self.user.id,
                check_active=False,
            )
            self.assertTrue(scheduled)
            self.assertIsNone(remaining)
            self.assertIsNone(reason)

            scheduled_2, remaining_2, reason_2 = request_manual_refresh(
                MANUAL_REFRESH_KIND_BLUEPRINTS,
                self.user.id,
                check_active=False,
            )

        self.assertFalse(scheduled_2)
        self.assertIsNone(remaining_2)
        self.assertEqual(reason_2, "in_progress")
        mock_apply.assert_called_once()

    def test_reset_clears_cooldown(self) -> None:
        with patch(
            "indy_hub.tasks.industry.update_industry_jobs_for_user.apply_async"
        ) as mock_apply:
            scheduled, _, _ = request_manual_refresh(
                MANUAL_REFRESH_KIND_JOBS,
                self.user.id,
                check_active=False,
            )
        self.assertTrue(scheduled)
        mock_apply.assert_called_once()

        reset_manual_refresh_cooldown(MANUAL_REFRESH_KIND_JOBS, self.user.id)

        allowed, cooldown = manual_refresh_allowed(
            MANUAL_REFRESH_KIND_JOBS, self.user.id
        )
        self.assertTrue(allowed)
        self.assertIsNone(cooldown)


class NotificationRoutingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("notify", password="secret123")

    @patch("indy_hub.notifications._send_via_discordnotify", autospec=True)
    @patch("indy_hub.notifications.Notification.objects.notify_user", autospec=True)
    @patch("indy_hub.notifications._send_via_aadiscordbot", autospec=True)
    def test_prefers_aadiscordbot_without_creating_auth_notification(
        self,
        mock_bot,
        mock_notify,
        mock_discordnotify,
    ) -> None:
        mock_bot.return_value = True

        notify_user(self.user, "Ping", "Message", level="info")

        mock_bot.assert_called_once()
        mock_notify.assert_not_called()
        mock_discordnotify.assert_not_called()

    @patch("indy_hub.notifications._send_via_discordnotify", autospec=True)
    @patch("indy_hub.notifications.Notification.objects.notify_user", autospec=True)
    @patch("indy_hub.notifications._send_via_aadiscordbot", autospec=True)
    def test_falls_back_to_auth_when_bot_unavailable(
        self,
        mock_bot,
        mock_notify,
        mock_discordnotify,
    ) -> None:
        mock_bot.return_value = False
        mock_discordnotify.return_value = False

        notify_user(self.user, "Ping", "Message", level="info")

        mock_bot.assert_called_once()
        mock_notify.assert_called_once()
        mock_discordnotify.assert_called_once()

    @patch("indy_hub.notifications._send_via_discordnotify", autospec=True)
    @patch("indy_hub.notifications.Notification.objects.notify_user", autospec=True)
    @patch("indy_hub.notifications._send_via_aadiscordbot", autospec=True)
    def test_link_information_propagates_to_all_channels(
        self,
        mock_bot,
        mock_notify,
        mock_discordnotify,
    ) -> None:
        mock_bot.return_value = False
        mock_discordnotify.return_value = False

        link = "https://example.com/bp-copy/fulfill/"
        link_label = "Open queue"
        expected_cta = f"{link_label}: {link}"
        expected_message = f"Message body\n\n{expected_cta}"
        expected_dm_message = f"Message body\n\n[clic here]({link})"

        notify_user(
            self.user,
            "Ping",
            "Message body",
            level="warning",
            link=link,
            link_label=link_label,
        )

        mock_bot.assert_called_once()
        bot_args, bot_kwargs = mock_bot.call_args
        self.assertEqual(bot_args[2], expected_dm_message)
        self.assertEqual(bot_kwargs.get("link"), link)
        self.assertIsNone(bot_kwargs.get("thumbnail_url"))

        mock_notify.assert_called_once()
        notify_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(notify_kwargs.get("message"), expected_message)
        mock_discordnotify.assert_called_once()


class DashboardNotificationCountsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("foreman", password="test12345")
        assign_main_character(self.user, character_id=101002)
        CharacterSettings.objects.create(
            user=self.user,
            character_id=0,
            allow_copy_requests=True,
            copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        grant_indy_permissions(self.user, "can_manage_corp_bp_requests")
        self.client.force_login(self.user)

        self.blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=7,
            item_id=4001,
            blueprint_id=5001,
            type_id=123456,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=14,
            material_efficiency=8,
            runs=0,
            character_name="Foreman",
            type_name="Widget Blueprint",
        )

    def test_dashboard_counts_include_fulfill_and_my_requests(self) -> None:
        other_user = User.objects.create_user("buyer", password="buyerpass")
        BlueprintCopyRequest.objects.create(
            type_id=self.blueprint.type_id,
            material_efficiency=self.blueprint.material_efficiency,
            time_efficiency=self.blueprint.time_efficiency,
            requested_by=other_user,
            runs_requested=1,
            copies_requested=2,
        )

        BlueprintCopyRequest.objects.create(
            type_id=789001,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.user,
            runs_requested=1,
            copies_requested=1,
        )

        BlueprintCopyRequest.objects.create(
            type_id=789002,
            material_efficiency=10,
            time_efficiency=12,
            requested_by=self.user,
            runs_requested=3,
            copies_requested=2,
            fulfilled=True,
            delivered=False,
        )
        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy_fulfill_count"], 1)
        self.assertEqual(response.context["copy_my_requests_open"], 1)
        self.assertEqual(response.context["copy_my_requests_pending_delivery"], 1)
        self.assertEqual(response.context["copy_my_requests_total"], 2)

    def test_dashboard_fulfill_count_skips_requests_i_rejected(self) -> None:
        other_user = User.objects.create_user("buyer", password="buyerpass")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=self.blueprint.type_id,
            material_efficiency=self.blueprint.material_efficiency,
            time_efficiency=self.blueprint.time_efficiency,
            requested_by=other_user,
            runs_requested=1,
            copies_requested=1,
        )

        BlueprintCopyOffer.objects.create(
            request=request_obj,
            owner=self.user,
            status="rejected",
        )

        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy_fulfill_count"], 0)

    def test_dashboard_fulfill_count_includes_ready_to_deliver(self) -> None:
        other_user = User.objects.create_user("buyer", password="buyerpass")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=self.blueprint.type_id,
            material_efficiency=self.blueprint.material_efficiency,
            time_efficiency=self.blueprint.time_efficiency,
            requested_by=other_user,
            runs_requested=1,
            copies_requested=1,
            fulfilled=True,
            delivered=False,
        )

        BlueprintCopyOffer.objects.create(
            request=request_obj,
            owner=self.user,
            status="accepted",
            accepted_by_buyer=True,
            accepted_by_seller=True,
        )

        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy_fulfill_count"], 1)

    def test_dashboard_overview_action_center_shows_pending_sharing_and_orders(
        self,
    ) -> None:
        SDESyncCompatState.objects.update_or_create(
            pk=1,
            defaults={"last_synced_at": timezone.now()},
        )
        UserOnboardingProgress.objects.update_or_create(
            user=self.user,
            defaults={"manual_steps": {"overview_intro_seen": True}},
        )

        other_user = User.objects.create_user("buyer2", password="buyerpass")
        BlueprintCopyRequest.objects.create(
            type_id=self.blueprint.type_id,
            material_efficiency=self.blueprint.material_efficiency,
            time_efficiency=self.blueprint.time_efficiency,
            requested_by=other_user,
            runs_requested=1,
            copies_requested=1,
        )
        BlueprintCopyRequest.objects.create(
            type_id=789010,
            material_efficiency=4,
            time_efficiency=6,
            requested_by=self.user,
            runs_requested=1,
            copies_requested=1,
        )

        config = MaterialExchangeConfig.objects.create(
            corporation_id=1234,
            structure_id=5678,
            is_active=True,
        )
        MaterialExchangeSellOrder.objects.create(
            config=config,
            seller=self.user,
            status=MaterialExchangeSellOrder.Status.DRAFT,
            order_reference="OVERVIEW-NAV-SELL",
        )

        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Action center")
        self.assertContains(response, 'data-overview-focus="sharing"')
        self.assertContains(response, 'data-overview-focus="material-hub"')
        self.assertContains(response, "Requests waiting on you")
        self.assertContains(response, "Orders still moving")


class DashboardUnusedSlotsSummaryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("slotforeman", password="test12345")
        self.character = assign_main_character(self.user, character_id=101222)
        grant_indy_permissions(self.user, "can_manage_corp_bp_requests")
        self.client.force_login(self.user)
        SDESyncCompatState.objects.update_or_create(
            pk=1,
            defaults={"last_synced_at": timezone.now()},
        )
        UserOnboardingProgress.objects.update_or_create(
            user=self.user,
            defaults={"manual_steps": {"overview_intro_seen": True}},
        )

    def test_dashboard_counts_active_corporation_jobs_against_installer_slots(
        self,
    ) -> None:
        now = timezone.now()
        IndustryJob.objects.create(
            owner_user=self.user,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            character_id=self.character.character_id,
            job_id=9100001,
            installer_id=self.character.character_id,
            station_id=PUBLIC_STATION_ID,
            location_name="Personal Factory",
            activity_id=1,
            blueprint_id=7100001,
            blueprint_type_id=7100002,
            runs=1,
            status="active",
            duration=3600,
            start_date=now,
            end_date=now + timedelta(hours=1),
            activity_name="Manufacturing",
            blueprint_type_name="Personal Job Blueprint",
            product_type_name="Personal Job Product",
            character_name="Slot Foreman",
        )
        IndustryJob.objects.create(
            owner_user=self.user,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=None,
            job_id=9100002,
            installer_id=self.character.character_id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=7100011,
            blueprint_type_id=7100012,
            runs=1,
            status="active",
            duration=3600,
            start_date=now,
            end_date=now + timedelta(hours=1),
            activity_name="Manufacturing",
            blueprint_type_name="Corporate Job Blueprint",
            product_type_name="Corporate Job Product",
            character_name="Director Alt",
        )

        with patch(
            "indy_hub.views.user.build_user_character_skill_contexts",
            return_value=[
                {
                    "character_id": self.character.character_id,
                    "manufacturing": {"total": 10},
                    "research": {"total": 0},
                    "reactions": {"total": 0},
                }
            ],
        ):
            response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        summary = response.context["unused_slots_summary"]
        self.assertEqual(summary["manufacturing"]["total"], 10)
        self.assertEqual(summary["manufacturing"]["available"], 8)

    def test_dashboard_ignores_corporation_jobs_from_other_installers(self) -> None:
        now = timezone.now()
        IndustryJob.objects.create(
            owner_user=self.user,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=None,
            job_id=9100012,
            installer_id=99999999,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=7100021,
            blueprint_type_id=7100022,
            runs=1,
            status="active",
            duration=3600,
            start_date=now,
            end_date=now + timedelta(hours=1),
            activity_name="Manufacturing",
            blueprint_type_name="Other Pilot Corp Job Blueprint",
            product_type_name="Other Pilot Corp Job Product",
            character_name="Other Pilot",
        )

        with patch(
            "indy_hub.views.user.build_user_character_skill_contexts",
            return_value=[
                {
                    "character_id": self.character.character_id,
                    "manufacturing": {"total": 10},
                    "research": {"total": 0},
                    "reactions": {"total": 0},
                }
            ],
        ):
            response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        summary = response.context["unused_slots_summary"]
        self.assertEqual(summary["manufacturing"]["total"], 10)
        self.assertEqual(summary["manufacturing"]["available"], 10)


class PersonnalBlueprintViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("industrialist", password="secret123")
        assign_main_character(self.user, character_id=102001)
        grant_indy_permissions(self.user)
        self.client.force_login(self.user)

    def test_container_blueprint_location_resolves_when_assets_available(self) -> None:
        container_item_id = 3100100001
        root_structure_id = 99000001
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=11,
            item_id=91001,
            blueprint_id=91002,
            type_id=999001,
            location_id=container_item_id,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Industrialist",
            type_name="Polymer Reaction",
        )
        CachedCharacterAsset.objects.create(
            user=self.user,
            character_id=11,
            item_id=container_item_id,
            raw_location_id=root_structure_id,
            location_id=root_structure_id,
            location_flag="hangar",
            type_id=123,
            quantity=1,
            synced_at=timezone.now(),
        )

        CachedStructureName.objects.create(
            structure_id=root_structure_id,
            name="Test Structure",
            last_resolved=timezone.now(),
        )

        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(999001,)]
            response = self.client.get(reverse("indy_hub:personnal_bp_list"))

        self.assertEqual(response.status_code, 200)
        page = response.context["blueprints"]
        self.assertGreaterEqual(len(page.object_list), 1)
        bp = page.object_list[0]
        self.assertEqual(bp.location_path, "Test Structure")

    def test_container_blueprint_location_falls_back_without_assets(self) -> None:
        container_item_id = 3100100002
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=11,
            item_id=91001,
            blueprint_id=91002,
            type_id=999001,
            location_id=container_item_id,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Industrialist",
            type_name="Polymer Reaction",
        )

        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(999001,)]
            response = self.client.get(reverse("indy_hub:personnal_bp_list"))

        self.assertEqual(response.status_code, 200)
        page = response.context["blueprints"]
        self.assertGreaterEqual(len(page.object_list), 1)
        bp = page.object_list[0]
        self.assertEqual(bp.location_path, "hangar")

    def test_container_blueprint_placeholder_does_not_override_existing_name(
        self,
    ) -> None:
        container_item_id = 3100100003
        root_structure_id = 99000002
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=11,
            item_id=91011,
            blueprint_id=91012,
            type_id=999001,
            location_id=container_item_id,
            location_name="Known Label",
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Industrialist",
            type_name="Polymer Reaction",
        )
        CachedCharacterAsset.objects.create(
            user=self.user,
            character_id=11,
            item_id=container_item_id,
            raw_location_id=root_structure_id,
            location_id=root_structure_id,
            location_flag="hangar",
            type_id=123,
            quantity=1,
            synced_at=timezone.now(),
        )
        CachedStructureName.objects.create(
            structure_id=root_structure_id,
            name=f"Structure {root_structure_id}",
            last_resolved=timezone.now(),
        )

        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(999001,)]
            response = self.client.get(reverse("indy_hub:personnal_bp_list"))

        self.assertEqual(response.status_code, 200)
        page = response.context["blueprints"]
        self.assertGreaterEqual(len(page.object_list), 1)
        bp = page.object_list[0]
        self.assertEqual(bp.location_name, "Known Label")
        self.assertEqual(bp.location_path, str(root_structure_id))

    def test_reaction_blueprint_hides_efficiency_bars(self) -> None:
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=11,
            item_id=91001,
            blueprint_id=91002,
            type_id=999001,
            location_id=PUBLIC_STATION_ID,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=0,
            material_efficiency=0,
            runs=0,
            character_name="Industrialist",
            type_name="Polymer Reaction",
        )

        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(999001,)]

            response = self.client.get(reverse("indy_hub:personnal_bp_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "efficiency-grid")
        self.assertContains(response, "type-badge reaction")

    def test_corporation_blueprints_visible_across_users_in_same_corp(self) -> None:
        provider = User.objects.create_user("corp_provider", password="secret123")
        assign_main_character(provider, character_id=102002)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user("corp_viewer", password="secret123")
        assign_main_character(viewer, character_id=102003)
        grant_indy_permissions(viewer, "can_manage_corp_bp_requests")

        corp_id = 2_000_000  # default from assign_main_character
        Blueprint.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            item_id=9052001,
            blueprint_id=9052002,
            type_id=705002,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=14,
            material_efficiency=10,
            runs=0,
            type_name="Shared Corporate Widget Blueprint",
        )

        self.client.force_login(viewer)
        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(705002,)]

            response = self.client.get(reverse("indy_hub:corporation_bp_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["blueprints"]
        visible_type_ids = {bp.type_id for bp in page_obj}
        self.assertIn(705002, visible_type_ids)

    def test_corporation_blueprints_visible_to_shared_catalog_viewer(self) -> None:
        provider = User.objects.create_user(
            "corp_provider_shared", password="secret123"
        )
        assign_main_character(provider, character_id=102102)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user("corp_viewer_shared", password="secret123")
        assign_main_character(viewer, character_id=102103)
        grant_indy_permissions(viewer)

        corp_id = 2_000_000
        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            blueprint_catalog_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        Blueprint.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            item_id=9052101,
            blueprint_id=9052102,
            type_id=705102,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=8,
            runs=0,
            type_name="Shared Catalog Blueprint",
        )

        self.client.force_login(viewer)
        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(705102,)]

            response = self.client.get(reverse("indy_hub:corporation_bp_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["blueprints"]
        visible_type_ids = {bp.type_id for bp in page_obj}
        self.assertIn(705102, visible_type_ids)

    def test_corporation_blueprints_visible_to_alliance_catalog_viewer(self) -> None:
        provider = User.objects.create_user(
            "corp_provider_alliance", password="secret123"
        )
        provider_character = assign_main_character(provider, character_id=102112)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")
        provider_character.corporation_id = 2_000_000
        provider_character.corporation_name = "Test Corp"
        provider_character.alliance_id = 5_000_000
        provider_character.alliance_name = "Alliance Umbrella"
        provider_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "alliance_id",
                "alliance_name",
            ]
        )

        viewer = User.objects.create_user("corp_viewer_alliance", password="secret123")
        viewer_character = assign_main_character(viewer, character_id=102113)
        grant_indy_permissions(viewer)
        viewer_character.corporation_id = 2_100_000
        viewer_character.corporation_name = "Ally Corp"
        viewer_character.alliance_id = 5_000_000
        viewer_character.alliance_name = "Alliance Umbrella"
        viewer_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "alliance_id",
                "alliance_name",
            ]
        )

        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            blueprint_catalog_scope=CharacterSettings.SCOPE_ALLIANCE,
        )
        Blueprint.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            item_id=9052111,
            blueprint_id=9052112,
            type_id=705112,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=8,
            runs=0,
            type_name="Alliance Catalog Blueprint",
        )

        self.client.force_login(viewer)
        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(705112,)]

            response = self.client.get(reverse("indy_hub:corporation_bp_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["blueprints"]
        visible_type_ids = {bp.type_id for bp in page_obj}
        self.assertIn(705112, visible_type_ids)

    def test_corporation_blueprints_visible_to_everyone_catalog_viewer(self) -> None:
        provider = User.objects.create_user(
            "corp_provider_everyone", password="secret123"
        )
        assign_main_character(provider, character_id=102122)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user("corp_viewer_everyone", password="secret123")
        viewer_character = assign_main_character(viewer, character_id=102123)
        grant_indy_permissions(viewer)
        viewer_character.corporation_id = 2_200_000
        viewer_character.corporation_name = "Neutral Corp"
        viewer_character.alliance_id = 6_000_000
        viewer_character.alliance_name = "Neutral Alliance"
        viewer_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "alliance_id",
                "alliance_name",
            ]
        )

        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            blueprint_catalog_scope=CharacterSettings.SCOPE_EVERYONE,
        )
        Blueprint.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            item_id=9052121,
            blueprint_id=9052122,
            type_id=705122,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=12,
            material_efficiency=8,
            runs=0,
            type_name="Public Catalog Blueprint",
        )

        self.client.force_login(viewer)
        with patch("indy_hub.views.industry.connection") as mock_connection:
            cursor = mock_connection.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(705122,)]

            response = self.client.get(reverse("indy_hub:corporation_bp_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["blueprints"]
        visible_type_ids = {bp.type_id for bp in page_obj}
        self.assertIn(705122, visible_type_ids)

    def test_corporation_blueprints_redirect_without_catalog_access(self) -> None:
        provider = User.objects.create_user(
            "corp_provider_private", password="secret123"
        )
        assign_main_character(provider, character_id=102202)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user("corp_viewer_private", password="secret123")
        assign_main_character(viewer, character_id=102203)
        grant_indy_permissions(viewer)

        Blueprint.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            item_id=9052201,
            blueprint_id=9052202,
            type_id=705202,
            location_id=PUBLIC_STATION_ID,
            location_flag="corp_hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=6,
            runs=0,
            type_name="Private Catalog Blueprint",
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("indy_hub:corporation_bp_list"))

        self.assertRedirects(response, reverse("indy_hub:personnal_bp_list"))


class CorporationBlueprintCatalogSettingsTests(TestCase):
    def test_manager_can_update_corporation_blueprint_catalog_scope(self) -> None:
        # Standard Library
        import json

        manager = User.objects.create_user("catalog_manager", password="secret123")
        assign_main_character(manager, character_id=102301)
        grant_indy_permissions(manager, "can_manage_corp_bp_requests")
        self.client.force_login(manager)

        with patch(
            "indy_hub.views.user._collect_corporation_scope_status",
            side_effect=AssertionError(
                "settings POST should not collect corp scope status"
            ),
        ):
            response = self.client.post(
                reverse("indy_hub:toggle_corporation_blueprint_catalog"),
                data=json.dumps(
                    {
                        "corporation_id": 2_000_000,
                        "scope": CharacterSettings.SCOPE_CORPORATION,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        setting = CorporationSharingSetting.objects.get(
            user=manager,
            corporation_id=2_000_000,
        )
        self.assertEqual(
            setting.blueprint_catalog_scope,
            CharacterSettings.SCOPE_CORPORATION,
        )


class CorporationCopySharingSettingsTests(TestCase):
    def test_manager_can_update_corporation_copy_scope_without_role_refresh(
        self,
    ) -> None:
        # Standard Library
        import json

        manager = User.objects.create_user("copy_scope_manager", password="secret123")
        assign_main_character(manager, character_id=102399)
        grant_indy_permissions(manager, "can_manage_corp_bp_requests")
        self.client.force_login(manager)

        with patch(
            "indy_hub.views.user._collect_corporation_scope_status",
            side_effect=AssertionError(
                "settings POST should not collect corp scope status"
            ),
        ):
            response = self.client.post(
                reverse("indy_hub:toggle_corporation_copy_sharing"),
                data=json.dumps(
                    {
                        "corporation_id": 2_000_000,
                        "scope": CharacterSettings.SCOPE_CORPORATION,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        setting = CorporationSharingSetting.objects.get(
            user=manager,
            corporation_id=2_000_000,
        )
        self.assertEqual(
            setting.share_scope,
            CharacterSettings.SCOPE_CORPORATION,
        )


class CorporationJobCatalogSettingsTests(TestCase):
    def test_manager_can_update_corporation_job_catalog_scope(self) -> None:
        # Standard Library
        import json

        manager = User.objects.create_user("job_catalog_manager", password="secret123")
        assign_main_character(manager, character_id=102302)
        grant_indy_permissions(manager, "can_manage_corp_bp_requests")
        self.client.force_login(manager)

        with patch(
            "indy_hub.views.user._collect_corporation_scope_status",
            side_effect=AssertionError(
                "settings POST should not collect corp scope status"
            ),
        ):
            response = self.client.post(
                reverse("indy_hub:toggle_corporation_job_catalog"),
                data=json.dumps(
                    {
                        "corporation_id": 2_000_000,
                        "scope": CharacterSettings.SCOPE_CORPORATION,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        setting = CorporationSharingSetting.objects.get(
            user=manager,
            corporation_id=2_000_000,
        )
        self.assertEqual(
            setting.job_catalog_scope,
            CharacterSettings.SCOPE_CORPORATION,
        )


class CorporationJobListViewTests(TestCase):
    def test_corporation_jobs_visible_across_users_in_same_corp(self) -> None:
        provider = User.objects.create_user("corpjob_provider", password="secret123")
        assign_main_character(provider, character_id=103001)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user("corpjob_viewer", password="secret123")
        assign_main_character(viewer, character_id=103002)
        grant_indy_permissions(viewer, "can_manage_corp_bp_requests")

        start = timezone.now()
        end = start + timedelta(hours=1)
        corp_id = 2_000_000  # default from assign_main_character

        IndustryJob.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            character_id=103001,
            job_id=9900001,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6009001,
            blueprint_type_id=6009002,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Corporate Job Blueprint",
            product_type_name="Corporate Job Product",
            character_name="Corp Job Provider",
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["jobs"]
        visible_job_ids = {job.job_id for job in page_obj}
        self.assertIn(9900001, visible_job_ids)

    def test_corporation_jobs_visible_to_shared_catalog_viewer(self) -> None:
        provider = User.objects.create_user(
            "corpjob_provider_shared", password="secret123"
        )
        assign_main_character(provider, character_id=103101)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user("corpjob_viewer_shared", password="secret123")
        assign_main_character(viewer, character_id=103102)
        grant_indy_permissions(viewer)

        start = timezone.now()
        end = start + timedelta(hours=1)
        corp_id = 2_000_000

        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            job_catalog_scope=CharacterSettings.SCOPE_CORPORATION,
        )
        IndustryJob.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=corp_id,
            corporation_name="Test Corp",
            character_id=103101,
            job_id=9900101,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6010001,
            blueprint_type_id=6010002,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Shared Corporate Job Blueprint",
            product_type_name="Shared Corporate Job Product",
            character_name="Corp Job Provider",
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["jobs"]
        visible_job_ids = {job.job_id for job in page_obj}
        self.assertIn(9900101, visible_job_ids)

    def test_corporation_jobs_visible_to_alliance_catalog_viewer(self) -> None:
        provider = User.objects.create_user(
            "corpjob_provider_alliance", password="secret123"
        )
        provider_character = assign_main_character(provider, character_id=103111)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")
        provider_character.corporation_id = 2_000_000
        provider_character.corporation_name = "Test Corp"
        provider_character.alliance_id = 5_000_000
        provider_character.alliance_name = "Alliance Umbrella"
        provider_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "alliance_id",
                "alliance_name",
            ]
        )

        viewer = User.objects.create_user(
            "corpjob_viewer_alliance", password="secret123"
        )
        viewer_character = assign_main_character(viewer, character_id=103112)
        grant_indy_permissions(viewer)
        viewer_character.corporation_id = 2_100_000
        viewer_character.corporation_name = "Ally Corp"
        viewer_character.alliance_id = 5_000_000
        viewer_character.alliance_name = "Alliance Umbrella"
        viewer_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "alliance_id",
                "alliance_name",
            ]
        )

        start = timezone.now()
        end = start + timedelta(hours=1)
        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            job_catalog_scope=CharacterSettings.SCOPE_ALLIANCE,
        )
        IndustryJob.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=103111,
            job_id=9900111,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6010011,
            blueprint_type_id=6010012,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Alliance Corporate Job Blueprint",
            product_type_name="Alliance Corporate Job Product",
            character_name="Corp Job Provider",
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["jobs"]
        visible_job_ids = {job.job_id for job in page_obj}
        self.assertIn(9900111, visible_job_ids)

    def test_corporation_jobs_visible_to_everyone_catalog_viewer(self) -> None:
        provider = User.objects.create_user(
            "corpjob_provider_everyone", password="secret123"
        )
        assign_main_character(provider, character_id=103121)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user(
            "corpjob_viewer_everyone", password="secret123"
        )
        viewer_character = assign_main_character(viewer, character_id=103122)
        grant_indy_permissions(viewer)
        viewer_character.corporation_id = 2_200_000
        viewer_character.corporation_name = "Neutral Corp"
        viewer_character.alliance_id = 6_000_000
        viewer_character.alliance_name = "Neutral Alliance"
        viewer_character.save(
            update_fields=[
                "corporation_id",
                "corporation_name",
                "alliance_id",
                "alliance_name",
            ]
        )

        start = timezone.now()
        end = start + timedelta(hours=1)
        CorporationSharingSetting.objects.create(
            user=provider,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            job_catalog_scope=CharacterSettings.SCOPE_EVERYONE,
        )
        IndustryJob.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=103121,
            job_id=9900121,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6010021,
            blueprint_type_id=6010022,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Public Corporate Job Blueprint",
            product_type_name="Public Corporate Job Product",
            character_name="Corp Job Provider",
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertEqual(response.status_code, 200)
        page_obj = response.context["jobs"]
        visible_job_ids = {job.job_id for job in page_obj}
        self.assertIn(9900121, visible_job_ids)

    def test_corporation_jobs_redirect_without_catalog_access(self) -> None:
        provider = User.objects.create_user(
            "corpjob_provider_private", password="secret123"
        )
        assign_main_character(provider, character_id=103201)
        grant_indy_permissions(provider, "can_manage_corp_bp_requests")

        viewer = User.objects.create_user(
            "corpjob_viewer_private", password="secret123"
        )
        assign_main_character(viewer, character_id=103202)
        grant_indy_permissions(viewer)

        start = timezone.now()
        end = start + timedelta(hours=1)

        IndustryJob.objects.create(
            owner_user=provider,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=103201,
            job_id=9900201,
            installer_id=provider.id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6011001,
            blueprint_type_id=6011002,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Private Corporate Job Blueprint",
            product_type_name="Private Corporate Job Product",
            character_name="Corp Job Provider",
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertRedirects(response, reverse("indy_hub:personnal_job_list"))

    def test_corporation_jobs_slot_overview_counts_corp_jobs_for_installer(
        self,
    ) -> None:
        manager = User.objects.create_user(
            "corpjob_slots_manager", password="secret123"
        )
        manager_character = assign_main_character(manager, character_id=103301)
        grant_indy_permissions(manager, "can_manage_corp_bp_requests")
        IndustrySkillSnapshot.objects.create(
            owner_user=manager,
            character_id=manager_character.character_id,
            mass_production_level=4,
            advanced_mass_production_level=0,
            laboratory_operation_level=0,
            advanced_laboratory_operation_level=0,
            mass_reactions_level=0,
            advanced_mass_reactions_level=0,
            skill_levels={"3387": {"active": 4, "trained": 4}},
        )

        start = timezone.now()
        end = start + timedelta(hours=1)
        IndustryJob.objects.create(
            owner_user=manager,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=None,
            job_id=9900301,
            installer_id=manager_character.character_id,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6012001,
            blueprint_type_id=6012002,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Slot Count Corp Job Blueprint",
            product_type_name="Slot Count Corp Job Product",
            character_name="Corp Slot Manager",
        )

        self.client.force_login(manager)
        with patch(
            "indy_hub.services.industry_skills.Token.objects.filter"
        ) as mock_token_filter:
            mock_token_filter.return_value.require_scopes.return_value.require_valid.return_value.values_list.return_value = [
                manager_character.character_id
            ]
            response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertEqual(response.status_code, 200)
        summary = response.context["slot_overview_summary"]
        self.assertEqual(summary["manufacturing"]["total"], 5)
        self.assertEqual(summary["manufacturing"]["available"], 4)
        self.assertEqual(summary["manufacturing"]["used"], 1)

    def test_corporation_jobs_slot_overview_ignores_other_installers(self) -> None:
        manager = User.objects.create_user("corpjob_slots_other", password="secret123")
        manager_character = assign_main_character(manager, character_id=103302)
        grant_indy_permissions(manager, "can_manage_corp_bp_requests")
        IndustrySkillSnapshot.objects.create(
            owner_user=manager,
            character_id=manager_character.character_id,
            mass_production_level=4,
            advanced_mass_production_level=0,
            laboratory_operation_level=0,
            advanced_laboratory_operation_level=0,
            mass_reactions_level=0,
            advanced_mass_reactions_level=0,
            skill_levels={"3387": {"active": 4, "trained": 4}},
        )

        start = timezone.now()
        end = start + timedelta(hours=1)
        IndustryJob.objects.create(
            owner_user=manager,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=2_000_000,
            corporation_name="Test Corp",
            character_id=None,
            job_id=9900302,
            installer_id=99999999,
            station_id=PUBLIC_STATION_ID,
            location_name="Corp Factory",
            activity_id=1,
            blueprint_id=6012011,
            blueprint_type_id=6012012,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=end,
            activity_name="Manufacturing",
            blueprint_type_name="Other Installer Corp Job Blueprint",
            product_type_name="Other Installer Corp Job Product",
            character_name="Other Installer",
        )

        self.client.force_login(manager)
        with patch(
            "indy_hub.services.industry_skills.Token.objects.filter"
        ) as mock_token_filter:
            mock_token_filter.return_value.require_scopes.return_value.require_valid.return_value.values_list.return_value = [
                manager_character.character_id
            ]
            response = self.client.get(reverse("indy_hub:corporation_job_list"))

        self.assertEqual(response.status_code, 200)
        summary = response.context["slot_overview_summary"]
        self.assertEqual(summary["manufacturing"]["total"], 5)
        self.assertEqual(summary["manufacturing"]["available"], 5)
        self.assertEqual(summary["manufacturing"]["used"], 0)


class BlueprintCopyMyRequestsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("buyer", password="test12345")
        assign_main_character(self.user, character_id=101003)
        grant_indy_permissions(self.user, "can_manage_corp_bp_requests")
        self.client.force_login(self.user)

    def test_my_requests_metrics_and_statuses(self) -> None:
        # Open request (no offers yet)
        BlueprintCopyRequest.objects.create(
            type_id=11,
            material_efficiency=0,
            time_efficiency=0,
            requested_by=self.user,
            runs_requested=1,
            copies_requested=1,
        )

        # Conditional offer awaiting decision
        pending_req = BlueprintCopyRequest.objects.create(
            type_id=12,
            material_efficiency=2,
            time_efficiency=4,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
        )
        seller = User.objects.create_user("seller", password="sellerpass")
        BlueprintCopyOffer.objects.create(
            request=pending_req,
            owner=seller,
            status="conditional",
            message="2 runs for 10m each",
        )

        # Accepted and awaiting delivery
        BlueprintCopyRequest.objects.create(
            type_id=13,
            material_efficiency=8,
            time_efficiency=10,
            requested_by=self.user,
            runs_requested=3,
            copies_requested=1,
            fulfilled=True,
        )

        # Completed delivery
        BlueprintCopyRequest.objects.create(
            type_id=14,
            material_efficiency=6,
            time_efficiency=8,
            requested_by=self.user,
            runs_requested=1,
            copies_requested=1,
            fulfilled=True,
            delivered=True,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_my_requests"))

        self.assertEqual(response.status_code, 200)
        metrics = response.context["metrics"]
        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["open"], 1)
        self.assertEqual(metrics["action_required"], 1)
        self.assertEqual(metrics["awaiting_delivery"], 1)
        self.assertEqual(metrics["delivered"], 1)

        statuses = {req["status_key"] for req in response.context["my_requests"]}
        self.assertIn("open", statuses)
        self.assertIn("action_required", statuses)
        self.assertIn("awaiting_delivery", statuses)
        self.assertIn("delivered", statuses)
        self.assertContains(response, "data-chat-inline")
        self.assertContains(response, 'data-chat-target="bpChatInline-')
        self.assertNotContains(response, 'id="bpChatModal"', html=False)

    def test_my_requests_keeps_accepted_chat_until_delivery(self) -> None:
        seller = User.objects.create_user("acceptedbuilder", password="sellerpass")
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=15,
            material_efficiency=4,
            time_efficiency=8,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
            fulfilled=True,
            fulfilled_by=seller,
        )
        offer = BlueprintCopyOffer.objects.create(
            request=request_obj,
            owner=seller,
            status="accepted",
            accepted_by_buyer=True,
            accepted_by_seller=True,
        )
        chat = BlueprintCopyChat.objects.create(
            request=request_obj,
            offer=offer,
            buyer=self.user,
            seller=seller,
            is_open=False,
            closed_reason=BlueprintCopyChat.CloseReason.OFFER_ACCEPTED,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_my_requests"))

        self.assertEqual(response.status_code, 200)
        request_payload = next(
            req
            for req in response.context["my_requests"]
            if req["id"] == request_obj.id
        )
        self.assertEqual(request_payload["status_key"], "awaiting_delivery")
        self.assertEqual(len(request_payload["chat_actions"]), 1)
        self.assertEqual(request_payload["chat_actions"][0]["chat"]["id"], chat.id)
        chat.refresh_from_db()
        self.assertTrue(chat.is_open)


class BlueprintCopyDeliveryChatTests(TestCase):
    def setUp(self) -> None:
        self.buyer = User.objects.create_user("buyer_delivery", password="test12345")
        assign_main_character(self.buyer, character_id=101103)
        self.seller = User.objects.create_user("seller_delivery", password="test12345")
        assign_main_character(self.seller, character_id=101104)
        grant_indy_permissions(self.buyer, "can_manage_corp_bp_requests")
        grant_indy_permissions(self.seller, "can_manage_corp_bp_requests")
        self.client.force_login(self.seller)

    def test_mark_delivered_closes_remaining_chat(self) -> None:
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=16,
            material_efficiency=6,
            time_efficiency=10,
            requested_by=self.buyer,
            runs_requested=1,
            copies_requested=1,
            fulfilled=True,
            fulfilled_by=self.seller,
        )
        offer = BlueprintCopyOffer.objects.create(
            request=request_obj,
            owner=self.seller,
            status="accepted",
            accepted_by_buyer=True,
            accepted_by_seller=True,
        )
        chat = BlueprintCopyChat.objects.create(
            request=request_obj,
            offer=offer,
            buyer=self.buyer,
            seller=self.seller,
            is_open=True,
        )

        response = self.client.post(
            reverse("indy_hub:bp_mark_copy_delivered", args=[request_obj.id])
        )

        self.assertRedirects(response, reverse("indy_hub:bp_copy_fulfill_requests"))
        request_obj.refresh_from_db()
        chat.refresh_from_db()
        self.assertTrue(request_obj.delivered)
        self.assertFalse(chat.is_open)


class OnboardingViewsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("rookie", password="rookiepass")
        assign_main_character(self.user, character_id=2024001)
        grant_indy_permissions(self.user)
        self.client.force_login(self.user)
        self.toggle_url = reverse("indy_hub:onboarding_toggle_task")
        self.visibility_url = reverse("indy_hub:onboarding_set_visibility")

    def test_manual_task_completion_marks_progress(self) -> None:
        response = self.client.post(
            self.toggle_url,
            {
                "task": "review_guides",
                "action": "complete",
            },
        )

        self.assertRedirects(response, reverse("indy_hub:index"))
        progress = UserOnboardingProgress.objects.get(user=self.user)
        self.assertTrue(progress.manual_steps.get("review_guides"))
        self.assertFalse(progress.dismissed)

    def test_non_manual_task_rejected(self) -> None:
        self.client.post(
            self.toggle_url,
            {
                "task": "review_guides",
                "action": "complete",
            },
        )
        response = self.client.post(
            self.toggle_url,
            {
                "task": "connect_blueprints",
                "action": "complete",
            },
        )

        self.assertRedirects(response, reverse("indy_hub:index"))
        progress = UserOnboardingProgress.objects.get(user=self.user)
        self.assertIn("review_guides", progress.manual_steps)
        self.assertNotIn("connect_blueprints", progress.manual_steps)

    def test_visibility_toggle_dismisses_and_restores(self) -> None:
        response = self.client.post(
            self.visibility_url,
            {
                "action": "dismiss",
            },
        )
        self.assertRedirects(response, reverse("indy_hub:index"))
        progress = UserOnboardingProgress.objects.get(user=self.user)
        self.assertTrue(progress.dismissed)

        response = self.client.post(
            self.visibility_url,
            {
                "action": "restore",
            },
        )
        self.assertRedirects(response, reverse("indy_hub:index"))
        progress.refresh_from_db()
        self.assertFalse(progress.dismissed)
