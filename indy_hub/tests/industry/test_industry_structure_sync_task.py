"""Tests for automatic synced industry structure refresh tasks."""

from __future__ import annotations

# Standard Library
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.tasks.industry_structure_sync import (
    sync_persisted_industry_structure_registry,
)


class IndustryStructureSyncTaskTests(TestCase):
    @patch("indy_hub.tasks.industry_structure_sync.sync_persisted_industry_structures")
    def test_task_refreshes_persisted_synced_structures(self, mock_sync) -> None:
        mock_sync.return_value = {
            "corporations": 2,
            "created": 1,
            "updated": 3,
            "unchanged": 4,
            "deleted": 1,
            "errors": [],
        }

        result = sync_persisted_industry_structure_registry()

        mock_sync.assert_called_once_with(force_refresh=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["corporations"], 2)
        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["deleted"], 1)

    @patch(
        "indy_hub.tasks.industry_structure_sync.sync_persisted_industry_structure_registry.apply_async"
    )
    @patch("indy_hub.tasks.industry_structure_sync.sync_persisted_industry_structures")
    def test_task_schedules_retry_after_transient_esi_failures(
        self,
        mock_sync,
        mock_apply_async,
    ) -> None:
        mock_sync.return_value = {
            "corporations": 3,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "deleted": 0,
            "skipped_unsupported": 0,
            "skipped_forbidden": 0,
            "skipped_unusable_token": 0,
            "rate_limited": 1,
            "deferred_due_to_rate_limit": 2,
            "retry_after_seconds": 30,
            "errors": [],
            "forbidden_samples": [],
            "unusable_token_samples": [],
            "rate_limit_samples": [],
        }

        sync_persisted_industry_structure_registry()

        mock_apply_async.assert_called_once_with(
            kwargs={"force_refresh": True},
            countdown=30,
        )
