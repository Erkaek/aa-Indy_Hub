"""Behavior and authorization regressions for the administration console."""

# Standard Library
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Scope, Token

# AA Example App
from indy_hub.models import AdminUserStatus, IndyHubUserUsage
from indy_hub.tasks.housekeeping import rebuild_selected_admin_user_statuses
from indy_hub.tests.fixtures import create_raw_fixture
from indy_hub.tests.test_smoke import assign_main_character, grant_indy_permissions
from indy_hub.views.hubs import _build_settings_admin_users_state


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class AdminUsersConsoleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = User.objects.create_user(
            "console-admin", is_superuser=True, is_staff=True
        )
        self.user = User.objects.create_user("console-member")
        self.regular = User.objects.create_user("console-regular")
        assign_main_character(self.admin, character_id=9900001)
        assign_main_character(self.regular, character_id=9900002)
        grant_indy_permissions(self.regular, "can_access_indy_hub")
        self.client.force_login(self.admin)
        self.action_url = reverse("indy_hub:settings_admin_users_actions")
        self.list_url = reverse("indy_hub:settings_admin_users")
        AdminUserStatus.objects.update_or_create(
            user=self.user,
            defaults={
                "main_character_name": "Main Pilot",
                "corporation_name": "Console Corp",
                "scope_blueprints": True,
                "scope_jobs": False,
                "scope_assets": True,
                "scope_skills": False,
                "settings_score": 10,
            },
        )

    def action(self, payload):
        return self.client.post(
            self.action_url, payload, content_type="application/json"
        )

    def test_fragments_are_local_bounded_and_do_not_build_navigation(self):
        with (
            patch(
                "indy_hub.views.hubs._build_settings_admin_users_context",
                side_effect=AssertionError("navigation"),
            ),
            patch(
                "esi.managers.TokenQueryset.require_valid",
                side_effect=AssertionError("OAuth"),
            ),
            CaptureQueriesContext(connection) as captured,
        ):
            response = self.client.get(self.list_url, {"fragment": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("console-member", response.json()["html"])
        self.assertNotIn("adminUsersPage", response.json()["html"])
        for table in (Token._meta.db_table, IndyHubUserUsage._meta.db_table):
            self.assertFalse(
                any(table in query["sql"] for query in captured.captured_queries)
            )

    def test_summary_excludes_undiagnosed_users_from_critical_counts(self):
        AdminUserStatus.objects.filter(user=self.admin).delete()
        AdminUserStatus.objects.filter(user=self.regular).delete()
        response = self.client.get(reverse("indy_hub:settings_admin_users_summary"))
        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertEqual(summary["pending"], 2)
        self.assertEqual(summary["critical"], 1)
        response = self.client.get(
            reverse("indy_hub:settings_admin_users_summary"), {"q": "console-member"}
        )
        self.assertEqual(response.json()["summary"]["total"], 1)

    def test_search_finds_alternate_characters_without_duplicate_users(self):
        for character_id in (9900101, 9900102):
            character = EveCharacter.objects.create(
                character_id=character_id,
                character_name=f"Alt Needle {character_id}",
                corporation_id=9901,
                corporation_name="Console Corp",
                corporation_ticker="CON",
            )
            CharacterOwnership.objects.create(
                user=self.user, character=character, owner_hash=str(character_id)
            )
        state = _build_settings_admin_users_state(self.admin, {"q": "Alt Needle"})
        self.assertEqual([row["user"].pk for row in state["rows"]], [self.user.pk])
        state = _build_settings_admin_users_state(self.admin, {"q": "9900101"})
        self.assertEqual([row["user"].pk for row in state["rows"]], [self.user.pk])

    def test_additional_filters_and_bounded_page_sizes(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        params = {
            "account": "disabled",
            "role": "member",
            "status": "ready",
            "scopes": "jobs",
            "notifications": "disabled",
            "per_page": "100",
        }
        state = _build_settings_admin_users_state(self.admin, params)
        self.assertEqual([row["user"].pk for row in state["rows"]], [self.user.pk])
        self.assertEqual(state["page_obj"].paginator.per_page, 100)
        self.assertIn("per_page=100", state["current_query"])
        state = _build_settings_admin_users_state(self.admin, {"per_page": "1000000"})
        self.assertEqual(state["page_obj"].paginator.per_page, 25)

    def test_non_superusers_cannot_access_any_console_endpoint(self):
        self.client.force_login(self.regular)
        for name, args in (
            ("settings_admin_users_summary", []),
            ("settings_admin_users_detail", [self.user.pk]),
            ("settings_admin_users_export", []),
            ("settings_admin_users_action_status", []),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.get(reverse(f"indy_hub:{name}", args=args)).status_code,
                    403,
                )
        self.assertEqual(
            self.action(
                {"action": "blueprints", "user_ids": [self.user.pk]}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(self.list_url, {"fragment": "1"}).status_code, 403
        )

    def test_anonymous_visitors_cannot_load_diagnostics(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(
                reverse("indy_hub:settings_admin_users_detail", args=[self.user.pk])
            ).status_code,
            302,
        )

    def test_actions_require_csrf_and_post(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        with patch("indy_hub.views.admin_users.request_manual_refresh") as refresh:
            self.assertEqual(
                client.post(self.action_url, {"action": "jobs"}).status_code, 403
            )
            self.assertEqual(self.client.get(self.action_url).status_code, 405)
        refresh.assert_not_called()

    def test_invalid_selections_are_rejected_without_dispatch(self):
        for payload in (
            [],
            {"action": "bad", "user_ids": [self.user.pk]},
            {"action": "jobs", "user_ids": []},
            {"action": "jobs", "user_ids": list(range(1, 27))},
            {"action": "jobs", "user_ids": [True]},
            {"action": "jobs", "user_ids": ["1"]},
            {"action": "jobs", "user_ids": [2**90]},
            {"action": "jobs", "user_ids": [self.user.pk], "scope": "bad"},
        ):
            with (
                self.subTest(payload=payload),
                patch("indy_hub.views.admin_users.request_manual_refresh") as refresh,
            ):
                self.assertEqual(self.action(payload).status_code, 400)
                refresh.assert_not_called()

    def test_missing_user_rejects_entire_selection(self):
        with patch("indy_hub.views.admin_users.request_manual_refresh") as refresh:
            response = self.action(
                {"action": "jobs", "user_ids": [self.user.pk, 999999]}
            )
        self.assertEqual(response.status_code, 404)
        refresh.assert_not_called()

    def test_manual_dispatch_reuses_force_refresh_contract_for_inactive_users(self):
        with patch(
            "indy_hub.views.admin_users.request_manual_refresh",
            return_value=(True, None, None),
        ) as refresh:
            response = self.action(
                {"action": "blueprints", "user_ids": [self.user.pk, self.user.pk]}
            )
        self.assertEqual(response.json()["scheduled"], 1)
        refresh.assert_called_once_with(
            "blueprints", self.user.pk, priority=5, scope="character"
        )

    def test_partial_failures_report_each_user_and_preserve_cooldowns(self):
        with patch(
            "indy_hub.views.admin_users.request_manual_refresh",
            side_effect=[
                (False, timedelta(minutes=3), "cooldown"),
                RuntimeError("broker"),
            ],
        ):
            response = self.action(
                {"action": "jobs", "user_ids": [self.user.pk, self.regular.pk]}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scheduled"], 0)
        self.assertEqual(
            [row["reason"] for row in response.json()["results"]], ["cooldown", "error"]
        )
        self.assertEqual(response.json()["results"][0]["remaining_seconds"], 180)

    def test_corporation_dispatch_requires_target_user_permission(self):
        with patch("indy_hub.views.admin_users.request_manual_refresh") as refresh:
            response = self.action(
                {"action": "jobs", "scope": "corporation", "user_ids": [self.user.pk]}
            )
        refresh.assert_not_called()
        self.assertEqual(response.json()["results"][0]["reason"], "permission")

    def test_accepted_job_refresh_also_forces_skill_snapshots(self):
        with (
            patch(
                "indy_hub.views.admin_users.request_manual_refresh",
                return_value=(True, None, None),
            ),
            patch(
                "indy_hub.views.admin_users.update_user_skill_snapshots.apply_async"
            ) as skills,
        ):
            response = self.action({"action": "jobs", "user_ids": [self.user.pk]})
        self.assertEqual(response.json()["scheduled"], 1)
        skills.assert_called_once_with(
            args=(self.user.pk,), kwargs={"force_refresh": True}, priority=5
        )

    def test_skill_dispatch_failure_does_not_hide_queued_job_refresh(self):
        with (
            patch(
                "indy_hub.views.admin_users.request_manual_refresh",
                return_value=(True, None, None),
            ),
            patch(
                "indy_hub.views.admin_users.update_user_skill_snapshots.apply_async",
                side_effect=RuntimeError("broker"),
            ),
        ):
            response = self.action({"action": "jobs", "user_ids": [self.user.pk]})
        self.assertEqual(response.json()["scheduled"], 1)
        self.assertIn(
            "Skill snapshots could not be scheduled",
            response.json()["results"][0]["message"],
        )

    def test_rebuild_status_dispatch_and_status_access_are_actor_scoped(self):
        with patch(
            "indy_hub.views.admin_users.rebuild_selected_admin_user_statuses.apply_async",
            return_value=SimpleNamespace(id="task-console"),
        ) as enqueue:
            response = self.action(
                {"action": "rebuild_status", "user_ids": [self.user.pk]}
            )
        enqueue.assert_called_once_with(
            kwargs={"actor_id": self.admin.pk, "user_ids": [self.user.pk]}, priority=8
        )
        self.assertEqual(response.json()["task_id"], "task-console")
        with patch("indy_hub.views.admin_users.AsyncResult") as result:
            result.return_value.ready.return_value = True
            result.return_value.failed.return_value = False
            result.return_value.state = "SUCCESS"
            status = self.client.get(
                reverse("indy_hub:settings_admin_users_action_status"),
                {"task_id": "task-console"},
            )
            self.assertFalse(status.json()["running"])
        other = User.objects.create_user("another-admin", is_superuser=True)
        assign_main_character(other, character_id=9900003)
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(
                reverse("indy_hub:settings_admin_users_action_status"),
                {"task_id": "task-console"},
            ).status_code,
            404,
        )

    def test_duplicate_rebuild_is_not_reported_as_a_new_dispatch(self):
        with patch(
            "indy_hub.views.admin_users.rebuild_selected_admin_user_statuses.apply_async",
            return_value=None,
        ):
            response = self.action(
                {"action": "rebuild_status", "user_ids": [self.user.pk]}
            )
        self.assertEqual(response.json()["scheduled"], 0)

    def test_selected_rebuild_task_rechecks_actor_permission(self):
        with patch("indy_hub.tasks.housekeeping.rebuild_statuses") as rebuild:
            result = rebuild_selected_admin_user_statuses(
                self.regular.pk, [self.user.pk]
            )
        self.assertEqual(result["error"], "permission")
        rebuild.assert_not_called()
        result = rebuild_selected_admin_user_statuses(self.admin.pk, [self.user.pk])
        self.assertEqual(result["rebuilt"], 1)

    def test_detail_never_validates_tokens_or_exposes_secrets(self):
        token = Token(
            user=self.user,
            character_id=9900103,
            character_name="Diagnostic Pilot",
            access_token="PRIVATE-ACCESS-SECRET",
            refresh_token="PRIVATE-REFRESH-SECRET",
        )
        create_raw_fixture(token)
        scope, _created = Scope.objects.get_or_create(name="esi-skills.read_skills.v1")
        token.scopes.add(scope)
        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(days=10)
        )
        with patch(
            "esi.managers.TokenQueryset.require_valid",
            side_effect=AssertionError("OAuth"),
        ):
            response = self.client.get(
                reverse("indy_hub:settings_admin_users_detail", args=[self.user.pk])
            )
        self.assertEqual(response.status_code, 200)
        html = response.json()["html"]
        self.assertIn("Renewable", html)
        self.assertIn("Diagnostic Pilot", html)
        self.assertNotIn("PRIVATE-", html)
        self.assertIn("Automatic industry sync", html)

    def test_detail_missing_status_stays_pending_and_exact_target_is_used(self):
        AdminUserStatus.objects.filter(user=self.user).delete()
        response = self.client.get(
            reverse("indy_hub:settings_admin_users_detail", args=[self.user.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], self.user.pk)
        self.assertIn(
            "Rebuild diagnostics to evaluate this account.", response.json()["html"]
        )

    def test_export_respects_filters_and_escapes_formula_cells(self):
        User.objects.filter(pk=self.user.pk).update(username="=FORMULA")
        response = self.client.get(
            reverse("indy_hub:settings_admin_users_export"), {"q": "=FORMULA"}
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8-sig")
        self.assertIn("'=FORMULA", content)
        self.assertNotIn("console-admin", content)
        self.assertNotIn("access_token", content)

    def test_refresh_status_validates_input_without_disclosing_other_tasks(self):
        response = self.client.get(
            reverse("indy_hub:settings_admin_users_action_status"),
            {"task_id": "unrelated-task"},
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.get(
            reverse("indy_hub:settings_admin_users_action_status"),
            {"kind": "jobs", "user_ids": "bad"},
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.get(
            reverse("indy_hub:settings_admin_users_action_status"),
            {"kind": "jobs", "user_ids": str(self.user.pk), "scope": "character"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["running"])
