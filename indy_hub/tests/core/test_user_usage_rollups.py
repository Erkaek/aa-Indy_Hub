"""Tests for prepared global usage analytics and reconstruction paths."""

from __future__ import annotations

# Standard Library
import importlib
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

# Django
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, migrations
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

# AA Example App
# Local
from indy_hub.models import IndyHubUsageDailyRollup, IndyHubUserUsage
from indy_hub.services.user_usage import track_indy_hub_usage_for_user
from indy_hub.services.user_usage_rollups import (
    build_indy_hub_global_usage_detail_from_rollups,
    rebuild_indy_hub_usage_rollup,
)
from indy_hub.tasks.housekeeping import consolidate_indy_hub_usage_rollups

User = get_user_model()


class IndyHubUsageRollupTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("rollup_user", password="secret123")

    def _create_historical_usage(self) -> IndyHubUserUsage:
        now = timezone.now()
        return IndyHubUserUsage.objects.create(
            user=self.user,
            first_used_at=now - timedelta(days=40),
            last_used_at=now,
            total_usage_count=20,
            activity_7d_count=8,
            activity_30d_count=11,
            daily_usage={
                (now - timedelta(days=40)).date().isoformat(): 9,
                (now - timedelta(days=1)).date().isoformat(): 4,
                now.date().isoformat(): 7,
            },
            page_usage={
                "route:indy_hub:index": {
                    "label": "Overview",
                    "total_usage_count": 11,
                    "daily_usage": {
                        (now - timedelta(days=1)).date().isoformat(): 4,
                        now.date().isoformat(): 7,
                    },
                },
                "/user_notifications_count/": {
                    "label": "Excluded",
                    "total_usage_count": 3,
                    "daily_usage": {now.date().isoformat(): 3},
                },
            },
        )

    def test_rebuild_backfills_retained_history_and_excludes_internal_pages(self):
        usage = self._create_historical_usage()

        rebuilt_rows = rebuild_indy_hub_usage_rollup(usage.id)

        usage.refresh_from_db()
        rows = IndyHubUsageDailyRollup.objects.filter(usage=usage)
        self.assertEqual(rebuilt_rows, 4)
        self.assertEqual(rows.filter(page_key="").count(), 2)
        self.assertEqual(rows.filter(page_key="route:indy_hub:index").count(), 2)
        self.assertFalse(rows.filter(page_key__contains="notifications").exists())
        self.assertEqual(
            sum(rows.filter(page_key="").values_list("usage_count", flat=True)),
            11,
        )
        self.assertEqual(usage.rollup_synced_at, usage.updated_at)

    def test_rebuild_is_idempotent_and_corrects_existing_rows(self):
        usage = self._create_historical_usage()
        rebuild_indy_hub_usage_rollup(usage.id)
        IndyHubUsageDailyRollup.objects.filter(
            usage=usage,
            page_key="",
        ).update(usage_count=999)

        rebuild_indy_hub_usage_rollup(usage.id)

        overall = IndyHubUsageDailyRollup.objects.filter(usage=usage, page_key="")
        self.assertEqual(overall.count(), 2)
        self.assertEqual(sum(overall.values_list("usage_count", flat=True)), 11)

    def test_tracking_updates_rollups_incrementally(self):
        now = timezone.now()

        track_indy_hub_usage_for_user(
            self.user,
            at=now,
            page_path="route:indy_hub:index",
            page_label="Overview",
        )
        track_indy_hub_usage_for_user(
            self.user,
            at=now + timedelta(seconds=1),
            page_path="route:indy_hub:index",
            page_label="Overview",
        )

        usage = IndyHubUserUsage.objects.get(user=self.user)
        counters = {
            row.page_key: row.usage_count
            for row in IndyHubUsageDailyRollup.objects.filter(usage=usage)
        }
        self.assertEqual(counters[""], 2)
        self.assertEqual(counters["route:indy_hub:index"], 2)
        self.assertEqual(usage.rollup_synced_at, usage.updated_at)

    def test_tracking_does_not_hide_pending_historical_backfill(self):
        usage = self._create_historical_usage()

        track_indy_hub_usage_for_user(
            self.user,
            page_path="route:indy_hub:index",
            page_label="Overview",
        )

        usage.refresh_from_db()
        self.assertIsNone(usage.rollup_synced_at)
        rebuild_indy_hub_usage_rollup(usage.id)
        usage.refresh_from_db()
        self.assertEqual(usage.rollup_synced_at, usage.updated_at)
        self.assertEqual(
            IndyHubUsageDailyRollup.objects.get(
                usage=usage,
                usage_day=timezone.localdate(),
                page_key="",
            ).usage_count,
            8,
        )

    def test_filtered_global_detail_uses_fixed_small_sql_aggregates_without_json(self):
        usage = self._create_historical_usage()
        rebuild_indy_hub_usage_rollup(usage.id)
        other_user = User.objects.create_user("other_rollup", password="secret123")
        other_usage = IndyHubUserUsage.objects.create(
            user=other_user,
            first_used_at=timezone.now(),
            last_used_at=timezone.now(),
            total_usage_count=500,
            activity_7d_count=500,
            activity_30d_count=500,
            daily_usage={timezone.localdate().isoformat(): 500},
        )
        rebuild_indy_hub_usage_rollup(other_usage.id)

        with CaptureQueriesContext(connection) as queries:
            detail = build_indy_hub_global_usage_detail_from_rollups(
                User.objects.filter(id=self.user.id)
            )

        sql = " ".join(query["sql"].lower() for query in queries.captured_queries)
        self.assertNotIn("daily_usage", sql)
        self.assertNotIn("page_usage", sql)
        self.assertEqual(len(queries), 7)
        self.assertEqual(detail["visible_user_count"], 1)
        self.assertEqual(detail["total_usage_count"], 20)
        self.assertEqual(detail["activity_30d_count"], 11)
        self.assertEqual(detail["active_user_count_30d"], 1)
        self.assertEqual(detail["rollup_pending_user_count"], 0)

    def test_global_detail_bounds_pages_and_groups_the_remainder(self):
        today = timezone.localdate()
        page_usage = {
            f"route:indy_hub:page_{index}": {
                "label": f"Page {index}",
                "total_usage_count": index,
                "daily_usage": {today.isoformat(): index},
            }
            for index in range(1, 15)
        }
        usage = IndyHubUserUsage.objects.create(
            user=self.user,
            first_used_at=timezone.now(),
            last_used_at=timezone.now(),
            total_usage_count=sum(range(1, 15)),
            activity_7d_count=sum(range(1, 15)),
            activity_30d_count=sum(range(1, 15)),
            daily_usage={today.isoformat(): sum(range(1, 15))},
            page_usage=page_usage,
        )
        rebuild_indy_hub_usage_rollup(usage.id)

        detail = build_indy_hub_global_usage_detail_from_rollups(
            User.objects.filter(id=self.user.id)
        )

        self.assertEqual(detail["page_total_30d"], sum(range(1, 15)))
        self.assertLessEqual(len(detail["page_bars"]), 11)
        self.assertIn(
            "Other pages",
            {page["label"] for page in detail["page_bars"]},
        )
        self.assertTrue(detail["page_breakdown_matches_activity_30d"])

    def test_management_command_rebuilds_requested_user(self):
        self._create_historical_usage()
        stdout = StringIO()

        call_command(
            "rebuild_indy_hub_usage_rollups",
            user_id=self.user.id,
            stdout=stdout,
        )

        self.assertTrue(
            IndyHubUsageDailyRollup.objects.filter(usage__user=self.user).exists()
        )
        self.assertIn("for 1 usage record", stdout.getvalue())

    def test_celery_consolidation_is_bounded_and_continues(self):
        usage_ids = []
        for index in range(5):
            user = User.objects.create_user(
                f"rollup_batch_{index}", password="secret123"
            )
            usage_ids.append(IndyHubUserUsage.objects.create(user=user).id)

        with (
            patch(
                "indy_hub.tasks.housekeeping.rebuild_usage_rollups",
                return_value=(5, 15),
            ) as rebuild,
            patch.object(consolidate_indy_hub_usage_rollups, "apply_async") as enqueue,
        ):
            result = consolidate_indy_hub_usage_rollups.run(batch_size=5)

        rebuild.assert_called_once_with(usage_ids)
        self.assertEqual(result["rebuilt_users"], 5)
        self.assertEqual(result["rebuilt_rows"], 15)
        self.assertTrue(result["has_more"])
        enqueue.assert_called_once_with(
            kwargs={"after_usage_id": usage_ids[-1], "batch_size": 5},
            countdown=1,
            priority=8,
        )

    def test_rollup_migration_is_schema_only_and_reversible(self):
        migration_module = importlib.import_module(
            "indy_hub.migrations.0114_indyhubusagedailyrollup_and_sync_cursor"
        )

        self.assertTrue(migration_module.Migration.operations)
        self.assertFalse(
            any(
                isinstance(operation, migrations.RunPython)
                for operation in migration_module.Migration.operations
            )
        )
        self.assertTrue(
            all(
                operation.reversible
                for operation in migration_module.Migration.operations
            )
        )
