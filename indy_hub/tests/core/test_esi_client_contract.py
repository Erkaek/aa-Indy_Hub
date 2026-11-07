"""Exercise the facade across the real django-esi/OpenAPI/cache boundary."""

# Standard Library
import ast
import json
from datetime import timedelta
from decimal import Decimal
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Third Party
import httpx2
from pydantic import BaseModel

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

# Alliance Auth
import esi
from esi.errors import TokenInvalidError
from esi.exceptions import ESIBucketLimitException, ESIErrorLimitException
from esi.models import Scope, Token
from esi.openapi_clients import ESIClientProvider, EsiOperation, SpecCachingClient

# AA Example App
from indy_hub.services.esi_client import (
    ESIClient,
    ESIClientError,
    ESIForbiddenError,
    ESITokenError,
    ESIUnmodifiedError,
)
from indy_hub.services.providers import ESI_REQUIRED_OPERATIONS, esi_provider
from indy_hub.tests.core.esi_contract_fixture import (
    BLUEPRINT_SCOPE,
    OPERATIONS,
    SKILLS_SCOPE,
    openapi_spec,
)


class EsiClientNativeContractTests(TestCase):
    def setUp(self):
        cache.clear()
        self.directory = TemporaryDirectory(prefix="indyhub-esi-contract-")
        self.addCleanup(self.directory.cleanup)
        spec_path = Path(self.directory.name) / "openapi.json"
        spec_path.write_text(json.dumps(openapi_spec()))
        self.wire_calls = []
        self.payload = {
            "skills": [
                {
                    "skill_id": 3380,
                    "active_skill_level": 5,
                    "trained_skill_level": 5,
                    "skillpoints_in_skill": 256000,
                }
            ],
            "total_sp": 256000,
        }
        self.status = 200
        self.headers = {}
        self.handler = None

        def respond(request):
            self.wire_calls.append(request)
            if self.handler:
                return self.handler(request)
            return self.response(request)

        transport = httpx2.MockTransport(respond)

        def session_factory(**kwargs):
            return SpecCachingClient(transport=transport, **kwargs)

        self.factory_patch = patch(
            "esi.openapi_clients.SpecCachingClient", session_factory
        )
        self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.provider = ESIClientProvider(
            compatibility_date=esi_provider._compatibility_date,
            ua_appname="IndyHubOfflineContract",
            ua_version="1.0",
            spec_file=str(spec_path),
            operations=ESI_REQUIRED_OPERATIONS,
        )
        self.facade = ESIClient()
        self.facade.provider = self.provider
        self.user = User.objects.create_user("esi-contract-owner")
        self.token = self.make_token(900001)

    def response(self, request, payload=None, status=None, headers=None):
        combined = {
            "Expires": format_datetime(
                timezone.now() + timedelta(hours=1), usegmt=True
            ),
            **self.headers,
            **(headers or {}),
        }
        return httpx2.Response(
            status or self.status,
            json=self.payload if payload is None else payload,
            headers=combined,
            request=request,
        )

    def make_token(self, character_id, access_token="synthetic-access", scopes=None):
        token = Token.objects.bulk_create(
            [
                Token(
                    user=self.user,
                    character_id=character_id,
                    character_name="Synthetic Pilot",
                    character_owner_hash="synthetic-owner",
                    access_token=access_token,
                    refresh_token="synthetic-refresh",
                )
            ]
        )[0]
        scope_names = (
            scopes
            if scopes is not None
            else {entry[3] for entry in OPERATIONS if entry[3]}
        )
        Token.scopes.through.objects.bulk_create(
            [
                Token.scopes.through(
                    token_id=token.pk,
                    scope_id=Scope.objects.get_or_create(name=name)[0].pk,
                )
                for name in scope_names
            ]
        )
        return token

    def skills(self, **kwargs):
        return self.facade.fetch_character_skills(
            self.token.character_id, token_obj=self.token, **kwargs
        )

    def warm_cache(self):
        self.skills()
        self.wire_calls.clear()

    def test_native_filtered_spec_covers_every_declared_operation(self):
        self.assertEqual(
            {entry[0] for entry in OPERATIONS}, set(ESI_REQUIRED_OPERATIONS)
        )
        client = self.provider.client
        for name, tag, *_ in OPERATIONS:
            with self.subTest(operation=name):
                self.assertIsInstance(getattr(getattr(client, tag), name), EsiOperation)
        with self.assertRaises(AttributeError):
            getattr(client.Status, "GetStatus")
        self.assertEqual(self.wire_calls, [])

    def test_fixture_parameters_and_tags_match_installed_public_stubs(self):
        stubs = ast.parse(Path(esi.__file__).with_name("stubs.pyi").read_text())
        functions = {
            (node.name.removeprefix("_"), fn.name): fn
            for node in ast.walk(stubs)
            if isinstance(node, ast.ClassDef)
            for fn in node.body
            if isinstance(fn, ast.FunctionDef)
        }
        for name, tag, path, scope, paginated, _ in OPERATIONS:
            with self.subTest(operation=name):
                args = {arg.arg for arg in functions[tag, name].args.args}
                path_args = {
                    part[1:-1] for part in path.split("/") if part.startswith("{")
                }
                self.assertTrue(path_args.issubset(args))
                self.assertEqual("page" in args, paginated)
                self.assertEqual("token" in args, scope is not None)
                if name == "PostUniverseNames":
                    self.assertIn("body", args)
                    self.assertNotIn("ids", args)

    def test_native_request_receives_token_object_and_validates_scopes(self):
        validate = EsiOperation._validate_token_scopes
        with patch.object(
            EsiOperation, "_validate_token_scopes", autospec=True, side_effect=validate
        ) as native_scopes:
            result = self.skills()
        self.assertEqual(result["skills"][0]["active_skill_level"], 5)
        native_scopes.assert_called_once()
        self.assertIs(native_scopes.call_args.args[1], self.token)
        self.assertEqual(
            self.wire_calls[0].headers["Authorization"], "Bearer synthetic-access"
        )
        self.assertEqual(len(self.wire_calls), 1)

    def test_missing_token_is_rejected_before_transport(self):
        with self.assertRaises(ESITokenError):
            self.facade.fetch_character_skills(999999)
        self.assertEqual(self.wire_calls, [])

    def test_native_scope_validation_remains_enabled_on_cold_requests(self):
        Token.scopes.through.objects.filter(
            token_id=self.token.pk, scope__name=SKILLS_SCOPE
        ).delete()
        with self.assertRaisesRegex(ValueError, "Token Missing Scopes"):
            self.provider.client.Skills.GetCharactersCharacterIdSkills(
                character_id=self.token.character_id, token=self.token
            ).result()
        self.assertEqual(self.wire_calls, [])

    def test_cold_and_warm_cache_reject_missing_current_scope(self):
        for warm in [False, True]:
            with self.subTest(warm=warm):
                if warm:
                    self.warm_cache()
                through = Token.scopes.through
                through.objects.filter(
                    token_id=self.token.pk, scope__name=SKILLS_SCOPE
                ).delete()
                with self.assertRaises(ESITokenError):
                    self.skills()
                self.assertEqual(self.wire_calls, [])
                through.objects.create(
                    token_id=self.token.pk,
                    scope_id=Scope.objects.get(name=SKILLS_SCOPE).pk,
                )

    def test_native_warm_cache_skips_validation_but_facade_checks_current_scopes(self):
        self.warm_cache()
        Token.scopes.through.objects.filter(
            token_id=self.token.pk, scope__name=SKILLS_SCOPE
        ).delete()
        # Demonstrate the native cache boundary that the facade must protect.
        native = self.provider.client.Skills.GetCharactersCharacterIdSkills(
            character_id=self.token.character_id, token=self.token
        ).result()
        self.assertIsInstance(native, BaseModel)
        with self.assertRaises(ESITokenError):
            self.skills()
        self.assertEqual(self.wire_calls, [])

    def test_warm_cache_rejects_token_reassigned_to_another_user(self):
        self.warm_cache()
        other = User.objects.create_user("esi-other-owner")
        Token.objects.filter(pk=self.token.pk).update(user=other)
        with self.assertRaises(ESITokenError):
            self.skills()
        self.assertEqual(self.wire_calls, [])

    def test_warm_cache_rejects_deleted_token_and_wrong_character(self):
        self.warm_cache()
        with self.assertRaises(ESITokenError):
            self.facade.fetch_character_skills(999999, token_obj=self.token)
        Token.objects.filter(pk=self.token.pk).delete()
        with self.assertRaises(ESITokenError):
            self.skills()
        self.assertEqual(self.wire_calls, [])

    def test_cold_and_warm_cache_renew_expired_supplied_token_natively(self):
        for warm in [False, True]:
            with self.subTest(warm=warm):
                if warm:
                    self.warm_cache()
                self.token.created = timezone.now() - timedelta(days=1)
                Token.objects.filter(pk=self.token.pk).update(
                    created=self.token.created
                )
                with (
                    patch(
                        "esi.sso.refresh_token",
                        return_value={
                            "access_token": "synthetic-renewed",
                            "refresh_token": "synthetic-rotated",
                        },
                    ) as exchange,
                    patch(
                        "esi.managers.TokenManager.validate_access_token",
                        return_value={"owner": "synthetic-owner"},
                    ),
                ):
                    result = self.skills()
                exchange.assert_called_once_with(
                    "synthetic-refresh" if not warm else "synthetic-rotated"
                )
                self.assertEqual(result["total_sp"], 256000)
                self.token.refresh_from_db()
                self.assertFalse(self.token.expired)
                self.assertEqual(len(self.wire_calls), 0 if warm else 1)

    def test_cold_and_warm_cache_reject_failed_renewal_without_replay(self):
        for warm in [False, True]:
            with self.subTest(warm=warm):
                if warm:
                    self.warm_cache()
                self.token.created = timezone.now() - timedelta(days=1)
                Token.objects.filter(pk=self.token.pk).update(
                    created=self.token.created
                )
                with patch(
                    "esi.sso.refresh_token", side_effect=TokenInvalidError()
                ) as exchange:
                    with self.assertRaises(ESITokenError) as caught:
                        self.skills()
                self.assertIsInstance(caught.exception.__cause__, TokenInvalidError)
                exchange.assert_called_once()
                self.assertEqual(self.wire_calls, [])
                self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())
                self.token.created = timezone.now()
                Token.objects.filter(pk=self.token.pk).update(
                    created=self.token.created
                )

    def test_selected_expired_token_is_renewed_before_warm_paginated_cache(self):
        self.payload = [{"item_id": 123, "type_id": 34, "quantity": 1}]
        self.facade.fetch_character_blueprints(self.token.character_id)
        self.wire_calls.clear()
        Token.objects.filter(pk=self.token.pk).update(
            created=timezone.now() - timedelta(days=1)
        )
        with (
            patch(
                "esi.sso.refresh_token",
                return_value={
                    "access_token": "synthetic-new",
                    "refresh_token": "synthetic-rotated",
                },
            ) as exchange,
            patch(
                "esi.managers.TokenManager.validate_access_token",
                return_value={"owner": "synthetic-owner"},
            ),
        ):
            payload = self.facade.fetch_character_blueprints(self.token.character_id)
        exchange.assert_called_once()
        self.assertEqual(payload[0]["item_id"], 123)
        self.assertEqual(self.wire_calls, [])

    def test_native_invalid_token_cleanup_precedes_warm_paginated_cache(self):
        self.payload = [{"item_id": 123, "type_id": 34, "quantity": 1}]
        self.facade.fetch_character_blueprints(self.token.character_id)
        self.wire_calls.clear()
        Token.objects.filter(pk=self.token.pk).update(
            created=timezone.now() - timedelta(days=1)
        )
        with patch(
            "esi.sso.refresh_token", side_effect=TokenInvalidError()
        ) as exchange:
            with self.assertRaises(ESITokenError):
                self.facade.fetch_character_blueprints(self.token.character_id)
        exchange.assert_called_once()
        self.assertFalse(Token.objects.filter(pk=self.token.pk).exists())
        self.assertEqual(self.wire_calls, [])

    def test_paginated_cache_does_not_bypass_current_blueprint_scope(self):
        self.payload = []
        self.facade.fetch_character_blueprints(self.token.character_id)
        self.wire_calls.clear()
        Token.scopes.through.objects.filter(
            token_id=self.token.pk, scope__name=BLUEPRINT_SCOPE
        ).delete()
        with self.assertRaises(ESITokenError):
            self.facade.fetch_character_blueprints(self.token.character_id)
        self.assertEqual(self.wire_calls, [])

    def test_selection_preserves_newest_eligible_token(self):
        newest = self.make_token(
            self.token.character_id, "synthetic-newest", scopes=[SKILLS_SCOPE]
        )
        Token.objects.filter(pk=newest.pk).update(
            created=timezone.now() + timedelta(seconds=1)
        )
        self.facade.fetch_character_skills(self.token.character_id)
        self.assertEqual(
            self.wire_calls[0].headers["Authorization"], "Bearer synthetic-newest"
        )

    def test_internal_type_error_never_reexecutes_native_operation(self):
        error = TypeError("is not of type 'string'")
        with patch.object(
            EsiOperation, "result", autospec=True, side_effect=error
        ) as result:
            with self.assertRaises(ESIClientError) as caught:
                self.skills()
        self.assertIs(caught.exception.__cause__, error)
        result.assert_called_once()

    def test_type_error_after_request_begins_never_replays_with_string(self):
        error = TypeError("is not of type 'string'")

        def handler(request):
            raise error

        self.handler = handler
        with self.assertRaises(ESIClientError) as caught:
            self.skills()
        cause = caught.exception
        chain = []
        while cause is not None:
            chain.append(cause)
            cause = cause.__cause__ or cause.__context__
        self.assertIn(error, chain)
        self.assertEqual(len(self.wire_calls), 1)

    def test_type_error_after_successful_response_never_replays_logical_call(self):
        error = TypeError("is not of type 'string'")
        with patch.object(EsiOperation, "_send_signal", side_effect=error):
            with self.assertRaises(ESIClientError) as caught:
                self.skills()
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(len(self.wire_calls), 1)

    def test_paginated_type_error_does_not_use_raw_token_fallback(self):
        error = TypeError("is not of type 'string'")
        with patch.object(
            EsiOperation, "results", autospec=True, side_effect=error
        ) as results:
            with self.assertRaises(ESIClientError) as caught:
                self.facade.fetch_character_blueprints(self.token.character_id)
        self.assertIs(caught.exception.__cause__, error)
        results.assert_called_once()

    def test_paginated_payload_headers_and_new_operation_per_call(self):
        def handler(request):
            page = int(request.url.params.get("page", 1))
            cid = int(request.url.path.split("/")[2])
            return self.response(
                request,
                payload=[{"item_id": cid * 10 + page, "type_id": 34, "quantity": 1}],
                headers={
                    "X-Pages": "2",
                    "Last-Modified": "Wed, 01 Jul 2026 12:00:00 GMT",
                },
            )

        self.handler = handler
        first = self.facade.fetch_character_blueprints(self.token.character_id)
        other = self.make_token(900002, "synthetic-other")
        second = self.facade.fetch_character_blueprints(other.character_id)
        self.assertEqual([row["item_id"] for row in first], [9000011, 9000012])
        self.assertEqual([row["item_id"] for row in second], [9000021, 9000022])
        self.assertEqual(len(self.wire_calls), 4)
        self.assertEqual(
            [request.headers["Authorization"] for request in self.wire_calls],
            ["Bearer synthetic-access"] * 2 + ["Bearer synthetic-other"] * 2,
        )

    def test_single_response_lists_do_not_use_paginated_entrypoint(self):
        self.payload = [{"job_id": 42, "status": "active"}]
        with patch.object(
            EsiOperation,
            "results",
            side_effect=AssertionError("Non-paginated endpoint"),
        ):
            jobs = self.facade.fetch_character_industry_jobs(self.token.character_id)
            self.payload = [
                {"record_id": 123, "type_id": 34, "quantity": 2, "is_included": True}
            ]
            personal = self.facade.fetch_character_contract_items(
                self.token.character_id, 12
            )
            corporation = self.facade.fetch_corporation_contract_items(
                980001, 13, self.token.character_id
            )
        self.assertEqual(jobs[0]["job_id"], 42)
        self.assertEqual(personal[0]["quantity"], 2)
        self.assertEqual(corporation[0]["record_id"], 123)

    def test_post_universe_names_sends_body_once_and_preserves_batches(self):
        def handler(request):
            ids = json.loads(request.content)
            self.assertEqual(request.method, "POST")
            self.assertNotIn("ids", request.url.params)
            return self.response(
                request,
                payload=[
                    {"id": item, "name": "Synthetic " + str(item)} for item in ids
                ],
            )

        self.handler = handler
        names = self.facade.resolve_ids_to_names(list(range(1, 1002)))
        self.assertEqual(len(names), 1001)
        self.assertEqual(names[1001], "Synthetic 1001")
        self.assertEqual(
            [len(json.loads(request.content)) for request in self.wire_calls], [1000, 1]
        )

    def test_names_preserves_best_effort_invalid_batch_behavior(self):
        def handler(request):
            ids = json.loads(request.content)
            if ids[0] == 1:
                return self.response(
                    request, status=404, payload={"error": "Synthetic invalid ID"}
                )
            return self.response(
                request,
                payload=[
                    {"id": item, "name": "Synthetic " + str(item)} for item in ids
                ],
            )

        self.handler = handler
        self.assertEqual(
            self.facade.resolve_ids_to_names(list(range(1, 1002))),
            {1001: "Synthetic 1001"},
        )
        self.assertEqual(len(self.wire_calls), 2)

    def test_names_ignores_native_cache_from_a_different_request_body(self):
        def handler(request):
            ids = json.loads(request.content)
            return self.response(
                request,
                payload=[
                    {"id": item, "name": "Synthetic " + str(item)} for item in ids
                ],
            )

        self.handler = handler
        self.provider.client.Universe.PostUniverseNames(body=[1]).result()
        self.wire_calls.clear()
        self.assertEqual(self.facade.resolve_ids_to_names([2]), {2: "Synthetic 2"})
        self.assertEqual(len(self.wire_calls), 1)

    def test_names_type_error_does_not_trigger_alternative_body_attempt(self):
        with patch.object(
            EsiOperation,
            "result",
            autospec=True,
            side_effect=TypeError("Internal failure"),
        ) as execute:
            self.assertEqual(self.facade.resolve_ids_to_names([1]), {})
        execute.assert_called_once()

    def test_scalar_and_public_list_payload_shapes(self):
        self.payload = {"roles": ["Director"]}
        self.assertEqual(
            self.facade.fetch_character_corporation_roles(self.token.character_id),
            {"roles": ["Director"]},
        )
        self.payload = {
            "hangar": [{"division": 1, "name": "Synthetic hangar"}],
            "wallet": [],
        }
        self.assertEqual(
            self.facade.fetch_corporation_divisions(
                980001, character_id=self.token.character_id
            )["hangar"][0]["division"],
            1,
        )
        self.payload = {"name": "Synthetic station"}
        self.assertEqual(self.facade.fetch_station_name(60003760), "Synthetic station")
        self.assertEqual(
            self.facade.fetch_structure_name(1030000000001, self.token.character_id),
            "Synthetic station",
        )
        self.payload = [
            {
                "solar_system_id": 30000142,
                "cost_indices": [{"activity": "manufacturing", "cost_index": 0.0125}],
            }
        ]
        self.assertEqual(
            self.facade.fetch_industry_systems()[0]["cost_indices"][0]["cost_index"],
            0.0125,
        )

    def test_remaining_list_operations_keep_explicit_path_parameters(self):
        self.payload = []
        calls = [
            lambda: self.facade.fetch_corporation_blueprints(
                980001, character_id=self.token.character_id
            ),
            lambda: self.facade.fetch_corporation_industry_jobs(
                980001, character_id=self.token.character_id
            ),
            lambda: self.facade.fetch_corporation_contracts(
                980001, self.token.character_id
            ),
            lambda: self.facade.fetch_character_contracts(self.token.character_id),
            lambda: self.facade.fetch_corporation_assets(
                980001, character_id=self.token.character_id
            ),
            lambda: self.facade.fetch_character_assets(
                character_id=self.token.character_id
            ),
            lambda: self.facade.fetch_corporation_structures(
                980001, character_id=self.token.character_id
            ),
        ]
        for fetch in calls:
            self.assertEqual(fetch(), [])
        self.assertEqual(len(self.wire_calls), len(calls))

    def test_explicit_304_recovery_for_structure_and_station_is_retained(self):
        def handler(request):
            count = sum(
                previous.url.path == request.url.path for previous in self.wire_calls
            )
            if count == 1:
                return self.response(request, status=304, payload={})
            return self.response(request, payload={"name": "Synthetic recovered name"})

        self.handler = handler
        self.assertEqual(
            self.facade.fetch_station_name(60003760), "Synthetic recovered name"
        )
        self.assertEqual(
            self.facade.fetch_structure_name(1030000000001, self.token.character_id),
            "Synthetic recovered name",
        )
        self.assertEqual(len(self.wire_calls), 4)

    def test_numeric_and_datetime_conversion_uses_native_pydantic_values(self):
        class NumericPayload(BaseModel):
            quantity: Decimal

        self.assertEqual(
            self.facade._coerce_mapping(NumericPayload(quantity=Decimal("1.25")))[
                "quantity"
            ],
            Decimal("1.25"),
        )
        self.payload = [{"job_id": 42, "start_date": "2026-07-21T12:00:00Z"}]
        result = self.facade.fetch_character_industry_jobs(self.token.character_id)
        self.assertEqual(result[0]["start_date"].year, 2026)

    def test_http_errors_keep_business_meaning_without_application_replay(self):
        cases = [
            (304, ESIUnmodifiedError),
            (401, ESITokenError),
            (403, ESIForbiddenError),
            (420, ESIErrorLimitException),
            (429, ESIBucketLimitException),
            (500, ESIClientError),
            (503, ESIClientError),
        ]
        make_request = EsiOperation._make_request
        for status, expected in cases:
            with self.subTest(status=status):
                cache.clear()
                self.wire_calls.clear()
                self.status = status
                self.payload = {"error": "Synthetic refusal"}
                self.headers = {"Retry-After": "2", "X-Ratelimit-Reset": "2"}
                with patch.object(
                    EsiOperation,
                    "_make_request",
                    autospec=True,
                    side_effect=make_request,
                ) as logical:
                    with self.assertRaises(expected) as caught:
                        self.skills()
                logical.assert_called_once()
                self.assertIsNotNone(
                    caught.exception.__cause__ or caught.exception.__context__
                )
                self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())
                if status in [500, 503]:
                    self.assertEqual(caught.exception.status_code, status)
                    self.assertGreater(caught.exception.retry_after, 0)
                    self.assertGreater(
                        self.facade._tranquility_cooldown_seconds_remaining(), 0
                    )

    def test_reauthorization_refreshes_passive_roles_for_industry_inactive_owner(self):
        # Django
        from django.contrib.auth.models import Permission

        # Alliance Auth
        from allianceauth.authentication.models import CharacterOwnership
        from allianceauth.eveonline.models import EveCharacter

        # AA Example App
        from indy_hub.models import CharacterRoles
        from indy_hub.services import esi_client as client_module
        from indy_hub.tasks.user import update_character_roles_for_character
        from indy_hub.utils.token_status import token_management_live_cache_key

        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="indy_hub",
                codename__in=["can_access_indy_hub", "can_manage_corp_bp_requests"],
            )
        )
        character = EveCharacter.objects.create(
            character_id=self.token.character_id,
            character_name="Contract Pilot",
            corporation_id=980001,
            corporation_name="Contract Corp",
            corporation_ticker="SYN",
        )
        CharacterOwnership.objects.create(
            user=self.user, character=character, owner_hash="synthetic-owner"
        )
        self.user.profile.main_character = character
        self.user.profile.save()
        snapshot = CharacterRoles.objects.create(
            owner_user=self.user,
            character_id=character.character_id,
            corporation_id=character.corporation_id,
            roles=["Director"],
        )
        CharacterRoles.objects.filter(pk=snapshot.pk).update(
            last_updated=timezone.now() - timedelta(days=35)
        )
        role_scope = Scope.objects.get(name="esi-characters.read_corporation_roles.v1")
        Token.scopes.through.objects.filter(
            token_id=self.token.pk, scope=role_scope
        ).delete()
        key = token_management_live_cache_key(self.user.pk)
        cache.set(key, {"stale": True}, timeout=300)
        self.payload = {
            "roles": ["Director"],
            "roles_at_hq": [],
            "roles_at_base": [],
            "roles_at_other": [],
        }

        def deliver(*, args, kwargs, **options):
            return update_character_roles_for_character.apply(
                args=args, kwargs=kwargs, throw=True
            )

        with (
            patch(
                "indy_hub.tasks.industry._is_user_active", return_value=False
            ) as activity,
            patch.object(client_module.shared_client, "provider", self.provider),
            patch.object(
                update_character_roles_for_character, "apply_async", side_effect=deliver
            ) as publication,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.token.scopes.add(role_scope)
                publication.assert_not_called()
            publication.assert_called_once()
            activity.assert_called_once_with(self.user)
            self.client.force_login(self.user)
            with (
                patch(
                    "indy_hub.views.user.get_corporation_name",
                    return_value="Contract Corp",
                ),
                patch(
                    "indy_hub.views.user.get_character_name",
                    return_value="Contract Pilot",
                ),
            ):
                response = self.client.get("/indy_hub/esi/")

        snapshot.refresh_from_db()
        self.assertGreater(snapshot.last_updated, timezone.now() - timedelta(minutes=1))
        self.assertEqual(snapshot.owner_user_id, self.user.pk)
        self.assertEqual(snapshot.corporation_id, character.corporation_id)
        self.assertEqual(snapshot.roles, ["Director"])
        self.assertIsNone(cache.get(key))
        self.assertEqual(len(self.wire_calls), 1)
        self.assertTrue(self.wire_calls[0].url.path.endswith("/roles"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Director token ready")
        self.assertNotContains(response, "Roles unverified")
        self.assertNotContains(response, "ESI roles check failed")
        self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())

    def test_native_rate_limit_exceptions_are_not_wrapped(self):
        for method in ["result", "results"]:
            error = ESIBucketLimitException(bucket="synthetic", reset=2)
            with (
                self.subTest(method=method),
                patch.object(EsiOperation, method, side_effect=error),
            ):
                with self.assertRaises(ESIBucketLimitException) as caught:
                    if method == "result":
                        self.skills()
                    else:
                        self.facade.fetch_character_blueprints(self.token.character_id)
            self.assertIs(caught.exception, error)
