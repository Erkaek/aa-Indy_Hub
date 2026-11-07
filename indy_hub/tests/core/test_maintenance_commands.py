"""Regression coverage for Indy Hub maintenance command loading."""

# Standard Library
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import CommandError, call_command, load_command_class
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter
from esi.openapi_clients import EsiOperation

# AA Example App
from indy_hub.models import Blueprint, IndustryJob
from indy_hub.services.esi_client import _TRANQUILITY_COOLDOWN_CACHE_KEY, shared_client
from indy_hub.services.market_prices import MARKET_PRICES_CACHE_KEY
from indy_hub.services.providers import esi_provider
from indy_hub.utils import eve


class MaintenanceCommandLoadingTests(SimpleTestCase):
    def test_commands_load_and_expose_help(self):
        for name in ("cache_esi_data", "esi_status"):
            with self.subTest(command=name):
                command = load_command_class("indy_hub", name)
                self.assertIn(
                    name, command.create_parser("manage.py", name).format_help()
                )

    def test_status_runs_without_database_or_network(self):
        output = StringIO()
        call_command("esi_status", stdout=output)
        self.assertIn("Indy Hub ESI", output.getvalue())

    def test_status_reports_real_cooldown_without_changing_it(self):
        shared_client._set_tranquility_cooldown(120)
        self.addCleanup(cache.delete, _TRANQUILITY_COOLDOWN_CACHE_KEY)
        before = cache.get(_TRANQUILITY_COOLDOWN_CACHE_KEY)
        output = StringIO()
        call_command("esi_status", stdout=output)
        self.assertIn("Tranquility global cooldown: 120s remaining", output.getvalue())
        self.assertEqual(cache.get(_TRANQUILITY_COOLDOWN_CACHE_KEY), before)

    def test_clear_cache_preserves_shared_responses_and_protections(self):
        operation = SimpleNamespace(
            api=SimpleNamespace(app_name=esi_provider._ua_appname),
            _cache_key=lambda: "esi_test_response",
        )
        etag_key = EsiOperation._etag_key(operation)
        preserved = {
            "other_app_etag_esi_test_response": "other ETag",
            "esi_test_response": "shared ESI response",
            _TRANQUILITY_COOLDOWN_CACHE_KEY: "cooldown",
            "esi_error_limit_reset": 60,
            "indy_hub:manual-refresh:in-flight": "task lock",
            "indy_hub:temporary-project:test": "unsaved project",
        }
        values = {**preserved, etag_key: "Indy Hub ETag", MARKET_PRICES_CACHE_KEY: {}}
        cache.set_many(values)
        self.addCleanup(cache.delete_many, list(values))
        call_command("esi_status", clear_cache=True, stdout=StringIO())
        self.assertIsNone(cache.get(etag_key))
        self.assertIsNone(cache.get(MARKET_PRICES_CACHE_KEY))
        self.assertEqual(cache.get_many(preserved), preserved)

    def test_obsolete_reset_option_is_explicit_and_preserves_protections(self):
        cache.set("esi_error_limit_reset", 60)
        self.addCleanup(cache.delete, "esi_error_limit_reset")
        with self.assertRaisesMessage(CommandError, "obsolete"):
            call_command("esi_status", reset_circuit_breakers=True)
        self.assertEqual(cache.get("esi_error_limit_reset"), 60)

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    )
    def test_unsupported_clear_backend_fails_before_deleting_data(self):
        cache.set(MARKET_PRICES_CACHE_KEY, {34: "prices"})
        with self.assertRaisesMessage(CommandError, "delete_pattern"):
            call_command("esi_status", clear_cache=True)
        self.assertEqual(cache.get(MARKET_PRICES_CACHE_KEY), {34: "prices"})

    def test_cache_command_rejects_missing_or_conflicting_options(self):
        for options in (
            {},
            {"all": True, "user": "pilot"},
            {"all": True, "types_only": True, "characters_only": True},
        ):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command("cache_esi_data", stdout=StringIO(), **options)


class CacheNameCommandTests(TestCase):
    def setUp(self):
        self.addCleanup(eve._CHAR_NAME_CACHE.clear)
        eve._CHAR_NAME_CACHE.clear()
        self.owner = User.objects.create_user("cache-owner")
        self.job_owner = User.objects.create_user("cache-job-owner")
        EveCharacter.objects.bulk_create(
            [
                EveCharacter(character_id=101, character_name="Local Pilot"),
                EveCharacter(character_id=102, character_name="Job Pilot"),
            ]
        )
        Blueprint.objects.bulk_create(
            [
                Blueprint(
                    owner_user=self.owner,
                    character_id=101,
                    item_id=1,
                    type_id=34,
                    location_id=1,
                    location_flag="Hangar",
                    quantity=1,
                ),
                Blueprint(
                    owner_user=self.owner,
                    character_id=999,
                    item_id=2,
                    type_id=99,
                    owner_kind=Blueprint.OwnerKind.CORPORATION,
                    location_id=1,
                    location_flag="Hangar",
                    quantity=1,
                ),
            ]
        )
        IndustryJob.objects.bulk_create(
            [
                IndustryJob(
                    owner_user=self.job_owner,
                    character_id=102,
                    job_id=1,
                    installer_id=102,
                    activity_id=1,
                    blueprint_id=3,
                    blueprint_type_id=35,
                    product_type_id=36,
                    runs=1,
                    status="active",
                    duration=60,
                    start_date=timezone.now(),
                    end_date=timezone.now(),
                ),
            ]
        )

    def test_character_names_use_one_local_query_and_unknown_ids_fall_back(self):
        with self.assertNumQueries(1):
            names = eve.batch_cache_character_names([101, 102, 103, 101, None])
        self.assertEqual(names, {101: "Local Pilot", 102: "Job Pilot", 103: "103"})
        with self.assertNumQueries(0):
            self.assertEqual(eve.get_character_name(101), "Local Pilot")
            self.assertEqual(eve.batch_cache_character_names([]), {})

    @patch("indy_hub.management.commands.cache_esi_data.batch_cache_type_names")
    def test_all_collects_both_owners_and_excludes_corporation_rows(self, cache_types):
        output = StringIO()
        call_command("cache_esi_data", all=True, stdout=output)
        self.assertEqual(set(cache_types.call_args.args[0]), {34, 35, 36})
        self.assertEqual(eve._CHAR_NAME_CACHE, {101: "Local Pilot", 102: "Job Pilot"})
        self.assertIn("local to this command process", output.getvalue())

    @patch("indy_hub.management.commands.cache_esi_data.batch_cache_type_names")
    def test_selected_user_and_characters_only_skip_type_resolution(self, cache_types):
        call_command(
            "cache_esi_data",
            user=self.owner.username,
            characters_only=True,
            stdout=StringIO(),
        )
        cache_types.assert_not_called()
        self.assertEqual(eve._CHAR_NAME_CACHE, {101: "Local Pilot"})

    @patch("indy_hub.management.commands.cache_esi_data.batch_cache_character_names")
    @patch("indy_hub.management.commands.cache_esi_data.batch_cache_type_names")
    def test_types_only_skips_character_resolution(self, cache_types, cache_characters):
        call_command("cache_esi_data", all=True, types_only=True, stdout=StringIO())
        cache_characters.assert_not_called()
        self.assertEqual(set(cache_types.call_args.args[0]), {34, 35, 36})

    def test_unknown_user_raises_command_error(self):
        with self.assertRaisesMessage(CommandError, "not found"):
            call_command("cache_esi_data", user="missing", stdout=StringIO())
