"""Tests for the toggle_favorite_structure API endpoint."""

import json

from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, TestCase

from indy_hub.models import IndustryStructure, UserFavoriteStructure
from indy_hub.views.api import toggle_favorite_structure


def _make_user(username: str) -> User:
    return User.objects.create_user(username, password="x")


def _make_structure(name: str = "Test Structure") -> IndustryStructure:
    return IndustryStructure.objects.create(
        name=name,
        solar_system_name="Jita",
        visibility_scope=IndustryStructure.VisibilityScope.PUBLIC,
    )


def _unwrap(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


class ToggleFavoriteStructureTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = _make_user("favuser")
        self.structure = _make_structure()
        self._view = _unwrap(toggle_favorite_structure)

    def _post(self, data):
        request = self.factory.post("/", data)
        request.user = self.user
        return self._view(request)

    def test_missing_structure_id_returns_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)

    def test_invalid_structure_id_returns_400(self):
        response = self._post({"structure_id": "abc"})
        self.assertEqual(response.status_code, 400)

    def test_zero_structure_id_returns_400(self):
        response = self._post({"structure_id": 0})
        self.assertEqual(response.status_code, 400)

    def test_nonexistent_structure_id_raises_404(self):
        request = self.factory.post("/", {"structure_id": 99999})
        request.user = self.user
        with self.assertRaises(Http404):
            self._view(request)

    def test_first_toggle_creates_favorite_and_returns_is_favorite_true(self):
        response = self._post({"structure_id": self.structure.pk})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["is_favorite"])
        self.assertEqual(data["structure_id"], self.structure.pk)
        self.assertTrue(
            UserFavoriteStructure.objects.filter(
                user=self.user, structure=self.structure
            ).exists()
        )

    def test_second_toggle_removes_favorite_and_returns_is_favorite_false(self):
        UserFavoriteStructure.objects.create(user=self.user, structure=self.structure)
        response = self._post({"structure_id": self.structure.pk})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data["is_favorite"])
        self.assertEqual(data["structure_id"], self.structure.pk)
        self.assertFalse(
            UserFavoriteStructure.objects.filter(
                user=self.user, structure=self.structure
            ).exists()
        )

    def test_toggle_is_per_user(self):
        other_user = _make_user("otheruser")
        UserFavoriteStructure.objects.create(user=other_user, structure=self.structure)
        response = self._post({"structure_id": self.structure.pk})
        data = json.loads(response.content)
        self.assertTrue(data["is_favorite"])
        # other_user's favorite must still exist
        self.assertTrue(
            UserFavoriteStructure.objects.filter(
                user=other_user, structure=self.structure
            ).exists()
        )
