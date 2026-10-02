# Standard Library
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

# Django
from django.test import SimpleTestCase, override_settings

location_population_migration = import_module(
    "indy_hub.migrations.0025_populate_location_names_on_migrate"
)


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class LocationPopulationMigrationTests(SimpleTestCase):
    @patch("indy_hub.services.location_population.populate_location_names")
    @patch("indy_hub.tasks.industry.populate_location_names_async")
    def test_enqueues_location_population_with_queue_once_arguments(
        self, mock_task, mock_populate
    ):
        mock_task.delay.return_value = SimpleNamespace(id="location-task")

        location_population_migration._populate_location_names(None, None)

        mock_task.delay.assert_called_once_with(
            location_ids=None,
            force_refresh=True,
            dry_run=False,
        )
        mock_populate.assert_not_called()

    @patch("indy_hub.services.location_population.populate_location_names")
    @patch("indy_hub.tasks.industry.populate_location_names_async")
    def test_falls_back_to_synchronous_population_when_enqueue_fails(
        self, mock_task, mock_populate
    ):
        mock_task.delay.side_effect = RuntimeError("broker unavailable")
        mock_populate.return_value = {
            "blueprints": 1,
            "jobs": 2,
            "locations": 3,
        }

        location_population_migration._populate_location_names(None, None)

        mock_task.delay.assert_called_once_with(
            location_ids=None,
            force_refresh=True,
            dry_run=False,
        )
        mock_populate.assert_called_once_with(
            logger_override=location_population_migration.logger,
            force_refresh=True,
            schedule_async=False,
        )
