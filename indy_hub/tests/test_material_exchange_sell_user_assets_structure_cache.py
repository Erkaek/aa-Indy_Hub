"""Tests for material exchange sell-user asset refresh warming structure names."""

# Standard Library
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import CachedCharacterAsset, CachedStructureName
from indy_hub.tasks import material_exchange


class _FakeTokenQuerySet(list):
    def require_scopes(self, scopes):
        return self

    def exists(self):
        return True


class MaterialExchangeSellAssetsStructureCacheTests(TestCase):
    def test_refresh_sell_user_assets_warms_structure_name_cache(self) -> None:
        user = User.objects.create_user("me_assets_user", password="secret123")

        character_id = 12345
        character = EveCharacter.objects.create(
            character_id=character_id,
            character_name="Test Char",
            corporation_id=2000000,
            corporation_name="Test Corp",
            corporation_ticker="TEST",
            alliance_id=None,
            alliance_name="",
            alliance_ticker="",
            faction_id=None,
            faction_name="",
        )
        CharacterOwnership.objects.create(
            user=user,
            character=character,
            owner_hash=f"hash-{character_id}-{user.id}",
        )

        container_item_id = 1044300603008
        structure_id = 1042090993674

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

        fake_tokens = _FakeTokenQuerySet([object()])

        with (
            patch.object(
                material_exchange.Token.objects,
                "filter",
                return_value=fake_tokens,
            ),
            patch.object(
                material_exchange.shared_client,
                "fetch_character_assets",
                return_value=assets_payload,
            ),
            patch.object(
                material_exchange,
                "resolve_structure_names",
                side_effect=resolve_side_effect,
            ) as mocked_resolve,
        ):
            material_exchange.refresh_material_exchange_sell_user_assets(int(user.id))

        # Ensure the cached assets were replaced and contain the new location fields.
        row = CachedCharacterAsset.objects.get(user=user)
        self.assertEqual(row.item_id, container_item_id)
        self.assertEqual(row.raw_location_id, structure_id)
        self.assertEqual(row.location_id, structure_id)

        # Ensure we attempted to resolve/cache the structure name.
        self.assertTrue(mocked_resolve.called)
        self.assertTrue(
            CachedStructureName.objects.filter(structure_id=structure_id).exists()
        )
