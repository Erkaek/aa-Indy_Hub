"""Delay units, cooldown compatibility and retry ownership regressions."""

# Standard Library
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

# Third Party
from celery.exceptions import Reject, Retry

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone as django_time

# Alliance Auth
from esi.exceptions import ESIErrorLimitException

# AA Example App
from indy_hub.models import (
    ESIContract,
    ESIContractItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
)
from indy_hub.services.esi_client import (
    ESIClient,
    ESIClientError,
    get_rate_limit_reset_seconds,
    get_retry_after_seconds,
)
from indy_hub.services.material_exchange_assets import get_sell_assets_refresh_progress
from indy_hub.tasks import material_exchange
from indy_hub.tasks import material_exchange_contracts as contracts
from indy_hub.tests.fixtures import create_raw_fixture


class RetryDelayTests(SimpleTestCase):
    def test_native_resets_are_durations_rounded_up(self):
        for raw, expected in [(1.9, 2), ("12.1", 13), (37, 37)]:
            with self.subTest(raw=raw):
                self.assertEqual(
                    get_rate_limit_reset_seconds(ESIErrorLimitException(reset=raw)),
                    expected,
                )

    def test_nonfinite_native_resets_use_conservative_fallback(self):
        for raw in [None, "bad", "NaN", "inf", float("inf"), "1e308", -1]:
            with self.subTest(raw=raw):
                self.assertEqual(
                    get_rate_limit_reset_seconds(
                        ESIErrorLimitException(reset=raw), fallback=60
                    ),
                    60,
                )

    def test_retry_after_http_date_uses_remaining_seconds(self):
        timestamp = datetime(2026, 11, 7, tzinfo=timezone.utc).timestamp()
        error = SimpleNamespace(
            headers={"Retry-After": "Sat, 07 Nov 2026 00:00:10 GMT"}
        )
        with patch("indy_hub.services.esi_client.time.time", return_value=timestamp):
            self.assertEqual(get_retry_after_seconds(error, fallback=60), 10)

    def test_malformed_header_does_not_hide_a_valid_reset(self):
        error = SimpleNamespace(
            headers={"Retry-After": "bad", "X-Ratelimit-Reset": "12.1"}
        )
        self.assertEqual(get_retry_after_seconds(error, fallback=60), 13)

    def test_outage_delays_keep_existing_bounds(self):
        for raw, expected in [("NaN", 60), ("inf", 60), ("1.2", 5), ("3600", 900)]:
            with self.subTest(raw=raw):
                error = SimpleNamespace(headers={"Retry-After": raw})
                self.assertEqual(
                    get_retry_after_seconds(error, fallback=60, minimum=5, maximum=900),
                    expected,
                )


class CooldownTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.facade = ESIClient()

    def test_shorter_outage_does_not_shorten_an_existing_cooldown(self):
        with patch("indy_hub.services.esi_client.time.time", return_value=1000):
            self.facade._set_tranquility_cooldown(60)
            self.facade._set_tranquility_cooldown(5)
            self.assertEqual(self.facade._tranquility_cooldown_seconds_remaining(), 60)

    def test_old_group_deadline_and_subject_isolation_are_preserved(self):
        endpoint = "/characters/900001/skills/"
        cache.set(
            self.facade._rate_limit_meta_cache_key(endpoint),
            {"group": "test-group", "max_tokens": 100, "window_seconds": 60},
            86400,
        )
        cache.set(
            self.facade._rate_limit_cooldown_cache_key("test-group", "char:900001"),
            "1010.5",
            60,
        )
        with patch("indy_hub.services.esi_client.time.time", return_value=1000):
            with self.assertRaises(ESIErrorLimitException) as caught:
                self.facade._enforce_rate_limit_cooldown(
                    endpoint=endpoint, throttle_key="char:900001"
                )
            self.assertEqual(caught.exception.reset, 11)
            self.facade._enforce_rate_limit_cooldown(
                endpoint=endpoint, throttle_key="char:900002"
            )
        with patch("indy_hub.services.esi_client.time.time", return_value=1011):
            self.facade._enforce_rate_limit_cooldown(
                endpoint=endpoint, throttle_key="char:900001"
            )

    def test_interval_parser_matches_native_units(self):
        for value, expected in [
            ("100/15m", (100, 900)),
            ("100/1h", (100, 3600)),
            ("100/s", (100, 1)),
            ("100/1d", (100, 86400)),
        ]:
            with self.subTest(value=value):
                self.assertEqual(self.facade._parse_rate_limit_limit(value), expected)

    def test_deferred_asset_progress_outlives_the_normal_worker_timeout(self):
        now = django_time.now()
        state = {
            "running": True,
            "finished": False,
            "error": "rate_limited",
            "started_at": (now - timedelta(minutes=10)).timestamp(),
            "last_progress_at": (now - timedelta(minutes=10)).timestamp(),
            "retry_at": (now + timedelta(minutes=5)).timestamp(),
        }
        key = material_exchange._me_sell_assets_progress_key(900001)
        cache.set(key, state, 1200)
        self.assertEqual(get_sell_assets_refresh_progress(900001), state)
        with patch(
            "indy_hub.services.material_exchange_assets.timezone.now",
            return_value=now + timedelta(minutes=9),
        ):
            recovered = get_sell_assets_refresh_progress(900001)
        self.assertFalse(recovered["running"])
        self.assertEqual(recovered["error"], "timeout")


