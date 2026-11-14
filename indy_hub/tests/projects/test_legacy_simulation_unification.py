# Standard Library
import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

# Django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth (External Libs)
from eve_sde.models import EveSDE

# AA Example App
from indy_hub.models import (
    CachedCharacterAsset,
    IndustryJob,
    IndustryStructure,
    IndustryStructureRig,
    IndustrySystemCostIndex,
    ProductionProject,
    ProductionProjectItem,
)
from indy_hub.services import industry_rules
from indy_hub.services.industry_rules import INDUSTRY_RULES_VERSION
from indy_hub.services.production_projects import (
    LEGACY_SINGLE_BLUEPRINT_PROJECT_NOTE,
    PROJECT_WORKSPACE_CORP_AUTHORIZATION_FINGERPRINT_KEY,
    PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY,
    PROJECT_WORKSPACE_PAYLOAD_CACHE_VERSION,
    PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_ID_LIMIT,
    PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_KEY,
    PROJECT_WORKSPACE_SDE_SIGNATURE_KEY,
    _scale_project_selected_items_for_runs,
    build_project_workspace_payload,
    create_project_from_entries,
    create_project_from_single_blueprint,
    get_cached_project_workspace_payload,
    get_project_structure_planner_signature,
    get_project_workspace_scoped_sde_signature,
    project_workspace_preserves_recipe,
    strip_project_workspace_cache,
)
from indy_hub.services.project_progress import normalize_project_progress
from indy_hub.tests.fixtures import load_ccp_hull_fixture
from indy_hub.views.api import (
    _apply_workspace_state_to_project,
    create_production_project,
    production_project_payload,
    save_production_project_progress,
    save_production_project_workspace,
    save_temporary_production_project_workspace,
)
from indy_hub.views.industry import (
    _craft_project_stock_refresh_progress_key,
    _ensure_craft_project_stock_refresh_started,
    _get_craft_project_stock_refresh_progress,
    craft_bp,
    craft_project,
    craft_project_stock_refresh_status,
)


