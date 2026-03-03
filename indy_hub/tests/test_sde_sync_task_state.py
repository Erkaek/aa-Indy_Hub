"""Tests for SDE compatibility periodic sync state tracking."""

# Standard Library
from datetime import datetime, timezone
from unittest.mock import patch

# Django
from django.test import TestCase

# AA Example App
from indy_hub.models import SDESyncCompatState
from indy_hub.tasks.sde_sync import sync_sde_compatibility_data


class SDESyncCompatTaskStateTests(TestCase):
    @patch("indy_hub.tasks.sde_sync.sync_sde_compat_tables")
    @patch("indy_hub.tasks.sde_sync._fetch_latest_sde_source_metadata")
    @patch("indy_hub.tasks.sde_sync.os.path.isdir")
    def test_skips_when_source_build_is_already_processed(
        self,
        mock_isdir,
        mock_fetch,
        mock_sync,
    ):
        SDESyncCompatState.objects.create(id=1, last_source_build_number=12345)
        mock_fetch.return_value = (12345, datetime(2026, 3, 1, tzinfo=timezone.utc))
        mock_isdir.return_value = True

        result = sync_sde_compatibility_data.run()

        self.assertEqual(result.get("skipped"), 1)
        self.assertEqual(result.get("reason"), "source_build_unchanged")
        mock_sync.assert_not_called()

    @patch("indy_hub.tasks.sde_sync.sync_sde_compat_tables")
    @patch("indy_hub.tasks.sde_sync._fetch_latest_sde_source_metadata")
    @patch("indy_hub.tasks.sde_sync.os.path.isdir")
    def test_updates_state_after_successful_sync(
        self,
        mock_isdir,
        mock_fetch,
        mock_sync,
    ):
        mock_fetch.return_value = (54321, datetime(2026, 3, 2, tzinfo=timezone.utc))
        mock_isdir.return_value = True
        mock_sync.return_value = {"market_groups": 1, "activities": 2}

        result = sync_sde_compatibility_data.run()

        state = SDESyncCompatState.objects.get(pk=1)
        self.assertEqual(state.last_source_build_number, 54321)
        self.assertIsNotNone(state.last_synced_at)
        self.assertEqual(result.get("source_build_number"), 54321)
        self.assertIn("source_release_date", result)