class CorporationCacheRetryTests(TestCase):
    def _run_with_error(self, error, expected_delay):
        task = material_exchange.refresh_corp_assets_cached
        task.push_request(
            id="synthetic-corp-cache",
            called_directly=False,
            is_eager=False,
            args=(980001,),
            kwargs={},
            retries=0,
        )
        try:
            with (
                patch.object(
                    material_exchange, "force_refresh_corp_assets", side_effect=error
                ),
                patch.object(task, "update_state"),
                patch.object(
                    task, "apply_async", return_value=SimpleNamespace(id="retry")
                ) as publication,
            ):
                with self.assertRaises(Retry):
                    task.run(980001)
            publication.assert_called_once()
            self.assertEqual(publication.call_args.kwargs["countdown"], expected_delay)
        finally:
            task.pop_request()

    def test_protocol_delay_has_one_retry_owner(self):
        self._run_with_error(ESIErrorLimitException(reset=12.1), 13)

    def test_outage_delay_has_one_retry_owner(self):
        self._run_with_error(
            ESIClientError("synthetic outage", status_code=503, retry_after=37), 37
        )

    def test_failed_retry_publication_has_no_autoretry_fallback(self):
        task = material_exchange.refresh_corp_assets_cached
        task.push_request(
            id="synthetic-corp-publish-failure",
            called_directly=False,
            is_eager=False,
            args=(980001,),
            kwargs={},
            retries=0,
        )
        try:
            with (
                patch.object(
                    material_exchange,
                    "force_refresh_corp_assets",
                    side_effect=ESIErrorLimitException(reset=12),
                ),
                patch.object(task, "update_state"),
                patch.object(
                    task,
                    "apply_async",
                    side_effect=ConnectionError("synthetic broker failure"),
                ) as publication,
            ):
                with self.assertRaises(Reject):
                    task.run(980001)
            publication.assert_called_once()
        finally:
            task.pop_request()


class ContractInterruptionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("synthetic-contract-limits")
        # Avoid configuration-save dispatch: this is a disposable fixture.
        self.config = create_raw_fixture(
            MaterialExchangeConfig(corporation_id=980001, structure_id=60003760)
        )
        self.enabled = patch.object(
            contracts.MaterialExchangeSettings,
            "get_solo",
            return_value=SimpleNamespace(is_enabled=True),
        )
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def test_cycle_stops_after_sync_publishes_its_retry(self):
        MaterialExchangeSellOrder.objects.create(config=self.config, seller=self.user)
        with (
            patch.object(
                contracts,
                "_sync_contracts_for_corporation",
                side_effect=ESIErrorLimitException(reset=12.1),
            ),
            patch.object(contracts.sync_esi_contracts, "apply_async") as publication,
            patch.object(contracts, "validate_material_exchange_sell_orders") as sell,
            patch.object(contracts, "validate_material_exchange_buy_orders") as buy,
            patch.object(
                contracts, "check_completed_material_exchange_contracts"
            ) as completion,
        ):
            result = contracts.run_material_exchange_cycle()
        publication.assert_called_once_with(countdown=13)
        self.assertEqual(result, {"status": "deferred", "retry_in": 13})
        sell.assert_not_called()
        buy.assert_not_called()
        completion.assert_not_called()

    def test_contract_item_limit_preserves_rows_and_freshness(self):
        now = django_time.now()
        for contract_id in [800001, 800002]:
            contract = ESIContract.objects.create(
                contract_id=contract_id,
                corporation_id=self.config.corporation_id,
                issuer_id=900001,
                issuer_corporation_id=980001,
                assignee_id=980001,
                contract_type="item_exchange",
                status="outstanding",
                title="INDY fixture",
                date_issued=now - timedelta(days=1),
                date_expired=now + timedelta(days=1),
            )
            ESIContractItem.objects.create(
                contract=contract, record_id=1, type_id=34, quantity=10
            )
        ESIContract.objects.update(last_synced=now - timedelta(hours=1))
        before_contracts = list(ESIContract.objects.order_by("pk").values())
        before_items = list(ESIContractItem.objects.order_by("pk").values())
        with (
            patch.object(contracts, "_get_character_for_scope", return_value=900001),
            patch.object(
                contracts.shared_client,
                "fetch_corporation_contracts",
                return_value=[
                    {
                        "contract_id": 800001,
                        "type": "item_exchange",
                        "status": "outstanding",
                        "title": "INDY fixture",
                        "date_issued": now,
                        "date_expired": now + timedelta(days=1),
                    }
                ],
            ),
            patch.object(
                contracts.shared_client,
                "fetch_corporation_contract_items",
                side_effect=ESIErrorLimitException(reset=37),
            ),
        ):
            with self.assertRaises(ESIErrorLimitException):
                contracts._sync_contracts_for_corporation(self.config.corporation_id)
        self.assertEqual(
            list(ESIContract.objects.order_by("pk").values()), before_contracts
        )
        self.assertEqual(
            list(ESIContractItem.objects.order_by("pk").values()), before_items
        )
