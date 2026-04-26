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

    @patch("indy_hub.utils.eve.EveIndustryActivityProduct")
    def test_get_blueprint_product_type_id_requires_published_blueprint_and_product(
        self, mock_activity_product
    ) -> None:
        product_row = SimpleNamespace(product_eve_type_id=16672)
        queryset = MagicMock()
        queryset.exists.return_value = True
        queryset.filter.return_value.first.return_value = product_row
        queryset.first.return_value = product_row
        mock_activity_product.objects.filter.return_value = queryset

        resolved = eve.get_blueprint_product_type_id(46207)

        mock_activity_product.objects.filter.assert_called_once_with(
            eve_type_id=46207,
            eve_type__published=True,
            product_eve_type__published=True,
        )
        self.assertEqual(resolved, 16672)

    @patch("indy_hub.utils.eve.EveIndustryActivityProduct")
    def test_is_reaction_blueprint_requires_published_blueprint_and_product(
        self, mock_activity_product
    ) -> None:
        mock_activity_product.objects.filter.return_value.exists.return_value = True

        value = eve.is_reaction_blueprint(46207)

        mock_activity_product.objects.filter.assert_called_once_with(
            eve_type_id=46207,
            activity_id__in=[9, 11],
            eve_type__published=True,
            product_eve_type__published=True,
        )
        self.assertTrue(value)