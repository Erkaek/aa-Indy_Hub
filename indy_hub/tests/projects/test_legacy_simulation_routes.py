"""Public navigation contracts for retired single-blueprint simulations."""

# Standard Library
from unittest.mock import patch

# Django
from django.contrib import messages
from django.contrib.auth.models import Permission, User
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import resolve, reverse

# AA Example App
from indy_hub.models import ProductionProject, ProductionProjectItem
from indy_hub.tests.test_smoke import assign_main_character

ROUTES = {
    "production_simulations": "simulations/legacy/",
    "rename_production_simulation": "simulations/{}/rename/",
    "delete_production_simulation": "simulations/{}/delete/",
    "edit_simulation_name": "simulations/{}/edit-name/",
}
NOTICE = "Legacy single-blueprint simulations were removed. Use craft tables instead."


class LegacySimulationRouteTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("legacy-route-owner")
        self.other = User.objects.create_user("legacy-route-other")
        self.denied = User.objects.create_user("legacy-route-denied")
        for number, user in enumerate([self.owner, self.other, self.denied]):
            assign_main_character(user, character_id=1_999_950_001 + number)
        access = Permission.objects.get(
            content_type__app_label="indy_hub", codename="can_access_indy_hub"
        )
        self.owner.user_permissions.add(access)
        self.other.user_permissions.add(access)
        self.project = ProductionProject.objects.create(
            user=self.owner,
            name="Current craft table",
            workspace_state={"simulation_name": "Current craft table"},
        )
        self.foreign = ProductionProject.objects.create(
            user=self.other, name="Other owner's craft table"
        )
        self.client.force_login(self.owner)

    def _url(self, name, identifier=None):
        args = [] if name == "production_simulations" else [identifier]
        return reverse("indy_hub:" + name, args=args)

    def _snapshot(self):
        return (
            list(ProductionProject.objects.order_by("pk").values()),
            list(ProductionProjectItem.objects.order_by("pk").values()),
        )

    def test_historical_names_paths_and_wrappers_still_resolve(self):
        for name, path in ROUTES.items():
            with self.subTest(route=name):
                url = self._url(name, self.project.pk)
                self.assertEqual(url, "/indy_hub/" + path.format(self.project.pk))
                self.assertEqual(resolve(url).url_name, name)
                self.assertEqual(resolve(url).func.__name__, name)

    def test_methods_ids_messages_and_analytics_preserve_navigation_only(self):
        before = self._snapshot()
        target = reverse("indy_hub:production_simulations_list")
        for name in ROUTES:
            identifiers = (
                [None]
                if name == "production_simulations"
                else [self.project.pk, self.foreign.pk, 999_999_999]
            )
            for identifier in identifiers:
                for method in [
                    "GET",
                    "POST",
                    "HEAD",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "OPTIONS",
                ]:
                    with (
                        self.subTest(route=name, identifier=identifier, method=method),
                        patch("indy_hub.views.user.emit_view_analytics_event") as event,
                        patch(
                            "indy_hub.views.industry.emit_view_analytics_event"
                        ) as industry_event,
                    ):
                        self.client.cookies.pop("messages", None)
                        response = self.client.generic(
                            method,
                            self._url(name, identifier)
                            + "?next=https://outside.invalid/",
                            data='{"name":"must not rename","delete":"1"}',
                            content_type="application/json",
                        )
                        self.assertEqual(response.status_code, 302)
                        self.assertEqual(response.url, target)
                        notices = list(get_messages(response.wsgi_request))
                        self.assertEqual(
                            [(m.level, str(m)) for m in notices],
                            [(messages.INFO, NOTICE)],
                        )
                        if name == "production_simulations":
                            event.assert_called_once_with(
                                view_name="user.production_simulations",
                                request=response.wsgi_request,
                            )
                        else:
                            event.assert_not_called()
                        industry_event.assert_not_called()
                        self.assertEqual(self._snapshot(), before)

    def test_anonymous_and_missing_permission_keep_their_refusals(self):
        before = self._snapshot()
        for user in [None, self.denied]:
            client = Client()
            if user:
                client.force_login(user)
            for name in ROUTES:
                for method in ["GET", "POST"]:
                    with (
                        self.subTest(user=user, route=name, method=method),
                        patch("indy_hub.views.user.emit_view_analytics_event") as event,
                    ):
                        response = client.generic(
                            method, self._url(name, self.project.pk)
                        )
                        self.assertEqual(response.status_code, 403 if user else 302)
                        if user is None:
                            self.assertTrue(
                                response.url.startswith(reverse("auth_login_user"))
                            )
                        event.assert_not_called()
        self.assertEqual(self._snapshot(), before)

    def test_unsafe_requests_still_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        before = self._snapshot()
        for name in ROUTES:
            with self.subTest(route=name):
                response = client.post(self._url(name, self.project.pk))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self._snapshot(), before)
