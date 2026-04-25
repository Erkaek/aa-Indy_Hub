# Standard Library
import json
from unittest.mock import patch

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

# AA Example App
from indy_hub.models import IndustryJob, ProductionProject, ProductionSimulation
from indy_hub.services.production_projects import (
    build_legacy_workspace_state,
    create_project_from_single_blueprint,
)
from indy_hub.services.project_progress import normalize_project_progress
from indy_hub.views.api import (
    save_production_project_progress,
    save_production_project_workspace,
)
from indy_hub.views.industry import craft_bp


def _unwrap_view(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


class LegacySimulationUnificationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="legacy-user", password="testpass123"
        )
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)

    @property
    def _craft_bp_view(self):
        return _unwrap_view(craft_bp)

    @property
    def _save_workspace_view(self):
        return _unwrap_view(save_production_project_workspace)

    @property
    def _save_progress_view(self):
        return _unwrap_view(save_production_project_progress)

    def _prepare_request(self, request: HttpRequest, *, user=None) -> HttpRequest:
        request.user = user or self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    @patch("indy_hub.views.industry.create_project_from_single_blueprint")
    def test_craft_bp_redirects_to_project_workspace(self, mock_create_project):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Vedmak",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )
        mock_create_project.return_value = project

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_bp", args=[603]),
                data={"runs": 4, "me": 7, "te": 12, "active_tab": "financial"},
            )
        )
        response = self._craft_bp_view(request, 603)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("indy_hub:craft_project", args=[project.project_ref]),
        )
        mock_create_project.assert_called_once()

    def test_save_production_project_workspace_persists_workspace_state(self):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Vedmak",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )

        request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:save_production_project_workspace",
                    args=[project.project_ref],
                ),
                data=json.dumps(
                    {
                        "blueprint_type_id": 603,
                        "blueprint_name": "Vedmak Blueprint",
                        "runs": 4,
                        "simulation_name": "Saved Vedmak Table",
                        "active_tab": "financial",
                        "items": [{"type_id": 34, "mode": "buy", "quantity": 12}],
                        "blueprint_efficiencies": [
                            {
                                "blueprint_type_id": 603,
                                "material_efficiency": 7,
                                "time_efficiency": 12,
                            }
                        ],
                        "custom_prices": [
                            {
                                "item_type_id": 34,
                                "unit_price": 5.5,
                                "is_sale_price": False,
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
        )
        response = self._save_workspace_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.name, "Saved Vedmak Table")
        self.assertEqual(project.workspace_state["active_tab"], "financial")
        self.assertEqual(project.workspace_state["items"][0]["mode"], "buy")

    def test_save_production_project_workspace_preserves_blueprint_context(self):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Merlin",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            workspace_state={
                "blueprint_type_id": 950,
                "blueprint_name": "Merlin Blueprint",
            },
        )

        request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:save_production_project_workspace",
                    args=[project.project_ref],
                ),
                data=json.dumps(
                    {
                        "runs": 4,
                        "simulation_name": "Saved Merlin Table",
                        "active_tab": "financial",
                    }
                ),
                content_type="application/json",
            )
        )
        response = self._save_workspace_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.workspace_state["blueprint_type_id"], 950)
        self.assertEqual(project.workspace_state["blueprint_name"], "Merlin Blueprint")

    @patch("indy_hub.services.production_projects.get_type_name")
    @patch("indy_hub.services.production_projects._get_blueprint_output_quantity")
    @patch("indy_hub.services.production_projects._resolve_blueprints_for_products")
    @patch("indy_hub.services.production_projects.get_blueprint_product_type_id")
    def test_create_project_from_single_blueprint_normalizes_product_type_input(
        self,
        mock_get_blueprint_product_type_id,
        mock_resolve_blueprints_for_products,
        mock_get_blueprint_output_quantity,
        mock_get_type_name,
    ):
        mock_get_blueprint_product_type_id.side_effect = [None, 603]
        mock_resolve_blueprints_for_products.return_value = {603: 950}
        mock_get_blueprint_output_quantity.return_value = 1
        mock_get_type_name.side_effect = lambda type_id: {
            603: "Merlin",
            950: "Merlin Blueprint",
        }.get(type_id, "")

        project = create_project_from_single_blueprint(
            user=self.user,
            blueprint_type_id=603,
            blueprint_name="Merlin",
            runs=4,
        )

        item = project.items.get()
        self.assertEqual(project.name, "Merlin")
        self.assertEqual(item.type_id, 603)
        self.assertEqual(item.type_name, "Merlin")
        self.assertEqual(item.blueprint_type_id, 950)
        self.assertEqual(item.quantity_requested, 4)
        self.assertEqual(project.workspace_state["blueprint_type_id"], 950)
        self.assertEqual(project.workspace_state["blueprint_name"], "Merlin Blueprint")

    def test_build_legacy_workspace_state_serializes_related_rows(self):
        simulation = ProductionSimulation.objects.create(
            user=self.user,
            blueprint_type_id=603,
            blueprint_name="Vedmak Blueprint",
            runs=3,
            simulation_name="Legacy Vedmak",
            active_tab="financial",
        )
        simulation.production_configs.create(
            user=self.user,
            blueprint_type_id=603,
            item_type_id=34,
            production_mode="buy",
            quantity_needed=25,
            runs=3,
        )
        simulation.blueprint_efficiencies.create(
            user=self.user,
            blueprint_type_id=603,
            material_efficiency=8,
            time_efficiency=14,
        )
        simulation.custom_prices.create(
            user=self.user,
            item_type_id=34,
            unit_price=6.2,
            is_sale_price=False,
        )

        state = build_legacy_workspace_state(simulation)

        self.assertEqual(state["runs"], 3)
        self.assertEqual(state["items"][0]["mode"], "buy")
        self.assertEqual(state["blueprint_efficiencies"][0]["material_efficiency"], 8)
        self.assertEqual(state["custom_prices"][0]["item_type_id"], 34)

    def test_save_production_project_progress_persists_summary_progress(self):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Progress Test",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            summary={"selected_items": 5},
        )
        first_item = project.items.create(
            type_id=None,
            type_name="Merlin",
            quantity_requested=4,
            is_selected=True,
            is_craftable=True,
            inclusion_mode="produce",
            blueprint_type_id=603,
        )
        project.items.create(
            type_id=34,
            type_name="Tritanium",
            quantity_requested=100,
            is_selected=True,
            is_craftable=False,
            inclusion_mode="buy",
        )
        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=90000001,
            corporation_id=None,
            corporation_name="",
            owner_kind="character",
            job_id=810001,
            installer_id=90000001,
            station_id=60003760,
            location_name="Jita IV - Moon 4",
            activity_id=1,
            blueprint_id=700001,
            blueprint_type_id=603,
            runs=2,
            product_type_id=603,
            status="delivered",
            duration=3600,
            start_date=timezone.now() - timezone.timedelta(hours=2),
            end_date=timezone.now() - timezone.timedelta(hours=1),
            completed_date=timezone.now() - timezone.timedelta(hours=1),
            successful_runs=2,
            product_type_name="Merlin",
            blueprint_type_name="Merlin Blueprint",
            character_name="Legacy User",
            activity_name="Manufacturing",
        )
        IndustryJob.objects.create(
            owner_user=self.user,
            character_id=90000001,
            corporation_id=None,
            corporation_name="",
            owner_kind="character",
            job_id=810002,
            installer_id=90000001,
            station_id=60003760,
            location_name="Jita IV - Moon 4",
            activity_id=1,
            blueprint_id=700002,
            blueprint_type_id=603,
            runs=2,
            product_type_id=603,
            status="active",
            duration=7200,
            start_date=timezone.now() - timezone.timedelta(hours=1),
            end_date=timezone.now() + timezone.timedelta(hours=1),
            successful_runs=0,
            product_type_name="Merlin",
            blueprint_type_name="Merlin Blueprint",
            character_name="Legacy User",
            activity_name="Manufacturing",
        )

        request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:save_production_project_progress",
                    args=[project.project_ref],
                ),
                data=json.dumps(
                    {
                        "in_progress_ids": [],
                        "completed_ids": [],
                        "linked_job_ids_by_item": {
                            str(first_item.id): ["810001", "810002"],
                        },
                    }
                ),
                content_type="application/json",
            )
        )
        response = self._save_progress_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        progress = normalize_project_progress(
            project, (project.summary or {}).get("item_progress")
        )
        self.assertEqual(progress["completed_ids"], [])
        self.assertEqual(progress["in_progress_ids"], [])
        self.assertEqual(
            progress["linked_job_ids_by_item"],
            {str(first_item.id): ["810001", "810002"]},
        )
        self.assertEqual(progress["total_count"], 1)
        self.assertEqual(progress["total_quantity"], 4)
        self.assertEqual(progress["progress_quantity"], 3)
        self.assertEqual(progress["completed_count"], 0)
        self.assertEqual(progress["completed_quantity"], 2)
        self.assertEqual(progress["in_progress_count"], 1)
        self.assertEqual(progress["completion_percentage"], 75)
        self.assertEqual(progress["items"][0]["linked_job_count"], 2)
        self.assertEqual(progress["items"][0]["auto_completed_quantity"], 2)
        self.assertEqual(progress["items"][0]["auto_progress_quantity"], 3)
