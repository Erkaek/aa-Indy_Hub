"""Tests for Indy Hub user usage historization and tracking hooks."""

from __future__ import annotations

# Standard Library
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

# AA Example App
# Local
from indy_hub.decorators import indy_hub_access_required, indy_hub_permission_required
from indy_hub.middleware import IndyHubUsageTrackingMiddleware
from indy_hub.models import IndyHubUserUsage
from indy_hub.services.user_usage import (
    build_indy_hub_global_usage_detail,
    build_indy_hub_usage_detail,
    track_indy_hub_usage_for_user,
    track_indy_hub_usage_once_per_request,
)

User = get_user_model()


def _grant_permission(user: User, codename: str) -> None:
    permission = Permission.objects.get(codename=codename)
    user.user_permissions.add(permission)


class IndyHubUserUsageModelTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("usage_user", password="secret123")

    def test_register_usage_tracks_first_last_total_and_rolling_windows(self) -> None:
        now = timezone.now()
        track_indy_hub_usage_for_user(self.user, at=now - timedelta(days=40))
        track_indy_hub_usage_for_user(self.user, at=now - timedelta(days=10))
        track_indy_hub_usage_for_user(self.user, at=now - timedelta(days=5))
        track_indy_hub_usage_for_user(self.user, at=now)

        usage = IndyHubUserUsage.objects.get(user=self.user)

        self.assertEqual(usage.total_usage_count, 4)
        self.assertEqual(usage.activity_30d_count, 3)
        self.assertEqual(usage.activity_7d_count, 2)
        self.assertEqual(usage.first_used_at, now - timedelta(days=40))
        self.assertEqual(usage.last_used_at, now)

        # The 40-day-old bucket is pruned from rolling storage.
        self.assertNotIn(
            (now - timedelta(days=40)).date().isoformat(), usage.daily_usage
        )

    def test_register_usage_tracks_page_breakdown(self) -> None:
        now = timezone.now()

        track_indy_hub_usage_for_user(
            self.user,
            at=now - timedelta(days=1),
            page_path="/indy_hub/index/",
            page_label="Overview",
        )
        track_indy_hub_usage_for_user(
            self.user,
            at=now,
            page_path="/indy_hub/settings/admin-users/",
            page_label="User admin",
        )
        track_indy_hub_usage_for_user(
            self.user,
            at=now,
            page_path="/indy_hub/settings/admin-users/",
            page_label="User admin",
        )

        usage = IndyHubUserUsage.objects.get(user=self.user)

        self.assertIn("/indy_hub/index", usage.page_usage)
        self.assertIn("/indy_hub/settings/admin-users", usage.page_usage)
        self.assertEqual(
            usage.page_usage["/indy_hub/index"]["total_usage_count"],
            1,
        )
        self.assertEqual(
            usage.page_usage["/indy_hub/settings/admin-users"]["total_usage_count"],
            2,
        )

    def test_usage_detail_hides_excluded_notification_endpoint(self) -> None:
        now = timezone.now()
        usage = IndyHubUserUsage.objects.create(
            user=self.user,
            first_used_at=now - timedelta(days=2),
            last_used_at=now,
            total_usage_count=4,
            activity_7d_count=4,
            activity_30d_count=4,
            daily_usage={
                (now - timedelta(days=1)).date().isoformat(): 2,
                now.date().isoformat(): 2,
            },
            page_usage={
                "/user_notifications_count/?foo=1": {
                    "label": "/user_notifications_count/",
                    "path": "/user_notifications_count/?foo=1",
                    "total_usage_count": 2,
                    "first_used_at": (now - timedelta(days=1)).isoformat(),
                    "last_used_at": now.isoformat(),
                    "daily_usage": {
                        now.date().isoformat(): 2,
                    },
                },
                "/audit/r/96515342/account/walletactivity": {
                    "label": "/audit/r/96515342/account/walletactivity",
                    "path": "/audit/r/96515342/account/walletactivity",
                    "total_usage_count": 2,
                    "first_used_at": (now - timedelta(days=1)).isoformat(),
                    "last_used_at": now.isoformat(),
                    "daily_usage": {
                        now.date().isoformat(): 2,
                    },
                },
            },
        )

        detail = build_indy_hub_usage_detail(usage)

        rendered_paths = {str(row.get("path")) for row in detail.get("page_rows", [])}
        self.assertNotIn("/user_notifications_count/?foo=1", rendered_paths)
        self.assertIn("/audit/r/96515342/account/walletactivity", rendered_paths)

    def test_register_usage_prunes_old_and_excess_page_usage_keys(self) -> None:
        now = timezone.now()
        max_keys = IndyHubUserUsage.PAGE_USAGE_MAX_KEYS
        usage = IndyHubUserUsage.objects.create(
            user=self.user,
            first_used_at=now - timedelta(days=2),
            last_used_at=now - timedelta(days=1),
            total_usage_count=1,
            activity_7d_count=1,
            activity_30d_count=1,
            daily_usage={
                (now - timedelta(days=1)).date().isoformat(): 1,
            },
            page_usage={
                "/old": {
                    "label": "old",
                    "path": "/old",
                    "total_usage_count": 1,
                    "first_used_at": (now - timedelta(days=200)).isoformat(),
                    "last_used_at": (now - timedelta(days=200)).isoformat(),
                    "daily_usage": {
                        (now - timedelta(days=200)).date().isoformat(): 1,
                    },
                },
                **{
                    f"/k/{idx}": {
                        "label": f"k{idx}",
                        "path": f"/k/{idx}",
                        "total_usage_count": 1,
                        "first_used_at": now.isoformat(),
                        "last_used_at": now.isoformat(),
                        "daily_usage": {now.date().isoformat(): 1},
                    }
                    for idx in range(max_keys + 5)
                },
            },
        )

        usage.register_usage(at=now, page_path="/recent", page_label="recent")
        usage.register_usage(
            at=now + timedelta(seconds=1),
            page_path="/recent",
            page_label="recent",
        )

        self.assertNotIn("/old", usage.page_usage)
        self.assertIn("/recent", usage.page_usage)
        self.assertLessEqual(len(usage.page_usage), usage.PAGE_USAGE_MAX_KEYS)

    def test_usage_detail_recomputes_rolling_windows_from_today(self) -> None:
        now = timezone.now()
        usage = IndyHubUserUsage.objects.create(
            user=self.user,
            first_used_at=now - timedelta(days=90),
            last_used_at=now - timedelta(days=60),
            total_usage_count=12,
            activity_7d_count=6,
            activity_30d_count=9,
            daily_usage={
                (now - timedelta(days=60)).date().isoformat(): 12,
            },
        )

        detail = build_indy_hub_usage_detail(usage)

        self.assertEqual(int(detail["activity_7d_count"]), 0)
        self.assertEqual(int(detail["activity_30d_count"]), 0)
        self.assertEqual(
            str(detail["overall_timeline"][-1]["iso"]),
            timezone.localdate().isoformat(),
        )

    def test_global_usage_detail_recomputes_active_users_30d_from_daily_usage(
        self,
    ) -> None:
        now = timezone.now()
        stale_user = User.objects.create_user("stale_usage", password="secret123")
        active_user = User.objects.create_user("active_usage", password="secret123")

        stale_usage = IndyHubUserUsage.objects.create(
            user=stale_user,
            first_used_at=now - timedelta(days=80),
            last_used_at=now - timedelta(days=60),
            total_usage_count=5,
            activity_7d_count=2,
            activity_30d_count=5,
            daily_usage={
                (now - timedelta(days=60)).date().isoformat(): 5,
            },
        )
        active_usage = IndyHubUserUsage.objects.create(
            user=active_user,
            first_used_at=now - timedelta(days=2),
            last_used_at=now - timedelta(days=1),
            total_usage_count=3,
            activity_7d_count=0,
            activity_30d_count=0,
            daily_usage={
                timezone.localdate().isoformat(): 3,
            },
        )

        detail = build_indy_hub_global_usage_detail([stale_usage, active_usage])

        self.assertEqual(int(detail["activity_30d_count"]), 3)
        self.assertEqual(int(detail["activity_7d_count"]), 3)
        self.assertEqual(int(detail["active_user_count_30d"]), 1)


class IndyHubUsageDecoratorHookTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("decorator_user", password="secret123")
        self.user_without_access = User.objects.create_user(
            "decorator_user_no_access",
            password="secret123",
        )
        self.user_without_manage_permission = User.objects.create_user(
            "decorator_user_no_manage",
            password="secret123",
        )
        _grant_permission(self.user, "can_access_indy_hub")
        _grant_permission(self.user, "can_manage_corp_bp_requests")
        _grant_permission(self.user_without_manage_permission, "can_access_indy_hub")

    def test_access_required_decorator_records_usage(self) -> None:
        @indy_hub_access_required
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/indy_hub/test")
        request.user = self.user

        response = protected_view(request)

        self.assertEqual(response.status_code, 200)
        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertEqual(usage.total_usage_count, 1)

    def test_permission_required_decorator_records_usage(self) -> None:
        @indy_hub_permission_required("can_manage_corp_bp_requests")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/indy_hub/test")
        request.user = self.user

        response = protected_view(request)

        self.assertEqual(response.status_code, 200)
        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertEqual(usage.total_usage_count, 1)

    def test_tracking_is_done_only_once_when_both_decorators_are_stacked(self) -> None:
        @indy_hub_access_required
        @indy_hub_permission_required("can_manage_corp_bp_requests")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/indy_hub/test")
        request.user = self.user

        response = protected_view(request)

        self.assertEqual(response.status_code, 200)
        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertEqual(usage.total_usage_count, 1)

    def test_access_required_returns_403_without_indy_hub_access(self) -> None:
        @indy_hub_access_required
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/indy_hub/test")
        request.user = self.user_without_access

        response = protected_view(request)

        self.assertEqual(response.status_code, 403)

    def test_permission_required_returns_403_without_specific_permission(self) -> None:
        @indy_hub_permission_required("can_manage_corp_bp_requests")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/indy_hub/test")
        request.user = self.user_without_manage_permission

        response = protected_view(request)

        self.assertEqual(response.status_code, 403)


class IndyHubUsageMiddlewareTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("middleware_user", password="secret123")

    @override_settings(INDY_HUB_USAGE_MIDDLEWARE_ENABLED=True)
    def test_tracks_allowed_route_as_normalized_route_key(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/audit/r/96515342/account/walletactivity")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("indy_hub",),
            view_name="indy_hub:settings_hub",
            url_name="settings_hub",
        )

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertIn("route:indy_hub:settings_hub", usage.page_usage)
        self.assertNotIn("/audit/r/96515342/account/walletactivity", usage.page_usage)

    def test_does_not_track_when_middleware_is_disabled(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/indy_hub/index/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("indy_hub",),
            view_name="indy_hub:index",
            url_name="index",
        )

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())

    @override_settings(INDY_HUB_USAGE_MIDDLEWARE_ENABLED=True)
    def test_does_not_track_non_get_requests(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.post("/audit/r/96515342/account/walletactivity")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("indy_hub",),
            view_name="indy_hub:settings_hub",
            url_name="settings_hub",
        )

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())

    @override_settings(INDY_HUB_USAGE_MIDDLEWARE_ENABLED=True)
    def test_does_not_track_user_notifications_count_endpoint(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/user_notifications_count/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("indy_hub",),
            view_name="indy_hub:notifications",
            url_name="notifications",
        )

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())

    @override_settings(INDY_HUB_USAGE_MIDDLEWARE_ENABLED=True)
    def test_does_not_track_disallowed_app_name(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/some/aa/page/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("allianceauth",),
            view_name="allianceauth:dashboard",
            url_name="dashboard",
        )

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())


class IndyHubUsageServiceHookTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("service_hook_user", password="secret123")

    def test_once_per_request_skips_user_notifications_count_endpoint(self) -> None:
        request = self.factory.get("/user_notifications_count/")
        request.user = self.user

        track_indy_hub_usage_once_per_request(request)

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())

    def test_once_per_request_tracks_settings_admin_users_endpoint(self) -> None:
        request = self.factory.get("/indy_hub/settings/admin-users/")
        request.user = self.user

        track_indy_hub_usage_once_per_request(request)

        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertIn("/indy_hub/settings/admin-users", usage.page_usage)

    def test_once_per_request_skips_settings_admin_users_fragment_routes(self) -> None:
        request = self.factory.get(
            "/indy_hub/settings/admin-users/global-usage-fragment/"
        )
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("indy_hub",),
            view_name="indy_hub:settings_admin_users_global_usage_fragment",
            url_name="settings_admin_users_global_usage_fragment",
        )

        track_indy_hub_usage_once_per_request(request)

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())

    def test_once_per_request_uses_route_key_for_indy_hub_pages(self) -> None:
        request = self.factory.get("/indy_hub/settings/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            app_names=("indy_hub",),
            view_name="indy_hub:settings_hub",
            url_name="settings_hub",
        )

        track_indy_hub_usage_once_per_request(request)

        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertIn("route:indy_hub:settings_hub", usage.page_usage)

    def test_once_per_request_never_raises_when_tracking_fails(self) -> None:
        request = self.factory.get("/indy_hub/index/")
        request.user = self.user

        with patch(
            "indy_hub.services.user_usage.track_indy_hub_usage_for_user",
            side_effect=RuntimeError("tracking down"),
        ):
            track_indy_hub_usage_once_per_request(request)

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())
        self.assertTrue(getattr(request, "_indy_hub_usage_tracked", False))
