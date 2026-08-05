"""Tests for Indy Hub user usage historization and tracking hooks."""

from __future__ import annotations

# Standard Library
from datetime import timedelta

# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

# AA Example App
# Local
from indy_hub.decorators import indy_hub_access_required, indy_hub_permission_required
from indy_hub.middleware import IndyHubUsageTrackingMiddleware
from indy_hub.models import IndyHubUserUsage
from indy_hub.services.user_usage import (
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

        self.assertIn("/indy_hub/index/", usage.page_usage)
        self.assertIn("/indy_hub/settings/admin-users/", usage.page_usage)
        self.assertEqual(
            usage.page_usage["/indy_hub/index/"]["total_usage_count"],
            1,
        )
        self.assertEqual(
            usage.page_usage["/indy_hub/settings/admin-users/"]["total_usage_count"],
            2,
        )
        self.assertEqual(
            usage.page_usage["/indy_hub/settings/admin-users/"]["label"],
            "User admin",
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


class IndyHubUsageDecoratorHookTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("decorator_user", password="secret123")
        _grant_permission(self.user, "can_access_indy_hub")
        _grant_permission(self.user, "can_manage_corp_bp_requests")

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


class IndyHubUsageMiddlewareTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user("middleware_user", password="secret123")

    def test_tracks_corptools_audit_path_as_exact_page_path(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/audit/r/96515342/account/walletactivity")
        request.user = self.user

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        usage = IndyHubUserUsage.objects.get(user=self.user)
        self.assertIn("/audit/r/96515342/account/walletactivity", usage.page_usage)
        self.assertEqual(
            usage.page_usage["/audit/r/96515342/account/walletactivity"]["path"],
            "/audit/r/96515342/account/walletactivity",
        )

    def test_does_not_track_non_get_requests(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.post("/audit/r/96515342/account/walletactivity")
        request.user = self.user

        middleware.process_view(request, lambda req: HttpResponse("ok"), (), {})

        self.assertFalse(IndyHubUserUsage.objects.filter(user=self.user).exists())

    def test_does_not_track_user_notifications_count_endpoint(self) -> None:
        middleware = IndyHubUsageTrackingMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/user_notifications_count/")
        request.user = self.user

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
