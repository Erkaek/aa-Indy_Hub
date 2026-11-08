"""Conservation contract and executable reasons to defer a native singleton.

The native proxy uses the real django-solo implementation against the disposable
settings table. It is not a second production API or a proposed migration.
"""

# Standard Library
from unittest.mock import patch

# Third Party
from solo.models import SingletonModel

# Django
from django.contrib.auth.models import Permission, User
from django.core.cache import caches
from django.db import transaction
from django.test import TestCase, override_settings
from django.test.utils import isolate_apps
from django.urls import reverse

# AA Example App
from indy_hub.models import MaterialExchangeSettings
from indy_hub.tasks.material_exchange_contracts import run_material_exchange_cycle
from indy_hub.tests.test_smoke import assign_main_character
from indy_hub.views.material_exchange import _is_material_exchange_enabled


class MaterialExchangeSettingsContractTests(TestCase):
    def setUp(self):
        # Historical schedule reconciliation may have initialized the table.
        MaterialExchangeSettings.objects.all().delete()

    def test_empty_table_creates_and_reuses_enabled_settings(self):
        first = MaterialExchangeSettings.get_solo()
        self.assertTrue(first.is_enabled)
        self.assertEqual(MaterialExchangeSettings.get_solo().pk, first.pk)
        self.assertEqual(MaterialExchangeSettings.objects.count(), 1)

    def test_disabled_settings_keep_identity_and_timestamps(self):
        for pk in (1, 42):
            with self.subTest(pk=pk):
                MaterialExchangeSettings.objects.all().delete()
                existing = MaterialExchangeSettings.objects.create(
                    pk=pk, is_enabled=False
                )
                before = list(MaterialExchangeSettings.objects.values())
                self.assertEqual(MaterialExchangeSettings.get_solo().pk, existing.pk)
                self.assertFalse(_is_material_exchange_enabled())
                self.assertEqual(
                    list(MaterialExchangeSettings.objects.values()), before
                )

    def test_duplicate_rows_are_preserved_with_lowest_pk_effective(self):
        for later_enabled in (False, True):
            with self.subTest(later_enabled=later_enabled):
                MaterialExchangeSettings.objects.all().delete()
                MaterialExchangeSettings.objects.create(pk=42, is_enabled=False)
                MaterialExchangeSettings.objects.create(pk=99, is_enabled=later_enabled)
                before = list(MaterialExchangeSettings.objects.order_by("pk").values())
                self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
                self.assertFalse(_is_material_exchange_enabled())
                self.assertEqual(
                    list(MaterialExchangeSettings.objects.order_by("pk").values()),
                    before,
                )

    @override_settings(SOLO_CACHE="default")
    def test_current_reads_see_save_bulk_update_and_rollback_immediately(self):
        existing = MaterialExchangeSettings.objects.create(pk=42, is_enabled=False)
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
        existing.is_enabled = True
        existing.save(update_fields=["is_enabled", "updated_at"])
        self.assertTrue(MaterialExchangeSettings.get_solo().is_enabled)
        MaterialExchangeSettings.objects.filter(pk=42).update(is_enabled=False)
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                existing.is_enabled = True
                existing.save(update_fields=["is_enabled"])
                raise RuntimeError("rollback")
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)

    def test_disabled_cycle_stops_before_configuration_or_external_work(self):
        MaterialExchangeSettings.objects.create(pk=42, is_enabled=False)
        with self.assertNumQueries(1):
            run_material_exchange_cycle()

    def test_toggle_preserves_identity_and_repeated_request(self):
        existing = MaterialExchangeSettings.objects.create(pk=42, is_enabled=False)
        user = User.objects.create_user("singleton-manager")
        assign_main_character(user, character_id=1999956101)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="indy_hub", codename="can_manage_material_hub"
            )
        )
        self.client.force_login(user)
        url = reverse("indy_hub:material_exchange_toggle_active")
        for desired in (True, True, False):
            response = self.client.post(url, {"is_active": "on"} if desired else {})
            self.assertEqual(response.status_code, 302)
            existing.refresh_from_db()
            self.assertEqual(existing.is_enabled, desired)
            self.assertEqual(MaterialExchangeSettings.get_solo().pk, 42)
            self.assertEqual(MaterialExchangeSettings.objects.count(), 1)

    def test_toggle_requires_login_and_management_permission(self):
        MaterialExchangeSettings.objects.create(pk=42, is_enabled=False)
        url = reverse("indy_hub:material_exchange_toggle_active")
        self.assertEqual(self.client.post(url, {"is_active": "on"}).status_code, 302)
        user = User.objects.create_user("singleton-member")
        assign_main_character(user, character_id=1999956102)
        self.client.force_login(user)
        self.assertEqual(self.client.post(url, {"is_active": "on"}).status_code, 403)
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)


