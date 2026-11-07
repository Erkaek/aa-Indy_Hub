"""Native pagination, cache costs and destructive snapshot boundaries, offline."""

# Standard Library
from datetime import timedelta
from email.utils import format_datetime
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token
from esi.openapi_clients import EsiOperation

# AA Example App
from indy_hub.models import Blueprint, CachedCharacterAsset
from indy_hub.services.esi_client import ESIClientError, ESIUnmodifiedError
from indy_hub.tests.core import test_esi_client_contract as contract


class NativePaginationSnapshotTests(TestCase):
    setUp = contract.EsiClientNativeContractTests.setUp
    response = contract.EsiClientNativeContractTests.response
    make_token = contract.EsiClientNativeContractTests.make_token

    def prepare_pages(self, *, pages=3, expires=True, etags=False):
        self.modified = format_datetime(timezone.now(), usegmt=True)
        self.responses = 0

        def handler(request):
            self.responses += 1
            page = int(request.url.params.get("page", "1"))
            headers = {"X-Pages": str(pages), "Last-Modified": self.modified}
            if not expires:
                headers["Cache-Control"] = "max-age=0"
            if etags:
                headers["ETag"] = f'"page-{page}"'
                if request.headers.get("If-None-Match") == headers["ETag"]:
                    return self.response(request, status=304, headers=headers)
            return self.response(
                request,
                payload=[self.blueprint(900010 + page)],
                headers=headers,
            )

        self.handler = handler
        return handler

    @staticmethod
    def blueprint(item_id):
        return {
            "item_id": item_id,
            "type_id": 34,
            "location_id": 60003760,
            "location_flag": "Hangar",
            "quantity": -1,
            "material_efficiency": 10,
            "time_efficiency": 20,
            "runs": -1,
        }

    def fetch(self, **kwargs):
        return self.facade.fetch_character_blueprints(self.token.character_id, **kwargs)

    def test_reuses_the_native_last_page_instead_of_probing_it_again(self):
        self.prepare_pages()
        original = EsiOperation.result
        with patch.object(
            EsiOperation, "result", autospec=True, side_effect=original
        ) as calls:
            result = self.fetch()
        self.assertEqual([row["item_id"] for row in result], [900011, 900012, 900013])
        self.assertEqual(calls.call_count, 5)  # Three native pages, two cache probes.
        self.assertEqual(len(self.wire_calls), 3)

    def test_force_refresh_rejects_mixed_last_modified(self):
        original = self.prepare_pages(pages=2)

        def mixed(request):
            response = original(request)
            if request.url.params.get("page") == "1":
                response.headers["Last-Modified"] = format_datetime(
                    timezone.now() - timedelta(hours=1), usegmt=True
                )
            return response

        self.handler = mixed
        with self.assertRaises(ESIClientError):
            self.fetch(force_refresh=True)

    def test_disabled_cache_still_rejects_mixed_last_modified(self):
        original = self.prepare_pages(pages=2)

        def mixed(request):
            response = original(request)
            if request.url.params.get("page") == "1":
                response.headers["Last-Modified"] = format_datetime(
                    timezone.now() - timedelta(hours=1), usegmt=True
                )
            return response

        self.handler = mixed
        with patch("esi.openapi_clients.app_settings.ESI_CACHE_RESPONSE", False):
            with self.assertRaises(ESIClientError):
                self.fetch()

    def test_probe_cannot_validate_newer_payload_instead_of_the_returned_page(self):
        original = self.prepare_pages(pages=2, expires=False)

        def drift(request):
            response = original(request)
            # The original first page was older, but a later probe sees a new
            # page with the last page's date. Comparing dates alone is unsafe.
            if len(self.wire_calls) == 1:
                response.headers["Last-Modified"] = format_datetime(
                    timezone.now() - timedelta(hours=1), usegmt=True
                )
            elif request.url.params.get("page") == "1":
                response = self.response(
                    request,
                    payload=[self.blueprint(999999)],
                    headers={
                        "X-Pages": "2",
                        "Last-Modified": self.modified,
                        "Cache-Control": "max-age=0",
                    },
                )
            return response

        self.handler = drift
        with self.assertRaises(ESIClientError):
            self.fetch()

    def test_missing_multi_page_date_is_uncertainty_not_success(self):
        original = self.prepare_pages(pages=2)

        def missing(request):
            response = original(request)
            response.headers.pop("Last-Modified", None)
            return response

        self.handler = missing
        with self.assertRaises(ESIClientError):
            self.fetch()

    def test_nonpositive_page_count_cannot_authorize_an_empty_snapshot(self):
        for count in ["0", "-1"]:
            with self.subTest(count=count):
                cache.clear()
                self.handler = lambda request: self.response(
                    request, payload=[], headers={"X-Pages": count}
                )
                with self.assertRaises(ESIClientError):
                    self.fetch()

    def test_shrinking_page_count_cannot_truncate_the_native_result(self):
        original = self.prepare_pages(pages=3)

        def shrinking(request):
            response = original(request)
            if request.url.params.get("page") == "2":
                response.headers["X-Pages"] = "1"
            return response

        self.handler = shrinking
        with self.assertRaises(ESIClientError):
            self.fetch()

    def test_measured_native_costs_keep_http_requests_distinct_from_logical_calls(self):
        scenarios = [
            ("single", 1, True, False, True, 1, 1),
            ("cold", 3, True, False, True, 5, 3),
            ("no-store", 3, False, False, True, 5, 5),
            ("force", 3, True, True, True, 5, 3),
            ("disabled", 3, True, False, False, 5, 5),
            ("warm", 3, True, False, True, 5, 0),
            ("expired", 3, True, False, True, 5, 3),
        ]
        for (
            name,
            pages,
            expires,
            force,
            enabled,
            logical_count,
            http_count,
        ) in scenarios:
            with self.subTest(scenario=name):
                cache.clear()
                self.wire_calls.clear()
                self.prepare_pages(pages=pages, expires=expires)
                if name in {"warm", "expired"}:
                    created = []
                    original_set = cache.set

                    def save(key, value, *args, **kwargs):
                        if hasattr(value, "status_code"):
                            created.append(key)
                        return original_set(key, value, *args, **kwargs)

                    with patch.object(cache, "set", side_effect=save):
                        self.fetch()
                    if name == "expired":
                        self.assertEqual(len(created), pages)
                        for key in created:
                            cache.touch(key, timeout=0)
                    self.wire_calls.clear()
                original = EsiOperation.result
                original_get = cache.get
                with (
                    patch(
                        "esi.openapi_clients.app_settings.ESI_CACHE_RESPONSE", enabled
                    ),
                    patch.object(
                        EsiOperation, "result", autospec=True, side_effect=original
                    ) as logical,
                    patch.object(cache, "get", side_effect=original_get) as reads,
                ):
                    result = self.fetch(force_refresh=force)
                print(
                    f"PAGINATION_COST {name}: logical={logical.call_count} cache_reads={reads.call_count} mock_http={len(self.wire_calls)}"
                )
                self.assertEqual(len(result), pages)
                self.assertEqual(logical.call_count, logical_count)
                self.assertEqual(len(self.wire_calls), http_count)

    def test_native_all_304_keeps_the_existing_blueprint_snapshot(self):
        self.prepare_pages(pages=2, etags=True)
        self.fetch()
        row = Blueprint.objects.create(
            owner_user=self.user,
            character_id=self.token.character_id,
            item_id=900011,
            location_id=60003760,
            type_id=34,
            quantity=-1,
            material_efficiency=10,
        )
        before = Blueprint.objects.values().get(pk=row.pk)
        original = EsiOperation.result
        self.wire_calls.clear()
        with patch.object(
            EsiOperation, "result", autospec=True, side_effect=original
        ) as calls:
            with self.assertRaises(ESIUnmodifiedError):
                self.fetch()
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(self.wire_calls, [])
        self.assertEqual(Blueprint.objects.values().get(pk=row.pk), before)

    def test_native_mixed_304_refetch_keeps_every_page(self):
        original = self.prepare_pages(pages=2, etags=True)
        created = []
        original_set = cache.set

        def save(key, value, *args, **kwargs):
            if hasattr(value, "url") and value.url.params.get("page") == "2":
                created.append(key)
            return original_set(key, value, *args, **kwargs)

        with patch.object(cache, "set", side_effect=save):
            self.fetch()
        self.assertEqual(len(created), 1)
        cache.touch(created[0], timeout=0)

        def changed(request):
            if request.url.params.get("page") == "2":
                return self.response(
                    request,
                    payload=[self.blueprint(900099)],
                    headers={
                        "X-Pages": "2",
                        "Last-Modified": self.modified,
                        "ETag": '"page-2-new"',
                    },
                )
            return original(request)

        self.handler = changed
        self.wire_calls.clear()
        native = EsiOperation.result
        with patch.object(
            EsiOperation, "result", autospec=True, side_effect=native
        ) as calls:
            result = self.fetch()
        self.assertEqual([row["item_id"] for row in result], [900011, 900099])
        self.assertEqual(calls.call_count, 5)
        self.assertEqual(len(self.wire_calls), 1)

    def test_multi_page_probe_metadata_and_unexpected_304_are_not_success(self):
        for defect in ["missing-date", "bad-date", "changed-count", "304"]:
            with self.subTest(defect=defect):
                cache.clear()
                self.wire_calls.clear()
                original = self.prepare_pages(pages=2, expires=False)

                def broken(request):
                    response = original(request)
                    if len(self.wire_calls) > 2:
                        if defect == "missing-date":
                            response.headers.pop("Last-Modified")
                        elif defect == "bad-date":
                            response.headers["Last-Modified"] = "bad"
                        elif defect == "changed-count":
                            response.headers["X-Pages"] = "3"
                        else:
                            return self.response(
                                request, status=304, headers={"X-Pages": "2"}
                            )
                    return response

                self.handler = broken
                with self.assertRaises(ESIClientError):
                    self.fetch()

    def test_probe_page_size_cannot_repartition_the_retained_last_page(self):
        original = self.prepare_pages(pages=2, expires=False)

        def shifted(request):
            response = original(request)
            if request.url.params.get("page") == "2":
                return self.response(
                    request,
                    payload=[self.blueprint(900012), self.blueprint(900013)],
                    headers={
                        "X-Pages": "2",
                        "Last-Modified": self.modified,
                        "Cache-Control": "max-age=0",
                    },
                )
            if len(self.wire_calls) == 1:
                response.headers["Last-Modified"] = format_datetime(
                    timezone.now() - timedelta(hours=1), usegmt=True
                )
            else:
                return self.response(
                    request,
                    payload=[self.blueprint(900011), self.blueprint(900012)],
                    headers={
                        "X-Pages": "2",
                        "Last-Modified": self.modified,
                        "Cache-Control": "max-age=0",
                    },
                )
            return response

        self.handler = shifted
        with self.assertRaises(ESIClientError):
            self.fetch()

    def test_single_page_missing_headers_keep_the_native_default_without_probes(self):
        for headers in [{}, {"X-Pages": "1"}]:
            with self.subTest(headers=headers):
                cache.clear()
                self.wire_calls.clear()
                self.handler = lambda request: self.response(
                    request, payload=[], headers=headers
                )
                self.assertEqual(self.fetch(), [])
                self.assertEqual(len(self.wire_calls), 1)

    def test_all_paginated_consumers_use_the_same_coherence_boundary(self):
        stamp = format_datetime(timezone.now(), usegmt=True)
        start = timezone.now().isoformat()
        consumers = [
            (
                lambda: self.facade.fetch_corporation_blueprints(
                    980001, character_id=self.token.character_id
                ),
                "item_id",
            ),
            (
                lambda: self.facade.fetch_corporation_industry_jobs(
                    980001, character_id=self.token.character_id
                ),
                "job_id",
            ),
            (
                lambda: self.facade.fetch_character_assets(
                    character_id=self.token.character_id
                ),
                "item_id",
            ),
            (
                lambda: self.facade.fetch_corporation_assets(
                    980001, character_id=self.token.character_id
                ),
                "item_id",
            ),
            (
                lambda: self.facade.fetch_character_contracts(self.token.character_id),
                "contract_id",
            ),
            (
                lambda: self.facade.fetch_corporation_contracts(
                    980001, self.token.character_id
                ),
                "contract_id",
            ),
            (
                lambda: self.facade.fetch_corporation_structures(
                    980001, character_id=self.token.character_id
                ),
                "structure_id",
            ),
        ]
        for index, (fetch, identity) in enumerate(consumers):
            with self.subTest(consumer=index):
                cache.clear()

                def reply(request):
                    page = int(request.url.params.get("page", "1"))
                    return self.response(
                        request,
                        payload=[{identity: 700000 + page, "start_date": start}],
                        headers={"X-Pages": "2", "Last-Modified": stamp},
                    )

                self.handler = reply
                result = fetch()
                self.assertEqual([row[identity] for row in result], [700001, 700002])

    def test_two_corporations_do_not_share_page_responses(self):
        stamp = format_datetime(timezone.now(), usegmt=True)

        def reply(request):
            corporation = int(request.url.path.split("/")[2])
            page = int(request.url.params.get("page", "1"))
            return self.response(
                request,
                payload=[
                    {
                        "item_id": corporation * 10 + page,
                        "quantity": 1,
                        "location_id": 60003760,
                    }
                ],
                headers={"X-Pages": "2", "Last-Modified": stamp},
            )

        self.handler = reply
        for corporation in [980001, 980002]:
            rows = self.facade.fetch_corporation_assets(
                corporation, character_id=self.token.character_id
            )
            self.assertEqual(
                [row["item_id"] for row in rows],
                [corporation * 10 + 1, corporation * 10 + 2],
            )

    def test_intermediate_and_last_page_errors_do_not_start_blueprint_reconciliation(
        self,
    ):
        # AA Example App
        from indy_hub.tasks import industry
        from indy_hub.tests.test_smoke import grant_indy_permissions

        self._asset_fixture()
        grant_indy_permissions(self.user)
        Blueprint.objects.create(
            owner_user=self.user,
            character_id=self.token.character_id,
            item_id=123,
            location_id=60003760,
            type_id=34,
            quantity=-1,
            material_efficiency=5,
        )
        other = User.objects.create_user("unrelated-blueprint-owner")
        Blueprint.objects.create(
            owner_user=other,
            character_id=900003,
            item_id=124,
            location_id=60003760,
            type_id=34,
            quantity=-1,
        )
        before = list(Blueprint.objects.order_by("pk").values())
        for failing_page in [2, 3]:
            with self.subTest(page=failing_page):
                cache.clear()
                original = self.prepare_pages(pages=3)

                def broken(request):
                    if int(request.url.params.get("page", "1")) == failing_page:
                        return self.response(
                            request,
                            status=503,
                            payload={"error": "Synthetic page outage"},
                        )
                    return original(request)

                self.handler = broken
                with (
                    patch.object(industry, "shared_client", self.facade),
                    patch("tenacity.nap.time.sleep"),
                ):
                    result = industry.update_blueprints_for_user.run(
                        self.user.pk,
                        scope="character",
                        character_id=self.token.character_id,
                        queue_source="manual",
                    )
                self.assertTrue(result["errors"])
                self.assertEqual(
                    list(Blueprint.objects.order_by("pk").values()), before
                )

    def test_empty_blueprint_snapshot_deletes_only_the_target_character(self):
        # AA Example App
        from indy_hub.tasks import industry
        from indy_hub.tests.test_smoke import grant_indy_permissions

        self._asset_fixture()
        grant_indy_permissions(self.user)
        for cid, item in [(self.token.character_id, 50001), (900002, 50002)]:
            Blueprint.objects.create(
                owner_user=self.user,
                character_id=cid,
                item_id=item,
                location_id=60003760,
                type_id=34,
                quantity=-1,
            )
        other_before = Blueprint.objects.values().get(item_id=50002)
        self.handler = lambda request: self.response(
            request, payload=[], headers={"X-Pages": "1"}
        )
        with (patch.object(industry, "shared_client", self.facade),):
            result = industry.update_blueprints_for_user.run(
                self.user.pk,
                scope="character",
                character_id=self.token.character_id,
                queue_source="manual",
            )
        self.assertFalse(result["errors"])
        self.assertFalse(Blueprint.objects.filter(item_id=50001).exists())
        self.assertEqual(Blueprint.objects.values().get(item_id=50002), other_before)

    def _asset_fixture(self):
        for cid in [self.token.character_id, 900002]:
            character = EveCharacter.objects.create(
                character_id=cid,
                character_name=f"Synthetic {cid}",
                corporation_id=980001,
                corporation_name="Synthetic Corp",
                corporation_ticker="SYN",
            )
            CharacterOwnership.objects.create(
                user=self.user, character=character, owner_hash=f"fixture-{cid}"
            )
            CachedCharacterAsset.objects.create(
                user=self.user,
                character_id=cid,
                item_id=cid + 100,
                location_id=60003760,
                location_flag="Hangar",
                type_id=34,
                quantity=10,
            )
        self.make_token(900002)
        CachedCharacterAsset.objects.filter(user=self.user).update(
            synced_at=timezone.now() - timedelta(hours=2)
        )

    def test_partial_character_task_cannot_delete_a_failed_character_snapshot(self):
        # AA Example App
        from indy_hub.tasks import material_exchange as tasks

        self._asset_fixture()
        before = list(CachedCharacterAsset.objects.filter(character_id=900002).values())

        def assets(request):
            if "/900002/" in request.url.path:
                return self.response(
                    request, status=403, payload={"error": "Synthetic refusal"}
                )
            return self.response(
                request,
                payload=[
                    {
                        "item_id": 900101,
                        "type_id": 34,
                        "location_id": 60003760,
                        "location_flag": "Hangar",
                        "quantity": 20,
                        "is_singleton": False,
                    }
                ],
                headers={"X-Pages": "1"},
            )

        self.handler = assets
        with (
            patch.object(tasks, "shared_client", self.facade),
            patch.object(tasks, "resolve_structure_names", return_value={}),
        ):
            tasks.refresh_material_exchange_sell_user_assets.run(self.user.pk)
        self.assertEqual(
            list(CachedCharacterAsset.objects.filter(character_id=900002).values()),
            before,
        )
        state = cache.get(tasks._me_sell_assets_progress_key(self.user.pk))
        self.assertTrue(state["error"])
        self.assertEqual(Token.objects.filter(user=self.user).count(), 2)

    def test_genuinely_empty_character_task_removes_only_its_complete_scope(self):
        # AA Example App
        from indy_hub.tasks import material_exchange as tasks

        self._asset_fixture()
        # The other character has no eligible grant; its historical rows remain.
        Token.objects.filter(character_id=900002).delete()
        before = list(CachedCharacterAsset.objects.filter(character_id=900002).values())
        self.handler = lambda request: self.response(
            request, payload=[], headers={"X-Pages": "1"}
        )
        with patch.object(tasks, "shared_client", self.facade):
            tasks.refresh_material_exchange_sell_user_assets.run(self.user.pk)
        self.assertFalse(
            CachedCharacterAsset.objects.filter(
                character_id=self.token.character_id
            ).exists()
        )
        self.assertEqual(
            list(CachedCharacterAsset.objects.filter(character_id=900002).values()),
            before,
        )

    def test_character_cache_service_failure_or_304_cannot_erase_rows(self):
        # AA Example App
        from indy_hub.services import asset_cache

        self._asset_fixture()
        before = list(CachedCharacterAsset.objects.order_by("pk").values())
        for status in [403, 304, 420, 429, 503]:
            with self.subTest(status=status):
                cache.clear()
                self.handler = lambda request: self.response(
                    request,
                    status=status,
                    payload={"error": "Synthetic unavailable snapshot"},
                    headers={
                        "X-Pages": "1",
                        "Retry-After": "30",
                        "X-RateLimit-Reset": "30",
                    },
                )
                with (
                    patch.object(asset_cache, "shared_client", self.facade),
                    patch.object(
                        asset_cache, "_cache_corp_structure_names", return_value={}
                    ),
                    patch("tenacity.nap.time.sleep"),
                ):
                    rows, _ = asset_cache.get_user_assets_cached(self.user)
                self.assertEqual(len(rows), 2)
                self.assertEqual(
                    list(CachedCharacterAsset.objects.order_by("pk").values()), before
                )

    def test_character_cache_service_empty_success_is_scoped(self):
        # AA Example App
        from indy_hub.services import asset_cache

        self._asset_fixture()
        Token.objects.filter(character_id=900002).delete()
        retained = CachedCharacterAsset.objects.values().get(character_id=900002)
        self.handler = lambda request: self.response(
            request, payload=[], headers={"X-Pages": "1"}
        )
        with (
            patch.object(asset_cache, "shared_client", self.facade),
            patch.object(asset_cache, "_cache_corp_structure_names", return_value={}),
        ):
            rows, _ = asset_cache.get_user_assets_cached(self.user)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["character_id"], 900002)
        self.assertFalse(
            CachedCharacterAsset.objects.filter(
                character_id=self.token.character_id
            ).exists()
        )
        self.assertEqual(
            CachedCharacterAsset.objects.values().get(character_id=900002), retained
        )

    def test_asset_task_304_retains_rows_freshness_and_success_version(self):
        # AA Example App
        from indy_hub.tasks import material_exchange as tasks

        self._asset_fixture()
        before = list(CachedCharacterAsset.objects.order_by("pk").values())
        key = tasks.me_user_assets_cache_version_key(self.user.pk)
        cache.set(key, 12345, 600)
        self.handler = lambda request: self.response(
            request, status=304, headers={"X-Pages": "1"}
        )
        with patch.object(tasks, "shared_client", self.facade):
            tasks.refresh_material_exchange_sell_user_assets.run(self.user.pk)
        self.assertEqual(
            list(CachedCharacterAsset.objects.order_by("pk").values()), before
        )
        self.assertEqual(cache.get(key), 12345)
        progress = cache.get(tasks._me_sell_assets_progress_key(self.user.pk))
        self.assertTrue(progress["finished"])
        self.assertIsNone(progress["error"])
