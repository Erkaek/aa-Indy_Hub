"""Tests for admin user bulk action scope helpers."""

from __future__ import annotations

# Standard Library
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Django
from django.test import SimpleTestCase

# AA Example App
from indy_hub.services.admin_user_bulk_actions import collect_user_scope_status_map


class CollectUserScopeStatusMapTests(SimpleTestCase):
    @patch("indy_hub.services.admin_user_bulk_actions.Token")
    def test_uses_prefetched_scopes_without_values_list(self, mock_token) -> None:
        scope_names = [
            "esi-characters.read_blueprints.v1",
            "esi-universe.read_structures.v1",
            "esi-industry.read_character_jobs.v1",
            "esi-assets.read_assets.v1",
            "esi-skills.read_skills.v1",
        ]
        scope_manager = MagicMock()
        scope_manager.all.return_value = [
            SimpleNamespace(name=name) for name in scope_names
        ]
        scope_manager.values_list.side_effect = AssertionError(
            "values_list should not be used with prefetched scopes"
        )
        fake_token = SimpleNamespace(user_id=77, scopes=scope_manager)

        tokens_qs = MagicMock()
        tokens_qs.require_valid.side_effect = AssertionError(
            "Scope listings must not validate or refresh tokens"
        )
        tokens_qs.prefetch_related.return_value = [fake_token]
        mock_token.objects.filter.return_value = tokens_qs

        result = collect_user_scope_status_map([77])

        scope_manager.all.assert_called_once_with()
        self.assertIn(77, result)
        self.assertTrue(bool(result[77]["is_complete"]))
        self.assertEqual(list(result[77]["missing_labels"]), [])
        self.assertNotIn("online", result[77]["flags"])

    @patch("indy_hub.services.admin_user_bulk_actions.Token")
    def test_does_not_count_split_scopes_across_tokens_as_complete(
        self, mock_token
    ) -> None:
        scope_manager_a = MagicMock()
        scope_manager_a.all.return_value = [
            SimpleNamespace(name="esi-characters.read_blueprints.v1")
        ]
        scope_manager_b = MagicMock()
        scope_manager_b.all.return_value = [
            SimpleNamespace(name="esi-universe.read_structures.v1")
        ]

        token_a = SimpleNamespace(user_id=77, scopes=scope_manager_a)
        token_b = SimpleNamespace(user_id=77, scopes=scope_manager_b)

        tokens_qs = MagicMock()
        tokens_qs.require_valid.side_effect = AssertionError(
            "Scope listings must not validate or refresh tokens"
        )
        tokens_qs.prefetch_related.return_value = [token_a, token_b]
        mock_token.objects.filter.return_value = tokens_qs

        result = collect_user_scope_status_map([77])

        self.assertIn(77, result)
        self.assertFalse(result[77]["flags"]["blueprints"])
        self.assertIn("blueprints", result[77]["missing_labels"])
        self.assertFalse(result[77]["is_complete"])
