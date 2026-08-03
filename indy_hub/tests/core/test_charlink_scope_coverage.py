# Standard Library
import importlib
import importlib.util
from unittest import skipUnless

# Django
from django.apps import apps
from django.contrib.auth.models import User
from django.db.models import Exists, OuterRef
from django.test import TestCase

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Scope, Token

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

    def test_returns_false_when_token_missing_one_scope(self):
        _make_token(self.user, self.character, _SCOPES[:-1])
        self.assertFalse(self._check(self.character, _SCOPES))

    def test_returns_false_when_no_token_exists(self):
        self.assertFalse(self._check(self.character, _SCOPES))

    def test_returns_false_when_no_character_ownership(self):
        CharacterOwnership.objects.filter(character=self.character).delete()
        _make_token(self.user, self.character, _SCOPES)
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
                character_id_ref=OuterRef("pk"),
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
