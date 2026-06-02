"""Regression tests for Indy Hub SDE sync management commands."""

# Standard Library
from unittest.mock import patch

# Django
from django.core.management import call_command
from django.test import TestCase

# AA Example App
from indy_hub.models import SDESyncCompatState


class SyncSdeCompatCommandTests(TestCase):
    @patch("indy_hub.management.commands.sync_sde_compat.sync_sde_compat_tables")
    @patch("indy_hub.management.commands.sync_sde_compat.os.path.isdir")
    def test_sync_uses_existing_folder(self, mock_isdir, mock_sync):
        mock_isdir.return_value = True

        call_command("sync_sde_compat", sde_folder="/tmp/existing")

        mock_sync.assert_called_once_with(sde_folder="/tmp/existing")
        state = SDESyncCompatState.objects.get(pk=1)
        self.assertIsNotNone(state.last_synced_at)

    @patch("indy_hub.management.commands.sync_sde_compat.sync_sde_compat_tables")
    @patch("eve_sde.sde_tasks.delete_sde_folder")
    @patch("eve_sde.sde_tasks.download_extract_sde")
    @patch("eve_sde.sde_tasks.SDE_FOLDER", new="/tmp/eve-sde")
    @patch("indy_hub.management.commands.sync_sde_compat.os.path.isdir")
    def test_sync_downloads_and_deletes_temp_folder_when_missing(
        self,
        mock_isdir,
        mock_download,
        mock_delete,
        mock_sync,
    ):
        mock_isdir.side_effect = [False, True]

        call_command("sync_sde_compat", sde_folder="/tmp/missing")

        mock_download.assert_called_once_with()
        mock_sync.assert_called_once_with(sde_folder="/tmp/eve-sde")
        mock_delete.assert_called_once_with()

    @patch("indy_hub.management.commands.sync_sde_compat.sync_sde_compat_tables")
    @patch("eve_sde.sde_tasks.delete_sde_folder")
    @patch("eve_sde.sde_tasks.download_extract_sde")
    @patch("eve_sde.sde_tasks.SDE_FOLDER", new="/tmp/eve-sde")
    @patch("indy_hub.management.commands.sync_sde_compat.os.path.isdir")
    def test_sync_cleans_temp_folder_even_when_sync_fails(
        self,
        mock_isdir,
        mock_download,
        mock_delete,
        mock_sync,
    ):
        mock_isdir.side_effect = [False, True]
        mock_sync.side_effect = RuntimeError("sync failed")

        with self.assertRaises(RuntimeError):
            call_command("sync_sde_compat", sde_folder="/tmp/missing")

        mock_download.assert_called_once_with()
        mock_delete.assert_called_once_with()

    @patch("indy_hub.management.commands.sync_sde_compat.sync_sde_compat_tables")
    @patch("eve_sde.sde_tasks.delete_sde_folder")
    @patch("eve_sde.sde_tasks.download_extract_sde")
    @patch("eve_sde.sde_tasks.SDE_FOLDER", new="/tmp/eve-sde")
    @patch("indy_hub.management.commands.sync_sde_compat.os.path.isdir")
    def test_sync_retries_download_on_transient_eoferror(
        self,
        mock_isdir,
        mock_download,
        mock_delete,
        mock_sync,
    ):
        mock_isdir.side_effect = [False, True]
        mock_download.side_effect = [EOFError("truncated"), None]

        call_command("sync_sde_compat", sde_folder="/tmp/missing")

        self.assertEqual(mock_download.call_count, 2)
        mock_sync.assert_called_once_with(sde_folder="/tmp/eve-sde")
        mock_delete.assert_called_once_with()


class IndySdeCommandTests(TestCase):
    @patch("indy_hub.management.commands.indy_sde.call_command")
    @patch("indy_hub.management.commands.indy_sde.download_extract_sde_with_retry")
    @patch("eve_sde.sde_tasks.SDE_FOLDER", new="/tmp/eve-sde")
    @patch("indy_hub.management.commands.indy_sde.os.path.isdir")
    def test_indy_sde_uses_retrying_download_when_folder_missing(
        self,
        mock_isdir,
        mock_download_with_retry,
        mock_call_command,
    ):
        mock_isdir.return_value = False

        call_command("indy_sde")

        mock_download_with_retry.assert_called_once_with(max_attempts=2)
        mock_call_command.assert_called_once_with(
            "sync_sde_compat",
            sde_folder="/tmp/eve-sde",
            verbosity=1,
        )


class IndySdeCompatAliasCommandTests(TestCase):
    @patch("indy_hub.management.commands.indy_sde_compat.call_command")
    def test_alias_forwards_to_sync_sde_compat(self, mock_call_command):
        call_command("indy_sde_compat", sde_folder="/tmp/alias")

        mock_call_command.assert_called_once_with(
            "sync_sde_compat",
            sde_folder="/tmp/alias",
            verbosity=1,
        )
