"""Tests for Indy Hub admin user visibility scope rules."""

from __future__ import annotations

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter

# AA Example App
# Local
from indy_hub.services.admin_user_visibility import (
    can_access_indy_hub_user_admin_scope,
    get_managed_corporation_ids_for_user_admin_scope,
    get_visible_indy_hub_user_ids_for_admin_scope,
    get_visible_indy_hub_users_for_admin_scope,
)

User = get_user_model()


def _grant_corp_admin_permission(user: User) -> None:
    permission = Permission.objects.get(codename="can_manage_corp_bp_requests")
    user.user_permissions.add(permission)


def _link_character(
    user: User,
    *,
    character_id: int,
    corporation_id: int,
    corporation_name: str,
) -> None:
    character, _ = EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={
            "character_name": f"Pilot {character_id}",
            "corporation_id": corporation_id,
            "corporation_name": corporation_name,
            "corporation_ticker": f"C{corporation_id}",
        },
    )
    if character.corporation_id != corporation_id:
        character.corporation_id = corporation_id
        character.corporation_name = corporation_name
        character.save(update_fields=["corporation_id", "corporation_name"])

    CharacterOwnership.objects.update_or_create(
        user=user,
        character=character,
        defaults={"owner_hash": f"hash-{user.id}-{character_id}"},
    )


class AdminUserVisibilityScopeTests(TestCase):
    def setUp(self) -> None:
        self.superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="secret123",
        )

        self.manager = User.objects.create_user("manager", password="secret123")
        _grant_corp_admin_permission(self.manager)
        _link_character(
            self.manager,
            character_id=9000001,
            corporation_id=1001,
            corporation_name="Managed Corp",
        )

        self.other_manager = User.objects.create_user(
            "other_manager",
            password="secret123",
        )
        _grant_corp_admin_permission(self.other_manager)
        _link_character(
            self.other_manager,
            character_id=9000002,
            corporation_id=2002,
            corporation_name="Other Corp",
        )

        self.member_same_corp = User.objects.create_user(
            "member_same",
            password="secret123",
        )
        _link_character(
            self.member_same_corp,
            character_id=9000011,
            corporation_id=1001,
            corporation_name="Managed Corp",
        )

        self.member_other_corp = User.objects.create_user(
            "member_other",
            password="secret123",
        )
        _link_character(
            self.member_other_corp,
            character_id=9000012,
            corporation_id=3003,
            corporation_name="Third Corp",
        )

        self.member_multi_corp = User.objects.create_user(
            "member_multi",
            password="secret123",
        )
        _link_character(
            self.member_multi_corp,
            character_id=9000013,
            corporation_id=1001,
            corporation_name="Managed Corp",
        )
        _link_character(
            self.member_multi_corp,
            character_id=9000014,
            corporation_id=4004,
            corporation_name="Fourth Corp",
        )

        self.regular_user = User.objects.create_user("regular", password="secret123")

    def test_superuser_can_access_scope(self) -> None:
        self.assertTrue(can_access_indy_hub_user_admin_scope(self.superuser))

    def test_regular_user_cannot_access_scope(self) -> None:
        self.assertFalse(can_access_indy_hub_user_admin_scope(self.regular_user))

    def test_manager_cannot_access_scope_even_with_corp_admin_permission(self) -> None:
        self.assertFalse(can_access_indy_hub_user_admin_scope(self.manager))

    def test_manager_managed_corporations_empty_without_scope_access(self) -> None:
        self.assertEqual(
            get_managed_corporation_ids_for_user_admin_scope(self.manager),
            set(),
        )

    def test_superuser_sees_all_users(self) -> None:
        visible_ids = set(
            get_visible_indy_hub_users_for_admin_scope(self.superuser).values_list(
                "id", flat=True
            )
        )
        all_ids = set(User.objects.values_list("id", flat=True))
        self.assertEqual(visible_ids, all_ids)

    def test_manager_sees_no_users(self) -> None:
        visible_ids = get_visible_indy_hub_user_ids_for_admin_scope(self.manager)
        self.assertEqual(visible_ids, set())

    def test_admin_without_permission_sees_no_users(self) -> None:
        visible_ids = get_visible_indy_hub_user_ids_for_admin_scope(self.regular_user)
        self.assertEqual(visible_ids, set())

    def test_manager_with_permission_but_no_corporation_sees_no_users(self) -> None:
        manager_no_corp = User.objects.create_user(
            "manager_no_corp",
            password="secret123",
        )
        _grant_corp_admin_permission(manager_no_corp)

        visible_ids = get_visible_indy_hub_user_ids_for_admin_scope(manager_no_corp)
        self.assertEqual(visible_ids, set())
