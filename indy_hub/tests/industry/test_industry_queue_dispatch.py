"""Exercise QueueOnce argument binding instead of mocking task dispatch away."""

# Standard Library
from unittest.mock import MagicMock, PropertyMock, patch

# Third Party
from celery_once.tasks import QueueOnce

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

# AA Example App
from indy_hub.tasks.industry import (
    _mark_manual_refresh_inflight,
    manual_refresh_allowed,
    manual_refresh_in_progress,
    queue_blueprint_update_for_user,
    queue_industry_job_update_for_user,
    request_manual_refresh,
    update_blueprints_for_user,
    update_industry_jobs_for_user,
)
from indy_hub.tests.test_smoke import assign_main_character, grant_indy_permissions


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class IndustryQueueDispatchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("queue-dispatch")
        assign_main_character(self.user, character_id=103501)
        grant_indy_permissions(self.user, "can_manage_corp_bp_requests")
        self.client.force_login(self.user)
        self.backend = MagicMock()
        for name, value in (("once_backend", self.backend), ("default_timeout", 60)):
            patcher = patch.object(
                QueueOnce, name, new_callable=PropertyMock, return_value=value
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("celery.app.task.Task.apply_async", autospec=True)
        self.publish = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("indy_hub.tasks.industry._is_user_active", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _check_published_lock(self, task, scope, source):
        calls = [call for call in self.publish.call_args_list if call.args[0] == task]
        self.assertEqual(len(calls), 1)
        args = calls[0].args[1]
        kwargs = calls[0].args[2]
        key = task.get_key(args=args, kwargs=kwargs)
        self.backend.raise_or_lock.assert_called_once_with(key, timeout=60)
        self.assertEqual(kwargs["scope"], scope)
        self.assertEqual(kwargs["queue_source"], source)
        self.assertIn("character_id", kwargs)
        if source == "manual":
            self.assertTrue(kwargs["force_refresh"])
        self.assertEqual(calls[0].args[0], task)
        return key

    def test_manual_refresh_binds_all_lock_keys_for_both_kinds_and_scopes(self):
        for kind, task in (
            ("blueprints", update_blueprints_for_user),
            ("jobs", update_industry_jobs_for_user),
        ):
            for scope in (None, "character", "corporation"):
                with self.subTest(kind=kind, scope=scope):
                    cache.clear()
                    self.backend.reset_mock()
                    self.publish.reset_mock()
                    with patch(
                        "indy_hub.tasks.industry._is_user_active", return_value=False
                    ) as active:
                        scheduled, remaining, reason = request_manual_refresh(
                            kind, self.user.id, scope=scope
                        )
                    active.assert_not_called()
                    self.assertTrue(scheduled)
                    self.assertIsNone(remaining)
                    self.assertIsNone(reason)
                    self._check_published_lock(task, scope, "manual")

    def test_automatic_queue_helpers_bind_default_scope_and_keep_source_isolated(self):
        for queue, task in (
            (queue_blueprint_update_for_user, update_blueprints_for_user),
            (queue_industry_job_update_for_user, update_industry_jobs_for_user),
        ):
            for scope in (None, "character", "corporation"):
                with self.subTest(task=task.name, scope=scope):
                    cache.clear()
                    self.backend.reset_mock()
                    self.publish.reset_mock()
                    self.assertTrue(queue(self.user.id, scope=scope))
                    auto_key = self._check_published_lock(task, scope, "auto")
                    kwargs = dict(self.publish.call_args.args[2], queue_source="manual")
                    self.assertNotEqual(
                        auto_key, task.get_key(args=(self.user.id,), kwargs=kwargs)
                    )

    def test_failed_argument_binding_releases_manual_marker_and_allows_retry(self):
        for kind, task in (
            ("blueprints", update_blueprints_for_user),
            ("jobs", update_industry_jobs_for_user),
        ):
            with self.subTest(kind=kind):
                cache.clear()
                with patch.object(
                    task, "get_key", side_effect=KeyError("character_id")
                ):
                    with self.assertRaises(KeyError):
                        request_manual_refresh(kind, self.user.id, scope="corporation")
                self.assertFalse(
                    manual_refresh_in_progress(kind, self.user.id, "corporation")
                )
                self.assertTrue(
                    manual_refresh_allowed(kind, self.user.id, "corporation")[0]
                )
                self.assertTrue(
                    request_manual_refresh(kind, self.user.id, scope="corporation")[0]
                )

    def test_corporation_blueprint_get_refresh_passes_real_queueonce_binding(self):
        with patch(
            "indy_hub.views.industry.get_viewable_corporation_ids",
            return_value=[2000000],
        ):
            response = self.client.get(
                reverse("indy_hub:corporation_bp_list"), {"refresh": "1"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Error handling blueprint refresh")
        self.assertNotContains(response, "Unable to schedule blueprint refresh")
        self._check_published_lock(update_blueprints_for_user, "corporation", "manual")

    def test_corporation_blueprint_ajax_post_refresh_passes_real_queueonce_binding(
        self,
    ):
        with patch(
            "indy_hub.views.industry.get_viewable_corporation_ids",
            return_value=[2000000],
        ):
            response = self.client.post(
                reverse("indy_hub:corporation_bp_list"), {"action": "refresh"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["scheduled"])
        self._check_published_lock(update_blueprints_for_user, "corporation", "manual")

    def test_corporation_job_ajax_post_refresh_passes_real_queueonce_binding(self):
        with (
            patch(
                "indy_hub.views.industry.get_viewable_corporation_job_ids",
                return_value=[2000000],
            ),
            patch("indy_hub.views.industry.update_user_skill_snapshots.apply_async"),
        ):
            response = self.client.post(
                reverse("indy_hub:corporation_job_list"), {"action": "refresh"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["scheduled"])
        self._check_published_lock(
            update_industry_jobs_for_user, "corporation", "manual"
        )

    def test_personal_get_and_ajax_refresh_schedule_for_inactive_users(self):
        for route, task in (
            ("personnal_bp_list", update_blueprints_for_user),
            ("personnal_job_list", update_industry_jobs_for_user),
        ):
            for method in ("get", "post"):
                with self.subTest(route=route, method=method):
                    cache.clear()
                    self.backend.reset_mock()
                    self.publish.reset_mock()
                    with (
                        patch(
                            "indy_hub.tasks.industry._is_user_active",
                            return_value=False,
                        ) as active,
                        patch(
                            "indy_hub.views.industry.update_user_skill_snapshots.apply_async"
                        ),
                    ):
                        response = getattr(self.client, method)(
                            reverse(f"indy_hub:{route}"),
                            (
                                {"refresh": "1"}
                                if method == "get"
                                else {"action": "refresh"}
                            ),
                        )
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, "user considered inactive")
                    if method == "post":
                        self.assertTrue(response.json()["scheduled"])
                    self._check_published_lock(task, "character", "manual")
                    active.assert_not_called()

    def test_queue_helpers_filter_only_automatic_inactive_users(self):
        for queue, task in (
            (queue_blueprint_update_for_user, update_blueprints_for_user),
            (queue_industry_job_update_for_user, update_industry_jobs_for_user),
        ):
            for scope in (None, "character", "corporation"):
                for source in ("auto", "manual"):
                    with self.subTest(task=task.name, scope=scope, source=source):
                        cache.clear()
                        self.backend.reset_mock()
                        self.publish.reset_mock()
                        with patch(
                            "indy_hub.tasks.industry._is_user_active",
                            return_value=False,
                        ):
                            self.assertEqual(
                                queue(self.user.id, scope=scope, queue_source=source),
                                source == "manual",
                            )
                        self.assertEqual(
                            self.publish.call_count, int(source == "manual")
                        )

    def test_inactive_automatic_tasks_clear_finished_refresh_markers(self):
        for kind, task in (
            ("blueprints", update_blueprints_for_user),
            ("jobs", update_industry_jobs_for_user),
        ):
            for scope in (None, "character", "corporation"):
                with self.subTest(kind=kind, scope=scope):
                    self.assertTrue(
                        _mark_manual_refresh_inflight(kind, self.user.id, scope)
                    )
                    with patch(
                        "indy_hub.tasks.industry._is_user_active", return_value=False
                    ):
                        result = task(self.user.id, scope=scope, queue_source="auto")
                    self.assertEqual(result["skipped"], "inactive_user")
                    self.assertFalse(
                        manual_refresh_in_progress(kind, self.user.id, scope)
                    )

    def test_manual_corporation_tasks_process_even_when_user_is_inactive(self):
        for kind, task in (
            ("blueprints", update_blueprints_for_user),
            ("jobs", update_industry_jobs_for_user),
        ):
            with self.subTest(kind=kind):
                self.assertTrue(
                    _mark_manual_refresh_inflight(kind, self.user.id, "corporation")
                )
                with (
                    patch(
                        "indy_hub.tasks.industry._is_user_active", return_value=False
                    ),
                    patch(
                        "indy_hub.tasks.industry._collect_corporation_contexts",
                        return_value={},
                    ) as contexts,
                ):
                    result = task(
                        self.user.id, scope="corporation", queue_source="manual"
                    )
                self.assertNotIn("skipped", result)
                contexts.assert_called_once()
                self.assertEqual(contexts.call_args.args[0].pk, self.user.pk)
                self.assertFalse(
                    manual_refresh_in_progress(kind, self.user.id, "corporation")
                )

    def test_manual_corporation_tasks_still_require_manager_permission(self):
        self.user.user_permissions.remove(
            self.user.user_permissions.get(codename="can_manage_corp_bp_requests")
        )
        for task in (update_blueprints_for_user, update_industry_jobs_for_user):
            with self.subTest(task=task.name):
                with (
                    patch(
                        "indy_hub.tasks.industry._is_user_active", return_value=False
                    ),
                    patch(
                        "indy_hub.tasks.industry._collect_corporation_contexts"
                    ) as contexts,
                ):
                    result = task(
                        self.user.id, scope="corporation", queue_source="manual"
                    )
                contexts.assert_not_called()
                self.assertTrue(result["errors"])

    def test_manual_tasks_bypass_inactivity_and_recent_sync_for_every_scope(self):
        for kind, task in (
            ("blueprints", update_blueprints_for_user),
            ("jobs", update_industry_jobs_for_user),
        ):
            for scope in (None, "character", "corporation"):
                with self.subTest(task=task.name, scope=scope):
                    self.assertTrue(
                        _mark_manual_refresh_inflight(kind, self.user.id, scope)
                    )
                    with (
                        patch(
                            "indy_hub.tasks.industry._is_user_active",
                            return_value=False,
                        ) as active,
                        patch(
                            "indy_hub.tasks.industry._user_recent_blueprint_sync",
                            return_value=True,
                        ) as recent_bp,
                        patch(
                            "indy_hub.tasks.industry._user_recent_job_sync",
                            return_value=True,
                        ) as recent_jobs,
                        patch(
                            "indy_hub.tasks.industry._collect_corporation_contexts",
                            return_value={},
                        ),
                    ):
                        result = task(self.user.id, scope=scope, queue_source="manual")
                    self.assertNotIn("skipped", result)
                    active.assert_not_called()
                    recent_bp.assert_not_called()
                    recent_jobs.assert_not_called()
                    self.assertFalse(
                        manual_refresh_in_progress(kind, self.user.id, scope)
                    )
