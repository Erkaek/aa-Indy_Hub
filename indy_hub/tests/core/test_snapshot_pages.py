"""Pages render local snapshots without refreshing OAuth or external data."""

# Standard Library
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

# AA Example App
from indy_hub.models import Blueprint, IndustryJob, MaterialExchangeConfig
from indy_hub.tests.test_smoke import assign_main_character, grant_indy_permissions
from indy_hub.views.navigation import build_nav_context


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class SnapshotPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("snapshot-pages")
        self.character = assign_main_character(self.user, character_id=102501)
        grant_indy_permissions(self.user)
        self.client.force_login(self.user)
        self.job_url = reverse("indy_hub:personnal_job_list")
        start = timezone.now()
        self.job = IndustryJob.objects.create(
            owner_user=self.user,
            character_id=self.character.character_id,
            job_id=9012501,
            installer_id=self.character.character_id,
            station_id=60003760,
            location_name="Local Factory",
            activity_id=1,
            blueprint_id=6012501,
            blueprint_type_id=6012502,
            runs=1,
            status="active",
            duration=3600,
            start_date=start,
            end_date=start + timedelta(hours=1),
            activity_name="Manufacturing",
            blueprint_type_name="Cached Blueprint",
            product_type_name="Cached Product",
            character_name="Local Character",
        )

    def test_navigation_defaults_to_passive_token_coverage(self):
        with patch(
            "indy_hub.utils.menu_badge.count_characters_missing_scopes", return_value=0
        ) as count:
            build_nav_context(self.user)
        count.assert_called_once_with(self.user.id, validate_tokens=False)

    def test_jobs_initial_and_fragment_use_only_local_data(self):
        with (
            patch(
                "esi.managers.TokenQueryset.require_valid",
                side_effect=AssertionError("OAuth called"),
            ) as validate,
            patch(
                "indy_hub.views.industry._fetch_character_skill_levels",
            ) as fetch,
            patch("indy_hub.views.industry.request_manual_refresh") as refresh,
        ):
            page = self.client.get(self.job_url, {"force_refresh": "1"})
            fragment = self.client.get(
                self.job_url, {"fragment": "1", "refresh": "1", "force_refresh": "1"}
            )
        self.assertContains(page, "Cached Product")
        self.assertEqual(fragment.status_code, 200)
        self.assertIn("Cached Product", fragment.json()["html"])
        self.assertIn("jobSlots", fragment.json()["extra_html"])
        self.assertEqual(fragment["Cache-Control"], "no-store")
        validate.assert_not_called()
        fetch.assert_not_called()
        refresh.assert_not_called()

    def test_job_fragment_tracks_changes_and_removals_and_isolates_users(self):
        other = User.objects.create_user("snapshot-other")
        IndustryJob.objects.filter(pk=self.job.pk).update(
            product_type_name="Updated Product"
        )
        response = self.client.get(self.job_url, {"fragment": "1"})
        self.assertIn("Updated Product", response.json()["html"])
        IndustryJob.objects.filter(pk=self.job.pk).update(owner_user=other)
        response = self.client.get(self.job_url, {"fragment": "1"})
        self.assertNotIn("Updated Product", response.json()["html"])
        self.job.delete()
        self.assertNotIn(
            "Updated Product",
            self.client.get(self.job_url, {"fragment": "1"}).json()["html"],
        )

    def test_job_refresh_queues_skills_in_background_once(self):
        with (
            patch(
                "indy_hub.views.industry.request_manual_refresh",
                return_value=(True, None, None),
            ) as refresh,
            patch(
                "indy_hub.views.industry.update_user_skill_snapshots.apply_async"
            ) as skills,
        ):
            response = self.client.post(self.job_url, {"action": "refresh"})
        self.assertTrue(response.json()["scheduled"])
        refresh.assert_called_once_with(
            "jobs", self.user.id, priority=5, scope="character"
        )
        skills.assert_called_once_with(
            args=(self.user.id,), kwargs={"force_refresh": True}, priority=5
        )
        with (
            patch(
                "indy_hub.views.industry.request_manual_refresh",
                return_value=(False, timedelta(seconds=30), "cooldown"),
            ),
            patch(
                "indy_hub.views.industry.update_user_skill_snapshots.apply_async"
            ) as skills,
        ):
            response = self.client.post(self.job_url, {"action": "refresh"})
        self.assertFalse(response.json()["scheduled"])
        skills.assert_not_called()

    def test_job_refresh_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with patch("indy_hub.views.industry.request_manual_refresh") as refresh:
            response = client.post(self.job_url, {"action": "refresh"})
        self.assertEqual(response.status_code, 403)
        refresh.assert_not_called()

    def test_corporation_job_fragment_and_refresh_recheck_access(self):
        url = reverse("indy_hub:corporation_job_list")
        with (
            patch(
                "indy_hub.views.industry.get_viewable_corporation_job_ids",
                return_value=[],
            ),
            patch("indy_hub.views.industry.request_manual_refresh") as refresh,
        ):
            self.assertEqual(self.client.get(url, {"fragment": "1"}).status_code, 403)
            self.assertEqual(
                self.client.post(url, {"action": "refresh"}).status_code, 403
            )
        refresh.assert_not_called()
        IndustryJob.objects.filter(pk=self.job.pk).update(
            owner_kind=Blueprint.OwnerKind.CORPORATION, corporation_id=2000000
        )
        with (
            patch(
                "indy_hub.views.industry.get_viewable_corporation_job_ids",
                return_value=[2000000],
            ),
            patch("indy_hub.views.industry.request_manual_refresh") as refresh,
        ):
            self.assertIn(
                "Cached Product", self.client.get(url, {"fragment": "1"}).json()["html"]
            )
            self.assertEqual(
                self.client.post(url, {"action": "refresh"}).status_code, 403
            )
        refresh.assert_not_called()

    def test_job_fragment_failure_returns_json_without_exception_details(self):
        with patch(
            "indy_hub.views.industry._build_slot_overview_rows",
            side_effect=RuntimeError("secret-detail"),
        ):
            response = self.client.get(self.job_url, {"fragment": "1"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-detail", response.content.decode())

    def _configure_material_exchange(self):
        grant_indy_permissions(self.user, "can_manage_material_hub")
        self.config = MaterialExchangeConfig.objects.create(
            corporation_id=self.character.corporation_id,
            structure_id=60003760,
            structure_name="Local station",
            hangar_division=1,
            sell_markup_percent=Decimal("5"),
        )

    def test_sell_initial_page_defers_inventory_and_prices_to_fragment(self):
        self._configure_material_exchange()
        url = reverse("indy_hub:material_exchange_sell")
        with (
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure_data",
                return_value=({34: 10}, {}, False),
            ) as assets,
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={34: {"buy": Decimal("5"), "sell": Decimal("6")}},
            ) as prices,
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34},
            ),
            patch(
                "indy_hub.views.material_exchange._get_group_map",
                return_value={34: "Minerals"},
            ),
            patch("indy_hub.views.material_exchange.batch_cache_type_names"),
            patch(
                "indy_hub.views.material_exchange.get_type_name",
                return_value="Tritanium",
            ),
        ):
            page = self.client.get(url)
            self.assertContains(page, "sellInitialStatus")
            assets.assert_not_called()
            prices.assert_not_called()
            fragment = self.client.get(
                url, headers={"x-requested-with": "XMLHttpRequest"}
            )
        self.assertEqual(fragment.status_code, 200)
        self.assertIn("Tritanium", fragment.json()["html"])
        self.assertEqual(fragment["Cache-Control"], "no-store")
        assets.assert_called_once()
        prices.assert_called_once_with([34])

    def test_config_initial_form_does_not_validate_or_fetch(self):
        self._configure_material_exchange()
        with (
            patch(
                "esi.managers.TokenQueryset.require_valid",
                side_effect=AssertionError("OAuth called"),
            ) as validate,
            patch(
                "indy_hub.views.material_exchange_config._get_user_corporations"
            ) as corps,
            patch(
                "indy_hub.views.material_exchange_config._get_corp_hangar_divisions"
            ) as divisions,
        ):
            page = self.client.get(reverse("indy_hub:material_exchange_config"))
        self.assertContains(page, 'id="configForm"')
        self.assertContains(page, "Local station")
        validate.assert_not_called()
        corps.assert_not_called()
        divisions.assert_not_called()

    def test_config_corporations_fragment_requires_management_permission(self):
        url = reverse("indy_hub:material_exchange_config")
        with patch(
            "indy_hub.views.material_exchange_config._get_user_corporations",
            return_value=[{"id": 2000000, "name": "Local corp", "ticker": "LC"}],
        ) as corps:
            self.assertEqual(self.client.get(url, {"fragment": "1"}).status_code, 403)
            corps.assert_not_called()
            grant_indy_permissions(self.user, "can_manage_material_hub")
            response = self.client.get(url, {"fragment": "1"})
        self.assertEqual(response.json()["corporations"][0]["name"], "Local corp")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_config_post_still_requires_esi_authorization(self):
        self._configure_material_exchange()
        with patch(
            "indy_hub.views.material_exchange_config._handle_config_save"
        ) as save:
            response = self.client.post(
                reverse("indy_hub:material_exchange_config"),
                {"corporation_id": "2000000"},
            )
        self.assertEqual(response.status_code, 302)
        save.assert_not_called()
        self.assertTrue(
            MaterialExchangeConfig.objects.filter(pk=self.config.pk).exists()
        )

    def test_structure_ajax_returns_missing_scope_flags_without_sso_redirect(self):
        grant_indy_permissions(self.user, "can_manage_material_hub")
        with (
            patch(
                "indy_hub.views.material_exchange_config._get_corp_structures",
                return_value=([], True),
            ),
            patch(
                "indy_hub.views.material_exchange_config._get_corp_hangar_divisions",
                return_value=({}, True),
            ),
        ):
            response = self.client.get(
                reverse("indy_hub:material_exchange_get_structures", args=[2000000])
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["division_scope_missing"])
        self.assertTrue(response.json()["assets_scope_missing"])

    def test_expired_renewable_skill_token_keeps_local_slot_counts(self):
        # Alliance Auth
        from esi.models import Scope, Token

        # AA Example App
        from indy_hub.models import IndustrySkillSnapshot

        token = Token.objects.bulk_create(
            [
                Token(
                    user=self.user,
                    character_id=self.character.character_id,
                    character_name=self.character.character_name,
                    character_owner_hash="local",
                    token_type="Character",
                    access_token="access",
                    refresh_token="refresh",
                )
            ]
        )[0]
        scope = Scope.objects.get_or_create(name="esi-skills.read_skills.v1")[0]
        Token.scopes.through.objects.bulk_create(
            [Token.scopes.through(token_id=token.pk, scope_id=scope.pk)]
        )
        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(days=1)
        )
        IndustrySkillSnapshot.objects.create(
            owner_user=self.user,
            character_id=self.character.character_id,
            mass_production_level=4,
            skill_levels={"3387": {"active": 4, "trained": 4}},
        )
        with patch(
            "esi.managers.TokenQueryset.require_valid",
            side_effect=AssertionError("OAuth called"),
        ):
            response = self.client.get(self.job_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["slot_overview_summary"]["manufacturing"]["total"], 5
        )
        self.assertEqual(
            response.context["slot_overview_summary"]["manufacturing"]["used"], 1
        )

    def test_background_skill_refresh_can_force_a_fresh_snapshot(self):
        # AA Example App
        from indy_hub.models import IndustrySkillSnapshot
        from indy_hub.tasks.industry import (
            update_character_skill_snapshot_for_character,
        )

        IndustrySkillSnapshot.objects.create(
            owner_user=self.user,
            character_id=self.character.character_id,
            mass_production_level=4,
            skill_levels={"3387": {"active": 4, "trained": 4}},
        )
        with (
            patch("indy_hub.tasks.industry.Token.objects.filter") as tokens,
            patch(
                "indy_hub.tasks.industry._fetch_character_skill_levels_with_token",
                return_value={3387: {"active": 5, "trained": 5}},
            ) as fetch,
        ):
            token = (
                tokens.return_value.require_scopes.return_value.require_valid.return_value.order_by.return_value.first.return_value
            )
            result = update_character_skill_snapshot_for_character(
                self.user.id, self.character.character_id, force_refresh=True
            )
        self.assertEqual(result["status"], "updated")
        fetch.assert_called_once_with(token, force_refresh=True)
        self.assertEqual(
            IndustrySkillSnapshot.objects.get(
                character_id=self.character.character_id
            ).mass_production_level,
            5,
        )

    def test_overview_skill_recap_does_not_validate_oauth(self):
        # AA Example App
        from indy_hub.models import UserOnboardingProgress

        UserOnboardingProgress.objects.update_or_create(
            user=self.user, defaults={"manual_steps": {"overview_intro_seen": True}}
        )
        with (
            patch(
                "indy_hub.views.user._build_sde_readiness_context",
                return_value={"sde_ready": True},
            ),
            patch(
                "esi.managers.TokenQueryset.require_valid",
                side_effect=AssertionError("OAuth called"),
            ) as validate,
        ):
            response = self.client.get(reverse("indy_hub:index"))
        self.assertEqual(response.status_code, 200)
        validate.assert_not_called()

    def test_sell_fragment_price_failure_returns_error_instead_of_empty_inventory(self):
        self._configure_material_exchange()
        with (
            patch(
                "indy_hub.views.material_exchange._is_material_exchange_enabled",
                return_value=True,
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_user_assets_for_structure_data",
                return_value=({34: 10}, {}, False),
            ),
            patch(
                "indy_hub.views.material_exchange._fetch_fuzzwork_prices",
                return_value={},
            ),
            patch(
                "indy_hub.views.material_exchange._get_allowed_type_ids_for_config",
                return_value={34},
            ),
        ):
            response = self.client.get(
                reverse("indy_hub:material_exchange_sell"),
                headers={"x-requested-with": "XMLHttpRequest"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())
