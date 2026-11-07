"""Tests for character asset refresh caching structure names."""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.errors import TokenError
from esi.managers import TokenQueryset
from esi.models import Scope, Token

# AA Example App
from indy_hub.models import CachedCharacterAsset, CharacterRoles
from indy_hub.services import asset_cache


class _FakeToken:
    def __init__(self, *, character_id: int):
        self.character_id = character_id
        self.character = type("Char", (), {"corporation_id": None})()


class _FakeTokenQuerySet(list):
    def require_scopes(self, scopes):
        return self

    def require_valid(self):
        return self

    def exists(self):
        return True


class CharacterAssetRefreshStructureNameTests(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_name_candidate_discovery_does_not_refresh_or_delete_shared_grants(self):
        cache.clear()
        self.addCleanup(cache.clear)
        user = User.objects.create_user("structure-candidate-owner")
        character = EveCharacter.objects.create(
            character_id=199998201,
            character_name="Candidate Pilot",
            corporation_id=2000082,
            corporation_name="Candidate Corp",
            corporation_ticker="SYN",
        )
        CharacterOwnership.objects.create(
            user=user, character=character, owner_hash="synthetic-candidate"
        )
        CharacterRoles.objects.create(
            owner_user=user,
            character_id=character.character_id,
            corporation_id=character.corporation_id,
            roles=["Director"],
        )
        scope = Scope.objects.get_or_create(name="esi-universe.read_structures.v1")[0]
        tokens = Token.objects.bulk_create(
            [
                Token(
                    user=user,
                    character_id=character.character_id,
                    character_name=character.character_name,
                    character_owner_hash="synthetic-candidate",
                    access_token="synthetic-access",
                    refresh_token="synthetic-refresh",
                    token_type="Character",
                )
                for _ in range(2)
            ]
        )
        Token.objects.filter(pk=tokens[0].pk).update(
            created=timezone.now() - timedelta(days=1)
        )
        Token.scopes.through.objects.bulk_create(
            [Token.scopes.through(token=token, scope=scope) for token in tokens]
        )
        private_structure = 1045806039392
        CachedCharacterAsset.objects.create(
            user=user,
            character_id=character.character_id,
            location_id=private_structure,
            location_flag="Hangar",
            type_id=34,
            quantity=1,
        )
        with (
            patch(
                "esi.managers.TokenQueryset.require_valid",
                autospec=True,
                side_effect=TokenQueryset.require_valid,
            ) as validate,
            patch.object(
                Token, "refresh", side_effect=TokenError("old grant")
            ) as renew,
            patch.object(asset_cache, "_cache_corp_structure_names", return_value={}),
            patch.object(
                asset_cache.shared_client,
                "resolve_ids_to_names",
                return_value={60003760: "Public station"},
            ),
            patch.object(asset_cache.shared_client, "fetch_structure_name") as fetch,
            patch("indy_hub.tasks.location.cache_structure_names_bulk.delay") as queue,
        ):
            result = asset_cache.resolve_structure_names(
                [60003760, private_structure],
                character_id=character.character_id,
                corporation_id=character.corporation_id,
                user=user,
                schedule_async=True,
            )
        self.assertEqual(result[60003760], "Public station")
        validate.assert_not_called()
        renew.assert_not_called()
        fetch.assert_not_called()
        queue.assert_called_once_with(
            [private_structure],
            character_id=character.character_id,
            owner_user_id=user.pk,
        )
        self.assertEqual(Token.objects.filter(pk__in=[t.pk for t in tokens]).count(), 2)

    def test_user_assets_cached_skips_esi_when_cache_is_younger_than_one_hour(
        self,
    ) -> None:
        user = User.objects.create_user("recent_assets_user", password="secret123")
        CachedCharacterAsset.objects.create(
            user=user,
            character_id=12345,
            location_id=60003760,
            location_flag="Hangar",
            type_id=34,
            quantity=100,
            synced_at=timezone.now() - timedelta(minutes=30),
        )

        with patch.object(asset_cache, "_refresh_character_assets") as refresh_mock:
            assets, scope_missing = asset_cache.get_user_assets_cached(
                user,
                allow_refresh=True,
                max_age_minutes=60,
            )

        refresh_mock.assert_not_called()
        self.assertFalse(scope_missing)
        self.assertEqual(assets[0]["type_id"], 34)

    def test_user_assets_cached_refreshes_when_cache_is_older_than_one_hour(
        self,
    ) -> None:
        user = User.objects.create_user("stale_assets_user", password="secret123")
        CachedCharacterAsset.objects.create(
            user=user,
            character_id=12345,
            location_id=60003760,
            location_flag="Hangar",
            type_id=34,
            quantity=100,
            synced_at=timezone.now() - timedelta(minutes=61),
        )
        refreshed_assets = [
            {
                "character_id": 12345,
                "location_id": 60003760,
                "location_flag": "Hangar",
                "type_id": 35,
                "quantity": 200,
                "is_singleton": False,
                "is_blueprint": False,
            }
        ]

        with patch.object(
            asset_cache,
            "_refresh_character_assets",
            return_value=(refreshed_assets, False),
        ) as refresh_mock:
            assets, scope_missing = asset_cache.get_user_assets_cached(
                user,
                allow_refresh=True,
                max_age_minutes=60,
            )

        refresh_mock.assert_called_once_with(user)
        self.assertFalse(scope_missing)
        self.assertEqual(assets, refreshed_assets)

    def test_build_user_asset_inventory_snapshot_groups_assets_by_location(
        self,
    ) -> None:
        user = User.objects.create_user("location_snapshot_user", password="secret123")
        cached_assets = [
            {
                "character_id": 12345,
                "location_id": 60003760,
                "location_name": "Jita IV - Moon 4",
                "type_id": 34,
                "quantity": 100,
                "is_blueprint": False,
            },
            {
                "character_id": 12345,
                "location_id": 60003760,
                "location_name": "Jita IV - Moon 4",
                "type_id": 35,
                "quantity": 50,
                "is_blueprint": False,
            },
            {
                "character_id": 67890,
                "location_id": 1029384756,
                "location_name": "Amarr Factory",
                "type_id": 34,
                "quantity": 25,
                "is_blueprint": False,
            },
        ]

        with patch.object(
            asset_cache,
            "get_user_assets_cached",
            return_value=(cached_assets, False),
        ):
            snapshot = asset_cache.build_user_asset_inventory_snapshot(
                user,
                allow_refresh=False,
            )

        self.assertEqual(snapshot["totals_by_type"]["34"], 125)
        self.assertEqual(len(snapshot["locations"]), 2)
        self.assertEqual(snapshot["locations"][0]["location_name"], "Amarr Factory")
        self.assertEqual(snapshot["locations"][0]["items_by_type"]["34"], 25)
        self.assertEqual(snapshot["locations"][1]["location_name"], "Jita IV - Moon 4")
        self.assertEqual(snapshot["locations"][1]["items_by_type"]["34"], 100)
        self.assertEqual(snapshot["locations"][1]["items_by_type"]["35"], 50)
