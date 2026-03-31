"""Tests for craft production timing helpers."""

# Standard Library
from unittest import TestCase
from unittest.mock import patch

# AA Example App
from indy_hub.services.craft_times import (
    build_craft_time_map,
    get_blueprint_max_production_limit,
    get_max_copy_runs_per_request,
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


class _ColumnStub:
    def __init__(self, name: str):
        self.name = name


class BuildCraftTimeMapTests(TestCase):
    def test_build_craft_time_map_returns_rows_for_craftable_items(self) -> None:
        cursor_sequence = iter(
            [
                _CursorStub(
                    fetchall_result=[
                        (2000, "Final Product", 1234, 1, 2, 120),
                        (3001, "Component A", 5678, 1, 1, 45),
                    ]
                )
            ]
        )

        with patch(
            "indy_hub.services.craft_times.connection.cursor",
            side_effect=lambda: next(cursor_sequence),
        ):
            result = build_craft_time_map(
                recipe_map={
                    2000: {"produced_per_cycle": 2, "inputs_per_cycle": []},
                    3001: {"produced_per_cycle": 1, "inputs_per_cycle": []},
                },
                product_type_id=2000,
                product_type_name="Final Product",
                product_output_per_cycle=2,
                root_blueprint_type_id=1234,
            )

        self.assertEqual(
            result,
            {
                2000: {
                    "type_id": 2000,
                    "type_name": "Final Product",
                    "blueprint_type_id": 1234,
                    "activity_id": 1,
                    "activity_label": "Manufacturing",
                    "produced_per_cycle": 2,
                    "base_time_seconds": 120,
                },
                3001: {
                    "type_id": 3001,
                    "type_name": "Component A",
                    "blueprint_type_id": 5678,
                    "activity_id": 1,
                    "activity_label": "Manufacturing",
                    "produced_per_cycle": 1,
                    "base_time_seconds": 45,
                },
            },
        )

    def test_build_craft_time_map_falls_back_to_root_blueprint_lookup(self) -> None:
        cursor_sequence = iter(
            [
                _CursorStub(fetchall_result=[]),
                _CursorStub(fetchone_result=(1, 3, 600)),
            ]
        )

        with patch(
            "indy_hub.services.craft_times.connection.cursor",
            side_effect=lambda: next(cursor_sequence),
        ):
            result = build_craft_time_map(
                recipe_map={"2000": {"produced_per_cycle": 3, "inputs_per_cycle": []}},
                product_type_id=2000,
                product_type_name="Fallback Product",
                product_output_per_cycle=3,
                root_blueprint_type_id=1234,
            )

        self.assertEqual(
            result,
            {
                2000: {
                    "type_id": 2000,
                    "type_name": "Fallback Product",
                    "blueprint_type_id": 1234,
                    "activity_id": 1,
                    "activity_label": "Manufacturing",
                    "produced_per_cycle": 3,
                    "base_time_seconds": 600,
                }
            },
        )

    def test_build_craft_time_map_clamps_negative_base_time_to_zero(self) -> None:
        cursor_sequence = iter(
            [
                _CursorStub(fetchall_result=[(2000, "Final Product", 1234, 1, 2, -25)]),
            ]
        )

        with patch(
            "indy_hub.services.craft_times.connection.cursor",
            side_effect=lambda: next(cursor_sequence),
        ):
            result = build_craft_time_map(
                recipe_map={2000: {"produced_per_cycle": 2, "inputs_per_cycle": []}},
                product_type_id=2000,
                product_type_name="Final Product",
                product_output_per_cycle=2,
                root_blueprint_type_id=1234,
            )

        self.assertEqual(result[2000]["base_time_seconds"], 0)

    def test_get_max_copy_runs_per_request_prefers_smallest_known_limit(self) -> None:
        with (
            patch(
                "indy_hub.services.craft_times.get_blueprint_max_production_limit",
                return_value=10,
            ),
            patch(
                "indy_hub.services.craft_times.get_max_manufacturing_runs_before_launch_window",
                return_value=74,
            ),
        ):
            result = get_max_copy_runs_per_request(
                blueprint_type_id=16213,
                time_efficiency=20,
            )

        self.assertEqual(result, 10)

    def test_get_max_copy_runs_per_request_falls_back_to_native_limit_only(
        self,
    ) -> None:
        with (
            patch(
                "indy_hub.services.craft_times.get_blueprint_max_production_limit",
                return_value=10,
            ),
            patch(
                "indy_hub.services.craft_times.get_max_manufacturing_runs_before_launch_window",
                return_value=None,
            ),
        ):
            result = get_max_copy_runs_per_request(
                blueprint_type_id=16213,
                time_efficiency=20,
            )

        self.assertEqual(result, 10)

    def test_get_blueprint_max_production_limit_supports_eve_sde_schema(self) -> None:
        cursor_sequence = iter(
            [
                _CursorStub(),
                _CursorStub(fetchone_result=(10,)),
            ]
        )

        with (
            patch(
                "indy_hub.services.craft_times.connection.cursor",
                side_effect=lambda: next(cursor_sequence),
            ),
            patch(
                "indy_hub.services.craft_times.connection.introspection.table_names",
                return_value=["eve_sde_blueprintactivity"],
            ),
            patch(
                "indy_hub.services.craft_times.connection.introspection.get_table_description",
                return_value=[
                    _ColumnStub("id"),
                    _ColumnStub("activity"),
                    _ColumnStub("time"),
                    _ColumnStub("max_production_limit"),
                    _ColumnStub("blueprint_item_type_id"),
                ],
            ),
        ):
            result = get_blueprint_max_production_limit(blueprint_type_id=16213)

        self.assertEqual(result, 10)
