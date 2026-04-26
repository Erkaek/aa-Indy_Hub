# Standard Library
import json
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

# AA Example App
from indy_hub.models import IndustryJob, ProductionProject, ProductionProjectItem
from indy_hub.services.production_projects import (
    LEGACY_SINGLE_BLUEPRINT_PROJECT_NOTE,
    _scale_project_selected_items_for_runs,
    create_project_from_single_blueprint,
    get_cached_project_workspace_payload,
    strip_project_workspace_cache,
)
from indy_hub.services.project_progress import normalize_project_progress
from indy_hub.views.api import (
    production_project_payload,
    save_production_project_progress,
    save_production_project_workspace,
)
from indy_hub.views.industry import craft_bp, craft_project


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

    @property
    def _production_project_payload_view(self):
        return _unwrap_view(production_project_payload)

    @property
    def _craft_project_view(self):
        return _unwrap_view(craft_project)

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
                        "buyTypeIds": [34],
                        "stockAllocations": {"34": 7},
                        "manualPrices": [
                            {
                                "typeId": 34,
                                "priceType": "real",
                                "value": 5.5,
                            }
                        ],
                        "fuzzworkPrices": {"34": 4.2, "603": 900000.0},
                        "meTeConfig": {
                            "mainME": 7,
                            "mainTE": 12,
                            "blueprintConfigs": {"603": {"me": 7, "te": 12}},
                        },
                        "copyRequests": [
                            {
                                "typeId": 603,
                                "selectValue": "7,12",
                                "runs": 4,
                                "copies": 1,
                            }
                        ],
                        "structure": {
                            "motherSystemInput": "Jita",
                            "selectedSolarSystemId": 30000142,
                            "selectedSolarSystemName": "Jita",
                            "assignments": [{"typeId": 603, "structureId": 102938}],
                        },
                        "cachedPayload": {
                            "materials_tree": [
                                {
                                    "type_id": 34,
                                    "type_name": "Tritanium",
                                    "quantity": 12,
                                    "project_inclusion_mode": "buy",
                                    "sub_materials": [],
                                }
                            ]
                        },
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
        self.assertEqual(project.workspace_state["buyTypeIds"], [34])
        self.assertEqual(project.workspace_state["stockAllocations"], {"34": 7})
        self.assertEqual(project.workspace_state["manualPrices"][0]["typeId"], 34)
        self.assertEqual(project.workspace_state["fuzzworkPrices"]["34"], 4.2)
        self.assertEqual(
            project.workspace_state["meTeConfig"]["blueprintConfigs"]["603"]["me"],
            7,
        )
        self.assertEqual(project.workspace_state["copyRequests"][0]["typeId"], 603)
        self.assertEqual(
            project.workspace_state["structure"]["assignments"][0]["structureId"],
            102938,
        )
        self.assertIn("cachedProjectPayload", project.workspace_state)
        self.assertIn("cachedProjectSdeSignature", project.workspace_state)
        self.assertEqual(
            project.workspace_state["cachedProjectPayload"]["materials_tree"][0][
                "project_inclusion_mode"
            ],
            "buy",
        )

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

    @patch("indy_hub.views.api.build_project_workspace_payload")
    def test_save_production_project_workspace_rebuilds_stale_cached_runs_payload(
        self, mock_build_project_workspace_payload
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Abatis",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )
        mock_build_project_workspace_payload.return_value = {
            "num_runs": 4,
            "final_product_qty": 4,
            "materials_tree": [
                {
                    "type_id": 23783,
                    "quantity": 4,
                    "sub_materials": [],
                }
            ],
        }

        request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:save_production_project_workspace",
                    args=[project.project_ref],
                ),
                data=json.dumps(
                    {
                        "runs": 4,
                        "pendingWorkspaceRefresh": True,
                        "pendingWorkspaceSourceTab": "cycles",
                        "cachedPayload": {
                            "num_runs": 1,
                            "final_product_qty": 1,
                            "workspace_state": {"runs": 4},
                            "materials_tree": [
                                {
                                    "type_id": 23783,
                                    "quantity": 1,
                                    "sub_materials": [],
                                }
                            ],
                        },
                    }
                ),
                content_type="application/json",
            )
        )

        response = self._save_workspace_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        mock_build_project_workspace_payload.assert_called_once()
        project.refresh_from_db()
        self.assertEqual(project.workspace_state["runs"], 4)
        self.assertFalse(project.workspace_state["pendingWorkspaceRefresh"])
        self.assertEqual(project.workspace_state["cachedProjectPayload"]["num_runs"], 4)
        self.assertEqual(
            project.workspace_state["cachedProjectPayload"]["materials_tree"][0][
                "quantity"
            ],
            4,
        )

    def test_get_cached_project_workspace_payload_discards_mismatched_runs_cache(self):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Cached Runs",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            workspace_state={
                "runs": 4,
                "cachedProjectPayload": {
                    "num_runs": 1,
                    "materials_tree": [],
                },
                "cachedProjectSdeSignature": {},
            },
        )

        payload, sde_has_changed = get_cached_project_workspace_payload(project)

        self.assertIsNone(payload)
        self.assertFalse(sde_has_changed)

    def test_strip_project_workspace_cache_removes_pending_refresh_flags(self):
        self.assertEqual(
            strip_project_workspace_cache(
                {
                    "runs": 4,
                    "pendingWorkspaceRefresh": True,
                    "pendingWorkspaceSourceTab": "plan",
                }
            ),
            {"runs": 4},
        )

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

    def test_scale_project_selected_items_for_runs_multiplies_all_project_outputs(self):
        project = ProductionProject(
            user=self.user,
            name="Batch Project",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            notes="",
        )
        selected_items = [
            ProductionProjectItem(
                type_id=603, type_name="Merlin", quantity_requested=3
            ),
            ProductionProjectItem(
                type_id=621, type_name="Caracal", quantity_requested=5
            ),
        ]

        scaled_items = _scale_project_selected_items_for_runs(
            project=project,
            selected_items=selected_items,
            saved_runs=1,
            target_runs=2,
        )

        self.assertEqual([item.quantity_requested for item in scaled_items], [6, 10])
        self.assertEqual([item.quantity_requested for item in selected_items], [3, 5])

    @patch("indy_hub.services.production_projects._get_blueprint_output_quantity")
    def test_scale_project_selected_items_for_runs_normalizes_legacy_single_blueprint_base(
        self, mock_get_blueprint_output_quantity
    ):
        mock_get_blueprint_output_quantity.return_value = 2
        project = ProductionProject(
            user=self.user,
            name="Merlin",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            notes=LEGACY_SINGLE_BLUEPRINT_PROJECT_NOTE,
        )
        selected_items = [
            ProductionProjectItem(
                type_id=603,
                type_name="Merlin",
                quantity_requested=8,
                blueprint_type_id=950,
            )
        ]

        scaled_items = _scale_project_selected_items_for_runs(
            project=project,
            selected_items=selected_items,
            saved_runs=4,
            target_runs=3,
        )

        self.assertEqual(scaled_items[0].quantity_requested, 6)
        self.assertEqual(selected_items[0].quantity_requested, 8)

    @patch("indy_hub.views.api.build_project_workspace_payload")
    def test_production_project_payload_passes_runs_override(
        self, mock_build_project_workspace_payload
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Runs Test",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )
        mock_build_project_workspace_payload.return_value = {"num_runs": 5}

        request = self._prepare_request(
            self.factory.get(
                reverse(
                    "indy_hub:production_project_payload",
                    args=[project.project_ref],
                ),
                data={"runs": 5},
            )
        )
        response = self._production_project_payload_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_build_project_workspace_payload.call_args.kwargs["runs_override"], 5
        )

    @patch("indy_hub.views.industry.build_project_workspace_payload")
    @patch("indy_hub.views.industry.get_cached_project_workspace_payload")
    def test_craft_project_renders_runs_control_and_uses_runs_override(
        self,
        mock_get_cached_project_workspace_payload,
        mock_build_project_workspace_payload,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Runs Workspace",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )
        mock_get_cached_project_workspace_payload.return_value = (
            {"num_runs": 3},
            False,
        )
        mock_build_project_workspace_payload.return_value = {
            "bp_type_id": 950,
            "num_runs": 7,
            "final_product_qty": 14,
            "product_type_id": 603,
            "me": 0,
            "te": 0,
            "materials": [],
            "direct_materials": [],
            "materials_tree": [],
            "craft_cycles_summary": {},
            "blueprint_configs_grouped": [],
            "materials_by_group": {},
            "market_group_map": {},
            "recipe_map": {},
            "debug": {},
            "fuzzwork_price_url": "",
            "main_bp_info": {},
            "copy_request_preview": {},
            "copy_request_pages": [],
            "production_time_map": {},
            "craft_character_advisor": {},
            "structure_planner": {},
            "final_outputs": [{"type_id": 603, "quantity": 14}],
        }

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project", args=[project.project_ref]),
                data={"runs": 7},
            )
        )
        response = self._craft_project_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_build_project_workspace_payload.call_args.kwargs["runs_override"], 7
        )
        self.assertContains(response, 'id="runsInput"', html=False)
        self.assertContains(response, 'value="7"', html=False)
        self.assertContains(response, 'id="recalcNowBtn"', html=False)

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


