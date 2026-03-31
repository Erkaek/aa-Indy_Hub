"""Tests for exact craft structure jump-distance helpers."""

# Standard Library
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.services.craft_structures import (
    _load_stargate_adjacency,
    compute_solar_system_jump_distances,
)


class _CursorStub:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.last_query = sql
        self.last_params = params

    def fetchall(self):
        return list(self._rows)


class CraftStructureJumpDistanceTests(TestCase):
    def tearDown(self) -> None:
        _load_stargate_adjacency.cache_clear()
        super().tearDown()

    @patch("indy_hub.services.craft_structures.connection.cursor")
    def test_compute_solar_system_jump_distances_uses_exact_bfs(
        self,
        mock_cursor,
    ) -> None:
        _load_stargate_adjacency.cache_clear()
        mock_cursor.return_value = _CursorStub(
            [
                (1, 2),
                (2, 3),
                (2, 4),
                (10, 11),
            ]
        )

        distances = compute_solar_system_jump_distances(1, [1, 3, 4, 9])

        self.assertEqual(
            distances,
            {
                1: 0,
                3: 2,
                4: 2,
                9: None,
            },
        )

    @patch("indy_hub.services.craft_structures.connection.cursor")
    def test_compute_solar_system_jump_distances_returns_empty_without_targets(
        self,
        mock_cursor,
    ) -> None:
        _load_stargate_adjacency.cache_clear()
        mock_cursor.return_value = _CursorStub([])

        self.assertEqual(compute_solar_system_jump_distances(30000142, []), {})
