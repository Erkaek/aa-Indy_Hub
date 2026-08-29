"""Tests for database-only Craft market fee profiles."""

# Standard Library
from decimal import Decimal

# Django
from django.contrib.auth.models import User
from django.test import TestCase

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership, UserProfile
from allianceauth.eveonline.models import EveCharacter

# AA Example App
from indy_hub.models import CharacterSettings, IndustrySkillSnapshot
from indy_hub.services.market_fees import (
    ACCOUNTING_SKILL_TYPE_ID,
    BROKER_RELATIONS_SKILL_TYPE_ID,
    build_craft_market_fee_profiles,
    compute_estimated_broker_fee_percent,
    compute_sales_tax_percent,
    persist_craft_market_fee_preference,
    sanitize_craft_market_fees,
)


class CraftMarketFeeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("market-fee-user")
        self.character = EveCharacter.objects.create(
            character_id=9_001_001,
            character_name="Erka Anaya",
            corporation_id=2_000_001,
            corporation_name="Industry Corp",
            corporation_ticker="INDY",
        )
        CharacterOwnership.objects.create(
            user=self.user,
            character=self.character,
            owner_hash="market-fee-owner",
        )
        profile, _created = UserProfile.objects.get_or_create(user=self.user)
        profile.main_character = self.character
        profile.save(update_fields=["main_character"])

    def test_current_skill_formulas(self) -> None:
        self.assertEqual(compute_sales_tax_percent(0), Decimal("7.5"))
        self.assertEqual(compute_sales_tax_percent(5), Decimal("3.375"))
        self.assertEqual(
            compute_estimated_broker_fee_percent(2),
            Decimal("2.4"),
        )

    def test_profiles_ignore_an_unsaved_or_placeholder_user(self) -> None:
        self.assertEqual(
            build_craft_market_fee_profiles(object()),
            {"characters": [], "default_character_id": None},
        )

    def test_profile_uses_cached_skills_and_saved_broker_fee(self) -> None:
        IndustrySkillSnapshot.objects.create(
            owner_user=self.user,
            character_id=self.character.character_id,
            skill_levels={
                str(ACCOUNTING_SKILL_TYPE_ID): {"active": 3, "trained": 3},
                str(BROKER_RELATIONS_SKILL_TYPE_ID): {"active": 2, "trained": 2},
            },
        )
        CharacterSettings.objects.create(
            user=self.user,
            character_id=self.character.character_id,
            market_broker_fee_percent=Decimal("2.37"),
        )

        payload = build_craft_market_fee_profiles(self.user)

        self.assertEqual(payload["default_character_id"], self.character.character_id)
        row = payload["characters"][0]
        self.assertEqual(row["accounting_level"], 3)
        self.assertEqual(row["broker_relations_level"], 2)
        self.assertEqual(row["sales_tax_percent"], 5.025)
        self.assertEqual(row["estimated_broker_fee_percent"], 2.4)
        self.assertEqual(row["saved_broker_fee_percent"], 2.37)
        self.assertIs(row["skills_missing"], False)

    def test_preference_save_preserves_other_character_settings(self) -> None:
        setting = CharacterSettings.objects.create(
            user=self.user,
            character_id=self.character.character_id,
            allow_copy_requests=True,
        )

        persist_craft_market_fee_preference(
            self.user,
            {
                "purpose": "market_sale",
                "sellerCharacterId": self.character.character_id,
                "brokerFeePercent": "2.405",
                "safetyTaxPercent": 0,
            },
        )

        setting.refresh_from_db()
        self.assertIs(setting.allow_copy_requests, True)
        self.assertEqual(setting.market_broker_fee_percent, Decimal("2.41"))

    def test_preference_save_requires_market_sale_purpose(self) -> None:
        for purpose in (None, "invalid", "personal_use"):
            with self.subTest(purpose=purpose):
                persist_craft_market_fee_preference(
                    self.user,
                    {
                        "purpose": purpose,
                        "sellerCharacterId": self.character.character_id,
                        "brokerFeePercent": "2.40",
                        "safetyTaxPercent": 0,
                    },
                )

        self.assertFalse(
            CharacterSettings.objects.filter(
                user=self.user,
                character_id=self.character.character_id,
            ).exists()
        )

    def test_workspace_state_rejects_unowned_character_and_clamps_rates(self) -> None:
        self.assertEqual(
            sanitize_craft_market_fees(
                self.user,
                {
                    "sellerCharacterId": 99,
                    "brokerFeePercent": 2.4,
                    "safetyTaxPercent": 0,
                },
            ),
            {},
        )
        self.assertEqual(
            sanitize_craft_market_fees(
                self.user,
                {
                    "purpose": "market_sale",
                    "sellerCharacterId": self.character.character_id,
                    "brokerFeePercent": 101,
                    "safetyTaxPercent": -2,
                },
            ),
            {
                "purpose": "market_sale",
                "sellerCharacterId": self.character.character_id,
                "brokerFeePercent": 100.0,
                "safetyTaxPercent": 0.0,
            },
        )

    def test_personal_use_state_does_not_require_a_selling_character(self) -> None:
        self.assertEqual(
            sanitize_craft_market_fees(
                self.user,
                {
                    "purpose": "personal_use",
                    "sellerCharacterId": None,
                    "brokerFeePercent": 2.4,
                    "safetyTaxPercent": 0.25,
                },
            ),
            {
                "purpose": "personal_use",
                "sellerCharacterId": None,
                "brokerFeePercent": 2.4,
                "safetyTaxPercent": 0.25,
            },
        )
