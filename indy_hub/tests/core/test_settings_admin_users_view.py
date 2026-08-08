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
from django.db import connection
from django.http import HttpRequest
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token

# AA Example App
from indy_hub.models import AdminUserStatus, CharacterSettings, IndyHubUserUsage
from indy_hub.services.user_usage_rollups import rebuild_indy_hub_usage_rollup
from indy_hub.views.hubs import (
    _build_settings_admin_users_state,
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
        for usage_id in IndyHubUserUsage.objects.values_list("id", flat=True):
            rebuild_indy_hub_usage_rollup(int(usage_id))

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

    def _user_row_html(self, response, user: User) -> str:
        content = response.content.decode()
        row_start = content.find(f'data-user-id="{user.id}"')
        if row_start < 0:
            return ""
        row_end = content.find("</tr>", row_start)
        return content[row_start:row_end]

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

    def test_initial_admin_page_never_validates_or_refreshes_tokens(self) -> None:
        self.client.force_login(self.superuser)

        with (
            patch(
                "esi.managers.TokenQueryset.require_valid",
                side_effect=AssertionError("require_valid must not be called"),
            ),
            patch(
                "esi.models.Token.refresh",
                side_effect=AssertionError("OAuth refresh must not be called"),
            ),
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("External HTTP must not be called"),
            ),
            patch(
                "indy_hub.views.hubs._build_settings_hub_context",
                side_effect=AssertionError("Heavy Settings context must not be built"),
            ),
        ):
            response = self.client.get(reverse("indy_hub:settings_admin_users"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Recorded scopes; token validity is not refreshed on this page.",
        )

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

    def test_global_usage_fragment_never_selects_source_json(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users_global_usage_fragment")
            ),
            user=self.superuser,
        )

        with CaptureQueriesContext(connection) as queries:
            response = self._settings_admin_global_usage_fragment_view(request)

        sql = " ".join(query["sql"].lower() for query in queries.captured_queries)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("daily_usage", sql)
        self.assertNotIn("page_usage", sql)

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
                {"corporation": "Other Corp"},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_other.username, users_html)
        self.assertNotIn(self.member_managed.username, users_html)

    def test_global_search_matches_username_beyond_first_page(self) -> None:
        for index in range(30):
            extra_user = User.objects.create_user(
                f"alpha_page_user_{index:02d}",
                password="secret123",
            )
            _grant_permission(extra_user, "can_access_indy_hub")

        target = User.objects.create_user(
            "zz_global_search_target",
            password="secret123",
        )
        _grant_permission(target, "can_access_indy_hub")

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"q": "global_search_target"},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, target.username)
        self.assertContains(response, "1 result")

    def test_global_search_matches_main_character_and_numeric_id(self) -> None:
        character = UserProfile.objects.get(user=self.member_other).main_character

        for search_query in (character.character_name, str(character.character_id)):
            with self.subTest(search_query=search_query):
                request = self._prepare_request(
                    self.factory.get(
                        reverse("indy_hub:settings_admin_users"),
                        {"q": search_query},
                    ),
                    user=self.superuser,
                )

                response = self._settings_admin_view(request)
                users_html = self._users_section_html(response)

                self.assertEqual(response.status_code, 200)
                self.assertIn(self.member_other.username, users_html)
                self.assertNotIn(self.member_managed.username, users_html)

    def test_oversized_numeric_search_is_handled_as_text(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"q": "9" * 100},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No users match the current filters.")

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
                {"corporation": "4444"},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)
        member_row_html = self._user_row_html(response, self.member_managed)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_managed.username, users_html)
        self.assertIn("Secondary Corp", users_html)
        self.assertNotIn("Managed Corp", member_row_html)

    def test_incomplete_filter(self) -> None:
        AdminUserStatus.objects.filter(user=self.member_other).update(
            scope_blueprints=False,
            scope_jobs=True,
            scope_assets=True,
            scope_skills=True,
            scope_online=True,
            scope_complete=False,
            scope_score=40,
        )
        AdminUserStatus.objects.filter(user=self.member_managed).update(
            scope_blueprints=True,
            scope_jobs=True,
            scope_assets=True,
            scope_skills=True,
            scope_online=True,
            scope_complete=True,
            scope_score=50,
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"incomplete": "1", "corporation": "Other Corp"},
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_other.username, users_html)
        self.assertNotIn(self.member_managed.username, users_html)

    def test_listing_reads_scope_status_without_rebuilding_it(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        with patch(
            "indy_hub.services.admin_user_status.collect_user_scope_status_map",
            side_effect=AssertionError("The listing must not rebuild scope state"),
        ):
            response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)

    def test_activity_scope_health_and_usage_filters_are_combined_in_sql(
        self,
    ) -> None:
        AdminUserStatus.objects.filter(user=self.member_other).update(
            scope_blueprints=True,
            scope_jobs=True,
            scope_assets=True,
            scope_skills=True,
            scope_online=True,
            scope_complete=True,
            scope_score=50,
            settings_score=20,
            activity_30d_count=5,
            total_usage_count=10,
            last_used_at=timezone.now(),
        )
        AdminUserStatus.objects.filter(user=self.member_managed).update(
            scope_complete=False,
            scope_score=0,
            settings_score=0,
            activity_30d_count=0,
            total_usage_count=0,
            last_used_at=timezone.now() - timedelta(days=45),
        )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {
                    "q": "member",
                    "corporation": "Other Corp",
                    "activity": "active",
                    "scopes": "complete",
                    "health_level": "good",
                    "usage": "has_usage",
                },
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)
        users_html = self._users_section_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.member_other.username, users_html)
        self.assertNotIn(self.member_managed.username, users_html)

    def test_active_and_inactive_filters_use_materialized_activity(self) -> None:
        cases = (
            ("active", self.member_other, self.member_managed),
            ("inactive", self.member_managed, self.member_other),
        )
        for activity_filter, included_user, excluded_user in cases:
            with self.subTest(activity_filter=activity_filter):
                request = self._prepare_request(
                    self.factory.get(
                        reverse("indy_hub:settings_admin_users"),
                        {
                            "q": "member_",
                            "activity": activity_filter,
                        },
                    ),
                    user=self.superuser,
                )

                response = self._settings_admin_view(request)

                self.assertNotEqual(self._user_row_html(response, included_user), "")
                self.assertEqual(self._user_row_html(response, excluded_user), "")

    def test_server_side_sorts_use_stable_id_tie_breaker(self) -> None:
        AdminUserStatus.objects.filter(user=self.manager).update(
            total_usage_count=30,
            scope_score=40,
        )
        AdminUserStatus.objects.filter(user=self.member_other).update(
            total_usage_count=10,
            scope_score=20,
        )

        username_state = _build_settings_admin_users_state(
            self.superuser,
            {"sort": "username", "direction": "desc"},
            include_page_usage_details=False,
            include_global_usage_detail=False,
        )
        usage_state = _build_settings_admin_users_state(
            self.superuser,
            {"sort": "usage", "direction": "desc"},
            include_page_usage_details=False,
            include_global_usage_detail=False,
        )

        usernames = [row["user"].username for row in username_state["rows"]]
        usage_pairs = [
            (row["total_usage_count"], row["user"].id) for row in usage_state["rows"]
        ]
        self.assertEqual(usernames, sorted(usernames, reverse=True))
        self.assertEqual(
            usage_pairs,
            sorted(usage_pairs, key=lambda row: (-row[0], row[1])),
        )

    def test_corporation_activity_scope_and_health_sorts_are_global(self) -> None:
        now = timezone.now()
        for index, user in enumerate(
            (
                self.superuser,
                self.manager,
                self.regular,
                self.member_managed,
                self.member_other,
            )
        ):
            AdminUserStatus.objects.filter(user=user).update(
                scope_complete=index % 2 == 0,
                scope_score=index * 10,
                settings_score=0,
                activity_30d_count=0,
                last_used_at=now - timedelta(days=index + 31),
            )

        value_extractors = {
            "corporation": lambda row: row["corporations"][0][
                "corporation_name"
            ].lower(),
            "activity": lambda row: row["last_used_at"],
            "scopes": lambda row: row["scope_is_complete"],
            "health": lambda row: row["health"]["score"],
        }
        for sort_key, extractor in value_extractors.items():
            with self.subTest(sort_key=sort_key):
                state = _build_settings_admin_users_state(
                    self.superuser,
                    {"sort": sort_key, "direction": "asc"},
                    include_page_usage_details=False,
                    include_global_usage_detail=False,
                )
                actual = [(extractor(row), row["user"].id) for row in state["rows"]]
                self.assertEqual(actual, sorted(actual, key=lambda row: row))

    def test_visibility_queryset_is_applied_before_global_search(self) -> None:
        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {"q": self.member_other.username},
            ),
            user=self.superuser,
        )
        visible_users = User.objects.exclude(id=self.member_other.id)

        with patch(
            "indy_hub.views.hubs.get_visible_indy_hub_users_for_admin_scope",
            return_value=visible_users,
        ):
            response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._user_row_html(response, self.member_other), "")
        self.assertContains(response, "No users match the current filters.")

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

    def test_initial_state_has_constant_page_enrichment_queries(
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

        with CaptureQueriesContext(connection) as captured_queries:
            state = _build_settings_admin_users_state(
                self.superuser,
                {},
                include_page_usage_details=False,
                include_global_usage_detail=False,
            )

        select_sql = [
            query["sql"]
            for query in captured_queries.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT")
        ]
        forbidden_tables = {
            Token._meta.db_table,
            CharacterSettings._meta.db_table,
            IndyHubUserUsage._meta.db_table,
        }

        self.assertEqual(len(state["rows"]), 25)
        self.assertLessEqual(len(captured_queries), 3)
        for table_name in forbidden_tables:
            self.assertFalse(
                any(table_name in sql for sql in select_sql),
                (table_name, select_sql),
            )

    def test_initial_state_reuses_paginator_count_and_defers_usage_json(self) -> None:
        with CaptureQueriesContext(connection) as captured_queries:
            state = _build_settings_admin_users_state(
                self.superuser,
                {},
                include_page_usage_details=False,
                include_global_usage_detail=False,
            )

        count_queries = [
            item["sql"]
            for item in captured_queries.captured_queries
            if "COUNT(" in item["sql"].upper()
            and connection.ops.quote_name(User._meta.db_table) in item["sql"]
        ]
        usage_queries = [
            item["sql"]
            for item in captured_queries.captured_queries
            if IndyHubUserUsage._meta.db_table in item["sql"]
            and item["sql"].lstrip().upper().startswith("SELECT")
        ]

        self.assertEqual(state["all_rows_count"], User.objects.count())
        self.assertLessEqual(len(captured_queries), 10)
        self.assertEqual(len(count_queries), 1)
        self.assertFalse(usage_queries)

    def test_pagination_links_preserve_search_filters_and_sort(self) -> None:
        for index in range(30):
            extra_user = User.objects.create_user(
                f"paged_filter_user_{index:02d}",
                password="secret123",
            )
            _grant_permission(extra_user, "can_access_indy_hub")
            _link_character(
                extra_user,
                character_id=9401000 + index,
                corporation_id=1001,
                corporation_name="Managed Corp",
            )

        request = self._prepare_request(
            self.factory.get(
                reverse("indy_hub:settings_admin_users"),
                {
                    "q": "paged_filter_user",
                    "corporation": "Managed Corp",
                    "activity": "inactive",
                    "scopes": "incomplete",
                    "health_level": "critical",
                    "usage": "no_usage",
                    "sort": "corporation",
                    "direction": "desc",
                },
            ),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "page=2")
        for query_part in (
            "q=paged_filter_user",
            "corporation=Managed+Corp",
            "activity=inactive",
            "scopes=incomplete",
            "health_level=critical",
            "usage=no_usage",
            "sort=corporation",
            "direction=desc",
        ):
            self.assertContains(response, query_part)

    def test_initial_admin_page_shows_manual_global_usage_trigger(self) -> None:
        request = self._prepare_request(
            self.factory.get(reverse("indy_hub:settings_admin_users")),
            user=self.superuser,
        )

        response = self._settings_admin_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Load usage analytics")
        self.assertContains(response, "AbortController")
        self.assertContains(response, "fragmentTimeoutMs = 12000")
        self.assertContains(response, "js-analytics-retry")
        self.assertContains(response, "hide.bs.modal")
