"""Tests for the scalable admin-user read model and its reconstruction paths."""

from __future__ import annotations

# Standard Library
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub import signals
from indy_hub.models import AdminUserStatus, CharacterSettings, IndyHubUserUsage
from indy_hub.services.admin_user_status import rebuild_admin_user_statuses
from indy_hub.tasks.housekeeping import (
    rebuild_admin_user_statuses as rebuild_admin_user_statuses_task,
)

User = get_user_model()


class AdminUserStatusTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("status_user", password="secret123")
        self.character = EveCharacter.objects.create(
            character_id=9800001,
            character_name="Status Pilot",
            corporation_id=9801,
            corporation_name="Status Corp",
            corporation_ticker="STAT",
        )
        profile, _created = UserProfile.objects.get_or_create(user=self.user)
        profile.main_character = self.character
        profile.save(update_fields=["main_character"])

    def test_rebuild_materializes_only_listing_scalars_from_local_state(self) -> None:
        CharacterSettings.objects.update_or_create(
            user=self.user,
            character_id=0,
            defaults={
                "copy_sharing_scope": CharacterSettings.SCOPE_CORPORATION,
                "allow_copy_requests": True,
                "jobs_notify_frequency": CharacterSettings.NOTIFY_DAILY,
                "jobs_notify_completed": True,
            },
        )
        usage = IndyHubUserUsage.objects.create(
            user=self.user,
            first_used_at=timezone.now(),
            last_used_at=timezone.now(),
            total_usage_count=42,
            activity_30d_count=7,
        )
        scope_status = {
            self.user.id: {
                "flags": {
                    "blueprints": True,
                    "jobs": True,
                    "assets": True,
                    "skills": True,
                },
                "is_complete": True,
            }
        }

        with (
            patch(
                "indy_hub.services.admin_user_status.collect_user_scope_status_map",
                return_value=scope_status,
            ),
            patch(
                "esi.managers.TokenQueryset.require_valid",
                side_effect=AssertionError("Read-model rebuild is local-only"),
            ),
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("Read-model rebuild must not use HTTP"),
            ),
        ):
            rebuilt = rebuild_admin_user_statuses([self.user.id])

        status = AdminUserStatus.objects.get(user=self.user)
        self.assertEqual(rebuilt, 1)
        self.assertEqual(status.main_character_id, self.character.character_id)
        self.assertEqual(status.main_character_name, self.character.character_name)
        self.assertEqual(status.corporation_id, self.character.corporation_id)
        self.assertEqual(status.scope_score, 50)
        self.assertTrue(status.scope_complete)
        self.assertFalse(status.scope_online)
        self.assertEqual(status.settings_score, 20)
        self.assertTrue(status.notifications_enabled)
        self.assertEqual(status.last_used_at, usage.last_used_at)
        self.assertEqual(status.activity_30d_count, 7)
        self.assertEqual(status.total_usage_count, 42)

    def test_usage_signal_updates_only_usage_scalars_when_status_exists(self) -> None:
        status = AdminUserStatus.objects.get(user=self.user)
        moment = timezone.now()

        with patch(
            "indy_hub.services.admin_user_status.collect_user_scope_status_map",
            side_effect=AssertionError("Usage update should not rebuild scopes"),
        ):
            IndyHubUserUsage.objects.create(
                user=self.user,
                first_used_at=moment,
                last_used_at=moment,
                total_usage_count=9,
                activity_30d_count=6,
            )

        status.refresh_from_db()
        self.assertEqual(status.last_used_at, moment)
        self.assertEqual(status.activity_30d_count, 6)
        self.assertEqual(status.total_usage_count, 9)

    def test_settings_and_profile_signals_refresh_targeted_status(self) -> None:
        CharacterSettings.objects.update_or_create(
            user=self.user,
            character_id=0,
            defaults={
                "copy_sharing_scope": CharacterSettings.SCOPE_EVERYONE,
                "allow_copy_requests": True,
                "jobs_notify_frequency": CharacterSettings.NOTIFY_WEEKLY,
                "jobs_notify_completed": True,
            },
        )
        status = AdminUserStatus.objects.get(user=self.user)
        self.assertEqual(status.settings_score, 20)
        self.assertTrue(status.notifications_enabled)

        replacement = EveCharacter.objects.create(
            character_id=9800002,
            character_name="Replacement Pilot",
            corporation_id=9802,
            corporation_name="Replacement Corp",
            corporation_ticker="REPL",
        )
        profile = UserProfile.objects.get(user=self.user)
        profile.main_character = replacement
        profile.save(update_fields=["main_character"])

        status.refresh_from_db()
        self.assertEqual(status.main_character_id, replacement.character_id)
        self.assertEqual(status.main_character_name, replacement.character_name)
        self.assertEqual(status.corporation_id, replacement.corporation_id)

    def test_token_and_scope_signal_handlers_target_owning_user(self) -> None:
        token = SimpleNamespace(user_id=self.user.id)

        with patch("indy_hub.signals._refresh_admin_user_status_safely") as refresh:
            signals.refresh_admin_user_status_on_token_change(
                sender=None,
                instance=token,
            )
            signals.refresh_admin_user_status_on_token_scopes_change(
                sender=None,
                instance=token,
                action="post_add",
                reverse=False,
                pk_set={1},
            )

        self.assertEqual(refresh.call_count, 2)
        refresh.assert_called_with(self.user.id)

    def test_management_command_reconstructs_missing_status_rows(self) -> None:
        AdminUserStatus.objects.filter(user=self.user).delete()
        stdout = StringIO()

        call_command(
            "rebuild_admin_user_statuses",
            user_id=self.user.id,
            stdout=stdout,
        )

        self.assertTrue(AdminUserStatus.objects.filter(user=self.user).exists())
        self.assertIn("Rebuilt 1 admin-user status row", stdout.getvalue())

    def test_celery_rebuild_processes_one_bounded_batch_and_continues(self) -> None:
        for index in range(24):
            User.objects.create_user(f"batch_user_{index}", password="secret123")

        with (
            patch(
                "indy_hub.tasks.housekeeping.rebuild_statuses",
                return_value=25,
            ) as rebuild,
            patch.object(rebuild_admin_user_statuses_task, "apply_async") as enqueue,
        ):
            result = rebuild_admin_user_statuses_task.run(batch_size=25)

        rebuilt_ids = rebuild.call_args.args[0]
        self.assertEqual(len(rebuilt_ids), 25)
        self.assertEqual(result["rebuilt"], 25)
        self.assertTrue(result["has_more"])
        enqueue.assert_called_once_with(
            kwargs={
                "after_user_id": rebuilt_ids[-1],
                "batch_size": 25,
            },
            countdown=1,
            priority=8,
        )
