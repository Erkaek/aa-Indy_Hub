"""Tests for Indy Hub user health score calculation."""

from __future__ import annotations

# Django
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

# AA Example App
from indy_hub.models import CharacterSettings
from indy_hub.services.user_health import (
    HEALTH_LEVEL_CRITICAL,
    HEALTH_LEVEL_GOOD,
    HEALTH_LEVEL_MEDIUM,
    build_user_health_score,
)

User = get_user_model()


class UserHealthScoreTests(SimpleTestCase):
    def _settings(
        self,
        *,
        copy_sharing_scope: str = CharacterSettings.SCOPE_CORPORATION,
        allow_copy_requests: bool = True,
        jobs_notify_frequency: str = CharacterSettings.NOTIFY_DAILY,
        jobs_notify_completed: bool = True,
    ) -> CharacterSettings:
        return CharacterSettings(
            user=User(username="health-user"),
            character_id=0,
            copy_sharing_scope=copy_sharing_scope,
            allow_copy_requests=allow_copy_requests,
            jobs_notify_frequency=jobs_notify_frequency,
            jobs_notify_completed=jobs_notify_completed,
        )

    def test_health_score_is_100_when_scopes_settings_and_activity_are_good(
        self,
    ) -> None:
        result = build_user_health_score(
            scope_flags={
                "blueprints": True,
                "jobs": True,
                "assets": True,
                "skills": True,
                "online": True,
            },
            settings_obj=self._settings(),
            is_inactive=False,
            activity_30d_count=7,
        )

        self.assertEqual(result["score"], 100)
        self.assertEqual(result["level"], HEALTH_LEVEL_GOOD)
        self.assertEqual(
            result["breakdown"],
            {
                "scope_coverage": 50,
                "parameter_coherence": 20,
                "recent_activity": 30,
            },
        )

    def test_health_score_drops_to_medium_for_partial_scope_and_light_activity(
        self,
    ) -> None:
        result = build_user_health_score(
            scope_flags={
                "blueprints": True,
                "jobs": True,
                "assets": False,
                "skills": False,
                "online": True,
            },
            settings_obj=self._settings(),
            is_inactive=False,
            activity_30d_count=1,
        )

        self.assertEqual(result["score"], 65)
        self.assertEqual(result["level"], HEALTH_LEVEL_MEDIUM)
        self.assertEqual(result["breakdown"]["scope_coverage"], 30)
        self.assertEqual(result["breakdown"]["parameter_coherence"], 20)
        self.assertEqual(result["breakdown"]["recent_activity"], 15)

    def test_health_score_is_critical_when_parameters_are_incoherent_and_user_inactive(
        self,
    ) -> None:
        result = build_user_health_score(
            scope_flags={
                "blueprints": False,
                "jobs": False,
                "assets": False,
                "skills": False,
                "online": False,
            },
            settings_obj=self._settings(
                copy_sharing_scope=CharacterSettings.SCOPE_CORPORATION,
                allow_copy_requests=False,
                jobs_notify_frequency=CharacterSettings.NOTIFY_DAILY,
                jobs_notify_completed=False,
            ),
            is_inactive=True,
            activity_30d_count=0,
        )

        self.assertEqual(result["score"], 0)
        self.assertEqual(result["level"], HEALTH_LEVEL_CRITICAL)
        self.assertEqual(result["breakdown"]["parameter_coherence"], 0)
        self.assertEqual(result["breakdown"]["recent_activity"], 0)

    def test_health_score_handles_missing_settings(self) -> None:
        result = build_user_health_score(
            scope_flags={
                "blueprints": True,
                "jobs": False,
                "assets": False,
                "skills": False,
                "online": False,
            },
            settings_obj=None,
            is_inactive=False,
            activity_30d_count=8,
        )

        self.assertEqual(result["score"], 40)
        self.assertEqual(result["level"], HEALTH_LEVEL_CRITICAL)
        self.assertEqual(result["breakdown"]["scope_coverage"], 10)
        self.assertEqual(result["breakdown"]["parameter_coherence"], 0)
        self.assertEqual(result["breakdown"]["recent_activity"], 30)
