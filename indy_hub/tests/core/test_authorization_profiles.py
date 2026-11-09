"""Consent contracts captured before prompt 05; no real SSO or ESI traffic."""

# Standard Library
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

# Django
from django.contrib.auth.models import AnonymousUser, Permission, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.http import Http404, HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import CallbackRedirect, Scope, Token
from esi.views import receive_callback

# AA Example App
from indy_hub import authorization as auth
from indy_hub.tests.fixtures import create_raw_fixture
from indy_hub.views import material_exchange_config as config
from indy_hub.views import user as user_views

BEFORE = json.loads(
    Path(__file__).with_name("authorization_scopes_before.json").read_text()
)
ROUTES = {
    name: scopes for name, scopes in BEFORE.items() if not name.startswith("charlink_")
}


class AuthorizationRegistryTests(SimpleTestCase):
    def test_exact_preexisting_scope_sets_and_sorted_fresh_serialization(self):
        self.assertEqual(set(auth.AUTHORIZATION_PROFILES), set(BEFORE))
        for name, expected in BEFORE.items():
            with self.subTest(profile=name):
                profile = auth.AUTHORIZATION_PROFILES[name]
                self.assertEqual(profile.scopes, frozenset(expected))
                self.assertEqual(profile.scope_list(), expected)
                copy = profile.scope_list()
                copy.append("not-a-requested-scope")
                self.assertEqual(profile.scope_list(), expected)

    def test_registry_and_profiles_are_immutable(self):
        profile = auth.AUTHORIZATION_PROFILES["authorize_all"]
        with self.assertRaises(TypeError):
            auth.AUTHORIZATION_PROFILES["unknown"] = profile
        with self.assertRaises(FrozenInstanceError):
            profile.scopes = frozenset()
        self.assertEqual(
            profile.scopes - auth.AUTHORIZATION_PROFILES["charlink_personal"].scopes,
            {"esi-characters.read_corporation_roles.v1"},
        )

    def test_public_routes_still_resolve_to_named_wrappers(self):
        for name in ROUTES:
            with self.subTest(route=name):
                url = reverse("indy_hub:" + name)
                self.assertEqual(resolve(url).url_name, name)
                self.assertEqual(resolve(url).func.__name__, name)


class AuthorizationFlowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.member = self._user("profile-member", [auth.ACCESS_PERMISSION])
        self.corp = self._user(
            "profile-corp", [auth.ACCESS_PERMISSION, auth.CORPORATION_PERMISSION]
        )
        self.material = self._user(
            "profile-material", [auth.ACCESS_PERMISSION, auth.MATERIAL_PERMISSION]
        )
        self.no_base = self._user(
            "profile-no-base", [auth.CORPORATION_PERMISSION, auth.MATERIAL_PERMISSION]
        )
        self.no_perm = self._user("profile-no-permission", [])
        self.superuser = self._user("profile-superuser", [], is_superuser=True)
        self.character = self._character(self.member, 1_903_001)
        for index, user in enumerate(
            [self.corp, self.material, self.no_base, self.no_perm, self.superuser]
        ):
            self._character(user, 1_903_010 + index)
        # A coverage check must never reach an expired/unscoped grant by accident.
        refresh = patch.object(
            Token, "refresh", side_effect=AssertionError("Unexpected OAuth refresh")
        )
        refresh.start()
        self.addCleanup(refresh.stop)

    def _user(self, name, permissions, **kwargs):
        user = User.objects.create_user(name, **kwargs)
        for permission in permissions:
            app, code = permission.split(".", 1)
            user.user_permissions.add(
                Permission.objects.get(content_type__app_label=app, codename=code)
            )
        return User.objects.get(pk=user.pk)

    def _character(self, user, number):
        character = EveCharacter.objects.create(
            character_id=number,
            character_name=f"Profile Pilot {number}",
            corporation_id=2_000_000,
            corporation_name="Synthetic Corp",
            corporation_ticker="SYN",
        )
        CharacterOwnership.objects.create(
            user=user, character=character, owner_hash=f"owner-{number}"
        )
        return character

    def _token(self, scopes, *, user=None, character=None):
        user, character = user or self.member, character or self.character
        # Do not simulate the native linking signal when recording historical grants.
        token = create_raw_fixture(
            Token(
                user=user,
                character_id=character.character_id,
                character_name=character.character_name,
                character_owner_hash=f"synthetic-{user.pk}",
                token_type="Character",
                access_token="synthetic",
                refresh_token="synthetic",
            )
        )
        Token.scopes.through.objects.bulk_create(
            [
                Token.scopes.through(
                    token_id=token.pk,
                    scope_id=Scope.objects.get_or_create(name=name)[0].pk,
                )
                for name in scopes
            ]
        )
        return token

    def _request(self, route, user=None, query=None):
        request = self.factory.get(reverse("indy_hub:" + route), query or {})
        request.user = user if user is not None else self.member
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request

    def _view(self, request):
        module = (
            config
            if resolve(request.path).url_name.startswith("material_exchange_")
            else user_views
        )
        return getattr(module, resolve(request.path).url_name)(request)

    def test_permission_and_scope_matrix_for_every_public_entry(self):
        # Configuration's management-only guard and hub's base+manager guards
        # are intentionally different. Corp role snapshots are not consent guards.
        for name, expected in ROUTES.items():
            module = config if name.startswith("material_exchange_") else user_views
            for user in [
                AnonymousUser(),
                self.no_perm,
                self.member,
                self.corp,
                self.material,
                self.no_base,
                self.superuser,
            ]:
                with self.subTest(route=name, user=str(user)):
                    required = [auth.ACCESS_PERMISSION]
                    if name.startswith("material_exchange_"):
                        required = [auth.MATERIAL_PERMISSION]
                    elif "_corp_" in name:
                        required.append(auth.CORPORATION_PERMISSION)
                    elif name == "authorize_material_exchange":
                        required.append(auth.MATERIAL_PERMISSION)
                    allowed = user.is_authenticated and user.has_perms(required)
                    request = self._request(name, user)
                    with patch.object(
                        module,
                        "sso_redirect",
                        return_value=HttpResponseRedirect("/prepared/"),
                    ) as sso:
                        response = self._view(request)
                    if allowed:
                        self.assertEqual(response.url, "/prepared/")
                        self.assertEqual(
                            sso.call_args.kwargs["scopes"].split(), expected
                        )
                        self.assertEqual(
                            sso.call_args.kwargs["return_to"],
                            (
                                "indy_hub:material_exchange_config"
                                if module is config
                                else "indy_hub:token_management"
                            ),
                        )
                    else:
                        sso.assert_not_called()
                        if user.is_authenticated and (
                            module is config
                            or not user.has_perm(auth.ACCESS_PERMISSION)
                        ):
                            self.assertEqual(response.status_code, 403)
                        else:
                            self.assertEqual(response.status_code, 302)

    def test_all_owned_characters_need_coverage_for_each_personal_button(self):
        second = self._character(self.member, 1_903_002)
        for route in [
            "authorize_assets",
            "authorize_blueprints",
            "authorize_jobs",
            "authorize_all",
        ]:
            with self.subTest(route=route):
                Token.objects.all().delete()
                self._token(BEFORE[route])
                with patch.object(
                    user_views,
                    "sso_redirect",
                    return_value=HttpResponseRedirect("/prepared/"),
                ) as sso:
                    self.assertEqual(self._view(self._request(route)).url, "/prepared/")
                    self._token(BEFORE[route], character=second)
                    self.assertEqual(
                        self._view(self._request(route)).url,
                        reverse("indy_hub:token_management"),
                    )
                    self.assertEqual(sso.call_count, 1)

    def test_partial_grant_does_not_cover_one_capability(self):
        self._token(["esi-characters.read_blueprints.v1"])
        self._token(["esi-universe.read_structures.v1"])
        with patch.object(
            user_views, "sso_redirect", return_value=HttpResponseRedirect("/prepared/")
        ) as sso:
            self._view(self._request("authorize_blueprints"))
        sso.assert_called_once()

    def test_combined_button_accepts_separate_capability_grants_without_roles(self):
        for scopes in [
            BEFORE["authorize_blueprints"],
            BEFORE["authorize_jobs"],
            BEFORE["authorize_assets"],
            ["esi-skills.read_skills.v1"],
        ]:
            self._token(scopes)
        with patch.object(user_views, "sso_redirect") as sso:
            response = self._view(self._request("authorize_all"))
        self.assertEqual(response.url, reverse("indy_hub:token_management"))
        sso.assert_not_called()

    def test_foreign_user_grant_cannot_cover_owned_character(self):
        self._token(BEFORE["authorize_all"], user=self.no_perm)
        with patch.object(
            user_views, "sso_redirect", return_value=HttpResponseRedirect("/prepared/")
        ) as sso:
            self._view(self._request("authorize_all"))
        sso.assert_called_once()

    def test_scope_filter_precedes_active_validation(self):
        token = self._token(["esi-mail.read_mail.v1"])
        # Standard Library
        from datetime import timedelta

        # Django
        from django.utils import timezone

        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(days=1)
        )
        with patch.object(
            user_views, "sso_redirect", return_value=HttpResponseRedirect("/prepared/")
        ):
            self._view(self._request("authorize_all"))
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_no_characters_and_force_contracts(self):
        empty_user = self._user("profile-empty", [auth.ACCESS_PERMISSION])
        for route in [
            "authorize_assets",
            "authorize_blueprints",
            "authorize_jobs",
            "authorize_all",
        ]:
            for forced in [False, True]:
                with (
                    self.subTest(route=route, force=forced),
                    patch.object(
                        user_views,
                        "sso_redirect",
                        return_value=HttpResponseRedirect("/prepared/"),
                    ) as sso,
                ):
                    self._view(
                        self._request(
                            route, empty_user, {"force": "1"} if forced else {}
                        )
                    )
                    self.assertEqual(sso.called, forced and route == "authorize_all")

    def test_internal_external_and_missing_next_for_covered_character(self):
        self._token(BEFORE["authorize_all"])
        for next_url, expected in [
            ("/indy_hub/esi/?tab=profiles", "/indy_hub/esi/?tab=profiles"),
            ("https://outside.invalid/", reverse("indy_hub:token_management")),
            ("//outside.invalid/", reverse("indy_hub:token_management")),
            (None, reverse("indy_hub:token_management")),
        ]:
            with (
                self.subTest(next=next_url),
                patch.object(user_views, "sso_redirect") as sso,
            ):
                response = self._view(
                    self._request(
                        "authorize_all", query={"next": next_url} if next_url else {}
                    )
                )
                self.assertEqual(response.url, expected)
                sso.assert_not_called()

    def test_native_sso_callback_and_safe_return_keep_one_callback(self):
        token = self._token(BEFORE["authorize_all"])
        for next_url, expected in [
            ("/indy_hub/esi/?tab=profiles", "/indy_hub/esi/?tab=profiles"),
            ("https://outside.invalid/", None),
            (None, None),
        ]:
            request = self._request(
                "authorize_all",
                query={"force": "1", **({"next": next_url} if next_url else {})},
            )
            request.session.create()
            previous = CallbackRedirect.objects.create(
                session_key=request.session.session_key,
                state="previous",
                url="/old/",
                token=token,
            )
            with (
                self.subTest(next=next_url),
                patch(
                    "esi.views.sso.authorization_url",
                    return_value=("https://oauth.invalid/prepared", "synthetic-state"),
                ) as boundary,
            ):
                response = self._view(request)
                self.assertEqual(response.url, "https://oauth.invalid/prepared")
                self.assertEqual(
                    boundary.call_args.args[0], [" ".join(BEFORE["authorize_all"])]
                )
                self.assertFalse(
                    CallbackRedirect.objects.filter(pk=previous.pk).exists()
                )
                callback = CallbackRedirect.objects.get(
                    session_key=request.session.session_key
                )
                self.assertEqual(callback.url, reverse("indy_hub:token_management"))
                self.assertEqual(
                    request.session.get("indy_hub_authorize_next"), expected
                )
                request.GET = request.GET.copy()
                request.GET.update({"code": "synthetic-code", "state": callback.state})
                with patch(
                    "esi.views.Token.objects.create_from_request", return_value=token
                ):
                    returned = receive_callback(request)
                self.assertEqual(returned.url, callback.url)
                if expected:
                    self.assertEqual(user_views.token_management(request).url, expected)
                    self.assertNotIn("indy_hub_authorize_next", request.session)
                self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_unknown_profiles_are_refused_before_sso(self):
        for helper, module in [
            (user_views._authorize_profile, user_views),
            (config._request_authorization_profile, config),
        ]:
            with (
                patch.object(module, "sso_redirect") as sso,
                self.assertRaises(Http404),
            ):
                helper(self._request("authorize_all"), "browser-arbitrary-scopes")
            sso.assert_not_called()

    def test_config_ignores_next_and_uses_fixed_narrow_profile(self):
        request = self._request(
            "material_exchange_request_all_scopes",
            self.material,
            {
                "next": "https://outside.invalid/",
                "scopes": "esi-wallet.read_corporation_wallets.v1",
            },
        )
        with patch.object(
            config, "sso_redirect", return_value=HttpResponseRedirect("/prepared/")
        ) as sso:
            self._view(request)
        self.assertEqual(
            sso.call_args.kwargs["scopes"].split(),
            BEFORE["material_exchange_request_all_scopes"],
        )
        self.assertEqual(
            sso.call_args.kwargs["return_to"], "indy_hub:material_exchange_config"
        )
        self.assertNotIn("indy_hub_authorize_next", request.session)

    def test_sso_failure_preserves_safe_return_and_message(self):
        request = self._request("authorize_all", query={"next": "/indy_hub/esi/"})
        with patch.object(
            user_views, "sso_redirect", side_effect=RuntimeError("synthetic failure")
        ):
            response = self._view(request)
        self.assertEqual(response.url, "/indy_hub/esi/")
        self.assertTrue(
            any(
                "Error setting up ESI authorization" in str(message)
                for message in request._messages
            )
        )
