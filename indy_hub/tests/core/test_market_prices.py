"""Public prices through real django-esi with a local spec and HTTP transport."""

# Standard Library
import json
import warnings
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

# Third Party
import httpx2

# Django
from django.core.cache import cache
from django.test import SimpleTestCase

# Alliance Auth
from esi import app_settings
from esi.http_utils import build_timeout
from esi.models import Token
from esi.openapi_clients import ESIClientProvider, EsiOperation, SpecCachingClient

# AA Example App
from indy_hub.services import market_prices
from indy_hub.services.esi_client import ESIClient
from indy_hub.services.industry_copy_estimated_values import (
    build_copy_estimated_item_values,
)
from indy_hub.services.providers import ESI_REQUIRED_OPERATIONS, esi_provider
from indy_hub.tests.core.esi_contract_fixture import openapi_spec
from indy_hub.tests.craft.test_copy_install_eiv import _CursorStub


class PublicMarketPriceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.directory = TemporaryDirectory(prefix="indyhub-prices-contract-")
        self.addCleanup(self.directory.cleanup)
        spec = Path(self.directory.name) / "openapi.json"
        spec.write_text(
            json.dumps(
                openapi_spec(compatibility_date=esi_provider._compatibility_date)
            )
        )
        self.calls = []
        self.status = 200
        self.headers = {}
        self.body = [{"type_id": 34, "adjusted_price": 1.25, "average_price": 2.5}]
        self.raw = None
        self.failure = None
        self.before_response = None

        def respond(request):
            self.calls.append(request)
            if self.failure:
                raise self.failure
            if self.before_response:
                self.before_response()
            kwargs = (
                {"content": self.raw} if self.raw is not None else {"json": self.body}
            )
            return httpx2.Response(
                self.status,
                request=request,
                headers={
                    "Content-Type": "application/json",
                    "Cache-Control": "max-age=7200",
                    **self.headers,
                },
                **kwargs,
            )

        transport = httpx2.MockTransport(respond)

        def session_factory(**kwargs):
            return SpecCachingClient(transport=transport, **kwargs)

        self.enterContext(
            patch("esi.openapi_clients.SpecCachingClient", session_factory)
        )
        self.facade = ESIClient()
        self.facade.provider = ESIClientProvider(
            ua_appname=esi_provider._ua_appname,
            ua_version=esi_provider._ua_version,
            ua_url=esi_provider._ua_url,
            compatibility_date=esi_provider._compatibility_date,
            spec_file=str(spec),
            operations=ESI_REQUIRED_OPERATIONS,
        )
        self.enterContext(patch.object(market_prices, "shared_client", self.facade))
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", DeprecationWarning)

    def test_native_result_preserves_decimal_contract_and_public_identity(self):
        with (
            patch.object(Token.objects, "get_queryset") as select_token,
            patch.object(Token, "refresh") as refresh,
        ):
            result = market_prices.fetch_market_price_references()
        self.assertEqual(
            result,
            {34: {"adjusted_price": Decimal("1.25"), "average_price": Decimal("2.5")}},
        )
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.url.path, "/markets/prices")
        self.assertFalse(request.url.query)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(
            request.headers["X-Compatibility-Date"], esi_provider._compatibility_date
        )
        self.assertIn(esi_provider._ua_appname, request.headers["User-Agent"])
        select_token.assert_not_called()
        refresh.assert_not_called()

    def test_raw_response_keeps_decimal_strings_without_native_float_coercion(self):
        self.body[0]["adjusted_price"] = "1234567890.1234567890123456789"
        result = market_prices.fetch_market_price_references()
        self.assertEqual(
            result[34]["adjusted_price"], Decimal(self.body[0]["adjusted_price"])
        )

    def test_legacy_invalid_rows_missing_fields_and_null_prices_remain_compatible(self):
        self.body = [
            None,
            {"type_id": "bad"},
            {"type_id": 0},
            {"type_id": -1},
            {"type_id": "34", "adjusted_price": None},
            {"type_id": 35},
        ]
        self.assertEqual(
            market_prices.fetch_market_price_references(),
            {
                34: {"adjusted_price": Decimal(0), "average_price": Decimal(0)},
                35: {"adjusted_price": Decimal(0), "average_price": Decimal(0)},
            },
        )
        self.assertEqual(len(self.calls), 1)

    def test_malformed_top_level_json_and_amounts_are_errors_not_cached_success(self):
        cases = [
            ("object", {"unexpected": []}, None),
            ("JSON", [], b"invalid JSON"),
            ("amount", [{"type_id": 34, "adjusted_price": "invalid"}], None),
            ("nonfinite", [{"type_id": 34, "adjusted_price": "NaN"}], None),
        ]
        for label, body, raw in cases:
            with self.subTest(case=label):
                cache.clear()
                self.body, self.raw = body, raw
                with self.assertRaises(market_prices.MarketPriceError):
                    market_prices.fetch_market_price_references()
                self.assertIsNone(cache.get(market_prices.MARKET_PRICES_CACHE_KEY))

    def test_hot_cache_does_not_create_a_native_client_or_use_transport(self):
        expected = {34: {"adjusted_price": Decimal("7"), "average_price": Decimal("8")}}
        cache.set(market_prices.MARKET_PRICES_CACHE_KEY, expected, timeout=3600)
        self.assertIsNone(self.facade.provider._client)
        self.assertEqual(market_prices.fetch_market_price_references(), expected)
        self.assertIsNone(self.facade.provider._client)
        self.assertEqual(self.calls, [])

    def test_business_cache_keeps_exact_key_ttl_and_expiry_forces_new_body(self):
        original_set = cache.set
        with patch.object(cache, "set", wraps=original_set) as writes:
            market_prices.fetch_market_price_references()
        writes_for_prices = [
            call
            for call in writes.call_args_list
            if call.args[0] == market_prices.MARKET_PRICES_CACHE_KEY
        ]
        self.assertEqual(len(writes_for_prices), 1)
        self.assertEqual(writes_for_prices[0].args[2], 3600)
        self.assertEqual(
            market_prices.MARKET_PRICES_CACHE_KEY, "indy_hub:market_prices:esi:v1"
        )
        market_prices.fetch_adjusted_prices([34, "34", 35, None, "bad", -1])
        self.assertEqual(len(self.calls), 1)
        cache.touch(market_prices.MARKET_PRICES_CACHE_KEY, timeout=0)
        self.body[0]["adjusted_price"] = 9
        self.assertEqual(
            market_prices.fetch_adjusted_prices([34])[34]["adjusted_price"], Decimal(9)
        )
        self.assertEqual(len(self.calls), 2)
        for request in self.calls:
            self.assertNotIn("If-None-Match", request.headers)
            self.assertNotIn("If-Modified-Since", request.headers)

    def test_adjusted_price_subset_preserves_int_keys_and_unknown_type_zeros(self):
        self.assertEqual(
            market_prices.fetch_adjusted_prices(["34", 34, 35, None, "bad", 0]),
            {
                34: {
                    "adjusted_price": Decimal("1.25"),
                    "average_price": Decimal("2.5"),
                },
                35: {"adjusted_price": Decimal(0), "average_price": Decimal(0)},
            },
        )
        self.assertEqual(len(self.calls), 1)

    def test_unexpected_304_without_business_data_is_controlled_and_not_replayed(self):
        self.status, self.raw = 304, b""
        with self.assertRaises(market_prices.MarketPriceError) as caught:
            market_prices.fetch_market_price_references()
        self.assertEqual(caught.exception.status_code, 304)
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIsNone(cache.get(market_prices.MARKET_PRICES_CACHE_KEY))
        self.assertEqual(len(self.calls), 1)

    def test_304_can_reuse_concurrently_populated_valid_business_cache(self):
        expected = {
            34: {"adjusted_price": Decimal("9"), "average_price": Decimal("10")}
        }
        self.status, self.raw = 304, b""
        self.before_response = lambda: cache.set(
            market_prices.MARKET_PRICES_CACHE_KEY, expected, timeout=3600
        )
        self.assertEqual(market_prices.fetch_market_price_references(), expected)
        self.assertEqual(len(self.calls), 1)

    def test_limits_remain_identifiable_and_stop_subsequent_wire_access(self):
        for status in (420, 429):
            with self.subTest(status=status):
                cache.clear()
                self.calls.clear()
                self.status, self.body = status, {"error": "Synthetic limit"}
                self.headers = {"Retry-After": "17", "X-Ratelimit-Reset": "17"}
                for attempt in range(2):
                    with self.assertRaises(market_prices.MarketPriceError) as caught:
                        market_prices.fetch_market_price_references()
                    # A repeated call is stopped by the existing facade guard,
                    # whose scoped cooldown uses ESIErrorLimitException (420).
                    self.assertEqual(
                        caught.exception.status_code, status if attempt == 0 else 420
                    )
                    self.assertGreater(caught.exception.retry_after, 0)
                self.assertEqual(len(self.calls), 1)
                self.assertIsNone(cache.get(market_prices.MARKET_PRICES_CACHE_KEY))

    def test_server_errors_use_native_retry_only_and_keep_global_cooldown(self):
        for status in (500, 503):
            with self.subTest(status=status):
                cache.clear()
                self.calls.clear()
                self.status, self.body = status, {"error": "Synthetic outage"}
                self.headers = {"Retry-After": "13"}
                original = EsiOperation._make_request
                with patch.object(
                    EsiOperation, "_make_request", autospec=True, side_effect=original
                ) as logical:
                    with self.assertRaises(market_prices.MarketPriceError) as caught:
                        market_prices.fetch_market_price_references()
                logical.assert_called_once()
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.retry_after, 13)
                wires = len(self.calls)
                self.assertGreater(wires, 0)
                with self.assertRaises(market_prices.MarketPriceError):
                    market_prices.fetch_market_price_references()
                self.assertEqual(len(self.calls), wires)
                self.assertIsNone(cache.get(market_prices.MARKET_PRICES_CACHE_KEY))

    def test_network_failure_is_not_an_empty_price_table(self):
        self.failure = httpx2.ConnectError("Synthetic disconnected transport")
        with self.assertRaises(market_prices.MarketPriceError) as caught:
            market_prices.fetch_market_price_references()
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIsNone(cache.get(market_prices.MARKET_PRICES_CACHE_KEY))

    def test_html_error_bodies_keep_limit_status_and_reset_without_a_replay(self):
        for status in (420, 429, 503):
            with self.subTest(status=status):
                cache.clear()
                self.calls.clear()
                self.status, self.raw = (
                    status,
                    b"<html>Synthetic gateway refusal</html>",
                )
                self.headers = {
                    "Content-Type": "text/html",
                    "Retry-After": "13",
                    "X-Ratelimit-Reset": "13",
                }
                with self.assertRaises(market_prices.MarketPriceError) as caught:
                    market_prices.fetch_market_price_references()
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.retry_after, 13)
                self.assertEqual(len(self.calls), 1)
                self.assertIsNone(cache.get(market_prices.MARKET_PRICES_CACHE_KEY))

    def test_timeout_is_explicitly_deprecated_and_never_an_openapi_parameter(self):
        native_timeout = build_timeout().as_dict()
        configured = app_settings.ESI_REQUESTS_READ_TIMEOUT
        with self.assertWarnsMessage(DeprecationWarning, "does not override"):
            market_prices.fetch_market_price_references(timeout=1)
        self.assertEqual(self.calls[0].extensions["timeout"], native_timeout)
        self.assertNotIn("timeout", str(self.calls[0].url))
        self.assertEqual(app_settings.ESI_REQUESTS_READ_TIMEOUT, configured)

    def test_native_prices_feed_the_existing_manufacturing_basket_eiv(self):
        self.body = [
            {"type_id": 34, "adjusted_price": "10", "average_price": "11"},
            {"type_id": 36, "adjusted_price": "22.535", "average_price": "22"},
            {"type_id": 222, "adjusted_price": "0.1"},
        ]
        cursor = _CursorStub([(220, 222)], [(220, 34, 1000), (220, 36, 200)])
        result = build_copy_estimated_item_values(
            [SimpleNamespace(type_id=220, runs_requested=20)],
            connection=SimpleNamespace(cursor=lambda: cursor),
            fetch_adjusted_prices_fn=market_prices.fetch_adjusted_prices,
            market_price_error_type=market_prices.MarketPriceError,
        )
        self.assertEqual(result[220]["unit_value"], Decimal("14507"))
        self.assertEqual(result[220]["estimated_item_value"], Decimal("290140"))
        self.assertEqual(result[220]["source"], "adjusted_price")
        self.assertEqual(len(self.calls), 1)
