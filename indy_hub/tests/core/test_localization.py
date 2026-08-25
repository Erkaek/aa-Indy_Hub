"""Tests for Indy Hub localization catalogs and template integration."""

from __future__ import annotations

# Standard Library
from html import unescape
from types import SimpleNamespace

# Django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.middleware.locale import LocaleMiddleware
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.utils import translation

User = get_user_model()


@override_settings(
    LANGUAGE_CODE="en-us",
    LANGUAGES=(
        ("en", "English"),
        ("de", "German"),
        ("es", "Spanish"),
        ("it-it", "Italian"),
        ("ja", "Japanese"),
        ("ko-kr", "Korean"),
        ("fr-fr", "French"),
        ("nl-nl", "Dutch"),
        ("pl-pl", "Polish"),
        ("ru", "Russian"),
        ("uk", "Ukrainian"),
        ("zh-hans", "Simplified Chinese"),
    ),
)
class IndyHubLocalizationTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("language_user", password="secret123")
        self.locale_middleware = LocaleMiddleware(lambda request: None)

    def tearDown(self) -> None:
        translation.deactivate_all()

    def _localized_request(self, language: str, *, accept_language="de-DE"):
        request = self.factory.get(
            "/indy_hub/",
            HTTP_ACCEPT_LANGUAGE=accept_language,
        )
        request.user = self.user
        request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = language
        self.locale_middleware.process_request(request)
        return request

    def test_standard_django_locale_is_exposed_to_indy_hub_javascript(self) -> None:
        request = self._localized_request("fr-fr", accept_language="de-DE")

        html = render_to_string(
            "indy_hub/sde_not_ready.html",
            request=request,
        )

        self.assertEqual(request.LANGUAGE_CODE, "fr-fr")
        self.assertIn('window.INDY_HUB_LOCALE = "fr\\u002Dfr"', html)
        self.assertIn('indyHubTranslationMeta.content = "notranslate"', html)
        self.assertIn(
            'document.documentElement.setAttribute("translate", "no")',
            html,
        )

    def test_material_exchange_page_uses_indy_hub_french_catalog(self) -> None:
        request = self._localized_request("fr-fr", accept_language="de-DE")

        rendered = unescape(
            render_to_string(
                "indy_hub/material_exchange/my_orders.html",
                {
                    "page_obj": SimpleNamespace(object_list=[]),
                    "total_sell": 0,
                    "total_buy": 0,
                },
                request=request,
            )
        )

        self.assertIn("Commandes d'achat", rendered)
        self.assertIn("Commandes de vente", rendered)
        self.assertIn("Mes commandes", rendered)
        self.assertNotIn("Buy Orders", rendered)
        self.assertIn('window.INDY_HUB_LOCALE = "fr\\u002Dfr"', rendered)
        self.assertNotIn("dark-mode-adaptive.css", rendered)

    def test_material_exchange_catalogs_cover_all_alliance_auth_languages(
        self,
    ) -> None:
        expected_labels = {
            "de": "Kaufaufträge",
            "es": "Órdenes de compra",
            "fr-fr": "Commandes d'achat",
            "it-it": "Ordini di acquisto",
            "ja": "購入注文",
            "ko-kr": "구매 주문",
            "nl-nl": "Kooporders",
            "pl-pl": "Zlecenia kupna",
            "ru": "Ордера на покупку",
            "uk": "Замовлення на купівлю",
            "zh-hans": "购买订单",
        }

        for language, expected in expected_labels.items():
            with self.subTest(language=language):
                self._localized_request(language)

                self.assertEqual(translation.gettext("Buy Orders"), expected)

    def test_no_indy_hub_specific_language_middleware_is_configured(self) -> None:
        self.assertNotIn(
            "indy_hub.middleware.IndyHubLanguageMiddleware",
            settings.MIDDLEWARE,
        )
