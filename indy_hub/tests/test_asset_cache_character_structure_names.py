"""Tests for character asset refresh caching structure names."""

# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

# AA Example App
from indy_hub.models import CachedCharacterAsset
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
