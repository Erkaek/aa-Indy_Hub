"""Tests for the craft blueprint payload API."""

# Standard Library
import json
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, TestCase

# AA Example App
from indy_hub.services.craft_materials import (
    compute_job_material_quantity,
    is_base_item_material_efficiency_exempt,
)
from indy_hub.views.api import (
    craft_bp_payload,
    craft_structure_jump_distances,
    fuzzwork_price,
)


class _CursorStub:
    def __init__(self, *, fetchone_result=None, fetchall_result=None):
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result if fetchall_result is not None else []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.last_query = sql
        self.last_params = params

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result

    def fetchmany(self, size=None):
        return []

    def close(self):
        return None


class CraftBlueprintPayloadApiTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="builder", password="secret")
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)

    @patch("indy_hub.views.api.build_craft_time_map")
    @patch("indy_hub.views.api.emit_view_analytics_event")
    @patch("indy_hub.views.api.build_craft_structure_planner")
    @patch("indy_hub.views.api.build_craft_character_advisor")
    def test_payload_includes_structure_planner_data(
        self,
        mock_build_craft_character_advisor,
        mock_build_structure_planner,
        mock_emit_view_analytics_event,
        mock_build_craft_time_map,
    ) -> None:
        mock_build_structure_planner.return_value = {
            "items": [{"type_id": 2000, "options": [{"structure_id": 99}]}],
            "summary": {"has_structures": True},
        }
        mock_build_craft_character_advisor.return_value = {
            "characters": [],
            "items": {},
            "summary": {
                "characters": 0,
                "eligible_items": 0,
                "blocked_items": 0,
                "missing_skill_data_characters": 0,
            },
        }
        mock_emit_view_analytics_event.return_value = None
        mock_build_craft_time_map.return_value = {
            2000: {
                "type_id": 2000,
                "base_time_seconds": 120,
                "produced_per_cycle": 2,
            }
        }

        request = self.factory.get(
            "/indy_hub/api/craft-bp-payload/1234/",
            {"runs": 3, "me": 10, "te": 20},
        )
        request.user = self.user

        view = craft_bp_payload
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        cursor_sequence = iter(
            [
                _CursorStub(fetchone_result=(2000, 2)),
                _CursorStub(fetchall_result=[]),
                _CursorStub(
                    fetchone_result=(2,), fetchall_result=[(3001, 10), (3002, 1)]
                ),
            ]
        )

        with patch(
            "indy_hub.views.api.connection.cursor",
            side_effect=lambda: next(cursor_sequence),
        ):
            response = view(request, 1234)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)

        self.assertEqual(payload["product_type_id"], 2000)
        self.assertEqual(payload["output_qty_per_run"], 2)
        self.assertEqual(payload["product_output_per_cycle"], 2)
        self.assertEqual(payload["final_product_qty"], 6)
        self.assertEqual(
            payload["recipe_map"]["2000"],
            {
                "produced_per_cycle": 2,
                "inputs_per_cycle": [
                    {"type_id": 3001, "quantity": 9},
                    {"type_id": 3002, "quantity": 1},
                ],
                "inputs_per_cycle_me0": [
                    {"type_id": 3001, "quantity": 10},
                    {"type_id": 3002, "quantity": 1},
                ],
            },
        )
        self.assertEqual(
            payload["structure_planner"],
            mock_build_structure_planner.return_value,
        )
        self.assertEqual(
            payload["production_time_map"],
            {"2000": mock_build_craft_time_map.return_value[2000]},
        )
        self.assertEqual(
            payload["craft_character_advisor"],
            mock_build_craft_character_advisor.return_value,
        )
        mock_build_structure_planner.assert_called_once_with(
            product_type_id=2000,
            product_type_name="",
            product_output_per_cycle=2,
            craft_cycles_summary={},
        )
        mock_build_craft_time_map.assert_called_once_with(
            recipe_map={
                2000: {
                    "produced_per_cycle": 2,
                    "inputs_per_cycle": [
                        {"type_id": 3001, "quantity": 9},
                        {"type_id": 3002, "quantity": 1},
                    ],
                    "inputs_per_cycle_me0": [
                        {"type_id": 3001, "quantity": 10},
                        {"type_id": 3002, "quantity": 1},
                    ],
                }
            },
            product_type_id=2000,
            product_type_name="",
            product_output_per_cycle=2,
            root_blueprint_type_id=1234,
        )
        mock_build_craft_character_advisor.assert_called_once()

    def test_compute_job_material_quantity_uses_job_level_rounding(self) -> None:
        self.assertEqual(compute_job_material_quantity(75, 50, 10), 3375)
        self.assertEqual(compute_job_material_quantity(18, 50, 10), 810)
        self.assertEqual(compute_job_material_quantity(1, 50, 10), 45)

    def test_base_item_efficiency_exemption_matches_t1_to_t2_upgrade(self) -> None:
        self.assertTrue(is_base_item_material_efficiency_exempt(2, 6, 1, 6))
        self.assertFalse(is_base_item_material_efficiency_exempt(2, 6, None, 17))
        self.assertFalse(is_base_item_material_efficiency_exempt(1, 6, 1, 6))

    @patch("indy_hub.views.api.build_craft_time_map")
    @patch("indy_hub.views.api.emit_view_analytics_event")
    @patch("indy_hub.views.api.build_craft_structure_planner")
    @patch("indy_hub.views.api.build_craft_character_advisor")
    def test_payload_marks_base_item_inputs_as_material_bonus_exempt(
        self,
        mock_build_craft_character_advisor,
        mock_build_structure_planner,
        mock_emit_view_analytics_event,
        mock_build_craft_time_map,
    ) -> None:
        mock_build_structure_planner.return_value = {"items": [], "summary": {}}
        mock_build_craft_character_advisor.return_value = {
            "characters": [],
            "items": {},
            "summary": {
                "characters": 0,
                "eligible_items": 0,
                "blocked_items": 0,
                "missing_skill_data_characters": 0,
            },
        }
        mock_emit_view_analytics_event.return_value = None
        mock_build_craft_time_map.return_value = {
            3002: {"type_id": 3002, "base_time_seconds": 600}
        }

        request = self.factory.get(
            "/indy_hub/api/craft-bp-payload/1234/",
            {"runs": 50, "me": 10, "te": 20},
        )
        request.user = self.user

        view = craft_bp_payload
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        cursor_sequence = iter(
            [
                _CursorStub(fetchone_result=(2000, 1)),
                _CursorStub(
                    fetchall_result=[
                        (3001, "Regular Material", 75),
                        (3002, "Base Hull", 1),
                    ]
                ),
                _CursorStub(fetchone_result=(2, 6)),
                _CursorStub(fetchone_result=(None, 17)),
                _CursorStub(fetchone_result=None),
                _CursorStub(fetchone_result=(1, 6)),
                _CursorStub(fetchone_result=None),
                _CursorStub(fetchone_result=(1,)),
                _CursorStub(fetchall_result=[(3001, 75), (3002, 1)]),
            ]
        )

        with patch(
            "indy_hub.views.api.connection.cursor",
            side_effect=lambda: next(cursor_sequence),
        ):
            response = view(request, 1234)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        materials_tree = payload["materials_tree"]
        self.assertEqual(materials_tree[0]["type_id"], 3001)
        self.assertEqual(materials_tree[0]["quantity"], 3375)
        self.assertTrue(materials_tree[0]["material_bonus_applicable"])
        self.assertEqual(materials_tree[1]["type_id"], 3002)
        self.assertEqual(materials_tree[1]["quantity"], 50)
        self.assertFalse(materials_tree[1]["material_bonus_applicable"])
        self.assertEqual(
            payload["production_time_map"],
            {"3002": mock_build_craft_time_map.return_value[3002]},
        )
        self.assertEqual(
            payload["craft_character_advisor"],
            mock_build_craft_character_advisor.return_value,
        )

    @patch("indy_hub.views.api.compute_solar_system_jump_distances")
    @patch("indy_hub.views.api.resolve_solar_system_reference")
    def test_jump_distance_endpoint_returns_origin_and_distances(
        self,
        mock_resolve_solar_system_reference,
        mock_compute_solar_system_jump_distances,
    ) -> None:
        mock_resolve_solar_system_reference.return_value = (30000142, "Jita", "highsec")
        mock_compute_solar_system_jump_distances.return_value = {
            30000142: 0,
            30002187: 4,
            30002510: None,
        }

        request = self.factory.get(
            "/indy_hub/api/craft-structures/jump-distances/",
            {
                "solar_system_name": "Jita",
                "target_system_ids": ["30000142", "30002187", "30002510"],
            },
        )
        request.user = self.user

        view = craft_structure_jump_distances
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        response = view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["origin"]["solar_system_id"], 30000142)
        self.assertEqual(payload["origin"]["solar_system_name"], "Jita")
        self.assertEqual(
            payload["distances"],
            [
                {"solar_system_id": 30000142, "jumps": 0},
                {"solar_system_id": 30002187, "jumps": 4},
                {"solar_system_id": 30002510, "jumps": None},
            ],
        )
        mock_resolve_solar_system_reference.assert_called_once_with(
            solar_system_id=None,
            solar_system_name="Jita",
        )
        mock_compute_solar_system_jump_distances.assert_called_once_with(
            30000142,
            [30000142, 30002187, 30002510],
        )

    @patch("indy_hub.views.api.emit_view_analytics_event")
    @patch("indy_hub.services.market_prices.fetch_adjusted_prices")
    def test_fuzzwork_price_can_return_adjusted_prices(
        self,
        mock_fetch_adjusted_prices,
        mock_emit_view_analytics_event,
    ) -> None:
        mock_emit_view_analytics_event.return_value = None
        mock_fetch_adjusted_prices.return_value = {
            34: {
                "adjusted_price": Decimal("4.5"),
                "average_price": Decimal("5.5"),
            }
        }

        request = self.factory.get(
            "/indy_hub/api/fuzzwork-price/",
            {"type_id": "34", "price_source": "adjusted", "full": "1"},
        )
        request.user = self.user

        view = fuzzwork_price
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content),
            {
                "34": {
                    "adjusted_price": 4.5,
                    "average_price": 5.5,
                }
            },
        )
        mock_fetch_adjusted_prices.assert_called_once_with(["34"], timeout=10)

    @patch("indy_hub.views.api.resolve_solar_system_reference", return_value=None)
    def test_jump_distance_endpoint_rejects_unknown_origin(
        self,
        mock_resolve_solar_system_reference,
    ) -> None:
        request = self.factory.get(
            "/indy_hub/api/craft-structures/jump-distances/",
            {
                "solar_system_name": "Unknown",
                "target_system_ids": ["30000142"],
            },
        )
        request.user = self.user

        view = craft_structure_jump_distances
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        response = view(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            json.loads(response.content), {"error": "solar_system_not_found"}
        )
        mock_resolve_solar_system_reference.assert_called_once()
