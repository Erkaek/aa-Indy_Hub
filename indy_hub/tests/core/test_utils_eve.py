"""Regression tests for published-only SDE resolution helpers."""

# Standard Library
from unittest import TestCase
from unittest.mock import MagicMock, patch

# Django
from django.db import connection, models
from django.test import TestCase as DjangoTestCase
from django.test.utils import CaptureQueriesContext, isolate_apps
from django.utils import translation

# Alliance Auth (External Libs)
from eve_sde.models import ItemType

# AA Example App
from indy_hub.utils import eve


class EvePublishedDataTests(TestCase):
    def setUp(self) -> None:
        eve._TYPE_NAME_CACHE.clear()
        eve._BP_PRODUCT_CACHE.clear()
        eve._REACTION_CACHE.clear()

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


class EveTypeNameORMTests(DjangoTestCase):
    def setUp(self):
        eve._TYPE_NAME_CACHE.clear()
        self.addCleanup(eve._TYPE_NAME_CACHE.clear)

    def create_type(
        self, pk=34, *, name="Base English", english="Tritanium", published=True
    ):
        obj = ItemType.objects.create(pk=pk, name=name, published=published)
        queryset = ItemType.objects.all()
        if callable(getattr(queryset, "rewrite", None)):
            queryset = queryset.rewrite(False)
        fields = {f.name for f in ItemType._meta.concrete_fields}
        values = {"name": name}
        if "name_en" in fields:
            values["name_en"] = english
            for field, value in (
                ("name_de", "Deutscher Name"),
                ("name_fr_fr", "Nom français"),
                ("name_fr", "Nom français"),
            ):
                if field in fields:
                    values[field] = value
        queryset.filter(pk=pk).update(**values)
        return obj

    def test_names_are_english_under_en_fr_de_with_cold_and_warm_cache(self):
        self.create_type(name="Tritanium")
        multilingual = callable(getattr(ItemType.objects.all(), "rewrite", None))
        # AA uses fr-fr; a bare fr request also keeps the English result.
        for language, translated in (
            ("en", "Tritanium"),
            ("fr", "Tritanium"),
            ("fr-fr", "Nom français"),
            ("de", "Deutscher Name"),
        ):
            with self.subTest(language=language), translation.override(language):
                if multilingual:
                    # Prove the installed native manager really follows locale.
                    self.assertEqual(
                        ItemType.objects.values_list("name", flat=True).get(pk=34),
                        translated,
                    )
                eve._TYPE_NAME_CACHE.clear()
                with self.assertNumQueries(1):
                    self.assertEqual(eve.get_type_name(34), "Tritanium")
                with self.assertNumQueries(0):
                    self.assertEqual(eve.get_type_name(34), "Tritanium")
                with self.assertNumQueries(1):
                    self.assertEqual(
                        eve.batch_cache_type_names([34, 34]), {34: "Tritanium"}
                    )

    def test_batch_excludes_unpublished_but_unit_lookup_preserves_its_name(self):
        self.create_type(name="Tritanium")
        self.create_type(35, name="Hidden type", english="Hidden type", published=False)
        self.assertEqual(
            eve.batch_cache_type_names([34, 35]), {34: "Tritanium", 35: "35"}
        )
        self.assertEqual(eve.get_type_name(35), "Hidden type")
        # A warm unit cache must not bypass the batch's published filter.
        self.assertEqual(eve.batch_cache_type_names([35]), {35: "35"})

    def test_missing_and_invalid_inputs_preserve_existing_contract(self):
        with self.assertNumQueries(0):
            for value in (None, 0, ""):
                self.assertEqual(eve.get_type_name(value), "")
            self.assertEqual(eve.get_type_name("invalid"), "invalid")
            self.assertEqual(eve.batch_cache_type_names([]), {})
            self.assertEqual(eve.batch_cache_type_names([None, 0]), {})
            with self.assertRaises(ValueError):
                eve.batch_cache_type_names(["invalid"])
        self.assertEqual(eve.get_type_name(987654321), "987654321")
        self.assertEqual(
            eve.batch_cache_type_names([987654321]), {987654321: "987654321"}
        )

    def test_missing_names_are_not_stuck_after_sde_data_appears(self):
        self.assertEqual(eve.get_type_name(34), "34")
        self.assertEqual(eve.batch_cache_type_names([34]), {34: "34"})
        self.assertNotIn(34, eve._TYPE_NAME_CACHE)
        self.create_type(name="Tritanium")
        self.assertEqual(eve.get_type_name(34), "Tritanium")
        self.assertEqual(eve.batch_cache_type_names([34]), {34: "Tritanium"})

    def test_null_and_empty_names_preserve_raw_coalesce_results(self):
        fields = {field.name for field in ItemType._meta.concrete_fields}
        has_english = "name_en" in fields
        self.create_type()
        queryset = ItemType.objects.all()
        if callable(getattr(queryset, "rewrite", None)):
            queryset = queryset.rewrite(False)
        for english, base, expected_unit, expected_batch in (
            ("English", "Base", "English", "English"),
            (None, "Base", "Base", "Base"),
            ("", "Base", "34", ""),
            (None, "", "34", ""),
        ):
            values = {"name": base}
            if has_english:
                values["name_en"] = english
            else:
                expected_unit, expected_batch = base or "34", base
            queryset.filter(pk=34).update(**values)
            for language in ("en", "fr", "de"):
                with self.subTest(english=english, base=base, language=language):
                    with translation.override(language):
                        eve._TYPE_NAME_CACHE.clear()
                        self.assertEqual(eve.get_type_name(34), expected_unit)
                        self.assertEqual(
                            eve.batch_cache_type_names([34]), {34: expected_batch}
                        )

    @patch("indy_hub.utils.eve._get_item_type_model", return_value=None)
    def test_absent_sde_model_falls_back_without_sql_or_esi(self, _model):
        with (
            self.assertNumQueries(0),
            patch("indy_hub.utils.eve.shared_client") as external,
        ):
            self.assertEqual(eve.get_type_name(34), "34")
            self.assertEqual(eve.batch_cache_type_names([34, 35]), {34: "34", 35: "35"})
        self.assertEqual(external.mock_calls, [])

    def test_large_batch_deduplicates_and_bounds_queries_by_backend_limit(self):
        self.create_type(name="Tritanium")
        ids = [34, *range(100_000, 102_500)]
        batch_size = min(1000, connection.features.max_query_params or 1000)
        expected_queries = (len(ids) + batch_size - 1) // batch_size
        with CaptureQueriesContext(connection) as queries:
            result = eve.batch_cache_type_names([*ids, *ids])
        self.assertEqual(len(queries), expected_queries)
        self.assertEqual(
            result, {pk: "Tritanium" if pk == 34 else str(pk) for pk in ids}
        )

    def test_no_translation_manager_preserves_null_versus_empty_english(self):
        self.enterContext(isolate_apps())

        class PlainType(models.Model):
            id = models.BigIntegerField(primary_key=True)
            name = models.CharField(max_length=250)
            name_en = models.CharField(max_length=250, null=True)
            published = models.BooleanField()

            class Meta:
                app_label = "indy_hub"
                managed = False
                db_table = ItemType._meta.db_table

        self.create_type()
        self.assertFalse(hasattr(PlainType.objects.all(), "rewrite"))
        with patch("indy_hub.utils.eve._get_item_type_model", return_value=PlainType):
            for english, base, unit, batch in (
                ("English", "Base", "English", "English"),
                (None, "Base", "Base", "Base"),
                ("", "Base", "34", ""),
                (None, "", "34", ""),
            ):
                with self.subTest(english=english, base=base):
                    PlainType.objects.filter(pk=34).update(name=base, name_en=english)
                    eve._TYPE_NAME_CACHE.clear()
                    self.assertEqual(eve.get_type_name(34), unit)
                    self.assertEqual(eve.batch_cache_type_names([34]), {34: batch})
            PlainType.objects.filter(pk=34).update(name_en="English now available")
            self.assertEqual(eve.get_type_name(34), "English now available")
