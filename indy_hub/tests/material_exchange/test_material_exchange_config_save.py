# Standard Library
import json
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import transaction
from django.test import RequestFactory, TestCase
from django.utils import timezone

# AA Example App
from indy_hub.models import MaterialExchangeAcceptedLocation, MaterialExchangeConfig
from indy_hub.views.material_exchange_config import _handle_config_save


class MaterialExchangeConfigSaveCheckboxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("config-admin", password="testpass123")
        self.factory = RequestFactory()
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=123456,
            structure_id=60000001,
            structure_name="Test Structure",
            hangar_division=1,
            sell_markup_percent="0.00",
            sell_markup_base="buy",
            buy_markup_percent="5.00",
            buy_markup_base="buy",
            enforce_jita_price_bounds=True,
            notify_admins_on_sell_anomaly=True,
            is_active=True,
        )

    def _build_request(self, post_data):
        request = self.factory.post("/indy-hub/material-exchange/config/", post_data)
        request.user = self.user

        session_middleware = SessionMiddleware(lambda _request: None)
        session_middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))

        return request

    def _base_post_data(self):
        return {
            "corporation_id": str(self.config.corporation_id),
            "structure_id": str(self.config.structure_id),
            "structure_name": self.config.structure_name,
            "hangar_division": str(self.config.hangar_division),
            "sell_markup_percent": "0",
            "sell_markup_base": "buy",
            "buy_markup_percent": "5",
            "buy_markup_base": "buy",
        }

    def test_unchecked_notification_checkbox_is_saved_false(self):
        post_data = self._base_post_data()

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertFalse(self.config.notify_admins_on_sell_anomaly)

    def test_unchecked_enforce_bounds_checkbox_is_saved_false(self):
        post_data = self._base_post_data()

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertFalse(self.config.enforce_jita_price_bounds)

    def test_checked_checkboxes_are_saved_true(self):
        self.config.notify_admins_on_sell_anomaly = False
        self.config.enforce_jita_price_bounds = False
        self.config.save(
            update_fields=[
                "notify_admins_on_sell_anomaly",
                "enforce_jita_price_bounds",
            ]
        )

        post_data = self._base_post_data()
        post_data["notify_admins_on_sell_anomaly"] = "on"
        post_data["enforce_jita_price_bounds"] = "on"

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertTrue(self.config.notify_admins_on_sell_anomaly)
        self.assertTrue(self.config.enforce_jita_price_bounds)

    @patch("indy_hub.views.material_exchange_config._resolve_specific_type_ids")
    def test_fixed_member_sell_price_override_is_saved(self, mock_resolve) -> None:
        mock_resolve.side_effect = lambda tokens: (
            ([74534], []) if tokens else ([], [])
        )
        post_data = self._base_post_data()
        post_data["sell_price_overrides_text"] = "Ducinium II-Grade = 4500,25"

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.sell_price_overrides, {"74534": "4500.25"})

    def test_is_active_keeps_existing_value_when_field_missing(self):
        self.config.is_active = False
        self.config.save(update_fields=["is_active"])

        post_data = self._base_post_data()
        post_data["notify_admins_on_sell_anomaly"] = "on"
        post_data["enforce_jita_price_bounds"] = "on"

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertFalse(self.config.is_active)

    @patch("indy_hub.views.material_exchange_config._get_token_for_corp")
    @patch("indy_hub.views.material_exchange_config.resolve_structure_names")
    def test_structure_name_is_resolved_server_side(
        self, mock_resolve_structure_names, mock_get_token_for_corp
    ):
        mock_get_token_for_corp.return_value = None
        mock_resolve_structure_names.return_value = {
            self.config.structure_id: "Authoritative Structure Name"
        }

        post_data = self._base_post_data()
        post_data["structure_name"] = "Client Supplied Name"

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.structure_name, "Authoritative Structure Name")

    @patch("indy_hub.views.material_exchange_config._get_token_for_corp")
    @patch("indy_hub.views.material_exchange_config.resolve_structure_names")
    def test_structure_name_does_not_trust_client_value_when_resolution_fails(
        self, mock_resolve_structure_names, mock_get_token_for_corp
    ):
        mock_get_token_for_corp.return_value = None
        mock_resolve_structure_names.side_effect = RuntimeError("ESI unavailable")

        post_data = self._base_post_data()
        post_data["structure_name"] = "Client Supplied Name"

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.structure_name, "Test Structure")

    @patch("indy_hub.views.material_exchange_config._resolve_specific_type_ids")
    @patch("indy_hub.views.material_exchange_config._get_token_for_corp")
    @patch("indy_hub.views.material_exchange_config.resolve_structure_names")
    def test_save_persists_multiple_locations_and_specific_type_filters(
        self,
        mock_resolve_structure_names,
        mock_get_token_for_corp,
        mock_resolve_specific_type_ids,
    ):
        mock_get_token_for_corp.return_value = None
        mock_resolve_structure_names.return_value = {
            60000001: "Primary Structure",
            60000002: "Secondary Structure",
        }
        mock_resolve_specific_type_ids.side_effect = [([34], []), ([35, 36], [])]

        post_data = self._base_post_data()
        post_data["accepted_locations_json"] = json.dumps(
            [
                {
                    "structure_id": 60000001,
                    "structure_name": "Wrong Primary",
                    "hangar_division": 1,
                },
                {
                    "structure_id": 60000002,
                    "structure_name": "Wrong Secondary",
                    "hangar_division": 3,
                },
            ]
        )
        post_data["allowed_type_ids_buy_text"] = "Tritanium"
        post_data["allowed_type_ids_sell_text"] = "Pyerite\nMexallon"

        request = self._build_request(post_data)
        response = _handle_config_save(request, self.config)

        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.structure_id, 60000001)
        self.assertEqual(self.config.structure_name, "Primary Structure")
        self.assertEqual(self.config.hangar_division, 1)
        self.assertEqual(self.config.allowed_type_ids_buy, [34])
        self.assertEqual(self.config.allowed_type_ids_sell, [35, 36])

        locations = list(
            MaterialExchangeAcceptedLocation.objects.filter(config=self.config)
            .order_by("sort_order", "id")
            .values_list("structure_id", "structure_name", "hangar_division")
        )
        self.assertEqual(
            locations,
            [
                (60000001, "Primary Structure", 1),
                (60000002, "Secondary Structure", 3),
            ],
        )

    def _save_locations(self, locations, *, corporation_id=None):
        post_data = self._base_post_data()
        post_data["accepted_locations_json"] = json.dumps(locations)
        if corporation_id is not None:
            post_data["corporation_id"] = str(corporation_id)
        with (
            patch(
                "indy_hub.views.material_exchange_config._get_token_for_corp",
                return_value=None,
            ),
            patch(
                "indy_hub.views.material_exchange_config.resolve_structure_names",
                return_value={},
            ),
        ):
            return _handle_config_save(self._build_request(post_data), self.config)

    @patch("indy_hub.tasks.material_exchange._sync_stock_impl")
    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_new_configuration_queues_only_after_commit(self, publish, stock_impl):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                MaterialExchangeConfig.objects.create(
                    corporation_id=123456, structure_id=60000003, hangar_division=1
                )
                publish.assert_not_called()
                stock_impl.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        publish.assert_called_once_with(retry=False)
        stock_impl.assert_not_called()

    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_rollback_discards_refresh(self, publish):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesMessage(RuntimeError, "rollback"):
                with transaction.atomic():
                    MaterialExchangeConfig.objects.create(
                        corporation_id=123456, structure_id=60000003, hangar_division=1
                    )
                    raise RuntimeError("rollback")
        self.assertEqual(callbacks, [])
        publish.assert_not_called()

    @patch("indy_hub.tasks.material_exchange._sync_stock_impl")
    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_secondary_location_refresh_sees_all_saved_locations(
        self, publish, stock_impl
    ):
        seen_locations = []

        def observe_publication(**kwargs):
            seen_locations.extend(
                self.config.accepted_locations.order_by("sort_order").values_list(
                    "structure_id", flat=True
                )
            )

        publish.side_effect = observe_publication
        with self.captureOnCommitCallbacks(execute=True):
            response = self._save_locations(
                [
                    {"structure_id": 60000001, "hangar_division": 1},
                    {"structure_id": 60000002, "hangar_division": 3},
                ]
            )
            publish.assert_not_called()
            stock_impl.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(seen_locations, [60000001, 60000002])
        publish.assert_called_once_with(retry=False)
        stock_impl.assert_not_called()

    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_primary_location_change_queues_one_refresh(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            response = self._save_locations(
                [
                    {"structure_id": 60000004, "hangar_division": 2},
                    {"structure_id": 60000005, "hangar_division": 3},
                ]
            )
            publish.assert_not_called()
        self.assertEqual(response.status_code, 302)
        publish.assert_called_once_with(retry=False)

    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_corporation_and_secondary_location_change_do_not_duplicate_refresh(
        self, publish
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = self._save_locations(
                [
                    {"structure_id": 60000001, "hangar_division": 1},
                    {"structure_id": 60000002, "hangar_division": 3},
                ],
                corporation_id=654321,
            )
        self.assertEqual(response.status_code, 302)
        publish.assert_called_once_with(retry=False)

    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_timestamp_save_does_not_reuse_creation_refresh_flag(self, publish):
        self.config.last_stock_sync = timezone.now()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.config.save(update_fields=["last_stock_sync"])
        self.assertEqual(callbacks, [])
        publish.assert_not_called()

    @patch("indy_hub.tasks.material_exchange._sync_stock_impl")
    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_stock.apply_async")
    def test_broker_failure_preserves_configuration_without_inline_fallback(
        self, publish, stock_impl
    ):
        publish.side_effect = RuntimeError("broker unavailable")
        with self.captureOnCommitCallbacks(execute=True):
            response = self._save_locations(
                [
                    {"structure_id": 60000006, "hangar_division": 2},
                ]
            )
        self.assertEqual(response.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.structure_id, 60000006)
        publish.assert_called_once_with(retry=False)
        stock_impl.assert_not_called()

    @patch("indy_hub.tasks.material_exchange.sync_material_exchange_prices.run")
    @patch(
        "indy_hub.tasks.material_exchange.get_corp_assets_cached",
        return_value=([], False),
    )
    def test_worker_stock_sync_keeps_single_price_follow_up(self, assets, prices):
        # AA Example App
        from indy_hub.tasks.material_exchange import sync_material_exchange_stock

        sync_material_exchange_stock()
        prices.assert_called_once_with()
