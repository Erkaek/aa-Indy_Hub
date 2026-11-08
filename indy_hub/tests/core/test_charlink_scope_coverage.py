# Standard Library
import importlib.util
import json
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

# Django
from django.apps import apps
from django.contrib.auth.models import Group, Permission, User
from django.db.models import Exists, OuterRef
from django.test import TestCase

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Scope, Token

# AA Example App
from indy_hub.tests.fixtures import create_raw_fixture

_CHARLINK_AVAILABLE = importlib.util.find_spec(
    "charlink"
) is not None and apps.is_installed("charlink")


def _make_user(username: str) -> User:
    return User.objects.create_user(username, password="x")


def _make_character(character_id: int) -> EveCharacter:
    character, _ = EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={
            "character_name": f"Pilot {character_id}",
            "corporation_id": 2_000_000,
            "corporation_name": "Test Corp",
            "corporation_ticker": "TEST",
        },
    )
    return character


def _link(user: User, character: EveCharacter) -> None:
    CharacterOwnership.objects.get_or_create(
        user=user,
        character=character,
        defaults={"owner_hash": f"hash-{character.character_id}-{user.id}"},
    )


def _make_token(user: User, character: EveCharacter, scope_names: list[str]) -> Token:
    token = Token.objects.create(
        user=user,
        character_id=int(character.character_id),
        character_name=character.character_name,
        character_owner_hash=f"hash-{character.character_id}",
        token_type="Character",
        access_token="access",
        refresh_token="refresh",
    )
    for name in scope_names:
        scope, _ = Scope.objects.get_or_create(name=name)
        token.scopes.add(scope)
    return token


_SCOPES = [
    "esi-assets.read_assets.v1",
    "esi-characters.read_blueprints.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-skills.read_skills.v1",
    "esi-universe.read_structures.v1",
]


@skipUnless(_CHARLINK_AVAILABLE, "charlink not in INSTALLED_APPS")
class HasScopeCoverageTests(TestCase):
    def setUp(self):
        # Deferred import: charlink may not be in INSTALLED_APPS in all envs
        # AA Example App
        from indy_hub.thirdparty.charlink_hook import _has_scope_coverage

        self._check = _has_scope_coverage

        self.user = _make_user("scopeuser")
        self.character = _make_character(1_001_001)
        _link(self.user, self.character)

    def test_returns_true_when_single_token_covers_all_scopes(self):
        _make_token(self.user, self.character, _SCOPES)
        self.assertTrue(self._check(self.character, _SCOPES))

    def test_listing_never_validates_oauth(self):
        _make_token(self.user, self.character, _SCOPES)
        with patch("esi.managers.TokenQueryset.require_valid") as validate:
            self.assertTrue(self._check(self.character, _SCOPES))
        validate.assert_not_called()

    def test_returns_false_when_token_missing_one_scope(self):
        _make_token(self.user, self.character, _SCOPES[:-1])
        self.assertFalse(self._check(self.character, _SCOPES))

    def test_returns_false_when_no_token_exists(self):
        self.assertFalse(self._check(self.character, _SCOPES))

    def test_returns_false_when_no_character_ownership(self):
        _make_token(self.user, self.character, _SCOPES)
        # AA creates ownership on Token post_save; remove it after that boundary.
        CharacterOwnership.objects.filter(character=self.character).delete()
        self.assertFalse(self._check(self.character, _SCOPES))

    def test_returns_false_when_scopes_spread_across_multiple_tokens(self):
        # Scopes split across two tokens: no single token qualifies
        _make_token(self.user, self.character, _SCOPES[:3])
        _make_token(self.user, self.character, _SCOPES[3:])
        self.assertFalse(self._check(self.character, _SCOPES))


