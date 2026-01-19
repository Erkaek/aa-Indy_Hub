"""Tests for character asset refresh caching structure names."""

# Standard Library
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

# AA Example App
from indy_hub.models import CachedCharacterAsset, CachedStructureName
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
    def test_refresh_character_assets_triggers_structure_name_cache(self) -> None:
        user = User.objects.create_user("assets_user", password="secret123")

        character_id = 12345
        container_item_id = 1044300603008
        structure_id = 1042090993674

        fake_tokens = _FakeTokenQuerySet([_FakeToken(character_id=character_id)])

        assets_payload = [
            {
                "item_id": container_item_id,
                "location_id": structure_id,
                "location_flag": "Hangar",
                "type_id": 999,
                "quantity": 1,
                "is_singleton": True,
                "is_blueprint": False,
            }
        ]

        def resolve_side_effect(structure_ids, character_id=None, user=None, **kwargs):
            now = timezone.now()
            for sid in structure_ids:
                CachedStructureName.objects.update_or_create(
                    structure_id=int(sid),
                    defaults={"name": f"Structure {sid}", "last_resolved": now},
                )
            return {int(sid): f"Structure {sid}" for sid in structure_ids}

        with (
            patch.object(asset_cache.Token.objects, "filter", return_value=fake_tokens),
            patch.object(
                asset_cache.shared_client,
                "fetch_character_assets",
                return_value=assets_payload,
            ),
            patch.object(
                asset_cache,
                "resolve_structure_names",
                side_effect=resolve_side_effect,
            ) as mocked_resolve,
        ):
            refreshed_assets, scope_missing = asset_cache._refresh_character_assets(
                user
            )

        self.assertFalse(scope_missing)
        self.assertEqual(len(refreshed_assets), 1)

        # Ensure assets got written with new fields.
        row = CachedCharacterAsset.objects.get(user=user)
        self.assertEqual(row.item_id, container_item_id)
        self.assertEqual(row.raw_location_id, structure_id)
        self.assertEqual(row.location_id, structure_id)

        # Ensure we attempted to resolve/cache the structure name.
        self.assertTrue(mocked_resolve.called)
        self.assertTrue(
            CachedStructureName.objects.filter(structure_id=structure_id).exists()
        )
