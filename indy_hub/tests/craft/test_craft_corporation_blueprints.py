"""Tests for corporation blueprint sources in personal crafting projects."""

# Standard Library
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# AA Example App
from indy_hub.models import Blueprint
from indy_hub.services.production_projects import _resolve_user_blueprint_inventory


class CraftCorporationBlueprintInventoryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("corp-crafter", password="secret")
        self.provider = User.objects.create_user("corp-provider", password="secret")
        self.type_id = 900001
        self.corporation_id = 2_000_001

    def _create_blueprint(
        self,
        *,
        owner_user: User,
        item_id: int,
        owner_kind: str,
        material_efficiency: int,
        time_efficiency: int,
        corporation_id: int | None = None,
    ) -> Blueprint:
        return Blueprint.objects.create(
            owner_user=owner_user,
            owner_kind=owner_kind,
            character_id=1001 if owner_kind == Blueprint.OwnerKind.CHARACTER else None,
            corporation_id=corporation_id,
            corporation_name="Test Corp" if corporation_id else "",
            item_id=item_id,
            blueprint_id=item_id,
            type_id=self.type_id,
            location_id=60003760,
            location_flag="Hangar",
            quantity=-1,
            bp_type=Blueprint.BPType.ORIGINAL,
            material_efficiency=material_efficiency,
            time_efficiency=time_efficiency,
            runs=-1,
            type_name="Test Blueprint",
        )

    def test_disabled_project_uses_personal_blueprints_only(self) -> None:
        self._create_blueprint(
            owner_user=self.user,
            item_id=1,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            material_efficiency=5,
            time_efficiency=10,
        )
        self._create_blueprint(
            owner_user=self.provider,
            item_id=2,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=self.corporation_id,
            material_efficiency=10,
            time_efficiency=20,
        )

        with patch(
            "indy_hub.services.production_projects.get_viewable_corporation_ids"
        ) as mock_get_viewable:
            result = _resolve_user_blueprint_inventory(
                user=self.user,
                blueprint_type_ids=[self.type_id],
                include_corp=False,
            )

        mock_get_viewable.assert_not_called()
        self.assertEqual(result[self.type_id]["original"], {"me": 5, "te": 10})
        self.assertFalse(result[self.type_id]["corp_source"])

    def test_enabled_project_uses_authorized_corp_blueprint_as_fallback(self) -> None:
        self._create_blueprint(
            owner_user=self.provider,
            item_id=3,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=self.corporation_id,
            material_efficiency=10,
            time_efficiency=20,
        )

        with patch(
            "indy_hub.services.production_projects.get_viewable_corporation_ids",
            return_value={self.corporation_id},
        ):
            result = _resolve_user_blueprint_inventory(
                user=self.user,
                blueprint_type_ids=[self.type_id],
                include_corp=True,
            )

        self.assertEqual(result[self.type_id]["original"], {"me": 10, "te": 20})
        self.assertTrue(result[self.type_id]["corp_source"])

    def test_enabled_project_does_not_expose_unauthorized_corp_blueprint(
        self,
    ) -> None:
        self._create_blueprint(
            owner_user=self.provider,
            item_id=4,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=self.corporation_id,
            material_efficiency=10,
            time_efficiency=20,
        )

        with patch(
            "indy_hub.services.production_projects.get_viewable_corporation_ids",
            return_value=set(),
        ):
            result = _resolve_user_blueprint_inventory(
                user=self.user,
                blueprint_type_ids=[self.type_id],
                include_corp=True,
            )

        self.assertEqual(result, {})

    def test_personal_blueprint_precedes_better_corp_blueprint(self) -> None:
        self._create_blueprint(
            owner_user=self.user,
            item_id=5,
            owner_kind=Blueprint.OwnerKind.CHARACTER,
            material_efficiency=3,
            time_efficiency=6,
        )
        self._create_blueprint(
            owner_user=self.provider,
            item_id=6,
            owner_kind=Blueprint.OwnerKind.CORPORATION,
            corporation_id=self.corporation_id,
            material_efficiency=10,
            time_efficiency=20,
        )

        with patch(
            "indy_hub.services.production_projects.get_viewable_corporation_ids",
            return_value={self.corporation_id},
        ):
            result = _resolve_user_blueprint_inventory(
                user=self.user,
                blueprint_type_ids=[self.type_id],
                include_corp=True,
            )

        entry = result[self.type_id]
        self.assertEqual(entry["original"], {"me": 3, "te": 6})
        self.assertEqual(entry["corp_original"], {"me": 10, "te": 20})
        self.assertFalse(entry["corp_source"])
