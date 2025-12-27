"""Tests for indy_hub.utils.industry helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import SimpleTestCase

from indy_hub.utils.industry import (
    calculate_industry_metrics,
    calculate_production_costs,
    estimate_completion_time,
    optimize_production_chain,
)


class IndustryUtilsTests(SimpleTestCase):
    def test_calculate_industry_metrics_empty(self) -> None:
        metrics = calculate_industry_metrics([])
        self.assertEqual(metrics["total_jobs"], 0)
        self.assertEqual(metrics["completed_jobs"], 0)
        self.assertEqual(metrics["completion_rate"], 0)
        self.assertEqual(metrics["average_duration"], 0)

    def test_calculate_industry_metrics_uses_duration(self) -> None:
        jobs = [
            {"status": "delivered", "duration": 10},
            {"status": "active", "duration": 30},
        ]
        metrics = calculate_industry_metrics(jobs)
        self.assertEqual(metrics["total_jobs"], 2)
        self.assertEqual(metrics["completed_jobs"], 1)
        self.assertAlmostEqual(metrics["completion_rate"], 0.5)
        self.assertEqual(metrics["average_duration"], 20)
        self.assertEqual(metrics["active_jobs"], 1)

    def test_estimate_completion_time_from_end_date(self) -> None:
        end_date = datetime.now(dt_timezone.utc) + timedelta(seconds=3)
        remaining = estimate_completion_time({"end_date": end_date})
        self.assertGreaterEqual(remaining, 0)
        self.assertLessEqual(remaining, 3)

    def test_optimize_production_chain_scores_me_te(self) -> None:
        result = optimize_production_chain(
            [{"material_efficiency": 10, "time_efficiency": 20}]
        )
        self.assertEqual(result["efficiency_score"], 100)
        self.assertEqual(result["suggestions"], [])

    def test_calculate_production_costs_is_explicitly_unavailable(self) -> None:
        result = calculate_production_costs(12345, runs=2)
        self.assertFalse(result["available"])
        self.assertIsNone(result["material_cost"])
        self.assertIsNone(result["facility_fees"])
        self.assertIsNone(result["total_cost"])