@skipUnless(_CHARLINK_AVAILABLE, "charlink not in INSTALLED_APPS")
class CharacterScopeCoverageAnnotationTests(TestCase):
    """Verify that the Exists annotation correctly drives the Charlink listing."""

    def setUp(self):
        # AA Example App
        from indy_hub.thirdparty.charlink_hook import _character_scope_coverage_queryset

        self._queryset_fn = _character_scope_coverage_queryset

        self.user = _make_user("annouser")
        self.character = _make_character(1_002_001)
        _link(self.user, self.character)

    def _annotate(self, scope_names):
        annotation = Exists(
            self._queryset_fn(
                scope_names=scope_names,
                character_id_ref=OuterRef("character_id"),
            )
        )
        return (
            EveCharacter.objects.filter(character_id=self.character.character_id)
            .annotate(is_linked=annotation)
            .get()
            .is_linked
        )

    def test_annotation_true_when_token_covers_all_scopes(self):
        _make_token(self.user, self.character, _SCOPES)
        self.assertTrue(self._annotate(_SCOPES))

    def test_annotation_false_when_token_missing_one_scope(self):
        _make_token(self.user, self.character, _SCOPES[:-1])
        self.assertFalse(self._annotate(_SCOPES))

    def test_annotation_false_when_no_token(self):
        self.assertFalse(self._annotate(_SCOPES))

    def test_annotation_false_when_scopes_split_across_tokens(self):
        # ESI OAuth grants scopes per-token; split tokens do not qualify
        _make_token(self.user, self.character, _SCOPES[:3])
        _make_token(self.user, self.character, _SCOPES[3:])
        self.assertFalse(self._annotate(_SCOPES))

    def test_foreign_user_token_matches_python_owner_refusal(self):
        # A transferred character may still have a historical shared grant.
        # Bypass Token post_save, which would transfer the AA ownership itself.
        other = _make_user("previousowner")
        token = create_raw_fixture(
            Token(
                user=other,
                character_id=self.character.character_id,
                character_name=self.character.character_name,
                character_owner_hash="historical-owner",
                token_type="Character",
                access_token="synthetic",
                refresh_token="synthetic",
            )
        )
        token.scopes.set([Scope.objects.get_or_create(name=s)[0] for s in _SCOPES])
        # AA Example App
        from indy_hub.thirdparty.charlink_hook import _has_scope_coverage

        with patch("esi.managers.TokenQueryset.require_valid") as validate:
            self.assertFalse(_has_scope_coverage(self.character, _SCOPES))
            self.assertFalse(self._annotate(_SCOPES))
        validate.assert_not_called()


