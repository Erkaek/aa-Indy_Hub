# Django
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404, HttpRequest
from django.test import RequestFactory, TestCase
from django.urls import reverse

# AA Example App
from indy_hub.models import ProductionProject
from indy_hub.views.industry import delete_production_project


class ProductionProjectDeleteViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="project-owner", password="testpass123"
        )
        self.other_user = user_model.objects.create_user(
            username="other-owner", password="testpass123"
        )
        permission = Permission.objects.get(codename="can_access_indy_hub")
        self.user.user_permissions.add(permission)
        self.other_user.user_permissions.add(permission)

        self.project = ProductionProject.objects.create(
            user=self.user,
            name="Fleet Vedmak",
            status=ProductionProject.Status.DRAFT,
            source_kind=ProductionProject.SourceKind.EFT,
            summary={
                "selected_items": 4,
                "selected_quantity": 12,
                "craftable_items": 3,
                "buy_items": 1,
            },
        )

    @property
    def _view(self):
        return delete_production_project.__wrapped__.__wrapped__

    def _prepare_request(self, request: HttpRequest, *, user=None) -> HttpRequest:
        request.user = user or self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_get_delete_view_renders_confirmation_page(self):
        request = self._prepare_request(
            self.factory.get(
                reverse(
                    "indy_hub:delete_production_project",
                    args=[self.project.project_ref],
                )
            )
        )
        response = self._view(request, self.project.project_ref)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fleet Vedmak")
        self.assertContains(response, "Delete craft table")

    def test_post_delete_view_removes_project_and_redirects(self):
        request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:delete_production_project",
                    args=[self.project.project_ref],
                )
            )
        )
        response = self._view(request, self.project.project_ref)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("indy_hub:production_simulations_list"))
        self.assertFalse(
            ProductionProject.objects.filter(
                project_ref=self.project.project_ref
            ).exists()
        )

    def test_user_cannot_delete_another_users_project(self):
        request = self._prepare_request(
            self.factory.post(
                reverse(
                    "indy_hub:delete_production_project",
                    args=[self.project.project_ref],
                )
            ),
            user=self.other_user,
        )
        with self.assertRaises(Http404):
            self._view(request, self.project.project_ref)
        self.assertTrue(
            ProductionProject.objects.filter(
                project_ref=self.project.project_ref
            ).exists()
        )
