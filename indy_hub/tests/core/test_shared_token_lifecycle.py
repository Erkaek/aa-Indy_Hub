"""Offline regressions against the installed django-esi manager and real M2M."""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.errors import TokenError
from esi.models import Scope, Token

# AA Example App
# Indy Hub
from indy_hub.models import AdminUserStatus, CharacterRoles, CorporationSharingSetting
from indy_hub.services.esi_client import ESIClientError
from indy_hub.tasks.industry import (
    BLUEPRINT_SCOPE,
    CORP_BLUEPRINT_SCOPE_SET,
    CORP_ROLES_SCOPE,
    JOBS_SCOPE,
    SKILLS_SCOPE,
    _collect_corporation_contexts,
    get_character_corporation_roles,
)
from indy_hub.utils.token_status import token_management_live_cache_key
from indy_hub.views.user import _collect_corporation_scope_status


class TokenFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("shared-token-owner")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="indy_hub",
                codename__in=["can_access_indy_hub", "can_manage_corp_bp_requests"],
            )
        )
        self.character = EveCharacter.objects.create(
            character_id=199991001,
            character_name="Synthetic Shared Pilot",
            corporation_id=2000010,
            corporation_name="Synthetic Corp",
            corporation_ticker="SYN",
        )
        CharacterOwnership.objects.create(
            user=self.user,
            character=self.character,
            owner_hash="synthetic-owner",
        )
        self.publishers = {}
        for name in (
            "queue_blueprint_update_for_user",
            "queue_industry_job_update_for_user",
            "update_character_skill_snapshot_for_character.apply_async",
        ):
            p = patch("indy_hub.tasks.industry." + name, return_value=True)
            self.publishers[name] = p.start()
            self.addCleanup(p.stop)
        p = patch(
            "indy_hub.tasks.user.update_character_roles_for_character.apply_async",
            return_value=True,
        )
        self.roles_publish = p.start()
        self.addCleanup(p.stop)
        for name, value in [
            ("get_character_name", "Synthetic Shared Pilot"),
            ("get_corporation_name", "Synthetic Corp"),
        ]:
            p = patch("indy_hub.views.user." + name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def token(self, scopes=(), user=None):
        token = Token.objects.create(
            user=user or self.user,
            character_id=self.character.character_id,
            character_name=self.character.character_name,
            character_owner_hash="synthetic-owner",
            token_type="character",
            access_token="synthetic-access",
            refresh_token="synthetic-refresh",
        )
        token.scopes.add(*[Scope.objects.get_or_create(name=s)[0] for s in scopes])
        return token

    def native_create(self, scopes):
        with (
            patch(
                "esi.managers.sso.exchange_code",
                return_value={
                    "access_token": "synthetic-access-new",
                    "refresh_token": "synthetic-refresh-new",
                },
            ),
            patch(
                "esi.managers.TokenManager.validate_access_token",
                return_value={
                    "character_id": self.character.character_id,
                    "name": self.character.character_name,
                    "owner": "synthetic-owner",
                    "token_type": "character",
                    "scp": scopes,
                },
            ),
        ):
            return Token.objects.create_from_code("synthetic-code", user=self.user)

    def snapshot(self, roles, *, stale=False, corporation_id=None):
        snapshot, _ = CharacterRoles.objects.update_or_create(
            character_id=self.character.character_id,
            defaults={
                "owner_user": self.user,
                "corporation_id": corporation_id or self.character.corporation_id,
                "roles": roles,
            },
        )
        if stale:
            CharacterRoles.objects.filter(pk=snapshot.pk).update(
                last_updated=timezone.now() - timedelta(days=2)
            )
        return snapshot

    def passive(self):
        return _collect_corporation_scope_status(
            self.user,
            include_warnings=True,
            allow_live_role_fetch=False,
            validate_tokens=False,
        )


class SharedTokenTests(TokenFixture, TestCase):
    def test_creation_does_not_delete_older_empty_or_scoped_token(self):
        old = self.token()
        scoped = self.token([BLUEPRINT_SCOPE])
        new = self.token()
        self.assertEqual(
            Token.objects.filter(pk__in=[old.pk, scoped.pk, new.pk]).count(), 3
        )

    def test_commit_publishes_once_per_ready_capability(self):
        with self.captureOnCommitCallbacks(execute=True):
            token = self.token()
            for scope in [
                BLUEPRINT_SCOPE,
                JOBS_SCOPE,
                SKILLS_SCOPE,
                CORP_ROLES_SCOPE,
                "esi-universe.read_structures.v1",
            ]:
                token.scopes.add(Scope.objects.get_or_create(name=scope)[0])
            token.scopes.add(
                *Scope.objects.filter(name__in=[BLUEPRINT_SCOPE, SKILLS_SCOPE])
            )
            for publisher in self.publishers.values():
                publisher.assert_not_called()
            self.roles_publish.assert_not_called()
        for publisher in self.publishers.values():
            publisher.assert_called_once()
        self.roles_publish.assert_called_once()
        self.assertTrue(AdminUserStatus.objects.get(user=self.user).scope_jobs)

    def test_rollback_publishes_nothing_and_does_not_reserve_debounce(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.token([BLUEPRINT_SCOPE, SKILLS_SCOPE])
                    raise ValueError("rollback")
            except ValueError:
                pass
        for publisher in self.publishers.values():
            publisher.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            self.token([BLUEPRINT_SCOPE, SKILLS_SCOPE])
        self.publishers["queue_blueprint_update_for_user"].assert_called_once()

    def test_removal_before_commit_prevents_publication(self):
        with self.captureOnCommitCallbacks(execute=True):
            token = self.token([SKILLS_SCOPE])
            token.scopes.clear()
        self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ].assert_not_called()

    def test_publish_failure_logs_and_never_runs_inline(self):
        publish = self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ]
        publish.side_effect = RuntimeError("synthetic broker failure")
        with patch(
            "indy_hub.tasks.industry._fetch_character_skill_levels_with_token"
        ) as fetch:
            with self.assertLogs("indy_hub.services.token_sync", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    self.token([SKILLS_SCOPE])
            fetch.assert_not_called()
        # A subsequent event can retry publication; a failed attempt isn't success.
        publish.side_effect = None
        publish.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            self.token([SKILLS_SCOPE])
        publish.assert_called_once()

    def test_foreign_user_or_missing_permission_cannot_trigger_sync(self):
        foreign = User.objects.create_user("foreign-token-user")
        for owner in [foreign, self.user]:
            if owner == self.user:
                self.user.user_permissions.clear()
            with self.captureOnCommitCallbacks(execute=True):
                self.token([BLUEPRINT_SCOPE], user=owner)
        self.publishers["queue_blueprint_update_for_user"].assert_not_called()

    def test_invalid_native_refresh_deletes_token_and_invalidates_status(self):
        token = self.token([BLUEPRINT_SCOPE])
        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(days=2)
        )
        key = token_management_live_cache_key(self.user.pk)
        cache.set(key, "old")
        with patch.object(
            Token, "refresh", side_effect=TokenError("synthetic invalid refresh")
        ):
            self.assertFalse(Token.objects.filter(pk=token.pk).require_valid().exists())
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())
        self.assertIsNone(cache.get(key))
        self.assertFalse(AdminUserStatus.objects.get(user=self.user).scope_blueprints)

    def test_role_refusal_retains_multi_use_token(self):
        token = self.token([*CORP_BLUEPRINT_SCOPE_SET, "esi-mail.read_mail.v1"])
        self.snapshot(["Accountant"])
        status, warnings = self.passive()
        self.assertEqual(status, [])
        self.assertEqual(warnings[0]["reason"], "missing_required_roles")
        self.assertTrue(warnings[0]["token_retained"])
        self.assertTrue(
            Token.objects.filter(pk=token.pk)
            .require_scopes(["esi-mail.read_mail.v1"])
            .exists()
        )

    def test_unknown_stale_or_wrong_corporation_roles_never_grant_access(self):
        token = self.token(CORP_BLUEPRINT_SCOPE_SET)
        for variant in ["missing", "stale", "wrong_corp", "empty"]:
            with self.subTest(variant=variant):
                CharacterRoles.objects.all().delete()
                if variant != "missing":
                    self.snapshot(
                        [] if variant == "empty" else ["Director"],
                        stale=variant == "stale",
                        corporation_id=2000099 if variant == "wrong_corp" else None,
                    )
                with (
                    patch(
                        "indy_hub.views.user.shared_client.fetch_character_corporation_roles"
                    ) as fetch,
                    patch("esi.managers.TokenQueryset.require_valid") as valid,
                ):
                    status, warnings = self.passive()
                fetch.assert_not_called()
                valid.assert_not_called()
                self.assertTrue(
                    all(not row["blueprint"]["has_scope"] for row in status)
                )
                self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_live_role_outage_conservatively_retains_token(self):
        token = self.token(CORP_BLUEPRINT_SCOPE_SET)
        self.snapshot(["Director"], stale=True)
        with patch(
            "indy_hub.views.user.shared_client.fetch_character_corporation_roles",
            side_effect=ESIClientError("synthetic outage"),
        ):
            status, warnings = _collect_corporation_scope_status(
                self.user, include_warnings=True
            )
        self.assertFalse(status[0]["blueprint"]["has_scope"])
        self.assertEqual(warnings[0]["reason"], "roles_unavailable")
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_whitelist_and_foreign_corporation_reject_even_with_token(self):
        self.token(CORP_BLUEPRINT_SCOPE_SET)
        self.snapshot(["Director"])
        CorporationSharingSetting.objects.create(
            user=self.user,
            corporation_id=self.character.corporation_id,
            authorized_characters=[199999999],
        )
        status, _ = self.passive()
        self.assertEqual(status, [])
        self.assertEqual(
            _collect_corporation_contexts(self.user, CORP_BLUEPRINT_SCOPE_SET), {}
        )

    def test_corporation_capability_requires_its_scopes_on_one_token(self):
        required = [
            scope
            for scope in CORP_BLUEPRINT_SCOPE_SET
            if scope != "esi-universe.read_structures.v1"
        ]
        with self.captureOnCommitCallbacks(execute=True):
            first = self.token(required[:-1])
            self.token(required[-1:])
        self.publishers["queue_blueprint_update_for_user"].assert_not_called()
        # One corporation capability can become ready without the jobs or
        # Material Exchange profiles. Its worker still checks role/whitelist.
        with self.captureOnCommitCallbacks(execute=True):
            first.scopes.add(Scope.objects.get(name=required[-1]))
        self.publishers["queue_blueprint_update_for_user"].assert_called_once_with(
            self.user.pk, scope="corporation"
        )
        self.publishers["queue_industry_job_update_for_user"].assert_not_called()

    def test_owner_is_rechecked_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.token([SKILLS_SCOPE])
            CharacterOwnership.objects.filter(user=self.user).delete()
        self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ].assert_not_called()

    def test_plain_token_refresh_save_does_not_dispatch_again(self):
        token = self.token([SKILLS_SCOPE])
        with self.captureOnCommitCallbacks(execute=True):
            token.save()
        # Ignore the creation callback pending outside this capture.
        self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ].assert_not_called()

    def test_automatic_status_refresh_is_passive(self):
        self.token(CORP_BLUEPRINT_SCOPE_SET)
        self.snapshot(["Director"])
        # AA Example App
        from indy_hub.views.user import _build_token_management_live_payload

        with (
            patch("esi.managers.TokenQueryset.require_valid") as validate,
            patch.object(Token, "refresh") as refresh,
        ):
            _build_token_management_live_payload(self.user)
        validate.assert_not_called()
        refresh.assert_not_called()

    def test_cache_failure_never_dispatches_or_fetches(self):
        with self.assertLogs("indy_hub.services.token_sync", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                self.token([SKILLS_SCOPE])
                with patch(
                    "indy_hub.services.token_sync.cache.add",
                    side_effect=RuntimeError("synthetic cache outage"),
                ):
                    # AA Example App
                    from indy_hub.services.token_sync import _publish_ready_capabilities

                    _publish_ready_capabilities(
                        self.user.pk, self.character.character_id
                    )
        # The manually invoked failure cannot publish; the later committed
        # event may publish after the cache has recovered.
        self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ].assert_called_once()

    def test_task_role_lookup_does_not_use_stale_positive_cache_on_outage(self):
        self.snapshot(["Director"], stale=True)
        # AA Example App
        with patch(
            "indy_hub.tasks.industry.shared_client.fetch_character_corporation_roles",
            side_effect=ESIClientError("synthetic outage"),
        ):
            self.assertEqual(
                get_character_corporation_roles(self.character.character_id), set()
            )


