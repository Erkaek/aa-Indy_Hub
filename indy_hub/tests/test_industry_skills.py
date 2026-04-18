"""Regression tests for industry skill level handling."""

# Django
from django.test import SimpleTestCase

# AA Example App
from indy_hub.models import IndustryActivityMixin
from indy_hub.services.industry_skills import (
    SKILL_TYPE_IDS,
    build_skill_snapshot_defaults,
    compute_activity_time_bonus_percent,
    missing_skill_requirements,
)


class IndustrySkillLevelUsageTests(SimpleTestCase):
    def test_snapshot_defaults_keep_active_levels_for_slot_calculations(self) -> None:
        defaults = build_skill_snapshot_defaults(
            {
                SKILL_TYPE_IDS["mass_production"]: {"active": 3, "trained": 5},
                SKILL_TYPE_IDS["advanced_mass_production"]: {
                    "active": 1,
                    "trained": 4,
                },
            }
        )

        self.assertEqual(defaults["mass_production_level"], 3)
        self.assertEqual(defaults["advanced_mass_production_level"], 1)
        self.assertEqual(defaults["trained_mass_production_level"], 5)
        self.assertEqual(defaults["trained_advanced_mass_production_level"], 4)

    def test_time_bonus_uses_active_skill_level(self) -> None:
        bonus_percent = compute_activity_time_bonus_percent(
            {SKILL_TYPE_IDS["advanced_industry"]: {"active": 2, "trained": 5}},
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
            required_skill_ids=set(),
            skill_bonus_attributes={
                SKILL_TYPE_IDS["advanced_industry"]: {
                    "advancedIndustrySkillIndustryJobTimeBonus": -3.0,
                }
            },
        )

        self.assertEqual(bonus_percent, 6.0)

    def test_time_bonus_combines_copying_skills_multiplicatively(self) -> None:
        bonus_percent = compute_activity_time_bonus_percent(
            {
                SKILL_TYPE_IDS["science"]: {"active": 5, "trained": 5},
                SKILL_TYPE_IDS["advanced_industry"]: {"active": 5, "trained": 5},
            },
            activity_id=IndustryActivityMixin.ACTIVITY_COPYING,
            required_skill_ids={SKILL_TYPE_IDS["science"]},
            skill_bonus_attributes={
                SKILL_TYPE_IDS["science"]: {
                    "copySpeedBonus": -5.0,
                },
                SKILL_TYPE_IDS["advanced_industry"]: {
                    "advancedIndustrySkillIndustryJobTimeBonus": -3.0,
                },
            },
        )

        self.assertEqual(bonus_percent, 36.25)

    def test_missing_requirements_compare_against_active_level(self) -> None:
        missing = missing_skill_requirements(
            {SKILL_TYPE_IDS["research"]: {"active": 2, "trained": 5}},
            [
                {
                    "skill_id": SKILL_TYPE_IDS["research"],
                    "level": 3,
                    "skill_name": "Research",
                }
            ],
        )

        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["current_level"], 2)
        self.assertEqual(missing[0]["required_level"], 3)