@override_settings(SOLO_CACHE=None)
class NativeSingletonTransitionRiskTests(TestCase):
    def setUp(self):
        MaterialExchangeSettings.objects.all().delete()
        registry = self.enterContext(isolate_apps("indy_hub"))
        # Django unpickles cached models via its global registry. Point only
        # deserialization at this test registry; native cache behavior is real.
        self.enterContext(patch("django.db.models.base.apps", registry))

        class NativeSettings(SingletonModel, MaterialExchangeSettings):
            class Meta:
                app_label = "indy_hub"
                proxy = True
                default_permissions = ()

        self.native = NativeSettings
        self.addCleanup(caches["default"].delete, self.native.get_cache_key())
        caches["default"].delete(self.native.get_cache_key())

    def test_native_empty_table_reuses_fixed_identity(self):
        self.assertEqual(self.native.get_solo().pk, 1)
        self.assertTrue(self.native.get_solo().is_enabled)
        self.assertEqual(MaterialExchangeSettings.objects.count(), 1)

    def test_native_preserves_disabled_row_at_expected_identity(self):
        MaterialExchangeSettings.objects.create(pk=1, is_enabled=False)
        before = list(MaterialExchangeSettings.objects.values())
        self.assertFalse(self.native.get_solo().is_enabled)
        self.assertEqual(list(MaterialExchangeSettings.objects.values()), before)

    def test_direct_adoption_masks_disabled_row_at_another_identity(self):
        existing = MaterialExchangeSettings.objects.create(pk=42, is_enabled=False)
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
        self.assertTrue(self.native.get_solo().is_enabled)
        existing.refresh_from_db()
        self.assertFalse(existing.is_enabled)
        self.assertEqual(MaterialExchangeSettings.objects.count(), 2)

    def test_native_does_not_reconcile_equivalent_or_conflicting_duplicates(self):
        for later_enabled in (False, True):
            with self.subTest(later_enabled=later_enabled):
                MaterialExchangeSettings.objects.all().delete()
                MaterialExchangeSettings.objects.create(pk=1, is_enabled=False)
                MaterialExchangeSettings.objects.create(pk=42, is_enabled=later_enabled)
                before = list(MaterialExchangeSettings.objects.order_by("pk").values())
                self.assertFalse(self.native.get_solo().is_enabled)
                self.assertEqual(
                    list(MaterialExchangeSettings.objects.order_by("pk").values()),
                    before,
                )

    @override_settings(SOLO_CACHE="default")
    def test_native_cache_cold_warm_and_save(self):
        MaterialExchangeSettings.objects.create(pk=1, is_enabled=False)
        self.assertIsNone(caches["default"].get(self.native.get_cache_key()))
        obj = self.native.get_solo()
        with self.assertNumQueries(0):
            self.assertFalse(self.native.get_solo().is_enabled)
        obj.is_enabled = True
        obj.save(update_fields=["is_enabled", "updated_at"])
        with self.assertNumQueries(0):
            self.assertTrue(self.native.get_solo().is_enabled)
        self.native.clear_cache()
        self.assertTrue(self.native.get_solo().is_enabled)

    @override_settings(SOLO_CACHE="default")
    def test_native_warm_cache_is_stale_after_queryset_update(self):
        MaterialExchangeSettings.objects.create(pk=1, is_enabled=False)
        self.assertFalse(self.native.get_solo().is_enabled)
        self.native.objects.filter(pk=1).update(is_enabled=True)
        self.assertFalse(self.native.get_solo().is_enabled)
        self.assertTrue(MaterialExchangeSettings.get_solo().is_enabled)
        self.native.clear_cache()
        self.assertTrue(self.native.get_solo().is_enabled)

    @override_settings(SOLO_CACHE="default")
    def test_native_save_cache_outlives_database_rollback(self):
        MaterialExchangeSettings.objects.create(pk=1, is_enabled=False)
        obj = self.native.get_solo()
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                obj.is_enabled = True
                obj.save(update_fields=["is_enabled"])
                raise RuntimeError("rollback")
        self.assertFalse(MaterialExchangeSettings.get_solo().is_enabled)
        self.assertTrue(self.native.get_solo().is_enabled)
