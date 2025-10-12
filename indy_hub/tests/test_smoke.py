"""Basic smoke tests for the Indy Hub app."""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.apps import apps
from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import (
    Blueprint,
    BlueprintCopyOffer,
    BlueprintCopyRequest,
    CharacterSettings,
    IndustryJob,
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
            location_id=10,
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

    def test_reaction_detection_from_name(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=9002,
            item_id=9002001,
            blueprint_id=9003001,
            type_id=434343,
            location_id=11,
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
            location_id=12,
            location_flag="hangar",
            quantity=5,
            time_efficiency=0,
            material_efficiency=0,
            runs=2,
            character_name="Classifier",
            type_name="Widget Blueprint Copy",
        )
        self.assertEqual(blueprint.bp_type, Blueprint.BPType.COPY)


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

    def test_busy_blueprints_flagged_in_context(self) -> None:
        blueprint = Blueprint.objects.create(
            owner_user=self.user,
            character_id=42,
            item_id=3001,
            blueprint_id=4001,
            type_id=987001,
            location_id=5001,
            location_flag="hangar",
            quantity=-1,
            time_efficiency=10,
            material_efficiency=7,
            runs=0,
            character_name="Capsuleer",
            type_name="Busy Blueprint",
        )
        buyer = User.objects.create_user("busy_requester", password="test12345")
        BlueprintCopyRequest.objects.create(
            type_id=blueprint.type_id,
            material_efficiency=blueprint.material_efficiency,
            time_efficiency=blueprint.time_efficiency,
            requested_by=buyer,
            runs_requested=3,
            copies_requested=2,
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
            location_id=6001,
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
            location_id=7001,
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
            location_id=8001,
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
            location_id=8101,
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
            location_id=5001,
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

        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=self.blueprint.character_id,
            job_id=7770001,
            installer_id=self.user.id,
            station_id=self.blueprint.location_id,
            location_name="Busy Location",
            activity_id=5,
            blueprint_id=self.blueprint.item_id,
            blueprint_type_id=self.blueprint.type_id,
            runs=1,
            status="active",
            duration=3600,
            start_date=timezone.now() - timedelta(minutes=10),
            end_date=timezone.now() + timedelta(hours=2),
            activity_name="Copying",
            blueprint_type_name=self.blueprint.type_name,
            product_type_name="Busy Product",
            character_name=self.blueprint.character_name,
        )
        response = self.client.get(reverse("indy_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy_fulfill_count"], 2)
        self.assertEqual(response.context["copy_my_requests_open"], 1)
        self.assertEqual(response.context["copy_my_requests_pending_delivery"], 1)
        self.assertEqual(response.context["copy_my_requests_total"], 2)


class PersonnalBlueprintViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("industrialist", password="secret123")
        assign_main_character(self.user, character_id=102001)
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)
        self.client.force_login(self.user)

    def test_reaction_blueprint_hides_efficiency_bars(self) -> None:
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=11,
            item_id=91001,
            blueprint_id=91002,
            type_id=999001,
            location_id=91003,
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
