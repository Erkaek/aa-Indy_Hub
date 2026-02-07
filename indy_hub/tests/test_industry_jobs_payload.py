"""Tests for industry job payload validation."""

# Standard Library
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.tasks.industry import update_industry_jobs_for_user


class _FakeTokenQuerySet:
    def require_valid(self):
        return self

    def require_scopes(self, scopes):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def exists(self):
        return True


class _FakeTokenManager:
    def filter(self, *args, **kwargs):
        return _FakeTokenQuerySet()


class _FakeToken:
    objects = _FakeTokenManager()


class IndustryJobsPayloadTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("jobs-user", password="secret123")
        character_id = 9000001
        character = EveCharacter.objects.create(
            character_id=character_id,
            character_name="Jobs Tester",
            corporation_id=2000001,
            corporation_name="Test Corp",
            corporation_ticker="TEST",
            alliance_id=None,
            alliance_name="",
            alliance_ticker="",
            faction_id=None,
            faction_name="",
        )
        CharacterOwnership.objects.create(
            user=self.user,
            character=character,
            owner_hash=f"hash-{character_id}-{self.user.id}",
        )

    def test_skips_non_list_payload(self) -> None:
        with (
            patch("esi.models.Token", _FakeToken),
            patch(
                "indy_hub.tasks.industry.shared_client.fetch_character_industry_jobs",
                return_value="not-a-list",
            ),
            patch("indy_hub.tasks.industry.logger.warning") as warning_logger,
        ):
            update_industry_jobs_for_user(self.user.id)

        self.assertTrue(
            any(
                "unexpected payload type" in (call.args[0] if call.args else "")
                for call in warning_logger.call_args_list
            ),
            "Expected warning about unexpected payload type",
        )

    def test_skips_non_dict_job_items(self) -> None:
        with (
            patch("esi.models.Token", _FakeToken),
            patch(
                "indy_hub.tasks.industry.shared_client.fetch_character_industry_jobs",
                return_value=["bad-item"],
            ),
            patch("indy_hub.tasks.industry.logger.warning") as warning_logger,
        ):
            update_industry_jobs_for_user(self.user.id)

        self.assertTrue(
            any(
                "unexpected payload type" in (call.args[0] if call.args else "")
                for call in warning_logger.call_args_list
            ),
            "Expected warning about unexpected job item type",
        )
