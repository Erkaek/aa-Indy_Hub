"""Tests for industry job payload validation."""

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

# AA Example App
from indy_hub.tasks.industry import (
    _is_user_active,
    dispatch_pending_industry_bulk_updates,
    queue_blueprint_update_for_user,
    queue_industry_job_update_for_user,
    request_manual_refresh,
    update_all_blueprints,
    update_all_industry_jobs,
    update_industry_jobs_for_user,
)


class _FakeCache:
    def __init__(self) -> None:
        self.values = {}

    def add(self, key, value, timeout=None):
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, timeout=None):
        self.values[key] = value
        return True

    def touch(self, key, timeout=None):
        return key in self.values

    def delete(self, key):
        return bool(self.values.pop(key, None))


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

    def test_user_activity_is_permissive_when_corptools_table_is_missing(self) -> None:
        with patch(
            "indy_hub.tasks.industry._fetch_corptools_activity_rows",
            return_value=None,
        ):
            self.assertTrue(_is_user_active(self.user))

    def test_user_activity_is_permissive_when_corptools_login_is_empty(self) -> None:
        with patch(
            "indy_hub.tasks.industry._fetch_corptools_activity_rows",
            return_value=[(9000001, "")],
        ):
            self.assertTrue(_is_user_active(self.user))

    def test_user_activity_is_false_when_corptools_login_is_stale(self) -> None:
        stale_login = timezone.now() - timedelta(days=32)
        with patch(
            "indy_hub.tasks.industry._fetch_corptools_activity_rows",
            return_value=[(9000001, stale_login)],
        ):
            self.assertFalse(_is_user_active(self.user))

    def test_user_activity_is_true_when_corptools_login_is_recent(self) -> None:
        recent_login = timezone.now() - timedelta(days=2)
        with patch(
            "indy_hub.tasks.industry._fetch_corptools_activity_rows",
            return_value=[(9000001, recent_login)],
        ):
            self.assertTrue(_is_user_active(self.user))

    def test_queue_industry_job_update_skips_corporation_scope_without_permission(
        self,
    ) -> None:
        user = User.objects.create_user("corp-less-user", password="secret123")
        corp_permission = Permission.objects.get(codename="can_manage_corp_bp_requests")
        user.user_permissions.add(corp_permission)
        user.user_permissions.clear()

        with (
            patch("indy_hub.tasks.industry._is_user_active", return_value=True),
            patch(
                "indy_hub.tasks.industry.update_industry_jobs_for_user.apply_async"
            ) as apply_async,
        ):
            scheduled = queue_industry_job_update_for_user(
                user.id,
                scope="corporation",
                character_id=0,
            )

        self.assertFalse(scheduled)
        apply_async.assert_not_called()

    def test_queue_industry_job_update_skips_duplicate_enqueue_when_pending(
        self,
    ) -> None:
        with (
            patch("indy_hub.tasks.industry._is_user_active", return_value=True),
            patch("indy_hub.tasks.industry.cache.add", return_value=False) as cache_add,
            patch(
                "indy_hub.tasks.industry.update_industry_jobs_for_user.apply_async"
            ) as apply_async,
        ):
            scheduled = queue_industry_job_update_for_user(
                self.user.id,
                scope="character",
                character_id=9000001,
            )

        self.assertFalse(scheduled)
        cache_add.assert_called_once()
        apply_async.assert_not_called()

    def test_queue_blueprint_update_includes_character_id_kwarg_for_queueonce(self):
        with (
            patch("indy_hub.tasks.industry._is_user_active", return_value=True),
            patch("indy_hub.tasks.industry.cache.add", return_value=True),
            patch(
                "indy_hub.tasks.industry.update_blueprints_for_user.apply_async"
            ) as apply_async,
        ):
            scheduled = queue_blueprint_update_for_user(self.user.id)

        self.assertTrue(scheduled)
        apply_async.assert_called_once_with(
            args=(self.user.id,),
            kwargs={"character_id": None, "queue_source": "auto"},
            countdown=0,
            priority=None,
        )

    def test_queue_blueprint_update_passes_character_id_for_character_scope(self):
        with (
            patch("indy_hub.tasks.industry._is_user_active", return_value=True),
            patch("indy_hub.tasks.industry.cache.add", return_value=True),
            patch(
                "indy_hub.tasks.industry.update_blueprints_for_user.apply_async"
            ) as apply_async,
        ):
            scheduled = queue_blueprint_update_for_user(
                self.user.id,
                scope="character",
                character_id=9000001,
            )

        self.assertTrue(scheduled)
        apply_async.assert_called_once_with(
            args=(self.user.id,),
            kwargs={
                "character_id": 9000001,
                "scope": "character",
                "queue_source": "auto",
            },
            countdown=0,
            priority=None,
        )

    def test_manual_refresh_uses_manual_queue_source(self) -> None:
        with (
            patch(
                "indy_hub.tasks.industry.manual_refresh_allowed",
                return_value=(True, None),
            ),
            patch(
                "indy_hub.tasks.industry._mark_manual_refresh_inflight",
                return_value=True,
            ),
            patch(
                "indy_hub.tasks.industry.queue_industry_job_update_for_user",
                return_value=True,
            ) as queue_jobs,
        ):
            allowed, remaining, reason = request_manual_refresh(
                "jobs",
                self.user.id,
                scope="character",
            )

        self.assertTrue(allowed)
        self.assertIsNone(remaining)
        self.assertIsNone(reason)
        queue_jobs.assert_called_once_with(
            self.user.id,
            priority=None,
            scope="character",
            queue_source="manual",
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

    def test_bulk_job_updates_release_a_bounded_immediate_batch(self) -> None:
        fake_cache = _FakeCache()
        with (
            patch(
                "indy_hub.tasks.industry._select_industry_job_sync_user_page",
                return_value=([101, 202, 303], 303, True),
            ),
            patch(
                "indy_hub.tasks.industry._select_character_job_targets_for_users",
                return_value=[(101, 1001), (101, 1002), (202, 2001)],
            ),
            patch(
                "indy_hub.tasks.industry._select_corporation_job_user_ids_for_users",
                return_value=[202, 303],
            ),
            patch("indy_hub.tasks.industry.cache", fake_cache),
            patch("indy_hub.tasks.industry._get_target_per_min", return_value=3),
            patch(
                "indy_hub.tasks.industry.queue_industry_job_update_for_user",
                return_value=True,
            ) as queue_target,
            patch("indy_hub.tasks.industry.emit_analytics_event"),
        ):
            result = update_all_industry_jobs(batch_size=100)
            throttled = dispatch_pending_industry_bulk_updates()
            fake_cache.values["indy_hub:industry_jobs_bulk:state"][
                "last_dispatch_timestamp"
            ] = 0
            resumed = dispatch_pending_industry_bulk_updates()

        self.assertEqual(result["characters_queued"], 3)
        self.assertEqual(result["corporation_users_queued"], 0)
        self.assertEqual(result["targets_pending"], 2)
        self.assertFalse(result["done"])
        self.assertEqual(throttled["jobs"]["reason"], "dispatch_interval")
        self.assertEqual(resumed["jobs"]["corporation_users_queued"], 2)
        self.assertTrue(resumed["jobs"]["done"])
        self.assertEqual(queue_target.call_count, 5)
        for call in queue_target.call_args_list:
            self.assertNotIn("countdown", call.kwargs)
            self.assertEqual(call.kwargs["priority"], 7)
        self.assertNotIn("indy_hub:industry_jobs_bulk:state", fake_cache.values)
        self.assertNotIn("indy_hub:industry_jobs_bulk:lock", fake_cache.values)

    def test_bulk_job_updates_resume_from_cache_without_eta_continuation(self) -> None:
        fake_cache = _FakeCache()
        with (
            patch(
                "indy_hub.tasks.industry._select_industry_job_sync_user_page",
                side_effect=[([101, 202], 202, False), ([303], 303, True)],
            ) as select_page,
            patch(
                "indy_hub.tasks.industry._select_character_job_targets_for_users",
                side_effect=lambda user_ids: [
                    (user_id, user_id * 10) for user_id in user_ids
                ],
            ),
            patch(
                "indy_hub.tasks.industry._select_corporation_job_user_ids_for_users",
                return_value=[],
            ),
            patch("indy_hub.tasks.industry.cache", fake_cache),
            patch("indy_hub.tasks.industry._get_target_per_min", return_value=60),
            patch(
                "indy_hub.tasks.industry.queue_industry_job_update_for_user",
                return_value=True,
            ) as queue_target,
            patch("indy_hub.tasks.industry.emit_analytics_event"),
            patch(
                "indy_hub.tasks.industry.update_all_industry_jobs.apply_async"
            ) as requeue,
        ):
            first = update_all_industry_jobs(batch_size=2)
            fake_cache.values["indy_hub:industry_jobs_bulk:state"][
                "last_dispatch_timestamp"
            ] = 0
            second = dispatch_pending_industry_bulk_updates()["jobs"]

        requeue.assert_not_called()
        self.assertFalse(first["done"])
        self.assertEqual(first["last_user_id"], 202)
        self.assertTrue(second["done"])
        self.assertEqual(second["last_user_id"], 303)
        self.assertEqual(select_page.call_args_list[1].kwargs["last_user_id"], 202)
        self.assertEqual(queue_target.call_count, 3)
        for call in queue_target.call_args_list:
            self.assertNotIn("countdown", call.kwargs)

    def test_bulk_blueprint_updates_release_a_bounded_immediate_batch(self) -> None:
        fake_cache = _FakeCache()
        with (
            patch(
                "indy_hub.tasks.industry._select_blueprint_sync_user_page",
                return_value=([101, 202, 303], 303, True),
            ),
            patch(
                "indy_hub.tasks.industry._select_character_blueprint_targets_for_users",
                return_value=[(101, 1001), (101, 1002), (202, 2001)],
            ),
            patch(
                "indy_hub.tasks.industry."
                "_select_corporation_blueprint_user_ids_for_users",
                return_value=[202, 303],
            ),
            patch("indy_hub.tasks.industry.cache", fake_cache),
            patch("indy_hub.tasks.industry._get_target_per_min", return_value=4),
            patch(
                "indy_hub.tasks.industry.queue_blueprint_update_for_user",
                return_value=True,
            ) as queue_target,
            patch("indy_hub.tasks.industry.emit_analytics_event"),
        ):
            result = update_all_blueprints(batch_size=100)
            fake_cache.values["indy_hub:blueprints_bulk:state"][
                "last_dispatch_timestamp"
            ] = 0
            resumed = dispatch_pending_industry_bulk_updates()

        self.assertEqual(result["characters_queued"], 3)
        self.assertEqual(result["corporation_users_queued"], 1)
        self.assertEqual(result["targets_pending"], 1)
        self.assertFalse(result["done"])
        self.assertEqual(resumed["blueprints"]["corporation_users_queued"], 1)
        self.assertTrue(resumed["blueprints"]["done"])
        self.assertEqual(queue_target.call_count, 5)
        for call in queue_target.call_args_list:
            self.assertNotIn("countdown", call.kwargs)

    def test_bulk_dispatcher_discards_state_when_owner_lock_is_lost(self) -> None:
        fake_cache = _FakeCache()
        fake_cache.set(
            "indy_hub:blueprints_bulk:state",
            {
                "kind": "blueprints",
                "lock_token": "stale-token",
                "last_user_id": None,
                "batch_size": 500,
                "pending_targets": [
                    {"user_id": 101, "scope": "character", "character_id": 1001}
                ],
                "source_exhausted": True,
            },
        )
        with (
            patch("indy_hub.tasks.industry.cache", fake_cache),
            patch("indy_hub.tasks.industry.queue_blueprint_update_for_user") as queue,
        ):
            result = dispatch_pending_industry_bulk_updates()["blueprints"]

        self.assertEqual(result["reason"], "lock_lost")
        queue.assert_not_called()
        self.assertNotIn("indy_hub:blueprints_bulk:state", fake_cache.values)
