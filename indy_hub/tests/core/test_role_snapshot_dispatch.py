"""Role dispatch must use the same owner/corporation freshness as consumers."""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Scope, Token

# AA Example App
from indy_hub.models import CharacterRoles
from indy_hub.tasks.housekeeping import (
    _select_character_targets_for_stale_snapshots,
    refresh_stale_snapshots,
)
from indy_hub.tasks.user import CORP_ROLES_SCOPE, update_user_roles_snapshots
from indy_hub.tests.fixtures import create_raw_fixture


class RoleSnapshotDispatchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("current-role-owner")
        self.previous_user = User.objects.create_user("previous-role-owner")
        self.character = self.make_character(199997001)

    def make_character(self, character_id):
        character = EveCharacter.objects.create(
            character_id=character_id,
            character_name=f"Role Pilot {character_id}",
            corporation_id=2000001,
            corporation_name="Current Corp",
            corporation_ticker="CUR",
        )
        CharacterOwnership.objects.create(
            user=self.user, character=character, owner_hash=f"owner-{character_id}"
        )
        return character

    def make_snapshot(self, character=None, **overrides):
        character = character or self.character
        values = {
            "owner_user": self.user,
            "corporation_id": character.corporation_id,
            "roles": ["Director"],
            **overrides,
        }
        return CharacterRoles.objects.create(
            character_id=character.character_id, **values
        )

    def make_token(self, user):
        token = create_raw_fixture(
            Token(
                user=user,
                character_id=self.character.character_id,
                character_name=self.character.character_name,
                character_owner_hash="synthetic-role-owner",
                access_token="synthetic-access",
                refresh_token="synthetic-refresh",
            )
        )
        scope, _ = Scope.objects.get_or_create(name=CORP_ROLES_SCOPE)
        Token.scopes.through.objects.bulk_create(
            [Token.scopes.through(token_id=token.pk, scope_id=scope.pk)]
        )

    def select_targets(self, pairs, now=None):
        return _select_character_targets_for_stale_snapshots(
            token_pairs=pairs,
            snapshot_model=CharacterRoles,
            stale_hours=24,
            now=now or timezone.now(),
        )

    def test_user_refresh_renews_fresh_mismatched_roles(self):
        self.make_token(self.user)
        for mismatch in (
            {"corporation_id": self.character.corporation_id + 1},
            {"owner_user": self.previous_user},
        ):
            with self.subTest(mismatch=mismatch):
                CharacterRoles.objects.all().delete()
                self.make_snapshot(**mismatch)
                with (
                    patch("indy_hub.tasks.user._is_user_active", return_value=True),
                    patch(
                        "indy_hub.tasks.user.shared_client.fetch_character_corporation_roles",
                        return_value={"roles": ["Factory_Manager"]},
                    ) as fetch,
                ):
                    result = update_user_roles_snapshots.run(self.user.pk)
                self.assertEqual(result, {"updated": 1, "skipped": 0, "failures": 0})
                fetch.assert_called_once()
                snapshot = CharacterRoles.objects.get(
                    character_id=self.character.character_id
                )
                self.assertEqual(snapshot.owner_user_id, self.user.pk)
                self.assertEqual(snapshot.corporation_id, self.character.corporation_id)
                self.assertEqual(snapshot.roles, ["Factory_Manager"])

    def test_fresh_matching_roles_are_skipped_by_both_paths(self):
        self.make_snapshot()
        pair = (self.user.pk, self.character.character_id)
        self.assertEqual(self.select_targets([pair]), [])
        with (
            patch("indy_hub.tasks.user._is_user_active", return_value=True),
            patch("indy_hub.tasks.user.update_character_roles_for_character") as update,
        ):
            result = update_user_roles_snapshots.run(self.user.pk)
        update.assert_not_called()
        self.assertEqual(result, {"updated": 0, "skipped": 1, "failures": 0})

    def test_periodic_dispatch_rechecks_mismatches_only_for_current_owner(self):
        self.make_token(self.user)
        self.make_token(self.previous_user)
        for mismatch in (
            {"corporation_id": self.character.corporation_id + 1},
            {"owner_user": self.previous_user},
        ):
            with self.subTest(mismatch=mismatch):
                CharacterRoles.objects.all().delete()
                self.make_snapshot(**mismatch)
                with (
                    patch(
                        "indy_hub.tasks.housekeeping.update_character_roles_for_character.apply_async"
                    ) as publish,
                    patch(
                        "indy_hub.tasks.user.shared_client.fetch_character_corporation_roles"
                    ) as fetch,
                ):
                    result = refresh_stale_snapshots.run()
                publish.assert_called_once_with(
                    args=(self.user.pk, self.character.character_id), priority=7
                )
                fetch.assert_not_called()
                self.assertEqual(result["roles_characters_queued"], 1)
                self.assertEqual(result["roles_users_queued"], 1)

    def test_selector_preserves_missing_stale_boundary_and_deduplication(self):
        now = timezone.now()
        snapshot = self.make_snapshot()
        CharacterRoles.objects.filter(pk=snapshot.pk).update(
            last_updated=now - timedelta(hours=24)
        )
        missing = self.make_character(199997002)
        pair = (self.user.pk, self.character.character_id)
        missing_pair = (self.user.pk, missing.character_id)
        self.assertEqual(
            self.select_targets(
                [
                    pair,
                    missing_pair,
                    pair,
                    (self.previous_user.pk, missing.character_id),
                ],
                now=now,
            ),
            [pair, missing_pair],
        )

    def test_unowned_token_pairs_do_not_dispatch_roles(self):
        CharacterOwnership.objects.filter(character=self.character).delete()
        self.assertEqual(
            self.select_targets([(self.user.pk, self.character.character_id)]), []
        )
