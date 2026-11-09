# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import RequestFactory, TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.errors import TokenError
from esi.models import Scope, Token

# AA Example App
from indy_hub.models import CharacterRoles, CorporationSharingSetting
from indy_hub.tasks.industry import (
    CORP_BLUEPRINT_SCOPE_SET,
    CORP_JOBS_SCOPE_SET,
    MATERIAL_EXCHANGE_SCOPE_SET,
)
from indy_hub.tests.fixtures import create_raw_fixture
from indy_hub.utils.token_status import (
    corporation_scope_status_cache_key,
    token_management_live_cache_key,
)
from indy_hub.views.user import (
    _apply_token_validity_filter,
    _collect_corporation_scope_status,
    token_management_live_refresh,
)


class CorporationScopeStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("corporation-scope-status")
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="indy_hub",
                codename="can_access_indy_hub",
            ),
            Permission.objects.get(
                content_type__app_label="indy_hub",
                codename="can_manage_corp_bp_requests",
            ),
        )
        self.character = EveCharacter.objects.create(
            character_id=199999001,
            character_name="Scope Pilot",
            corporation_id=2000000,
            corporation_name="Scope Corp",
            corporation_ticker="SCOPE",
        )
        CharacterOwnership.objects.create(
            user=self.user, character=self.character, owner_hash="scope-owner"
        )
        # Passive eligibility requires a fresh, matching business-role snapshot.
        CharacterRoles.objects.create(
            owner_user=self.user,
            character_id=self.character.character_id,
            corporation_id=self.character.corporation_id,
            roles=["Director"],
        )
        for name, value in (
            ("get_corporation_name", "Scope Corp"),
            ("get_character_name", "Scope Pilot"),
        ):
            patcher = patch(f"indy_hub.views.user.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _make_token(self, scopes, *, expired=False):
        # Populate recorded tokens without triggering token-link sync tasks.
        token = create_raw_fixture(
            Token(
                user=self.user,
                character_id=self.character.character_id,
                character_name=self.character.character_name,
                character_owner_hash="scope-owner",
                token_type="Character",
                access_token="access",
                refresh_token="refresh",
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
        if expired:
            Token.objects.filter(pk=token.pk).update(
                created=timezone.now() - timedelta(days=1)
            )
        return token

    def test_unrelated_expired_token_is_not_refreshed_or_deleted(self):
        mail_token = self._make_token(["esi-mail.read_mail.v1"], expired=True)
        self._make_token(CORP_BLUEPRINT_SCOPE_SET)

        with patch.object(
            Token, "refresh", side_effect=TokenError("invalid token")
        ) as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False
            )

        refresh.assert_not_called()
        self.assertTrue(Token.objects.filter(pk=mail_token.pk).exists())
        self.assertTrue(status[0]["blueprint"]["has_scope"])

    def test_relevant_expired_token_is_refreshed_once_for_multiple_scopes(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET, expired=True)

        def refresh_token(instance, **kwargs):
            Token.objects.filter(pk=instance.pk).update(created=timezone.now())

        with patch.object(
            Token, "refresh", autospec=True, side_effect=refresh_token
        ) as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False
            )

        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0].pk, token.pk)
        self.assertTrue(status[0]["blueprint"]["has_scope"])
        self.assertTrue(status[0]["assets"]["has_scope"])
        self.assertCountEqual(status[0]["available_scopes"], CORP_BLUEPRINT_SCOPE_SET)

    def test_passive_collection_does_not_refresh_expired_tokens(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET, expired=True)

        with patch.object(Token, "refresh") as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False, validate_tokens=False
            )

        refresh.assert_not_called()
        self.assertTrue(status[0]["blueprint"]["has_scope"])
        self.assertCountEqual(status[0]["available_scopes"], CORP_BLUEPRINT_SCOPE_SET)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_passive_collection_keeps_expired_job_authorization(self):
        self._make_token(CORP_JOBS_SCOPE_SET, expired=True)

        with patch.object(Token, "refresh") as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False, validate_tokens=False
            )

        refresh.assert_not_called()
        self.assertTrue(status[0]["jobs"]["has_scope"])

    def test_passive_collection_excludes_expired_nonrenewable_tokens(self):
        for refresh_token in (None, ""):
            with self.subTest(refresh_token=refresh_token):
                token = self._make_token(CORP_BLUEPRINT_SCOPE_SET, expired=True)
                Token.objects.filter(pk=token.pk).update(refresh_token=refresh_token)
                with patch.object(Token, "refresh") as refresh:
                    status = _collect_corporation_scope_status(
                        self.user, allow_live_role_fetch=False, validate_tokens=False
                    )
                refresh.assert_not_called()
                self.assertEqual(status, [])
                self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_passive_filter_keeps_fresh_nonrenewable_token(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET)
        Token.objects.filter(pk=token.pk).update(refresh_token=None)
        self.assertTrue(
            _apply_token_validity_filter(
                Token.objects.filter(pk=token.pk), validate_tokens=False
            ).exists()
        )

    def test_stale_roles_page_is_passive_and_does_not_claim_an_esi_failure(self):
        self._make_token(CORP_BLUEPRINT_SCOPE_SET)
        CharacterRoles.objects.filter(character_id=self.character.character_id).update(
            last_updated=timezone.now() - timedelta(days=2)
        )
        self.user.profile.main_character = self.character
        self.user.profile.save()
        self.client.force_login(self.user)
        with (
            patch("esi.managers.TokenQueryset.require_valid") as valid,
            patch.object(Token, "refresh") as refresh,
            patch(
                "indy_hub.views.user.shared_client.fetch_character_corporation_roles"
            ) as fetch,
        ):
            response = self.client.get("/indy_hub/esi/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles unverified")
        self.assertContains(response, "A recent corporation roles check is required")
        self.assertNotContains(response, "ESI roles check failed")
        valid.assert_not_called()
        refresh.assert_not_called()
        fetch.assert_not_called()

    def test_explicit_role_check_for_stale_snapshot_bypasses_cached_etag(self):
        self._make_token(CORP_BLUEPRINT_SCOPE_SET)
        CharacterRoles.objects.filter(character_id=self.character.character_id).update(
            last_updated=timezone.now() - timedelta(days=2)
        )
        cache.set(
            f"indy_hub:corp_roles:{self.character.character_id}", ["DIRECTOR"], 600
        )
        with patch(
            "indy_hub.views.user.shared_client.fetch_character_corporation_roles",
            return_value={"roles": ["Director"]},
        ) as fetch:
            status = _collect_corporation_scope_status(self.user)
        self.assertTrue(fetch.call_args.kwargs["force_refresh"])
        self.assertFalse(status[0]["roles_unavailable"])
        self.assertTrue(status[0]["has_director_role"])
        self.assertGreater(
            CharacterRoles.objects.get(
                character_id=self.character.character_id
            ).last_updated,
            timezone.now() - timedelta(minutes=1),
        )

    def _warm_status_caches(self):
        keys = [
            token_management_live_cache_key(self.user.pk),
            corporation_scope_status_cache_key(self.user.pk, "dashboard"),
            corporation_scope_status_cache_key(self.user.pk, "settings"),
        ]
        cache.set_many(dict.fromkeys(keys, {"stale": True}), timeout=300)
        return keys

    def test_token_refresh_invalidates_all_status_pages(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET, expired=True)
        keys = self._warm_status_caches()
        # Emit the same save signal as django-esi's refresh without OAuth calls.
        post_save.send(sender=Token, instance=token, created=False)
        self.assertEqual(cache.get_many(keys), {})

    def test_token_deletion_invalidates_all_status_pages(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET)
        keys = self._warm_status_caches()
        token.delete()
        self.assertEqual(cache.get_many(keys), {})

    def test_scope_removal_invalidates_all_status_pages(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET)
        keys = self._warm_status_caches()
        token.scopes.remove(Scope.objects.get(name=CORP_BLUEPRINT_SCOPE_SET[0]))
        self.assertEqual(cache.get_many(keys), {})

    def test_reverse_scope_clear_invalidates_all_status_pages(self):
        self._make_token(CORP_BLUEPRINT_SCOPE_SET)
        keys = self._warm_status_caches()
        Scope.objects.get(name=CORP_BLUEPRINT_SCOPE_SET[0]).token_set.clear()
        self.assertEqual(cache.get_many(keys), {})

    def test_corporation_authorization_change_invalidates_all_status_pages(self):
        setting = CorporationSharingSetting.objects.create(
            user=self.user, corporation_id=self.character.corporation_id
        )
        keys = self._warm_status_caches()
        setting.authorized_characters = [self.character.character_id]
        setting.save(update_fields=["authorized_characters"])
        self.assertEqual(cache.get_many(keys), {})

    def test_live_refresh_clears_stale_dashboard_and_settings(self):
        keys = self._warm_status_caches()
        payload = {"corporations": [], "warnings": [], "hash": "new"}
        request = RequestFactory().get("/indy_hub/tokens/live-refresh/")
        request.user = self.user
        with patch(
            "indy_hub.views.user._build_token_management_live_payload",
            return_value=payload,
        ):
            response = token_management_live_refresh(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get(keys[0]), payload)
        self.assertIsNone(cache.get(keys[1]))
        self.assertIsNone(cache.get(keys[2]))

    def test_material_exchange_requires_all_scopes_on_one_token(self):
        self._make_token(MATERIAL_EXCHANGE_SCOPE_SET[:-1])
        self._make_token(MATERIAL_EXCHANGE_SCOPE_SET[-1:])

        status = _collect_corporation_scope_status(
            self.user, allow_live_role_fetch=False, validate_tokens=False
        )

        self.assertFalse(status[0]["material_exchange"]["has_scope"])
        self.assertCountEqual(
            status[0]["available_scopes"], MATERIAL_EXCHANGE_SCOPE_SET
        )
