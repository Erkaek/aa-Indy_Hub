"""Historical fixtures must remain inert and usable across SQL backends."""

# Standard Library
from unittest.mock import Mock, PropertyMock, patch

# Django
from django.contrib.auth.models import User
from django.db import connection
from django.db.models.signals import post_save
from django.test import TestCase

# Alliance Auth
from esi.models import Scope, Token

# AA Example App
from indy_hub.models import MaterialExchangeConfig
from indy_hub.tests.fixtures import create_raw_fixture


class RawFixtureTests(TestCase):
    def watch_saves(self, model):
        receiver = Mock()
        post_save.connect(receiver, sender=model, weak=False)
        self.addCleanup(post_save.disconnect, receiver, sender=model)
        return receiver

    def test_tokens_support_relations_without_generated_bulk_keys_or_signals(self):
        user = User.objects.create_user("raw-fixture-owner")
        receiver = self.watch_saves(Token)
        # Exercise the MySQL limitation even when this suite uses SQLite.
        with (
            patch.object(
                type(connection.features),
                "can_return_rows_from_bulk_insert",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch.object(Token, "save", side_effect=AssertionError("Token.save")),
        ):
            tokens = [
                create_raw_fixture(
                    Token(
                        user=user,
                        character_id=9_900_000 + index,
                        character_name="Historical Pilot",
                        access_token="synthetic-access",
                        refresh_token="synthetic-refresh",
                    )
                )
                for index in range(2)
            ]
        scope = Scope.objects.get_or_create(name="esi-skills.read_skills.v1")[0]
        for token in tokens:
            token.scopes.add(scope)
            token.refresh_from_db()
            self.assertEqual(token.user_id, user.pk)
            self.assertEqual(list(token.scopes.all()), [scope])
        self.assertNotEqual(tokens[0].pk, tokens[1].pk)
        receiver.assert_not_called()

    def test_configuration_fixture_avoids_save_dispatch(self):
        receiver = self.watch_saves(MaterialExchangeConfig)
        with patch.object(
            MaterialExchangeConfig, "save", side_effect=AssertionError("config.save")
        ):
            config = create_raw_fixture(
                MaterialExchangeConfig(corporation_id=980001, structure_id=60003760)
            )
        config.refresh_from_db()
        self.assertEqual(config.corporation_id, 980001)
        receiver.assert_not_called()
