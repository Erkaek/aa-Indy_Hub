# Standard Library
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.errors import TokenError
from esi.models import Scope, Token

# AA Example App
from indy_hub.tasks.industry import (
    CORP_BLUEPRINT_SCOPE_SET,
    MATERIAL_EXCHANGE_SCOPE_SET,
)
from indy_hub.views.user import _collect_corporation_scope_status


class CorporationScopeStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("corporation-scope-status")
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="indy_hub",
                codename="can_manage_corp_bp_requests",
            )
        )
        self.character = EveCharacter.objects.create(
            character_id=199999001,
            character_name="Scope Pilot",
            corporation_id=2000000,
            corporation_name="Scope Corp",
            corporation_ticker="SCOPE",
        )
        CharacterOwnership.objects.create(
            user=self.user, character=self.character, owner_hash="scope-owner"
        )
        for name, value in (
            ("get_corporation_name", "Scope Corp"),
            ("get_character_name", "Scope Pilot"),
        ):
            patcher = patch(f"indy_hub.views.user.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _make_token(self, scopes, *, expired=False):
        # Populate recorded tokens without triggering token-link sync tasks.
        token = Token.objects.bulk_create(
            [
                Token(
                    user=self.user,
                    character_id=self.character.character_id,
                    character_name=self.character.character_name,
                    character_owner_hash="scope-owner",
                    token_type="Character",
                    access_token="access",
                    refresh_token="refresh",
                )
            ]
        )[0]
        Token.scopes.through.objects.bulk_create(
            [
                Token.scopes.through(
                    token_id=token.pk,
                    scope_id=Scope.objects.get_or_create(name=name)[0].pk,
                )
                for name in scopes
            ]
        )
        if expired:
            Token.objects.filter(pk=token.pk).update(
                created=timezone.now() - timedelta(days=1)
            )
        return token

    def test_unrelated_expired_token_is_not_refreshed_or_deleted(self):
        mail_token = self._make_token(["esi-mail.read_mail.v1"], expired=True)
        self._make_token(CORP_BLUEPRINT_SCOPE_SET)

        with patch.object(
            Token, "refresh", side_effect=TokenError("invalid token")
        ) as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False
            )

        refresh.assert_not_called()
        self.assertTrue(Token.objects.filter(pk=mail_token.pk).exists())
        self.assertTrue(status[0]["blueprint"]["has_scope"])

    def test_relevant_expired_token_is_refreshed_once_for_multiple_scopes(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET, expired=True)

        def refresh_token(instance, **kwargs):
            Token.objects.filter(pk=instance.pk).update(created=timezone.now())

        with patch.object(
            Token, "refresh", autospec=True, side_effect=refresh_token
        ) as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False
            )

        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0].pk, token.pk)
        self.assertTrue(status[0]["blueprint"]["has_scope"])
        self.assertTrue(status[0]["assets"]["has_scope"])
        self.assertCountEqual(status[0]["available_scopes"], CORP_BLUEPRINT_SCOPE_SET)

    def test_passive_collection_does_not_refresh_expired_tokens(self):
        token = self._make_token(CORP_BLUEPRINT_SCOPE_SET, expired=True)

        with patch.object(Token, "refresh") as refresh:
            status = _collect_corporation_scope_status(
                self.user, allow_live_role_fetch=False, validate_tokens=False
            )

        refresh.assert_not_called()
        self.assertEqual(status, [])
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_material_exchange_requires_all_scopes_on_one_token(self):
        self._make_token(MATERIAL_EXCHANGE_SCOPE_SET[:-1])
        self._make_token(MATERIAL_EXCHANGE_SCOPE_SET[-1:])

        status = _collect_corporation_scope_status(
            self.user, allow_live_role_fetch=False, validate_tokens=False
        )

        self.assertFalse(status[0]["material_exchange"]["has_scope"])
        self.assertCountEqual(
            status[0]["available_scopes"], MATERIAL_EXCHANGE_SCOPE_SET
        )