@skipUnless(_CHARLINK_AVAILABLE, "charlink not in INSTALLED_APPS")
class CharLinkProfileContractTests(TestCase):
    def setUp(self):
        # AA Example App
        from indy_hub.thirdparty.charlink_hook import app_import

        self.imports = app_import.imports
        self.app_import = app_import
        self.user = _make_user("profile-charlink")

    def test_profiles_use_exact_historical_scopes_permissions_and_defaults(self):
        before = json.loads(
            Path(__file__).with_name("authorization_scopes_before.json").read_text()
        )
        expected_permissions = {
            "personal": ["can_access_indy_hub"],
            "corporation": ["can_access_indy_hub", "can_manage_corp_bp_requests"],
            "materialhub": ["can_access_indy_hub", "can_manage_material_hub"],
        }
        self.assertEqual(
            [item.unique_id for item in self.imports], list(expected_permissions)
        )
        permissions = list(
            Permission.objects.filter(
                content_type__app_label="indy_hub",
                codename__in=[
                    "can_access_indy_hub",
                    "can_manage_corp_bp_requests",
                    "can_manage_material_hub",
                ],
            )
        )
        for item in self.imports:
            with self.subTest(profile=item.unique_id):
                item.validate_import()
                self.assertEqual(item.scopes, before["charlink_" + item.unique_id])
                self.assertEqual(
                    item.default_initial_selection, item.unique_id == "personal"
                )
                for mask in range(8):
                    self.user.user_permissions.set(
                        [
                            permission
                            for i, permission in enumerate(permissions)
                            if mask & (1 << i)
                        ]
                    )
                    fresh = User.objects.get(pk=self.user.pk)
                    expected = fresh.has_perms(
                        [
                            "indy_hub." + name
                            for name in expected_permissions[item.unique_id]
                        ]
                    )
                    self.assertEqual(item.check_permissions(fresh), expected)
                superuser = User(username="synthetic-admin", is_superuser=True)
                self.assertTrue(item.check_permissions(superuser))

    def test_group_permissions_and_existing_audience_selector_are_preserved(self):
        group = Group.objects.create(name="profile-managers")
        permission = Permission.objects.get(
            content_type__app_label="indy_hub", codename="can_manage_corp_bp_requests"
        )
        group.permissions.add(permission)
        self.user.groups.add(group)
        corporation = self.imports[1]
        # Native CharLink's reporting audience is not its consent permission check.
        self.assertIn(self.user, corporation.get_users_with_perms())
        self.assertFalse(
            corporation.check_permissions(User.objects.get(pk=self.user.pk))
        )
        group.permissions.add(
            Permission.objects.get(
                content_type__app_label="indy_hub", codename="can_access_indy_hub"
            )
        )
        self.assertTrue(
            corporation.check_permissions(User.objects.get(pk=self.user.pk))
        )

    def test_python_and_sql_agree_for_all_profiles_and_correlated_characters(self):
        # Third Party
        from charlink.utils import chars_annotate_linked_apps

        all_scopes = sorted({scope for item in self.imports for scope in item.scopes})
        characters = [_make_character(1_905_001 + i) for i in range(6)]
        for character in characters[:-1]:
            _link(self.user, character)
        _make_token(self.user, characters[0], all_scopes)
        _make_token(self.user, characters[1], ["esi-universe.read_structures.v1"])
        for scope in all_scopes:
            _make_token(self.user, characters[2], [scope])
        other = _make_user("charlink-foreign-owner")
        foreign = create_raw_fixture(
            Token(
                user=other,
                character_id=characters[3].character_id,
                character_name=characters[3].character_name,
                character_owner_hash="historical",
                token_type="Character",
                access_token="synthetic",
                refresh_token="synthetic",
            )
        )
        foreign.scopes.set([Scope.objects.get(name=s) for s in all_scopes])
        unowned = create_raw_fixture(
            Token(
                user=self.user,
                character_id=characters[5].character_id,
                character_name=characters[5].character_name,
                character_owner_hash="unowned",
                token_type="Character",
                access_token="synthetic",
                refresh_token="synthetic",
            )
        )
        unowned.scopes.set([Scope.objects.get(name=s) for s in all_scopes])
        with (
            patch("esi.managers.TokenQueryset.require_valid") as validate,
            patch.object(Token, "refresh") as refresh,
        ):
            rows = chars_annotate_linked_apps(
                EveCharacter.objects.filter(pk__in=[c.pk for c in characters]),
                self.imports,
            )
            for row in rows:
                expected = row.pk == characters[0].pk
                for item in self.imports:
                    with self.subTest(
                        character=row.character_id, profile=item.unique_id
                    ):
                        self.assertEqual(item.is_character_added(row), expected)
                        self.assertEqual(getattr(row, item.get_query_id()), expected)
        validate.assert_not_called()
        refresh.assert_not_called()

    def test_persisted_charlink_selection_is_not_overwritten_by_registry(self):
        # Third Party
        from charlink.models import AppSettings

        self.user.is_superuser = True
        for item in self.imports:
            AppSettings.objects.update_or_create(
                app_name=item.get_query_id(),
                defaults={
                    "ignored": False,
                    "default_selection": not item.default_initial_selection,
                },
            )
        fields = self.app_import.get_form_fields(self.user)
        for item in self.imports:
            self.assertEqual(
                fields[item.get_query_id()].initial, not item.default_initial_selection
            )
