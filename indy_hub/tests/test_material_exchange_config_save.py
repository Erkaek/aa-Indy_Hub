# Django
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

# AA Example App
from indy_hub.models import MaterialExchangeConfig
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
