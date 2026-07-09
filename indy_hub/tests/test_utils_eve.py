"""Regression tests for published-only SDE resolution helpers."""

# Standard Library
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

# AA Example App
from indy_hub.utils import eve


class EvePublishedDataTests(TestCase):
    def setUp(self) -> None:
        eve._TYPE_NAME_CACHE.clear()
        eve._BP_PRODUCT_CACHE.clear()
        eve._REACTION_CACHE.clear()

    @patch("indy_hub.utils.eve._get_item_type_model")
    def test_batch_cache_type_names_filters_unpublished_types(
        self, mock_get_item_type_model
    ) -> None:
        item_type_model = MagicMock()
        item_type_model.objects.filter.return_value.only.return_value = [
            SimpleNamespace(id=34, name="Tritanium"),
        ]
        mock_get_item_type_model.return_value = item_type_model

        result = eve.batch_cache_type_names([34, 35])

        item_type_model.objects.filter.assert_called_once_with(
            id__in={34, 35},
            published=True,
        )
        self.assertEqual(result, {34: "Tritanium", 35: "35"})

    @patch("indy_hub.utils.eve.connection.cursor")
    def test_get_blueprint_product_type_id_requires_published_blueprint_and_product(
        self, mock_cursor
    ) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (16672,)
        mock_cursor.return_value = cursor

        resolved = eve.get_blueprint_product_type_id(46207)

        sql, params = cursor.execute.call_args[0]
        self.assertIn("eve_sde_blueprintactivityproduct", sql)
        self.assertIn("COALESCE(blueprint_t.published, 0) = 1", sql)
        self.assertIn("COALESCE(product_t.published, 0) = 1", sql)
        self.assertEqual(params, [46207])
        self.assertEqual(resolved, 16672)

    @patch("indy_hub.utils.eve.connection.cursor")
    def test_is_reaction_blueprint_requires_published_blueprint_and_product(
        self, mock_cursor
    ) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (1,)
        mock_cursor.return_value = cursor

        value = eve.is_reaction_blueprint(46207)

        sql, params = cursor.execute.call_args[0]
        self.assertIn("eve_sde_blueprintactivityproduct", sql)
        self.assertIn("ba.activity = 'reaction'", sql)
        self.assertIn("COALESCE(blueprint_t.published, 0) = 1", sql)
        self.assertIn("COALESCE(product_t.published, 0) = 1", sql)
        self.assertEqual(params, [46207])
        self.assertTrue(value)