def _unwrap_view(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


class _FakeOwnershipCountQuerySet:
    def __init__(self, count: int):
        self._count = int(count)

    def values_list(self, *args, **kwargs):
        return self

    def distinct(self):
        return self

    def count(self):
        return self._count


class _FakeTokenQuerySet:
    def __init__(self, exists: bool):
        self._exists = bool(exists)

    def require_scopes(self, scopes):
        return self

    def require_valid(self):
        return self

    def exists(self):
        return self._exists


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
    def _create_project_view(self):
        return _unwrap_view(create_production_project)

    @property
    def _save_temporary_workspace_view(self):
        return _unwrap_view(save_temporary_production_project_workspace)

    @property
    def _production_project_payload_view(self):
        return _unwrap_view(production_project_payload)

    @property
    def _craft_project_view(self):
        return _unwrap_view(craft_project)

    @property
    def _craft_project_stock_refresh_status_view(self):
        return _unwrap_view(craft_project_stock_refresh_status)

    def _prepare_request(self, request: HttpRequest, *, user=None) -> HttpRequest:
        request.user = user or self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _ensure_project_sde_rows(self, *, material_quantity: int = 10) -> None:
        item_type_model = apps.get_model("eve_sde", "ItemType")
        for type_id, type_name in (
            (34, "Tritanium"),
            (603, "Merlin"),
            (950, "Merlin Blueprint"),
        ):
            item_type_model.objects.update_or_create(
                id=type_id,
                defaults={"name": type_name, "published": True},
            )
        blueprint_activity_id = "950:manufacturing"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM eve_sde_blueprintactivity
                WHERE blueprint_item_type_id = %s
                AND activity = %s
                LIMIT 1
                """,
                [950, "manufacturing"],
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    """
                    UPDATE eve_sde_blueprintactivity
                    SET time = %s
                    WHERE id = %s
                    """,
                    [120, blueprint_activity_id],
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO eve_sde_blueprintactivity
                    (id, blueprint_item_type_id, activity, time)
                    VALUES (%s, %s, %s, %s)
                    """,
                    [blueprint_activity_id, 950, "manufacturing", 120],
                )
                cursor.execute(
                    """
                    SELECT id
                    FROM eve_sde_blueprintactivity
                    WHERE blueprint_item_type_id = %s
                    AND activity = %s
                    LIMIT 1
                    """,
                    [950, "manufacturing"],
                )
                created_row = cursor.fetchone()
                blueprint_activity_id = (
                    str(created_row[0]) if created_row else blueprint_activity_id
                )

            cursor.execute(
                """
                DELETE FROM eve_sde_blueprintactivityproduct
                WHERE blueprint_activity_id = %s
                AND item_type_id = %s
                """,
                [blueprint_activity_id, 603],
            )
            cursor.execute(
                """
                INSERT INTO eve_sde_blueprintactivityproduct
                (blueprint_activity_id, item_type_id, quantity)
                VALUES (%s, %s, %s)
                """,
                [blueprint_activity_id, 603, 1],
            )
            cursor.execute(
                """
                DELETE FROM eve_sde_blueprintactivitymaterial
                WHERE blueprint_activity_id = %s
                AND item_type_id = %s
                """,
                [blueprint_activity_id, 34],
            )
            cursor.execute(
                """
                INSERT INTO eve_sde_blueprintactivitymaterial
                (blueprint_activity_id, item_type_id, quantity)
                VALUES (%s, %s, %s)
                """,
                [blueprint_activity_id, 34, material_quantity],
            )

        sde_state = EveSDE.get_solo()
        sde_state.build_number = int(material_quantity)
        sde_state.release_date = timezone.now()
        sde_state.save(
            update_fields=["build_number", "release_date", "last_check_date"]
        )

    def _create_cached_merlin_project(self) -> ProductionProject:
        self._ensure_project_sde_rows()
        project = ProductionProject.objects.create(
            user=self.user,
            name="Merlin Project",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            workspace_state={"runs": 1},
        )
        ProductionProjectItem.objects.create(
            project=project,
            type_id=603,
            type_name="Merlin",
            quantity_requested=1,
            category_key="manual",
            category_label="Manual list",
            category_order=90,
            is_selected=True,
            is_craftable=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.PRODUCE,
            blueprint_type_id=950,
        )
        cached_payload = build_project_workspace_payload(
            project,
            include_full_structure_options=False,
        )
        project.workspace_state = {
            "runs": 1,
            PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY: cached_payload,
            PROJECT_WORKSPACE_SDE_SIGNATURE_KEY: {"global": "older"},
            PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_KEY: (
                get_project_workspace_scoped_sde_signature(cached_payload)
            ),
        }
        project.save(update_fields=["workspace_state", "updated_at"])
        return project

    def _create_cached_two_fit_project(
        self, *, cache_version: int
    ) -> ProductionProject:
        self._ensure_project_sde_rows()
        item_type_model = apps.get_model("eve_sde", "ItemType")
        for type_id, type_name in ((2048, "Damage Control II"), (37604, "Guardian")):
            item_type_model.objects.update_or_create(
                id=type_id,
                defaults={"name": type_name, "published": True},
            )
        project = ProductionProject.objects.create(
            user=self.user,
            name="Two Fits",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            workspace_state={"runs": 1},
        )
        for order, (
            type_id,
            type_name,
            quantity,
            fit_index,
            is_hull,
            blueprint_id,
        ) in enumerate(
            (
                (603, "Merlin", 1, 0, True, 950),
                (2048, "Damage Control II", 2, 0, False, None),
                (37604, "Guardian", 1, 1, True, None),
            )
        ):
            ProductionProjectItem.objects.create(
                project=project,
                type_id=type_id,
                type_name=type_name,
                quantity_requested=quantity,
                category_order=order,
                is_selected=True,
                is_craftable=bool(blueprint_id),
                blueprint_type_id=blueprint_id,
                inclusion_mode=(
                    ProductionProjectItem.InclusionMode.PRODUCE
                    if blueprint_id
                    else ProductionProjectItem.InclusionMode.BUY
                ),
                metadata={
                    "fit_group": f"eft_fit_{fit_index}",
                    "fit_index": fit_index,
                    "fit_hull_name": "Merlin" if fit_index == 0 else "Guardian",
                    "fit_display_name": "Fit A" if fit_index == 0 else "Fit B",
                    "is_fit_hull": is_hull,
                },
            )
        cached_payload = build_project_workspace_payload(
            project, include_full_structure_options=False
        )
        cached_payload["cache_version"] = cache_version
        if cache_version == 6:
            cached_payload.pop("industry_rules_version", None)
        project.workspace_state = {
            "runs": 1,
            PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY: cached_payload,
            PROJECT_WORKSPACE_SDE_SIGNATURE_KEY: {"global": "older"},
            PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_KEY: (
                get_project_workspace_scoped_sde_signature(cached_payload)
            ),
        }
        project.save(update_fields=["workspace_state", "updated_at"])
        project.refresh_from_db()
        return project

    @patch("indy_hub.views.industry.create_temporary_project_workspace")
    def test_craft_bp_redirects_to_temp_workspace(self, mock_create_workspace):
        mock_create_workspace.return_value = "temp123"

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
            reverse("indy_hub:craft_temp_project", args=["temp123"]),
        )
        mock_create_workspace.assert_called_once()

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
                        "revenueMode": "total",
                        "revenueTotalOverride": 12345.5,
                    }
                ),
                content_type="application/json",
            )
        )
        response = self._save_workspace_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.name, "Saved Vedmak Table")
        self.assertEqual(project.workspace_state["revenueMode"], "total")
        self.assertEqual(project.workspace_state["revenueTotalOverride"], 12345.5)
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
        self.assertIn("cachedProjectScopedSdeSignature", project.workspace_state)
        self.assertIsInstance(project.workspace_state["cachedProjectPayload"], dict)

    def test_create_production_project_creates_temporary_workspace_only(self):
        request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:create_production_project"),
                data=json.dumps(
                    {
                        "name": "Temp Guardian Fit",
                        "source_text": "[Guardian, Fit]\n\nDamage Control II",
                        "source_kind": "eft",
                        "items": [
                            {
                                "type_id": 37604,
                                "type_name": "Guardian",
                                "quantity": 1,
                                "resolved": True,
                                "is_craftable": True,
                                "blueprint_type_id": 5001,
                                "inclusion_mode": "produce",
                            }
                        ],
                        "fit_quantities": [
                            {
                                "fitGroup": "eft_fit_0",
                                "label": "Guardian",
                                "quantity": 2,
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
        )

        before_count = ProductionProject.objects.count()
        response = self._create_project_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])
        self.assertIn("/craft/project/temp/", payload["redirect_url"])
        self.assertEqual(ProductionProject.objects.count(), before_count)

    def test_create_project_from_entries_preserves_duplicate_items_in_same_category(
        self,
    ):
        selected_entries = [
            {
                "type_id": 12221,
                "type_name": "Medium Remote Armor Repairer II",
                "quantity": 1,
                "category_key": "high_slots",
                "category_label": "High slots",
                "category_order": 30,
                "source_line": "Medium Remote Armor Repairer II",
                "inclusion_mode": "produce",
                "is_craftable": True,
            },
            {
                "type_id": 12221,
                "type_name": "Medium Remote Armor Repairer II",
                "quantity": 1,
                "category_key": "high_slots",
                "category_label": "High slots",
                "category_order": 30,
                "source_line": "Medium Remote Armor Repairer II",
                "inclusion_mode": "produce",
                "is_craftable": True,
            },
        ]

        project = create_project_from_entries(
            user=self.user,
            name="Duplicate slot regression",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            source_text="[Guardian, Fit]",
            source_name="Guardian Fit",
            selected_entries=selected_entries,
        )

        duplicates = list(
            project.items.filter(type_id=12221, category_key="high_slots").order_by(
                "id"
            )
        )
        self.assertEqual(len(duplicates), 2)
        self.assertEqual([int(item.quantity_requested) for item in duplicates], [1, 1])

    def test_create_project_from_entries_defaults_non_craftable_to_buy(self):
        project = create_project_from_entries(
            user=self.user,
            name="Non craftable defaults",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            source_text="[Guardian, Fit]",
            source_name="Guardian Fit",
            selected_entries=[
                {
                    "type_id": 13978,
                    "type_name": "Shadow Serpentis Kinetic Armor Hardener",
                    "quantity": 1,
                    "is_craftable": False,
                }
            ],
        )

        item = project.items.get(type_id=13978)
        self.assertFalse(item.is_craftable)
        self.assertEqual(item.inclusion_mode, ProductionProjectItem.InclusionMode.BUY)

    @patch("indy_hub.views.api.build_project_workspace_payload")
    def test_save_temporary_workspace_creates_real_project(
        self,
        mock_build_project_workspace_payload,
    ):
        mock_build_project_workspace_payload.return_value = {
            "workspace_state": {},
            "final_outputs": [{"type_id": 37604, "quantity": 2}],
        }
        create_request = self._prepare_request(
            self.factory.post(
                reverse("indy_hub:create_production_project"),
                data=json.dumps(
                    {
                        "name": "Temp Guardian Fit",
                        "source_text": "[Guardian, Fit]\n\nDamage Control II",
                        "source_kind": "eft",
                        "items": [
                            {
                                "type_id": 37604,
                                "type_name": "Guardian",
                                "quantity": 1,
                                "resolved": True,
                                "is_craftable": True,
                                "blueprint_type_id": 5001,
                                "inclusion_mode": "produce",
                                "metadata": {
                                    "fit_group": "eft_fit_0",
                                    "fit_index": 0,
                                    "fit_hull_name": "Guardian",
                                    "is_fit_hull": True,
                                },
                            }
                        ],
                        "fit_quantities": [
                            {
                                "fitGroup": "eft_fit_0",
                                "label": "Guardian",
                                "quantity": 2,
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
        )
        create_response = self._create_project_view(create_request)
        temp_payload = json.loads(create_response.content)
        temp_ref = temp_payload["temp_project_ref"]

        save_request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:save_temporary_production_project_workspace",
                    args=[temp_ref],
                ),
                data=json.dumps(
                    {
                        "simulationName": "Saved Guardian Fit",
                        "active_tab": "materials",
                        "items": [],
                        "buyTypeIds": [],
                        "manualPrices": [],
                        "stockAllocations": {},
                        "copyRequests": [],
                        "structure": {},
                        "meTeConfig": {},
                        "finalOutputQuantities": [
                            {"index": 0, "typeId": 37604, "quantity": 2}
                        ],
                    }
                ),
                content_type="application/json",
            )
        )

        before_count = ProductionProject.objects.count()
        response = self._save_temporary_workspace_view(save_request, temp_ref)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])
        self.assertIn("/craft/project/", payload["redirect_url"])
        self.assertEqual(ProductionProject.objects.count(), before_count + 1)

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
    def test_save_production_project_workspace_updates_final_output_quantities(
        self,
        mock_build_project_workspace_payload,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Fleet Targets",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            workspace_state={"runs": 4},
        )
        first_item = ProductionProjectItem.objects.create(
            project=project,
            type_id=37604,
            type_name="Guardian",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.PRODUCE,
            category_order=1,
        )
        second_item = ProductionProjectItem.objects.create(
            project=project,
            type_id=35834,
            type_name="Azariel",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.PRODUCE,
            category_order=2,
        )
        mock_build_project_workspace_payload.return_value = {
            "workspace_state": {},
            "final_outputs": [
                {"type_id": first_item.type_id, "quantity": 2},
                {"type_id": second_item.type_id, "quantity": 3},
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
                        "simulation_name": "Fleet Targets",
                        "active_tab": "materials",
                        "finalOutputQuantities": [
                            {"index": 0, "typeId": first_item.type_id, "quantity": 2},
                            {"index": 1, "typeId": second_item.type_id, "quantity": 3},
                        ],
                    }
                ),
                content_type="application/json",
            )
        )
        response = self._save_workspace_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        first_item.refresh_from_db()
        second_item.refresh_from_db()
        self.assertEqual(first_item.quantity_requested, 2)
        self.assertEqual(second_item.quantity_requested, 3)
        self.assertEqual(project.workspace_state["runs"], 1)
        self.assertEqual(
            project.workspace_state["finalOutputQuantities"][0]["quantity"], 2
        )
        self.assertEqual(
            project.workspace_state["finalOutputQuantities"][1]["quantity"], 3
        )

    @patch("indy_hub.views.api.build_project_workspace_payload")
    def test_save_production_project_workspace_keeps_eft_item_base_quantities(
        self,
        mock_build_project_workspace_payload,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Guardian Fit",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            workspace_state={"runs": 1},
        )
        hull_item = ProductionProjectItem.objects.create(
            project=project,
            type_id=37604,
            type_name="Guardian",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.PRODUCE,
            category_order=1,
        )
        module_item = ProductionProjectItem.objects.create(
            project=project,
            type_id=2048,
            type_name="Damage Control II",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.BUY,
            category_order=2,
        )
        mock_build_project_workspace_payload.return_value = {
            "workspace_state": {},
            "final_outputs": [
                {"type_id": hull_item.type_id, "quantity": 2},
                {"type_id": module_item.type_id, "quantity": 2},
            ],
            "editable_final_outputs": [
                {"type_id": None, "type_name": "Project quantity", "quantity": 2}
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
                        "simulation_name": "Guardian Fit",
                        "active_tab": "materials",
                        "finalOutputQuantities": [
                            {"index": 0, "typeId": 0, "quantity": 2},
                        ],
                    }
                ),
                content_type="application/json",
            )
        )
        response = self._save_workspace_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        hull_item.refresh_from_db()
        module_item.refresh_from_db()
        self.assertEqual(hull_item.quantity_requested, 1)
        self.assertEqual(module_item.quantity_requested, 1)
        self.assertEqual(project.workspace_state["runs"], 1)
        self.assertEqual(
            project.workspace_state["finalOutputQuantities"][0]["quantity"], 2
        )

    def test_build_project_workspace_payload_uses_selected_hull_as_eft_final_output(
        self,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Guardian Fit",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            workspace_state={
                "finalOutputQuantities": [{"index": 0, "quantity": 2}],
            },
        )
        ProductionProjectItem.objects.create(
            project=project,
            type_id=37604,
            type_name="Guardian",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.PRODUCE,
            category_order=1,
            metadata={
                "fit_group": "eft_fit_0",
                "fit_index": 0,
                "fit_hull_name": "Guardian",
                "is_fit_hull": True,
            },
        )
        ProductionProjectItem.objects.create(
            project=project,
            type_id=2048,
            type_name="Damage Control II",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.BUY,
            category_order=2,
            metadata={
                "fit_group": "eft_fit_0",
                "fit_index": 0,
                "fit_hull_name": "Guardian",
            },
        )

        with (
            patch(
                "indy_hub.services.production_projects._resolve_type_names",
                return_value={37604: "Guardian", 2048: "Damage Control II"},
            ),
            patch(
                "indy_hub.services.production_projects._resolve_market_groups",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_time_map",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_character_advisor",
                return_value={"characters": [], "items": {}, "summary": {}},
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_structure_planner",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects._resolve_user_blueprint_inventory",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects._build_project_blueprint_configs_grouped",
                return_value=([], []),
            ),
        ):
            payload = build_project_workspace_payload(project)

        self.assertEqual(
            payload["editable_final_outputs"][0]["type_name"],
            "Guardian",
        )
        self.assertEqual(payload["editable_final_outputs"][0]["quantity"], 2)
        self.assertEqual(
            payload["materials_tree"][0]["type_name"],
            "Guardian",
        )
        self.assertEqual(payload["materials_tree"][0]["quantity"], 2)
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][0]["type_name"],
            "Guardian",
        )
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][0]["quantity"],
            2,
        )
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][-1]["type_name"],
            "Damage Control II",
        )
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][-1]["quantity"],
            2,
        )

    def test_build_project_workspace_payload_uses_project_quantity_when_eft_hull_is_deselected(
        self,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Guardian Fit",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            workspace_state={
                "finalOutputQuantities": [{"index": 0, "quantity": 3}],
            },
        )
        ProductionProjectItem.objects.create(
            project=project,
            type_id=2048,
            type_name="Damage Control II",
            quantity_requested=1,
            is_selected=True,
            inclusion_mode=ProductionProjectItem.InclusionMode.BUY,
            category_order=2,
            metadata={
                "fit_group": "eft_fit_0",
                "fit_index": 0,
                "fit_hull_name": "Guardian",
            },
        )

        with (
            patch(
                "indy_hub.services.production_projects._resolve_type_names",
                return_value={2048: "Damage Control II"},
            ),
            patch(
                "indy_hub.services.production_projects._resolve_market_groups",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_time_map",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_character_advisor",
                return_value={"characters": [], "items": {}, "summary": {}},
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_structure_planner",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects._resolve_user_blueprint_inventory",
                return_value={},
            ),
            patch(
                "indy_hub.services.production_projects._build_project_blueprint_configs_grouped",
                return_value=([], []),
            ),
        ):
            payload = build_project_workspace_payload(project)

        self.assertEqual(payload["editable_final_outputs"][0]["type_name"], "Guardian")
        self.assertEqual(payload["editable_final_outputs"][0]["quantity"], 3)
        self.assertEqual(payload["materials_tree"][0]["type_name"], "Guardian")
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][0]["type_name"],
            "Damage Control II",
        )
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][0]["quantity"], 3
        )

    @patch("indy_hub.views.api.build_project_workspace_payload")
    def test_save_workspace_scoped_signature_ignores_client_cached_payload_ids(
        self,
        mock_build_project_workspace_payload,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Trusted Signature",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )
        mock_build_project_workspace_payload.return_value = {
            "materials_tree": [
                {
                    "type_id": 34,
                    "type_name": "Tritanium",
                    "quantity": 1,
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
                        "runs": 1,
                        "cachedPayload": {
                            "materials_tree": [
                                {
                                    "type_id": 999999999,
                                    "type_name": "Client supplied row",
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
        scoped_signature = project.workspace_state[
            PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_KEY
        ]
        cached_payload = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        self.assertEqual(cached_payload["materials_tree"][0]["type_id"], 34)
        self.assertNotEqual(cached_payload["materials_tree"][0]["type_id"], 999999999)
        self.assertIn(34, scoped_signature["type_ids"])
        self.assertNotIn(999999999, scoped_signature["type_ids"])

    def test_scoped_sde_signature_uses_cache_for_repeated_payload(self):
        self._ensure_project_sde_rows()
        cache.clear()
        payload = {
            "materials_tree": [
                {
                    "type_id": 603,
                    "blueprint_type_id": 950,
                    "sub_materials": [
                        {"type_id": 34, "sub_materials": []},
                    ],
                }
            ],
        }

        first_signature = get_project_workspace_scoped_sde_signature(payload)
        with patch("indy_hub.services.production_projects._chunked_ids") as chunked_ids:
            second_signature = get_project_workspace_scoped_sde_signature(payload)

        chunked_ids.assert_not_called()
        self.assertEqual(second_signature, first_signature)

    def test_scoped_sde_signature_falls_back_for_oversized_payload_scope(self):
        oversized_payload = {
            "materials_tree": [
                {"type_id": type_id, "sub_materials": []}
                for type_id in range(
                    1,
                    PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_ID_LIMIT + 2,
                )
            ]
        }

        scoped_signature = get_project_workspace_scoped_sde_signature(oversized_payload)

        self.assertEqual(scoped_signature["scope"], "global-fallback")
        self.assertEqual(
            scoped_signature["type_id_count"],
            PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_ID_LIMIT + 1,
        )
        self.assertIn("global_signature", scoped_signature)

    def test_save_production_project_workspace_normalizes_revenue_mode(self):
        """`revenueMode` defaults to per_unit; 'total' is accepted; bad
        `revenueTotalOverride` (negative, garbage, NaN/inf) clamps to 0.0."""

        cases = [
            # (payload_extras, expected_mode, expected_override)
            ({}, "per_unit", 0.0),
            ({"revenueMode": "TOTAL"}, "total", 0.0),
            ({"revenueMode": "bogus"}, "per_unit", 0.0),
            (
                {"revenueMode": "total", "revenueTotalOverride": 250000.5},
                "total",
                250000.5,
            ),
            (
                {"revenueMode": "per_unit", "revenueTotalOverride": -42},
                "per_unit",
                0.0,
            ),
            (
                {"revenueMode": "total", "revenueTotalOverride": "not-a-number"},
                "total",
                0.0,
            ),
            (
                {"revenueMode": "total", "revenueTotalOverride": "1e309"},
                "total",
                0.0,
            ),
            (
                {"revenueMode": "total", "revenueTotalOverride": float("nan")},
                "total",
                0.0,
            ),
        ]

        for idx, (extras, expected_mode, expected_override) in enumerate(cases):
            with self.subTest(case=idx, extras=extras):
                project = ProductionProject.objects.create(
                    user=self.user,
                    name=f"RevMode {idx}",
                    status=ProductionProject.Status.DRAFT,
                    source_kind=ProductionProject.SourceKind.MANUAL,
                )
                payload = {
                    "blueprint_type_id": 603,
                    "runs": 1,
                    "active_tab": "financial",
                    **extras,
                }
                request = self._prepare_request(
                    self.factory.post(
                        reverse(
                            "indy_hub:save_production_project_workspace",
                            args=[project.project_ref],
                        ),
                        data=json.dumps(payload),
                        content_type="application/json",
                    )
                )
                response = self._save_workspace_view(request, project.project_ref)
                self.assertEqual(response.status_code, 200)
                project.refresh_from_db()
                self.assertEqual(project.workspace_state["revenueMode"], expected_mode)
                self.assertEqual(
                    project.workspace_state["revenueTotalOverride"],
                    expected_override,
                )

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

    def test_get_cached_project_workspace_payload_rejects_revoked_corp_access(self):
        corporation_id = 2_000_001
        cached_payload = {
            "cache_version": 6,
            "num_runs": 1,
            "workspace_state": {"use_corp_blueprints": True},
            PROJECT_WORKSPACE_CORP_AUTHORIZATION_FINGERPRINT_KEY: [corporation_id],
        }
        project = ProductionProject.objects.create(
            user=self.user,
            name="Revoked Corp Cache",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            workspace_state={
                "runs": 1,
                "use_corp_blueprints": True,
                PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY: cached_payload,
            },
        )

        with (
            patch(
                "indy_hub.services.production_projects.app_settings.PERSONAL_PROJECTS_ALLOW_CORP_BP",
                True,
            ),
            patch(
                "indy_hub.services.production_projects.get_viewable_corporation_ids",
                return_value=set(),
            ),
        ):
            payload, sde_has_changed = get_cached_project_workspace_payload(project)

        self.assertIsNone(payload)
        self.assertFalse(sde_has_changed)

    def test_get_cached_project_workspace_payload_rejects_legacy_corp_cache(self):
        cached_payload = {
            "cache_version": 6,
            "num_runs": 1,
            "workspace_state": {"use_corp_blueprints": True},
        }
        project = ProductionProject.objects.create(
            user=self.user,
            name="Legacy Corp Cache",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
            workspace_state={
                "runs": 1,
                "use_corp_blueprints": True,
                PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY: cached_payload,
            },
        )

        with patch(
            "indy_hub.services.production_projects.app_settings.PERSONAL_PROJECTS_ALLOW_CORP_BP",
            True,
        ):
            payload, sde_has_changed = get_cached_project_workspace_payload(project)

        self.assertIsNone(payload)
        self.assertFalse(sde_has_changed)

    def test_cached_project_ignores_unrelated_global_sde_signature_change(self):
        project = self._create_cached_merlin_project()

        payload, sde_has_changed = get_cached_project_workspace_payload(project)

        self.assertIsNotNone(payload)
        self.assertFalse(sde_has_changed)

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project", args=[project.project_ref])
            )
        )
        response = self._craft_project_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Saved snapshot uses older SDE data")
        self.assertNotContains(response, "Refresh from current SDE")

    def test_cached_project_warns_for_relevant_material_sde_change(self):
        project = self._create_cached_merlin_project()
        self._ensure_project_sde_rows(material_quantity=12)

        payload, sde_has_changed = get_cached_project_workspace_payload(project)

        self.assertIsNotNone(payload)
        self.assertTrue(sde_has_changed)

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project", args=[project.project_ref])
            )
        )
        response = self._craft_project_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved snapshot uses older SDE data")
        self.assertContains(response, "Refresh from current SDE")
        self.assertContains(response, "refresh_sde=1")

    def test_old_industry_cache_rebases_bonuses_and_preserves_frozen_recipe(self):
        project = self._create_cached_merlin_project()
        cached = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        cached["cache_version"] = 6
        cached.pop("industry_rules_version", None)
        cached["structure_planner"] = {"items": [{"obsolete_me": 99}]}
        project.workspace_state.update(
            {
                "buyTypeIds": [34],
                "stockAllocations": {"603": 1},
                "structure": {"assignments": [{"typeId": 603, "structureId": 123}]},
            }
        )
        project.save(update_fields=["workspace_state"])
        original_state = json.loads(json.dumps(project.workspace_state))
        self._ensure_project_sde_rows(material_quantity=12)

        with patch(
            "indy_hub.services.production_projects.build_craft_structure_planner",
            return_value={"items": [{"current_me": 5.9896}]},
        ) as planner:
            payload, changed = get_cached_project_workspace_payload(project)

        planner.assert_called_once()
        self.assertEqual(
            planner.call_args.kwargs["selected_structure_assignments"], {603: 123}
        )
        self.assertTrue(changed)
        self.assertEqual(
            payload["cache_version"], PROJECT_WORKSPACE_PAYLOAD_CACHE_VERSION
        )
        self.assertEqual(payload["industry_rules_version"], INDUSTRY_RULES_VERSION)
        for key in ("materials_tree", "recipe_map", "production_time_map"):
            self.assertEqual(payload[key], cached[key])
        for key in ("buyTypeIds", "stockAllocations", "structure"):
            self.assertEqual(payload["workspace_state"][key], original_state[key])
        self.assertEqual(
            payload["structure_planner"]["items"], [{"current_me": 5.9896}]
        )
        project.refresh_from_db()
        self.assertEqual(project.workspace_state, original_state)

    def test_current_industry_cache_reuses_even_empty_derived_result(self):
        project = self._create_cached_merlin_project()
        cached = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        cached["structure_planner"] = {"items": [], "structures": []}
        with patch(
            "indy_hub.services.production_projects.build_craft_structure_planner"
        ) as planner:
            payload, changed = get_cached_project_workspace_payload(project)
        planner.assert_not_called()
        self.assertFalse(changed)
        self.assertEqual(payload["structure_planner"], cached["structure_planner"])

    def test_current_parameters_refresh_without_replacing_saved_recipe_or_state(self):
        project = self._create_cached_merlin_project()
        cached = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        cached["calculation_parameters"] = {
            "job_launch_window_seconds": 1,
            "scc_surcharge_percent": 99,
        }
        before = deepcopy(project.workspace_state)
        with patch(
            "indy_hub.services.production_projects.build_craft_structure_planner"
        ) as planner:
            payload, _changed = get_cached_project_workspace_payload(project)
        planner.assert_not_called()
        self.assertEqual(
            payload["calculation_parameters"],
            {"job_launch_window_seconds": 2592000, "scc_surcharge_percent": 4.0},
        )
        self.assertEqual(payload["materials_tree"], cached["materials_tree"])
        self.assertEqual(project.workspace_state, before)

    def test_saved_planner_refreshes_after_registry_profile_changes(self):
        structure = IndustryStructure.objects.create(
            name="Before registry edit",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            system_security_band="nullsec",
        )
        project = self._create_cached_merlin_project()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY][
                "materials_tree"
            ]
        )
        structure.name = "After registry edit"
        structure.manufacturing_tax_percent = Decimal("7")
        structure.save()
        payload, _changed = get_cached_project_workspace_payload(project)
        row = next(
            row
            for row in payload["structure_planner"]["structures"]
            if row["structure_id"] == structure.id
        )
        self.assertEqual(row["name"], "After registry edit")
        self.assertEqual(payload["materials_tree"], original)

    def test_registry_fingerprint_covers_rig_and_index_changes_without_signals(self):
        structure = IndustryStructure.objects.create(
            name="Registry inputs",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            solar_system_id=30000142,
        )
        previous = get_project_structure_planner_signature()
        IndustryStructure.objects.filter(pk=structure.pk).update(
            manufacturing_tax_percent=7
        )
        current = get_project_structure_planner_signature()
        self.assertNotEqual(current, previous)
        previous = current
        IndustryStructureRig.objects.bulk_create(
            [
                IndustryStructureRig(
                    structure=structure,
                    slot_index=1,
                    rig_type_id=37181,
                )
            ]
        )
        current = get_project_structure_planner_signature()
        self.assertNotEqual(current, previous)
        previous = current
        IndustryStructureRig.objects.filter(structure=structure).delete()
        current = get_project_structure_planner_signature()
        self.assertNotEqual(current, previous)
        previous = current
        IndustrySystemCostIndex.objects.create(
            solar_system_id=30000142,
            solar_system_name="Fixture",
            activity_id=1,
            cost_index_percent=Decimal("4.2"),
        )
        current = get_project_structure_planner_signature()
        self.assertNotEqual(current, previous)
        previous = current
        IndustrySystemCostIndex.objects.filter(solar_system_id=30000142).update(
            cost_index_percent=5
        )
        self.assertNotEqual(get_project_structure_planner_signature(), previous)

    def test_cache_records_registry_generation_before_planner_reads(self):
        project = self._create_cached_merlin_project()
        with patch(
            "indy_hub.services.production_projects.get_project_structure_planner_signature",
            side_effect=["before-registry-read", "after-concurrent-registry-edit"],
        ) as signature:
            payload = build_project_workspace_payload(
                project, include_full_structure_options=False
            )
        signature.assert_called_once()
        self.assertEqual(payload["structure_planner_signature"], "before-registry-read")

    def test_pending_recipe_refresh_rejects_saved_calculation_cache(self):
        project = self._create_cached_merlin_project()
        project.workspace_state["pendingWorkspaceRefresh"] = True
        payload, _changed = get_cached_project_workspace_payload(project)
        self.assertIsNone(payload)

    def test_explicit_blueprint_override_page_bypasses_saved_cache(self):
        project = self._create_cached_merlin_project()
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project", args=[project.project_ref]),
                data={"me_950": "10"},
            )
        )
        with patch(
            "indy_hub.views.industry.build_project_workspace_payload",
            wraps=build_project_workspace_payload,
        ) as builder:
            response = self._craft_project_view(request, project.project_ref)
        self.assertEqual(response.status_code, 200)
        builder.assert_called_once()
        self.assertEqual(builder.call_args.kwargs["me_te_overrides"], {950: {"me": 10}})

    def test_failed_recipe_rebuild_keeps_workspace_and_items_atomic(self):
        project = self._create_cached_merlin_project()
        project.refresh_from_db()
        before = deepcopy(project.workspace_state)
        item_quantity = project.items.get().quantity_requested
        state = strip_project_workspace_cache(before)
        state.update(
            {
                "simulation_name": "Must not persist",
                "finalOutputQuantities": [{"index": 0, "quantity": 2}],
            }
        )
        with patch(
            "indy_hub.views.api.build_project_workspace_payload",
            side_effect=RuntimeError("synthetic calculation failure"),
        ):
            with self.assertRaises(RuntimeError):
                _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        self.assertEqual(project.name, "Merlin Project")
        self.assertEqual(project.workspace_state, before)
        self.assertEqual(project.items.get().quantity_requested, item_quantity)

    def test_changed_rules_refresh_warm_empty_bonus_result(self):
        project = self._create_cached_merlin_project()
        cached = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        cached["industry_rules_version"] = "obsolete-rules"
        cached["structure_planner"] = {"items": [], "structures": []}
        with patch(
            "indy_hub.services.production_projects.build_craft_structure_planner",
            return_value={"items": [{"current_me": 1}]},
        ) as planner:
            payload, _changed = get_cached_project_workspace_payload(project)
        planner.assert_called_once()
        self.assertEqual(payload["structure_planner"]["items"], [{"current_me": 1}])
        self.assertEqual(payload["industry_rules_version"], INDUSTRY_RULES_VERSION)

    def test_invalid_rules_preserve_project_and_allow_independent_stock_decisions(self):
        load_ccp_hull_fixture()
        structure = IndustryStructure.objects.create(
            name="Qualified Raitaru",
            structure_type_id=35825,
            structure_type_name="Raitaru",
            enable_manufacturing=True,
        )
        project = self._create_cached_merlin_project()
        project.workspace_state["structure"] = {
            "assignments": [{"typeId": 603, "structureId": structure.pk}]
        }
        project.save(update_fields=["workspace_state"])
        before = deepcopy(project.workspace_state)
        with (
            patch.object(industry_rules, "_DATA", {"schema_version": 999}),
            patch.object(industry_rules, "_VALIDATED", False),
        ):
            payload, _changed = get_cached_project_workspace_payload(project)
            self.assertEqual(
                payload["materials_tree"],
                before[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]["materials_tree"],
            )
            self.assertTrue(payload["structure_planner"]["invalid_structures"])
            self.assertEqual(project.workspace_state, before)
            state = strip_project_workspace_cache(before)
            state.update(
                {
                    "simulation_name": project.name,
                    "buyTypeIds": [603],
                    "stockAllocations": {"603": 1},
                }
            )
            _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        self.assertEqual(project.workspace_state["buyTypeIds"], [603])
        self.assertEqual(project.workspace_state["stockAllocations"], {"603": 1})
        self.assertEqual(project.workspace_state["structure"], before["structure"])
        self.assertEqual(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY][
                "materials_tree"
            ],
            before[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]["materials_tree"],
        )
        saved_state = deepcopy(project.workspace_state)
        repaired, _changed = get_cached_project_workspace_payload(project)
        self.assertEqual(repaired["structure_planner"]["invalid_structures"], [])
        self.assertEqual(project.workspace_state, saved_state)

    def test_stock_and_decision_save_preserves_snapshot_after_sde_change(self):
        project = self._create_cached_merlin_project()
        project.refresh_from_db()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        )
        self._ensure_project_sde_rows(material_quantity=12)
        state = strip_project_workspace_cache(project.workspace_state)
        state.update(
            {
                "simulation_name": project.name,
                "buyTypeIds": [34],
                "stockAllocations": {"603": 1},
            }
        )
        _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        saved = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        self.assertEqual(saved["materials_tree"], original["materials_tree"])
        self.assertEqual(saved["recipe_map"], original["recipe_map"])
        self.assertEqual(project.workspace_state["stockAllocations"], {"603": 1})
        self.assertEqual(project.workspace_state["buyTypeIds"], [34])
        _payload, changed = get_cached_project_workspace_payload(project)
        self.assertTrue(changed)

    def test_two_fit_stock_save_preserves_snapshot_after_sde_change(self):
        for cache_version in (6, PROJECT_WORKSPACE_PAYLOAD_CACHE_VERSION):
            with self.subTest(cache_version=cache_version):
                project = self._create_cached_two_fit_project(
                    cache_version=cache_version
                )
                before = deepcopy(project.workspace_state)
                original = before[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
                self.assertEqual(
                    [
                        (row["type_id"], row["quantity"])
                        for row in original["final_outputs"]
                    ],
                    [(603, 1), (2048, 2), (37604, 1)],
                )
                self.assertEqual(
                    [
                        (row["type_id"], row["type_name"], row["quantity"])
                        for row in original["editable_final_outputs"]
                    ],
                    [(603, "Fit A", 1), (37604, "Fit B", 1)],
                )
                self._ensure_project_sde_rows(material_quantity=12)
                state = strip_project_workspace_cache(before)
                state.update(
                    {
                        "simulation_name": project.name,
                        "finalOutputQuantities": [
                            {"index": 0, "typeId": 603, "quantity": 1},
                            {"index": 1, "typeId": 37604, "quantity": 1},
                        ],
                        "stockAllocations": {"603": 1},
                        "buyTypeIds": [34],
                    }
                )
                with patch(
                    "indy_hub.views.api.build_project_workspace_payload",
                    wraps=build_project_workspace_payload,
                ) as builder:
                    _apply_workspace_state_to_project(project, state)
                project.refresh_from_db()
                payload, changed = get_cached_project_workspace_payload(project)
                self.assertIsNotNone(payload)
                material = payload["materials_tree"][0]["sub_materials"][0][
                    "sub_materials"
                ][0]
                self.assertEqual(material["base_quantity_per_run"], 10)
                builder.assert_not_called()
                self.assertTrue(changed)
                self.assertEqual(
                    payload["cache_version"], PROJECT_WORKSPACE_PAYLOAD_CACHE_VERSION
                )
                for key in (
                    "materials_tree",
                    "recipe_map",
                    "production_time_map",
                    "final_outputs",
                    "editable_final_outputs",
                ):
                    self.assertEqual(payload[key], original[key])
                for key in (
                    PROJECT_WORKSPACE_SDE_SIGNATURE_KEY,
                    PROJECT_WORKSPACE_SCOPED_SDE_SIGNATURE_KEY,
                ):
                    self.assertEqual(project.workspace_state[key], before[key])
                self.assertEqual(
                    project.workspace_state["stockAllocations"], {"603": 1}
                )
                self.assertEqual(project.workspace_state["buyTypeIds"], [34])
                self.assertEqual(
                    list(
                        project.items.order_by("category_order", "id").values_list(
                            "type_id", "quantity_requested"
                        )
                    ),
                    [(603, 1), (2048, 2), (37604, 1)],
                )

    def test_changed_second_fit_quantity_rebuilds_snapshot(self):
        for cache_version in (6, PROJECT_WORKSPACE_PAYLOAD_CACHE_VERSION):
            with self.subTest(cache_version=cache_version):
                project = self._create_cached_two_fit_project(
                    cache_version=cache_version
                )
                self._ensure_project_sde_rows(material_quantity=12)
                state = strip_project_workspace_cache(project.workspace_state)
                state.update(
                    {
                        "simulation_name": project.name,
                        "finalOutputQuantities": [
                            {"index": 0, "typeId": 603, "quantity": 1},
                            {"index": 1, "typeId": 37604, "quantity": 2},
                        ],
                    }
                )
                with patch(
                    "indy_hub.views.api.build_project_workspace_payload",
                    wraps=build_project_workspace_payload,
                ) as builder:
                    _apply_workspace_state_to_project(project, state)
                project.refresh_from_db()
                payload, changed = get_cached_project_workspace_payload(project)
                self.assertIsNotNone(payload)
                self.assertEqual(payload["materials_tree"][1]["quantity"], 2)
                builder.assert_called_once()
                self.assertFalse(changed)
                self.assertEqual(
                    [
                        (row["type_id"], row["type_name"], row["quantity"])
                        for row in payload["editable_final_outputs"]
                    ],
                    [(603, "Fit A", 1), (37604, "Fit B", 2)],
                )
                self.assertEqual(
                    [
                        (row["type_id"], row["quantity"])
                        for row in payload["final_outputs"]
                    ],
                    [(603, 1), (2048, 2), (37604, 2)],
                )
                self.assertEqual(
                    payload["materials_tree"][1]["sub_materials"][0]["quantity"], 2
                )
                material = payload["materials_tree"][0]["sub_materials"][0][
                    "sub_materials"
                ][0]
                self.assertEqual(material["base_quantity_per_run"], 12)
                self.assertEqual(
                    list(
                        project.items.order_by("category_order", "id").values_list(
                            "type_id", "quantity_requested"
                        )
                    ),
                    [(603, 1), (2048, 2), (37604, 1)],
                )

    def test_legacy_cache_without_editable_outputs_uses_raw_output_fallback(self):
        project = self._create_cached_merlin_project()
        original = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        original.pop("editable_final_outputs")
        project.save(update_fields=["workspace_state"])
        project.refresh_from_db()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        )
        self._ensure_project_sde_rows(material_quantity=12)
        state = strip_project_workspace_cache(project.workspace_state)
        state.update(
            {
                "simulation_name": project.name,
                "finalOutputQuantities": [{"index": 0, "typeId": 603, "quantity": 1}],
                "stockAllocations": {"603": 1},
            }
        )
        with patch(
            "indy_hub.views.api.build_project_workspace_payload",
            wraps=build_project_workspace_payload,
        ) as builder:
            _apply_workspace_state_to_project(project, state)
        builder.assert_not_called()
        project.refresh_from_db()
        payload, changed = get_cached_project_workspace_payload(project)
        self.assertTrue(changed)
        self.assertEqual(payload["materials_tree"], original["materials_tree"])
        self.assertEqual(payload["recipe_map"], original["recipe_map"])
        self.assertNotIn("editable_final_outputs", payload)

        state = strip_project_workspace_cache(project.workspace_state)
        state["finalOutputQuantities"][0]["quantity"] = 2
        with patch(
            "indy_hub.views.api.build_project_workspace_payload",
            wraps=build_project_workspace_payload,
        ) as builder:
            _apply_workspace_state_to_project(project, state)
        builder.assert_called_once()
        project.refresh_from_db()
        payload, changed = get_cached_project_workspace_payload(project)
        self.assertFalse(changed)
        self.assertEqual(payload["editable_final_outputs"][0]["quantity"], 2)
        self.assertEqual(
            payload["materials_tree"][0]["sub_materials"][0]["base_quantity_per_run"],
            12,
        )

    def test_empty_editable_outputs_do_not_fall_back_to_raw_items(self):
        self.assertFalse(
            project_workspace_preserves_recipe(
                {
                    "final_outputs": [{"type_id": 603, "quantity": 1}],
                    "editable_final_outputs": [],
                },
                {"runs": 1},
                {
                    "runs": 1,
                    "finalOutputQuantities": [{"index": 0, "quantity": 1}],
                },
            )
        )

    def test_legacy_useless_recipe_choice_survives_non_recipe_save(self):
        project = self._create_cached_merlin_project()
        payload = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        payload["materials_tree"][0]["sub_materials"][0][
            "project_inclusion_mode"
        ] = "useless"
        project.save(update_fields=["workspace_state"])
        state = strip_project_workspace_cache(project.workspace_state)
        state.update({"simulation_name": project.name, "buyTypeIds": [603]})
        _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        saved = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        self.assertEqual(
            saved["materials_tree"][0]["sub_materials"][0]["project_inclusion_mode"],
            "useless",
        )

    def test_explicit_sde_refresh_can_replace_snapshot_on_save(self):
        project = self._create_cached_merlin_project()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        )
        self._ensure_project_sde_rows(material_quantity=12)
        state = strip_project_workspace_cache(project.workspace_state)
        state["simulation_name"] = project.name
        _apply_workspace_state_to_project(project, state, refresh_recipe=True)
        project.refresh_from_db()
        saved = project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        self.assertNotEqual(saved["materials_tree"], original["materials_tree"])
        _payload, changed = get_cached_project_workspace_payload(project)
        self.assertFalse(changed)

    def test_browser_default_normalization_does_not_replace_frozen_recipe(self):
        project = self._create_cached_merlin_project()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        )
        self._ensure_project_sde_rows(material_quantity=12)
        state = strip_project_workspace_cache(project.workspace_state)
        state.update(
            {
                "simulation_name": project.name,
                "meTeConfig": {
                    "mainME": 0,
                    "mainTE": 0,
                    "blueprintConfigs": {"950": {"me": 0, "te": 0}},
                },
                "finalOutputQuantities": [{"index": 0, "quantity": 1}],
                "stockAllocations": {"603": 1},
            }
        )
        _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        payload, changed = get_cached_project_workspace_payload(project)
        self.assertIsNotNone(payload)
        self.assertTrue(changed)
        self.assertEqual(payload["materials_tree"], original["materials_tree"])

    def test_changed_blueprint_efficiency_rebuilds_recipe(self):
        project = self._create_cached_merlin_project()
        self._ensure_project_sde_rows(material_quantity=12)
        state = strip_project_workspace_cache(project.workspace_state)
        state.update(
            {
                "simulation_name": project.name,
                "meTeConfig": {"blueprintConfigs": {"950": {"me": 10, "te": 0}}},
            }
        )
        _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        payload, changed = get_cached_project_workspace_payload(project)
        self.assertIsNotNone(payload)
        self.assertFalse(changed)
        child = payload["materials_tree"][0]["sub_materials"][0]
        self.assertEqual(child["base_quantity_per_run"], 12)
        self.assertEqual(child["blueprint_material_efficiency"], 10)

    def test_partial_efficiency_override_removal_is_a_recipe_edit(self):
        cached = {
            "page": {
                "blueprint_configs": [
                    {"type_id": 950, "material_efficiency": 10, "time_efficiency": 20},
                    {"type_id": 951, "material_efficiency": 10, "time_efficiency": 20},
                ]
            }
        }
        previous = {
            "meTeConfig": {
                "blueprintConfigs": {
                    "950": {"me": 10, "te": 20},
                    "951": {"me": 10, "te": 20},
                }
            }
        }
        for next_configs in (
            {"950": {"me": 10, "te": 20}},
            {"950": {"me": 10}, "951": {"me": 10, "te": 20}},
        ):
            with self.subTest(configs=next_configs):
                self.assertFalse(
                    project_workspace_preserves_recipe(
                        cached,
                        previous,
                        {"meTeConfig": {"blueprintConfigs": next_configs}},
                    )
                )

    def test_explicit_output_defaults_normalize_runs_without_invalidating_snapshot(
        self,
    ):
        project = self._create_cached_merlin_project()
        project.workspace_state["runs"] = 4
        original = build_project_workspace_payload(
            project, include_full_structure_options=False
        )
        project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY] = original
        project.save(update_fields=["workspace_state"])
        self._ensure_project_sde_rows(material_quantity=12)
        state = strip_project_workspace_cache(project.workspace_state)
        state.update(
            {
                "simulation_name": project.name,
                "finalOutputQuantities": [{"index": 0, "quantity": 4}],
            }
        )
        _apply_workspace_state_to_project(project, state)
        project.refresh_from_db()
        payload, changed = get_cached_project_workspace_payload(project)
        self.assertIsNotNone(payload)
        self.assertTrue(changed)
        self.assertEqual(payload["num_runs"], 1)
        self.assertEqual(payload["materials_tree"], original["materials_tree"])
        self.assertEqual(payload["final_outputs"][0]["quantity"], 4)

    def test_structure_options_api_preserves_frozen_recipe(self):
        project = self._create_cached_merlin_project()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        )
        self._ensure_project_sde_rows(material_quantity=12)
        request = self._prepare_request(
            self.factory.get(
                reverse(
                    "indy_hub:production_project_payload", args=[project.project_ref]
                ),
                data={"include_full_structure_options": "1"},
            )
        )
        with patch(
            "indy_hub.views.api.build_project_workspace_payload"
        ) as recipe_builder:
            response = self._production_project_payload_view(
                request, project.project_ref
            )
        recipe_builder.assert_not_called()
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["materials_tree"], original["materials_tree"])
        self.assertEqual(
            payload["recipe_map"], json.loads(json.dumps(original["recipe_map"]))
        )

    def test_installed_sde_generation_refreshes_only_derived_bonuses(self):
        project = self._create_cached_merlin_project()
        original = deepcopy(
            project.workspace_state[PROJECT_WORKSPACE_PAYLOAD_CACHE_KEY]
        )
        with (
            patch(
                "indy_hub.services.production_projects.get_industry_calculation_cache_token",
                return_value="new-installed-sde-generation",
            ),
            patch(
                "indy_hub.services.production_projects.build_craft_structure_planner",
                return_value={"items": [{"me": 2}]},
            ) as planner,
        ):
            payload, _changed = get_cached_project_workspace_payload(project)
        planner.assert_called_once()
        self.assertEqual(payload["materials_tree"], original["materials_tree"])
        self.assertEqual(
            payload["industry_calculation_version"], "new-installed-sde-generation"
        )

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

    @patch("indy_hub.views.industry.build_craft_market_fee_profiles")
    @patch("indy_hub.views.industry.build_user_asset_inventory_snapshot")
    @patch("indy_hub.views.industry._get_craft_project_stock_refresh_progress")
    @patch("indy_hub.views.industry.build_project_workspace_payload")
    @patch("indy_hub.views.industry.get_cached_project_workspace_payload")
    def test_craft_project_renders_tree_update_control_and_uses_runs_override(
        self,
        mock_get_cached_project_workspace_payload,
        mock_build_project_workspace_payload,
        mock_get_stock_refresh_progress,
        mock_build_user_asset_inventory_snapshot,
        mock_build_craft_market_fee_profiles,
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
        mock_build_user_asset_inventory_snapshot.return_value = {
            "scope_missing": False,
            "synced_at": "",
            "totals_by_type": {},
            "characters": [],
        }
        mock_get_stock_refresh_progress.return_value = {"running": True}

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
        mock_get_stock_refresh_progress.assert_called_once_with(self.user)
        mock_build_user_asset_inventory_snapshot.assert_called_once_with(
            self.user,
            allow_refresh=False,
        )
        self.assertNotContains(response, 'id="runsInput"', html=False)
        self.assertNotContains(response, 'id="recalcNowBtn"', html=False)
        self.assertContains(response, 'id="updateFinalOutputQuantitiesBtn"', html=False)
        mock_build_craft_market_fee_profiles.assert_not_called()

    @patch("indy_hub.views.industry.build_craft_market_fee_profiles")
    @patch("indy_hub.views.industry.build_user_asset_inventory_snapshot")
    @patch("indy_hub.views.industry._get_craft_project_stock_refresh_progress")
    @patch("indy_hub.views.industry.build_project_workspace_payload")
    @patch("indy_hub.views.industry.get_cached_project_workspace_payload")
    def test_craft_project_refresh_from_sde_bypasses_saved_snapshot(
        self,
        mock_get_cached_project_workspace_payload,
        mock_build_project_workspace_payload,
        mock_get_stock_refresh_progress,
        mock_build_user_asset_inventory_snapshot,
        mock_build_craft_market_fee_profiles,
    ):
        project = ProductionProject.objects.create(
            user=self.user,
            name="Refresh Workspace",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.MANUAL,
        )
        mock_build_project_workspace_payload.return_value = {
            "bp_type_id": 950,
            "num_runs": 1,
            "final_product_qty": 1,
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
            "final_outputs": [{"type_id": 603, "quantity": 1}],
        }
        mock_build_user_asset_inventory_snapshot.return_value = {
            "scope_missing": False,
            "synced_at": "",
            "totals_by_type": {},
            "characters": [],
        }
        mock_get_stock_refresh_progress.return_value = {"running": False}

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project", args=[project.project_ref]),
                data={"refresh_sde": "1"},
            )
        )
        response = self._craft_project_view(request, project.project_ref)

        self.assertEqual(response.status_code, 200)
        mock_get_cached_project_workspace_payload.assert_not_called()
        mock_build_project_workspace_payload.assert_called_once()
        mock_build_craft_market_fee_profiles.assert_not_called()
        self.assertNotContains(response, "Saved snapshot uses older SDE data")

    @patch("indy_hub.views.industry._ensure_craft_project_stock_refresh_started")
    def test_craft_project_stock_refresh_progress_skips_recent_asset_cache(
        self,
        mock_ensure_refresh_started,
    ):
        cache.delete(_craft_project_stock_refresh_progress_key(int(self.user.id)))
        CachedCharacterAsset.objects.create(
            user=self.user,
            character_id=12345,
            location_id=60003760,
            location_flag="Hangar",
            type_id=34,
            quantity=100,
            synced_at=timezone.now() - timedelta(minutes=30),
        )

        progress = _get_craft_project_stock_refresh_progress(self.user)

        self.assertEqual(progress, {})
        mock_ensure_refresh_started.assert_not_called()

    @patch(
        "indy_hub.views.industry._ensure_craft_project_stock_refresh_started",
        return_value={"running": True},
    )
    def test_craft_project_stock_refresh_progress_starts_stale_asset_cache(
        self,
        mock_ensure_refresh_started,
    ):
        cache.delete(_craft_project_stock_refresh_progress_key(int(self.user.id)))
        CachedCharacterAsset.objects.create(
            user=self.user,
            character_id=12345,
            location_id=60003760,
            location_flag="Hangar",
            type_id=34,
            quantity=100,
            synced_at=timezone.now() - timedelta(minutes=61),
        )

        progress = _get_craft_project_stock_refresh_progress(self.user)

        self.assertEqual(progress, {"running": True})
        mock_ensure_refresh_started.assert_called_once_with(self.user)

    @patch(
        "indy_hub.tasks.material_exchange.refresh_material_exchange_sell_user_assets.delay"
    )
    @patch("esi.models.Token.objects.filter")
    @patch("allianceauth.authentication.models.CharacterOwnership.objects.filter")
    def test_ensure_craft_project_stock_refresh_started_enqueues_celery_task(
        self,
        mock_character_ownership_filter,
        mock_token_filter,
        mock_delay,
    ):
        progress_key = _craft_project_stock_refresh_progress_key(int(self.user.id))
        cache.delete(progress_key)
        mock_character_ownership_filter.return_value = _FakeOwnershipCountQuerySet(1)
        mock_token_filter.return_value = _FakeTokenQuerySet(True)
        mock_delay.return_value = type("TaskResult", (), {"id": "task-123"})()

        progress = _ensure_craft_project_stock_refresh_started(self.user)

        self.assertTrue(progress["running"])
        self.assertEqual(progress["total"], 1)
        mock_delay.assert_called_once_with(int(self.user.id))

    def test_craft_project_stock_refresh_status_reports_changed_assets(self):
        progress_key = _craft_project_stock_refresh_progress_key(int(self.user.id))
        cache.set(
            progress_key,
            {"running": False, "finished": True, "error": None},
            600,
        )
        old_sync = timezone.now() - timedelta(minutes=70)
        latest_sync = timezone.now()
        CachedCharacterAsset.objects.create(
            user=self.user,
            character_id=12345,
            location_id=60003760,
            location_flag="Hangar",
            type_id=34,
            quantity=100,
            synced_at=latest_sync,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project_stock_refresh_status"),
                data={"since": old_sync.isoformat()},
            )
        )
        response = self._craft_project_stock_refresh_status_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["synced_at"], latest_sync.isoformat())

    def test_craft_project_stock_refresh_status_reports_changed_from_empty_snapshot(
        self,
    ):
        progress_key = _craft_project_stock_refresh_progress_key(int(self.user.id))
        cache.set(
            progress_key,
            {"running": False, "finished": True, "error": None},
            600,
        )
        latest_sync = timezone.now()
        CachedCharacterAsset.objects.create(
            user=self.user,
            character_id=12345,
            location_id=60003760,
            location_flag="Hangar",
            type_id=34,
            quantity=100,
            synced_at=latest_sync,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:craft_project_stock_refresh_status"),
                data={"since": ""},
            )
        )
        response = self._craft_project_stock_refresh_status_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["synced_at"], latest_sync.isoformat())

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
            project_ref="preexist95",
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
