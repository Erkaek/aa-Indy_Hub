"""Basic smoke tests for the Indy Hub app."""

# Django
from django.apps import apps
from django.test import TestCase

# AA Example App
from indy_hub.utils.eve import get_type_name


class IndyHubConfigTests(TestCase):
    def test_app_is_registered(self) -> None:
        """The indy_hub app should be installed and discoverable."""
        app_config = apps.get_app_config("indy_hub")
        self.assertEqual(app_config.name, "indy_hub")

    def test_get_type_name_graceful_fallback(self) -> None:
        """`get_type_name` should fall back to the stringified id when EveUniverse is absent."""
        self.assertEqual(get_type_name(12345), "12345")