class NativeAutocommitTests(TokenFixture, TransactionTestCase):
    def test_native_reuse_after_successive_scope_commits_uses_surviving_authorization(
        self,
    ):
        old = self.token([BLUEPRINT_SCOPE, SKILLS_SCOPE])
        cache.clear()
        for publisher in self.publishers.values():
            publisher.reset_mock()
        with patch("esi.managers.app_settings.ESI_ALWAYS_CREATE_TOKEN", False):
            result = self.native_create([SKILLS_SCOPE, BLUEPRINT_SCOPE])
        self.assertEqual(result.pk, old.pk)
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)
        self.publishers["queue_blueprint_update_for_user"].assert_called_once_with(
            self.user.pk, scope="character", character_id=self.character.character_id
        )
        self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ].assert_called_once()
        # Run the real consumer after native resolution, with only ESI simulated.
        with patch(
            "indy_hub.tasks.industry._fetch_character_skill_levels_with_token",
            return_value={},
        ) as fetch:
            # AA Example App
            from indy_hub.tasks.industry import (
                update_character_skill_snapshot_for_character,
            )

            result = update_character_skill_snapshot_for_character(
                self.user.pk, self.character.character_id
            )
        self.assertEqual(result["status"], "updated")
        self.assertEqual(fetch.call_args.args[0].pk, old.pk)

    def test_always_create_setting_preserves_both_native_tokens(self):
        old = self.token([SKILLS_SCOPE])
        cache.clear()
        with patch("esi.managers.app_settings.ESI_ALWAYS_CREATE_TOKEN", True):
            new = self.native_create([SKILLS_SCOPE])
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(Token.objects.filter(user=self.user).count(), 2)

    def test_autocommit_capability_is_ready_before_other_scopes_and_repeated_events_coalesce(
        self,
    ):
        token = self.token()
        skill_publish = self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ]
        bp_publish = self.publishers["queue_blueprint_update_for_user"]
        token.scopes.add(Scope.objects.get_or_create(name=SKILLS_SCOPE)[0])
        skill_publish.assert_called_once()
        bp_publish.assert_not_called()
        token.scopes.add(Scope.objects.get_or_create(name=BLUEPRINT_SCOPE)[0])
        bp_publish.assert_called_once()
        skill_publish.assert_called_once()
        token.scopes.remove(Scope.objects.get(name=SKILLS_SCOPE))
        token.scopes.add(Scope.objects.get(name=SKILLS_SCOPE))
        skill_publish.assert_called_once()

    def test_reverse_scope_add_is_supported(self):
        token = self.token()
        scope = Scope.objects.get_or_create(name=SKILLS_SCOPE)[0]
        scope.token_set.add(token)
        self.publishers[
            "update_character_skill_snapshot_for_character.apply_async"
        ].assert_called_once()