class ProductionProjectDataMigrationTests(TransactionTestCase):
    migrate_from = (
        "indy_hub",
        "0095_materialexchangeacceptedlocation_and_type_filters",
    )
    migrate_to = ("indy_hub", "0096_add_production_projects")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])

    def tearDown(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        super().tearDown()

    def _project_state_apps(self, target):
        return self.executor.loader.project_state([target]).apps

    def test_0096_migrates_legacy_simulations_and_drops_tables(self):
        old_apps = self._project_state_apps(self.migrate_from)
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="migration-user",
            password="testpass123",
        )

        production_simulation = old_apps.get_model("indy_hub", "ProductionSimulation")
        production_config = old_apps.get_model("indy_hub", "ProductionConfig")
        blueprint_efficiency = old_apps.get_model("indy_hub", "BlueprintEfficiency")
        custom_price = old_apps.get_model("indy_hub", "CustomPrice")

        simulation = production_simulation.objects.create(
            user_id=user.id,
            blueprint_type_id=603,
            blueprint_name="Merlin Blueprint",
            runs=3,
            simulation_name="Legacy Merlin",
            total_items=1,
            total_buy_items=1,
            total_prod_items=0,
            estimated_cost=125.5,
            estimated_revenue=250.0,
            estimated_profit=124.5,
            active_tab="financial",
        )
        production_config.objects.create(
            user_id=user.id,
            simulation_id=simulation.id,
            blueprint_type_id=603,
            item_type_id=34,
            production_mode="buy",
            quantity_needed=42,
            runs=3,
        )
        blueprint_efficiency.objects.create(
            user_id=user.id,
            simulation_id=simulation.id,
            blueprint_type_id=603,
            material_efficiency=8,
            time_efficiency=14,
        )
        custom_price.objects.create(
            user_id=user.id,
            simulation_id=simulation.id,
            item_type_id=34,
            unit_price=6.2,
            is_sale_price=False,
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])

        new_apps = self._project_state_apps(self.migrate_to)
        production_project = new_apps.get_model("indy_hub", "ProductionProject")
        production_project_item = new_apps.get_model(
            "indy_hub", "ProductionProjectItem"
        )

        project = production_project.objects.get(user_id=user.id)
        item = production_project_item.objects.get(project_id=project.id)

        self.assertEqual(project.name, "Legacy Merlin")
        self.assertEqual(project.status, "saved")
        self.assertEqual(project.summary["legacy_simulation_id"], simulation.id)
        self.assertEqual(project.workspace_state["simulation_name"], "Legacy Merlin")
        self.assertEqual(project.workspace_state["items"][0]["mode"], "buy")
        self.assertEqual(
            project.workspace_state["blueprint_efficiencies"][0]["material_efficiency"],
            8,
        )
        self.assertEqual(
            project.workspace_state["custom_prices"][0]["item_type_id"], 34
        )
        self.assertEqual(item.blueprint_type_id, 603)
        self.assertEqual(item.quantity_requested, 3)
        self.assertEqual(item.metadata["legacy_simulation_id"], simulation.id)

    def test_0096_reverse_restores_legacy_simulations_from_projects(self):
        old_apps = self._project_state_apps(self.migrate_from)
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="reverse-migration-user",
            password="testpass123",
        )

        production_simulation = old_apps.get_model("indy_hub", "ProductionSimulation")
        production_config = old_apps.get_model("indy_hub", "ProductionConfig")
        blueprint_efficiency = old_apps.get_model("indy_hub", "BlueprintEfficiency")
        custom_price = old_apps.get_model("indy_hub", "CustomPrice")

        legacy_simulation = production_simulation.objects.create(
            user_id=user.id,
            blueprint_type_id=603,
            blueprint_name="Merlin Blueprint",
            runs=3,
            simulation_name="Legacy Merlin",
            total_items=1,
            total_buy_items=1,
            total_prod_items=0,
            estimated_cost=125.5,
            estimated_revenue=250.0,
            estimated_profit=124.5,
            active_tab="financial",
        )
        production_config.objects.create(
            user_id=user.id,
            simulation_id=legacy_simulation.id,
            blueprint_type_id=603,
            item_type_id=34,
            production_mode="buy",
            quantity_needed=42,
            runs=3,
        )
        blueprint_efficiency.objects.create(
            user_id=user.id,
            simulation_id=legacy_simulation.id,
            blueprint_type_id=603,
            material_efficiency=8,
            time_efficiency=14,
        )
        custom_price.objects.create(
            user_id=user.id,
            simulation_id=legacy_simulation.id,
            item_type_id=34,
            unit_price=6.2,
            is_sale_price=False,
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])

        new_apps = self._project_state_apps(self.migrate_to)
        production_project = new_apps.get_model("indy_hub", "ProductionProject")
        production_project_item = new_apps.get_model(
            "indy_hub", "ProductionProjectItem"
        )

        fallback_project = production_project.objects.create(
            user_id=user.id,
            project_ref="fallback96",
            name="Fallback Project",
            status="draft",
            source_kind="manual",
            source_text="Merlin Blueprint",
            source_name="Merlin Blueprint",
            notes="",
            summary={"selected_items": 1, "selected_quantity": 5},
            workspace_state={},
        )
        production_project_item.objects.create(
            project_id=fallback_project.id,
            type_id=603,
            type_name="Merlin",
            quantity_requested=5,
            category_key="manual",
            category_label="Manual list",
            category_order=90,
            source_line="Merlin",
            is_selected=True,
            is_craftable=True,
            inclusion_mode="produce",
            blueprint_type_id=603,
            metadata={},
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])

        reversed_apps = self._project_state_apps(self.migrate_from)
        reversed_simulation = reversed_apps.get_model(
            "indy_hub", "ProductionSimulation"
        )
        reversed_config = reversed_apps.get_model("indy_hub", "ProductionConfig")
        reversed_efficiency = reversed_apps.get_model("indy_hub", "BlueprintEfficiency")
        reversed_price = reversed_apps.get_model("indy_hub", "CustomPrice")

        legacy_restored = reversed_simulation.objects.get(
            user_id=user.id,
            simulation_name="Legacy Merlin",
        )
        fallback_restored = reversed_simulation.objects.get(
            user_id=user.id,
            simulation_name="Fallback Project",
        )

        self.assertEqual(legacy_restored.id, legacy_simulation.id)
        self.assertEqual(legacy_restored.blueprint_type_id, 603)
        self.assertEqual(legacy_restored.runs, 3)
        self.assertEqual(legacy_restored.total_buy_items, 1)
        self.assertEqual(legacy_restored.active_tab, "financial")
        self.assertEqual(
            reversed_config.objects.get(
                simulation_id=legacy_restored.id,
                item_type_id=34,
            ).quantity_needed,
            42,
        )
        self.assertEqual(
            reversed_config.objects.get(
                simulation_id=legacy_restored.id,
                item_type_id=34,
            ).production_mode,
            "buy",
        )
        self.assertEqual(
            reversed_efficiency.objects.get(
                simulation_id=legacy_restored.id,
                blueprint_type_id=603,
            ).material_efficiency,
            8,
        )
        self.assertEqual(
            reversed_price.objects.get(
                simulation_id=legacy_restored.id,
                item_type_id=34,
            ).unit_price,
            Decimal("6.20"),
        )

        self.assertEqual(fallback_restored.blueprint_type_id, 603)
        self.assertEqual(fallback_restored.runs, 5)
        self.assertEqual(fallback_restored.total_prod_items, 1)
        self.assertEqual(
            reversed_config.objects.get(
                simulation_id=fallback_restored.id,
                item_type_id=603,
            ).production_mode,
            "prod",
        )
        self.assertEqual(
            reversed_config.objects.get(
                simulation_id=fallback_restored.id,
                item_type_id=603,
            ).quantity_needed,
            5,
        )

        self.assertIn(
            "indy_hub_productionsimulation",
            connection.introspection.table_names(),
        )
        self.assertIn(
            "indy_hub_productionconfig",
            connection.introspection.table_names(),
        )
        self.assertIn(
            "indy_hub_blueprintefficiency",
            connection.introspection.table_names(),
        )
        self.assertIn(
            "indy_hub_customprice",
            connection.introspection.table_names(),
        )
        self.assertNotIn(
            "indy_hub_productionproject",
            connection.introspection.table_names(),
        )
        self.assertNotIn(
            "indy_hub_productionprojectitem",
            connection.introspection.table_names(),
        )

    def test_0096_reverse_tolerates_preexisting_legacy_simulation_table(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="preexisting-legacy-table-user",
            password="testpass123",
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])

        new_apps = self._project_state_apps(self.migrate_to)
        production_project = new_apps.get_model("indy_hub", "ProductionProject")
        production_project_item = new_apps.get_model(
            "indy_hub", "ProductionProjectItem"
        )

        project = production_project.objects.create(
            user_id=user.id,
            project_ref="preexist95a",
            name="Preexisting Table Project",
            status="saved",
            source_kind="manual",
            source_text="Merlin Blueprint",
            source_name="Merlin Blueprint",
            notes="",
            summary={"selected_items": 1, "selected_quantity": 2},
            workspace_state={
                "blueprint_type_id": 603,
                "blueprint_name": "Merlin Blueprint",
                "runs": 2,
                "simulation_name": "Preexisting Table Project",
                "active_tab": "materials",
                "items": [
                    {
                        "type_id": 34,
                        "mode": "buy",
                        "quantity": 10,
                    }
                ],
            },
        )
        production_project_item.objects.create(
            project_id=project.id,
            type_id=603,
            type_name="Merlin",
            quantity_requested=2,
            category_key="manual",
            category_label="Manual list",
            category_order=90,
            source_line="Merlin",
            is_selected=True,
            is_craftable=True,
            inclusion_mode="produce",
            blueprint_type_id=603,
            metadata={},
        )

        old_apps = self._project_state_apps(self.migrate_from)
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(
                old_apps.get_model("indy_hub", "ProductionSimulation")
            )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])

        reversed_apps = self._project_state_apps(self.migrate_from)
        reversed_simulation = reversed_apps.get_model(
            "indy_hub", "ProductionSimulation"
        )
        restored = reversed_simulation.objects.get(
            user_id=user.id,
            simulation_name="Preexisting Table Project",
        )

        self.assertEqual(restored.blueprint_type_id, 603)
        self.assertEqual(restored.runs, 2)
