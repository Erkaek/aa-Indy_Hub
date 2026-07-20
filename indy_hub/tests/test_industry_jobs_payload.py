"""Tests for industry job payload validation."""

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
from indy_hub.models import CharacterOnlineStatus
from indy_hub.tasks.industry import (
    update_all_blueprints,
    update_all_industry_jobs,
    update_industry_jobs_for_user,
)


class _FakeTokenQuerySet:
    def require_valid(self):
        return self

    def require_scopes(self, scopes):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return _FakeToken()

    def exists(self):
        return True


class _FakeTokenManager:
    def filter(self, *args, **kwargs):
        return _FakeTokenQuerySet()


class _FakeToken:
    objects = _FakeTokenManager()


class _MissingTokenQuerySet(_FakeTokenQuerySet):
    def first(self):
        return None

    def exists(self):
        return False


class _MissingTokenManager:
    def filter(self, *args, **kwargs):
        return _MissingTokenQuerySet()


class _MissingToken:
    objects = _MissingTokenManager()


class IndustryJobsPayloadTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("jobs-user", password="secret123")
        self.user.last_login = self.user.date_joined
        self.user.save(update_fields=["last_login"])
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
        CharacterOnlineStatus.objects.create(
            owner_user=self.user,
            character_id=character_id,
            online=True,
            last_login=timezone.now(),
            last_logout=timezone.now(),
            logins=1,
        )

    def test_skips_non_list_payload(self) -> None:
        with (
            patch("indy_hub.tasks.industry.Token", _FakeToken),
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

    def test_skips_character_without_valid_job_token(self) -> None:
        with (
            patch("indy_hub.tasks.industry.Token", _MissingToken),
            patch(
                "indy_hub.tasks.industry._refresh_online_status_for_user",
            ),
            patch(
                "indy_hub.tasks.industry._is_user_active",
                return_value=True,
            ),
            patch(
                "indy_hub.tasks.industry.shared_client.fetch_character_industry_jobs",
            ) as fetch_jobs,
            patch("indy_hub.tasks.industry.logger.debug") as debug_logger,
        ):
            update_industry_jobs_for_user(self.user.id)

        fetch_jobs.assert_not_called()
        self.assertTrue(
            any(
                "missing token for scopes" in (call.args[0] if call.args else "")
                for call in debug_logger.call_args_list
            ),
            "Expected debug log about missing job token scopes",
        )

    def test_skips_non_dict_job_items(self) -> None:
        with (
            patch("indy_hub.tasks.industry.Token", _FakeToken),
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

    def test_bulk_job_updates_are_staggered_per_character(self) -> None:
        with (
            patch(
                "indy_hub.tasks.industry._select_industry_job_sync_user_ids",
                return_value=[101, 202, 303],
            ),
            patch(
                "indy_hub.tasks.industry._select_character_job_targets_for_users",
                return_value=[(101, 1001), (101, 1002), (202, 2001)],
            ),
            patch(
                "indy_hub.tasks.industry._select_corporation_job_user_ids_for_users",
                return_value=[202, 303],
            ),
            patch("indy_hub.tasks.industry._queue_staggered_industry_job_character_tasks") as queue_characters,
            patch("indy_hub.tasks.industry._queue_staggered_industry_job_corporation_tasks") as queue_corporations,
            patch("indy_hub.tasks.industry.emit_analytics_event"),
            patch("indy_hub.tasks.industry.update_all_industry_jobs.apply_async"),
            patch("indy_hub.tasks.industry.cache.add", return_value=True),
            patch("indy_hub.tasks.industry.cache.get", return_value=None),
        ):
            queue_characters.return_value = 3
            queue_corporations.return_value = 2
            result = update_all_industry_jobs(batch_size=100)

        queue_characters.assert_called_once()
        args, kwargs = queue_characters.call_args
        self.assertEqual(kwargs["window_minutes"], 60)
        self.assertEqual(kwargs["priority"], 7)
        self.assertEqual(args[0], [(101, 1001), (101, 1002), (202, 2001)])
        queue_corporations.assert_called_once_with(
            [202, 303],
            window_minutes=60,
            priority=7,
        )
        self.assertEqual(result["characters_queued"], 3)
        self.assertEqual(result["corporation_users_queued"], 2)

    def test_bulk_job_updates_delay_next_batch_until_window_ends(self) -> None:
        with (
            patch(
                "indy_hub.tasks.industry._select_industry_job_sync_user_ids",
                return_value=[101, 202],
            ),
            patch(
                "indy_hub.tasks.industry._select_character_job_targets_for_users",
                return_value=[(101, 1001), (202, 2001)],
            ),
            patch(
                "indy_hub.tasks.industry._select_corporation_job_user_ids_for_users",
                return_value=[],
            ),
            patch(
                "indy_hub.tasks.industry._queue_staggered_industry_job_character_tasks",
                return_value=2,
            ),
            patch(
                "indy_hub.tasks.industry._queue_staggered_industry_job_corporation_tasks",
                return_value=0,
            ),
            patch("indy_hub.tasks.industry.emit_analytics_event"),
            patch("indy_hub.tasks.industry.cache.add", return_value=True),
            patch("indy_hub.tasks.industry.cache.get", return_value=None),
            patch("indy_hub.tasks.industry.update_all_industry_jobs.apply_async") as requeue,
        ):
            update_all_industry_jobs(batch_size=2)

        requeue.assert_called_once()
        kwargs = requeue.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["last_user_id"], 202)
        self.assertEqual(kwargs["batch_size"], 2)
        self.assertTrue(kwargs["lock_token"])
        self.assertEqual(requeue.call_args.kwargs["countdown"], 3600)

    def test_bulk_blueprint_updates_are_staggered_per_character(self) -> None:
        with (
            patch(
                "indy_hub.tasks.industry._select_blueprint_sync_user_ids",
                return_value=[101, 202, 303],
            ),
            patch(
                "indy_hub.tasks.industry._select_character_blueprint_targets_for_users",
                return_value=[(101, 1001), (101, 1002), (202, 2001)],
            ),
            patch(
                "indy_hub.tasks.industry._select_corporation_blueprint_user_ids_for_users",
                return_value=[202, 303],
            ),
            patch("indy_hub.tasks.industry._queue_staggered_blueprint_character_tasks") as queue_characters,
            patch("indy_hub.tasks.industry._queue_staggered_blueprint_corporation_tasks") as queue_corporations,
            patch("indy_hub.tasks.industry.emit_analytics_event"),
            patch("indy_hub.tasks.industry.update_all_blueprints.apply_async"),
            patch("indy_hub.tasks.industry.cache.add", return_value=True),
            patch("indy_hub.tasks.industry.cache.get", return_value=None),
        ):
            queue_characters.return_value = 3
            queue_corporations.return_value = 2
            result = update_all_blueprints(batch_size=100)

        queue_characters.assert_called_once()
        args, kwargs = queue_characters.call_args
        self.assertEqual(kwargs["window_minutes"], 720)
        self.assertEqual(kwargs["priority"], 7)
        self.assertEqual(args[0], [(101, 1001), (101, 1002), (202, 2001)])
        queue_corporations.assert_called_once_with(
            [202, 303],
            window_minutes=720,
            priority=7,
        )
        self.assertEqual(result["characters_queued"], 3)
        self.assertEqual(result["corporation_users_queued"], 2)

    def test_bulk_blueprint_updates_delay_next_batch_until_window_ends(self) -> None:
        with (
            patch(
                "indy_hub.tasks.industry._select_blueprint_sync_user_ids",
                return_value=[101, 202],
            ),
            patch(
                "indy_hub.tasks.industry._select_character_blueprint_targets_for_users",
                return_value=[(101, 1001), (202, 2001)],
            ),
            patch(
                "indy_hub.tasks.industry._select_corporation_blueprint_user_ids_for_users",
                return_value=[],
            ),
            patch(
                "indy_hub.tasks.industry._queue_staggered_blueprint_character_tasks",
                return_value=2,
            ),
            patch(
                "indy_hub.tasks.industry._queue_staggered_blueprint_corporation_tasks",
                return_value=0,
            ),
            patch("indy_hub.tasks.industry.emit_analytics_event"),
            patch("indy_hub.tasks.industry.cache.add", return_value=True),
            patch("indy_hub.tasks.industry.cache.get", return_value=None),
            patch("indy_hub.tasks.industry.update_all_blueprints.apply_async") as requeue,
        ):
            update_all_blueprints(batch_size=2)

        requeue.assert_called_once()
        kwargs = requeue.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["last_user_id"], 202)
        self.assertEqual(kwargs["batch_size"], 2)
        self.assertTrue(kwargs["lock_token"])
        self.assertEqual(requeue.call_args.kwargs["countdown"], 43200)
