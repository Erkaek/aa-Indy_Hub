"""Integration tests for Indy Hub private Settings user-admin page."""

from __future__ import annotations

# Standard Library
import json
from datetime import timedelta
from unittest.mock import patch

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import CharacterSettings, IndyHubUserUsage
from indy_hub.views.hubs import (
    settings_admin_users,
    settings_admin_users_global_usage_fragment,
    settings_admin_users_usage_detail_fragment,
    settings_hub,
)

User = get_user_model()


def _grant_permission(user: User, codename: str) -> None:
    permission = Permission.objects.get(
        content_type__app_label="indy_hub",
        codename=codename,
    )
    user.user_permissions.add(permission)


def _link_character(
    user: User,
    *,
    character_id: int,
    corporation_id: int,
    corporation_name: str,
) -> EveCharacter:
    character, _ = EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={
            "character_name": f"Pilot {character_id}",
            "corporation_id": corporation_id,
            "corporation_name": corporation_name,
            "corporation_ticker": f"C{corporation_id}",
        },
    )
    CharacterOwnership.objects.update_or_create(
        user=user,
        character=character,
        defaults={"owner_hash": f"hash-{user.id}-{character_id}"},
    )
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not profile.main_character_id:
        profile.main_character = character
        profile.save(update_fields=["main_character"])
    return character


class SettingsAdminUsersViewTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

        self.superuser = User.objects.create_superuser(
            username="root_admin",
            email="root@example.com",
            password="secret123",
        )
        _link_character(
            self.superuser,
            character_id=9100001,
            corporation_id=9999,
            corporation_name="Root Corp",
        )

        self.manager = User.objects.create_user("manager", password="secret123")
        _grant_permission(self.manager, "can_access_indy_hub")
        _grant_permission(self.manager, "can_manage_corp_bp_requests")
        _link_character(
            self.manager,
            character_id=9100002,
            corporation_id=1001,
            corporation_name="Managed Corp",
        )

        self.regular = User.objects.create_user("regular", password="secret123")
        _grant_permission(self.regular, "can_access_indy_hub")
        _link_character(
            self.regular,
            character_id=9100003,
            corporation_id=3003,
            corporation_name="Regular Corp",
        )

        self.member_managed = User.objects.create_user(
            "member_managed",
            password="secret123",
        )
        _grant_permission(self.member_managed, "can_access_indy_hub")
        _link_character(
            self.member_managed,
            character_id=9100011,
            corporation_id=1001,
            corporation_name="Managed Corp",
        )

        self.member_other = User.objects.create_user(
            "member_other",
            password="secret123",
        )
        _grant_permission(self.member_other, "can_access_indy_hub")
        _link_character(
            self.member_other,
            character_id=9100012,
            corporation_id=2002,
            corporation_name="Other Corp",
        )

        CharacterSettings.objects.update_or_create(
            user=self.superuser,
            character_id=0,
            defaults={
                "jobs_notify_frequency": CharacterSettings.NOTIFY_DAILY,
                "copy_sharing_scope": CharacterSettings.SCOPE_CORPORATION,
            },
        )
        CharacterSettings.objects.update_or_create(
            user=self.manager,
            character_id=0,
            defaults={
                "jobs_notify_frequency": CharacterSettings.NOTIFY_DAILY,
                "copy_sharing_scope": CharacterSettings.SCOPE_CORPORATION,
            },
        )
        CharacterSettings.objects.update_or_create(
            user=self.member_managed,
            character_id=0,
            defaults={
                "jobs_notify_frequency": CharacterSettings.NOTIFY_DISABLED,
                "copy_sharing_scope": CharacterSettings.SCOPE_NONE,
            },
        )
        CharacterSettings.objects.update_or_create(
            user=self.member_other,
            character_id=0,
            defaults={
                "jobs_notify_frequency": CharacterSettings.NOTIFY_WEEKLY,
                "copy_sharing_scope": CharacterSettings.SCOPE_EVERYONE,
            },
        )

        now = timezone.now()
        IndyHubUserUsage.objects.update_or_create(
            user=self.manager,
            defaults={
                "first_used_at": now - timedelta(days=20),
                "last_used_at": now,
                "total_usage_count": 30,
                "activity_7d_count": 7,
                "activity_30d_count": 14,
                "daily_usage": {now.date().isoformat(): 1},
                "page_usage": {
                    reverse("indy_hub:index"): {
                        "label": "Overview",
                        "path": reverse("indy_hub:index"),
                        "total_usage_count": 18,
                        "first_used_at": (now - timedelta(days=20)).isoformat(),
                        "last_used_at": now.isoformat(),
                        "daily_usage": {
                            (now - timedelta(days=1)).date().isoformat(): 2,
                            now.date().isoformat(): 1,
                        },
                    },
                    reverse("indy_hub:settings_admin_users"): {
                        "label": "User admin",
                        "path": reverse("indy_hub:settings_admin_users"),
                        "total_usage_count": 12,
                        "first_used_at": (now - timedelta(days=18)).isoformat(),
                        "last_used_at": now.isoformat(),
                        "daily_usage": {
                            (now - timedelta(days=2)).date().isoformat(): 1,
                            now.date().isoformat(): 2,
                        },
                    },
                },
            },
        )
        IndyHubUserUsage.objects.update_or_create(
            user=self.member_managed,
            defaults={
                "first_used_at": now - timedelta(days=80),
                "last_used_at": now - timedelta(days=45),
                "total_usage_count": 8,
                "activity_7d_count": 0,
                "activity_30d_count": 0,
                "daily_usage": {(now - timedelta(days=45)).date().isoformat(): 1},
            },
        )
        IndyHubUserUsage.objects.update_or_create(
            user=self.member_other,
            defaults={
                "first_used_at": now - timedelta(days=5),
                "last_used_at": now,
                "total_usage_count": 10,
                "activity_7d_count": 5,
                "activity_30d_count": 5,
                "daily_usage": {now.date().isoformat(): 1},
            },
        )

    @property
    def _settings_hub_view(self):
        return settings_hub.__wrapped__.__wrapped__

    @property
    def _settings_admin_view(self):
        return settings_admin_users.__wrapped__.__wrapped__

    @property
    def _settings_admin_global_usage_fragment_view(self):
        return settings_admin_users_global_usage_fragment.__wrapped__.__wrapped__

    @property
    def _settings_admin_usage_detail_fragment_view(self):
        return settings_admin_users_usage_detail_fragment.__wrapped__.__wrapped__

    def _prepare_request(self, request: HttpRequest, *, user: User) -> HttpRequest:
        request.user = user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _users_section_html(self, response) -> str:
        content = response.content.decode()
        users_header = content.rfind("<h3>Users</h3>")
        if users_header < 0:
            return content
        return content[users_header:]

    def test_settings_hub_hides_admin_nav_link_for_manager(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_hub")),
            user=self.manager,
        )

        response = self._settings_hub_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("indy_hub:settings_admin_users"))

    def test_settings_hub_hides_admin_nav_link_for_regular_user(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_hub")),
            user=self.regular,
        )

        response = self._settings_hub_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("indy_hub:settings_admin_users"))

    def test_regular_user_cannot_access_private_admin_page(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.regular,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("indy_hub:index"))

    def test_superuser_sees_users_outside_managed_corporations(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.member_managed.username)
        self.assertContains(response, self.member_other.username)

    def test_manager_cannot_access_private_admin_page(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.manager,
        )

        response = self._settings_admin_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("indy_hub:index"))

    def test_usage_detail_button_shows_page_history_modal(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Details", users_html)
        self.assertIn("Usage detail", users_html)
        self.assertIn("Loading user analytics", users_html)

    def test_global_usage_fragment_loads_for_superuser(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users_global_usage_fragment")
            ),
            user=self.superuser,
        )
        response = self._settings_admin_global_usage_fragment_view(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertIn("html", payload)
        self.assertIn("Visible users", payload["html"])

    def test_usage_detail_fragment_loads_for_superuser(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse(
                    "indy_hub:settings_admin_users_usage_detail_fragment",
                    args=[self.manager.id],
                )
            ),
            user=self.superuser,
        )
        response = self._settings_admin_usage_detail_fragment_view(
            request,
            user_id=self.manager.id,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertIn("html", payload)
        self.assertIn("Pages visited", payload["html"])

    def test_usage_detail_fragment_forbidden_without_admin_scope(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse(
                    "indy_hub:settings_admin_users_usage_detail_fragment",
                    args=[self.superuser.id],
                )
            ),
            user=self.manager,
        )
        response = self._settings_admin_usage_detail_fragment_view(
            request,
            user_id=self.superuser.id,
        )

        self.assertEqual(response.status_code, 403)

    def test_managed_corporation_filter(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"managed_corporation_id": "2002"},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_other.username, users_html)
        self.assertNotIn(self.member_managed.username, users_html)

    def test_user_table_displays_main_character_corporation_only(self) -> None:
        secondary_character = _link_character(
            self.member_managed,
            character_id=9100099,
            corporation_id=4444,
            corporation_name="Secondary Corp",
        )
        profile = UserProfile.objects.get(user=self.member_managed)
        profile.main_character = secondary_character
        profile.save(update_fields=["main_character"])

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"managed_corporation_id": "4444"},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_managed.username, users_html)
        self.assertIn("Secondary Corp", users_html)
        self.assertNotIn("Managed Corp", users_html)

    def test_incomplete_filter(self) -> None:
        fake_status_map = {
            self.superuser.id: {
                "flags": {
                    "blueprints": True,
                    "jobs": True,
                    "assets": True,
                    "skills": True,
                    "online": True,
                },
                "is_complete": True,
                "missing_labels": [],
            },
            self.manager.id: {
                "flags": {
                    "blueprints": True,
                    "jobs": True,
                    "assets": True,
                    "skills": True,
                    "online": True,
                },
                "is_complete": True,
                "missing_labels": [],
            },
            self.member_managed.id: {
                "flags": {
                    "blueprints": True,
                    "jobs": True,
                    "assets": True,
                    "skills": True,
                    "online": True,
                },
                "is_complete": True,
                "missing_labels": [],
            },
            self.member_other.id: {
                "flags": {
                    "blueprints": False,
                    "jobs": True,
                    "assets": True,
                    "skills": True,
                    "online": True,
                },
                "is_complete": False,
                "missing_labels": ["blueprints"],
            },
            self.regular.id: {
                "flags": {
                    "blueprints": True,
                    "jobs": True,
                    "assets": True,
                    "skills": True,
                    "online": True,
                },
                "is_complete": True,
                "missing_labels": [],
            },
        }

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"incomplete": "1", "managed_corporation_id": "2002"},
            ),
            user=self.superuser,
        )

        with patch(
            "indy_hub.views.hubs.collect_user_scope_status_map",
            return_value=fake_status_map,
        ):
            response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_other.username, users_html)
        self.assertNotIn(self.member_managed.username, users_html)

    def test_scope_status_fallback_is_not_eagerly_evaluated(self) -> None:
        complete_flags = {
            "blueprints": True,
            "jobs": True,
            "assets": True,
            "skills": True,
            "online": True,
        }
        fake_status_map = {
            self.superuser.id: {
                "flags": complete_flags,
                "is_complete": True,
                "missing_labels": [],
            },
            self.manager.id: {
                "flags": complete_flags,
                "is_complete": True,
                "missing_labels": [],
            },
            self.member_managed.id: {
                "flags": complete_flags,
                "is_complete": True,
                "missing_labels": [],
            },
            self.member_other.id: {
                "flags": complete_flags,
                "is_complete": True,
                "missing_labels": [],
            },
            self.regular.id: {
                "flags": complete_flags,
                "is_complete": True,
                "missing_labels": [],
            },
        }

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        with (
            patch(
                "indy_hub.views.hubs.collect_user_scope_status_map",
                return_value=fake_status_map,
            ),
            patch(
                "indy_hub.views.hubs.collect_user_scope_status",
                side_effect=AssertionError("Fallback should not be called"),
            ),
        ):
            response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)

    def test_admin_page_paginates_visible_rows(self) -> None:
        for index in range(30):
            extra_user = User.objects.create_user(
                f"extra_user_{index}",
                password="secret123",
            )
            _grant_permission(extra_user, "can_access_indy_hub")
            _link_character(
                extra_user,
                character_id=9201000 + index,
                corporation_id=1001,
                corporation_name="Managed Corp",
            )

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users"), {"page": "2"}),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page 2 of")
        self.assertContains(response, "Previous")

    def test_initial_admin_page_fetches_scope_status_for_current_page_only(
        self,
    ) -> None:
        for index in range(80):
            extra_user = User.objects.create_user(
                f"scope_page_user_{index}",
                password="secret123",
            )
            _grant_permission(extra_user, "can_access_indy_hub")
            _link_character(
                extra_user,
                character_id=9301000 + index,
                corporation_id=1001,
                corporation_name="Managed Corp",
            )

        captured_batches: list[list[int]] = []

        def _fake_scope_status_map(user_ids):
            normalized_ids = [int(user_id) for user_id in user_ids]
            captured_batches.append(normalized_ids)
            return {
                int(user_id): {
                    "flags": {
                        "blueprints": True,
                        "jobs": True,
                        "assets": True,
                        "skills": True,
                        "online": True,
                    },
                    "is_complete": True,
                    "missing_labels": [],
                }
                for user_id in normalized_ids
            }

        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        with patch(
            "indy_hub.views.hubs.collect_user_scope_status_map",
            side_effect=_fake_scope_status_map,
        ):
            response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured_batches)
        self.assertTrue(all(len(batch) <= 25 for batch in captured_batches))

    def test_initial_admin_page_shows_manual_global_usage_trigger(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Load usage analytics")
