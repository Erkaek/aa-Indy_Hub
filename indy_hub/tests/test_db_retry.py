"""Regression tests for MySQL retry helpers."""

# Standard Library
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Django
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.utils import OperationalError
from django.test import SimpleTestCase, TransactionTestCase

# AA Example App
from indy_hub.models import IndustrySkillSnapshot
from indy_hub.tasks.industry import (
    update_character_skill_snapshot_for_character,
    update_user_skill_snapshots,
)
from indy_hub.tasks.location import cache_structure_name
from indy_hub.tasks.material_exchange_contracts import sync_esi_contracts
from indy_hub.tasks.user import (
    update_character_roles_for_character,
    update_user_roles_snapshots,
)
from indy_hub.utils.db_retry import update_or_create_with_mysql_retry


class MySQLRetryHelperTests(SimpleTestCase):
    databases = {"default"}

    def test_task_modules_import_and_expose_expected_callables(self) -> None:
        self.assertTrue(callable(update_user_skill_snapshots))
        self.assertTrue(callable(update_character_skill_snapshot_for_character))
        self.assertTrue(callable(update_user_roles_snapshots))
        self.assertTrue(callable(update_character_roles_for_character))
        self.assertTrue(callable(cache_structure_name))
        self.assertTrue(callable(sync_esi_contracts))

    def test_deadlock_retries_then_updates_successfully(self) -> None:
        manager = Mock()
        manager.update_or_create.side_effect = [
            OperationalError(1213, "Deadlock found when trying to get lock"),
            ("row", False),
        ]

        model = type(
            "DummyModel",
            (),
            {
                "__name__": "DummyModel",
                "DoesNotExist": type("DoesNotExist", (Exception,), {}),
                "objects": manager,
            },
        )

        with (
            patch("indy_hub.utils.db_retry.random.random", return_value=0.0),
            patch("indy_hub.utils.db_retry.time.sleep") as mock_sleep,
        ):
            result = update_or_create_with_mysql_retry(
                model,
                lookup={"character_id": 1},
                defaults={"owner_user": "user"},
            )

        self.assertEqual(result, ("row", False))
        self.assertEqual(manager.update_or_create.call_count, 2)
        mock_sleep.assert_called_once()

    def test_duplicate_key_refreshes_existing_row_in_transaction(self) -> None:
        existing = SimpleNamespace(save=Mock())

        class DummyManager:
            def __init__(self) -> None:
                self.update_or_create = Mock(
                    side_effect=IntegrityError(
                        1062, "Duplicate entry '1' for key 'character_id'"
                    )
                )

            def select_for_update(self):
                return self

            def get(self, **kwargs):
                return existing

        model = type(
            "DummyModel",
            (),
            {
                "__name__": "DummyModel",
                "DoesNotExist": type("DoesNotExist", (Exception,), {}),
                "objects": DummyManager(),
            },
        )

        with (
            patch("indy_hub.utils.db_retry.random.random", return_value=0.0),
            patch("indy_hub.utils.db_retry.time.sleep"),
        ):
            result = update_or_create_with_mysql_retry(
                model,
                lookup={"character_id": 1},
                defaults={"owner_user": "user", "level": 5},
            )

        self.assertIs(result[0], existing)
        self.assertFalse(result[1])
        self.assertEqual(existing.owner_user, "user")
        self.assertEqual(existing.level, 5)
        existing.save.assert_called_once_with(update_fields=["owner_user", "level"])

    def test_duplicate_key_retries_when_row_is_not_visible_yet(self) -> None:
        manager = Mock()
        manager.update_or_create.side_effect = [
            IntegrityError(1062, "Duplicate entry '1' for key 'character_id'"),
            ("row", False),
        ]

        class DummyManager:
            def __init__(self) -> None:
                self.update_or_create = manager.update_or_create
                self._get_calls = 0

            def select_for_update(self):
                return self

            def get(self, **kwargs):
                self._get_calls += 1
                raise DummyModel.DoesNotExist()

        DummyModel = type(
            "DummyModel",
            (),
            {
                "__name__": "DummyModel",
                "DoesNotExist": type("DoesNotExist", (Exception,), {}),
                "objects": DummyManager(),
            },
        )

        with (
            patch("indy_hub.utils.db_retry.random.random", return_value=0.0),
            patch("indy_hub.utils.db_retry.time.sleep") as mock_sleep,
        ):
            result = update_or_create_with_mysql_retry(
                DummyModel,
                lookup={"character_id": 1},
                defaults={"owner_user": "user"},
            )

        self.assertEqual(result, ("row", False))
        self.assertEqual(DummyModel.objects.update_or_create.call_count, 2)
        self.assertEqual(DummyModel.objects._get_calls, 1)
        mock_sleep.assert_called_once()

    def test_duplicate_key_retries_when_recovery_hits_deadlock(self) -> None:
        manager = Mock()
        manager.update_or_create.side_effect = [
            IntegrityError(1062, "Duplicate entry '1' for key 'character_id'"),
            ("row", False),
        ]

        class DummyManager:
            def __init__(self) -> None:
                self.update_or_create = manager.update_or_create
                self._get_calls = 0

            def select_for_update(self):
                return self

            def get(self, **kwargs):
                self._get_calls += 1
                raise OperationalError(1213, "Deadlock found when trying to get lock")

        DummyModel = type(
            "DummyModel",
            (),
            {
                "__name__": "DummyModel",
                "DoesNotExist": type("DoesNotExist", (Exception,), {}),
                "objects": DummyManager(),
            },
        )

        with (
            patch("indy_hub.utils.db_retry.random.random", return_value=0.0),
            patch("indy_hub.utils.db_retry.time.sleep") as mock_sleep,
        ):
            result = update_or_create_with_mysql_retry(
                DummyModel,
                lookup={"character_id": 1},
                defaults={"owner_user": "user"},
            )

        self.assertEqual(result, ("row", False))
        self.assertEqual(DummyModel.objects.update_or_create.call_count, 2)
        self.assertEqual(DummyModel.objects._get_calls, 1)
        mock_sleep.assert_called_once()


