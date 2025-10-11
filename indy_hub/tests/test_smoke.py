"""Basic smoke tests for the Indy Hub app."""

# Django
from django.apps import apps
from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

# Alliance Auth
from allianceauth.authentication.models import UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import (
    Blueprint,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    CharacterSettings,
)
from indy_hub.utils.eve import get_type_name


def assign_main_character(user: User, *, character_id: int) -> EveCharacter:
    """Ensure the given user has a main character to satisfy middleware requirements."""

    profile, _ = UserProfile.objects.get_or_create(user=user)

    character, _ = EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={
            "character_name": f"{user.username.title()}",
            "corporation_id": 2000000,
            "corporation_name": "Test Corp",
            "corporation_ticker": "TEST",
            "alliance_id": None,
            "alliance_name": "",
            "alliance_ticker": "",
            "faction_id": None,
            "faction_name": "",
        },
    )
    profile.main_character = character
    profile.save(update_fields=["main_character"])
    return character


class IndyHubConfigTests(TestCase):
    def test_app_is_registered(self) -> None:
        """The indy_hub app should be installed and discoverable."""
        app_config = apps.get_app_config("indy_hub")
        self.assertEqual(app_config.name, "indy_hub")

    def test_get_type_name_graceful_fallback(self) -> None:
        """`get_type_name` should fall back to the stringified id when EveUniverse is absent."""
        self.assertEqual(get_type_name(12345), "12345")


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
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)
        self.client.force_login(self.user)

    def test_request_visible_for_own_blueprint(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=1001,
            blueprint_id=2001,
            type_id=987654,
            location_id=3001,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=5,
            runs=0,
            character_name="Capsuleer",
            type_name="Test Blueprint",
        )
        buyer = User.objects.create_user("requester", password="test12345")
        request_obj = BlueprintCopyRequest.objects.create(
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
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["id"], request_obj.id)
        self.assertEqual(requests[0]["status_key"], "awaiting_response")
        self.assertTrue(requests[0]["show_offer_actions"])
        self.assertFalse(requests[0]["is_self_request"])

    def test_self_request_visible_but_read_only(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=2001,
            blueprint_id=3001,
            type_id=555,
            location_id=4001,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=8,
            material_efficiency=7,
            runs=0,
            character_name="Capsuleer",
            type_name="Another Blueprint",
        )
        request_obj = BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=self.user,
            runs_requested=2,
            copies_requested=1,
        )

        response = self.client.get(reverse("indy_hub:bp_copy_fulfill_requests"))

        self.assertEqual(response.status_code, 200)
        requests = response.context["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["id"], request_obj.id)
        self.assertEqual(response.context["metrics"]["total"], 1)
        self.assertEqual(response.context["metrics"]["awaiting_response"], 0)
        self.assertTrue(requests[0]["is_self_request"])
        self.assertEqual(requests[0]["status_key"], "self_request")
        self.assertFalse(requests[0]["show_offer_actions"])
        self.assertFalse(requests[0]["can_mark_delivered"])


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
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)
        self.client.force_login(self.user)

        self.blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=7,
            item_id=4001,
            blueprint_id=5001,
            type_id=123456,
            location_id=6001,
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
            type_id=self.blueprint.type_id,
            material_efficiency=self.blueprint.material_efficiency,
            time_efficiency=self.blueprint.time_efficiency,
            requested_by=self.user,
            runs_requested=3,
            copies_requested=1,
        )
        BlueprintCopyRequest.objects.create(
            type_id=self.blueprint.type_id,
            material_efficiency=self.blueprint.material_efficiency,
            time_efficiency=self.blueprint.time_efficiency,
            requested_by=self.user,
            runs_requested=1,
            copies_requested=1,
            fulfilled=True,
            delivered=False,
        )

        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy_fulfill_count"], 2)
        self.assertEqual(response.context["copy_my_requests_open"], 1)
        self.assertEqual(response.context["copy_my_requests_pending_delivery"], 1)
        self.assertEqual(response.context["copy_my_requests_total"], 2)


class BlueprintCopyMyRequestsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("buyer", password="test12345")
        assign_main_character(self.user, character_id=101003)
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)
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