class MySQLRetryHelperIntegrationTests(TransactionTestCase):
    def test_duplicate_key_recovery_updates_existing_db_row(self) -> None:
        user_model = get_user_model()
        original_owner = user_model.objects.create_user(
            username="retry-original",
            password="x",
        )
        new_owner = user_model.objects.create_user(
            username="retry-new",
            password="x",
        )
        existing = IndustrySkillSnapshot.objects.create(
            owner_user=original_owner,
            character_id=9001,
            skill_levels={"1": {"trained": 1, "active": 1}},
            mass_production_level=1,
        )

        original_update_or_create = IndustrySkillSnapshot.objects.update_or_create

        def simulated_race(*args, **kwargs):
            if not hasattr(simulated_race, "called"):
                simulated_race.called = True
                raise IntegrityError(
                    1062,
                    "Duplicate entry '9001' for key 'character_id'",
                )
            return original_update_or_create(*args, **kwargs)

        with patch.object(
            IndustrySkillSnapshot.objects,
            "update_or_create",
            side_effect=simulated_race,
        ):
            instance, created = update_or_create_with_mysql_retry(
                IndustrySkillSnapshot,
                lookup={"character_id": 9001},
                defaults={
                    "owner_user": new_owner,
                    "skill_levels": {"2": {"trained": 5, "active": 5}},
                    "mass_production_level": 5,
                },
            )

        self.assertFalse(created)
        self.assertEqual(instance.pk, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.owner_user_id, new_owner.pk)
        self.assertEqual(existing.skill_levels, {"2": {"trained": 5, "active": 5}})
        self.assertEqual(existing.mass_production_level, 5)
        self.assertEqual(
            IndustrySkillSnapshot.objects.filter(character_id=9001).count(),
            1,
        )
