# Standard Library
from unittest.mock import patch

# Third Party
from django_celery_beat.models import PeriodicTask

# Django
from django.test import TestCase

# AA Example App
from indy_hub.apps import _reconcile_periodic_tasks_at_beat_start
from indy_hub.schedules import INDY_HUB_BEAT_SCHEDULE
from indy_hub.tasks import setup_periodic_tasks


class PeriodicTaskReconciliationTests(TestCase):
    def setUp(self) -> None:
        PeriodicTask.objects.filter(name__startswith="indy-hub-").delete()

    def test_setup_creates_dispatcher_and_all_current_schedules(self) -> None:
        result = setup_periodic_tasks()

        self.assertEqual(result["created"], len(INDY_HUB_BEAT_SCHEDULE))
        self.assertEqual(
            set(
                PeriodicTask.objects.filter(name__startswith="indy-hub-").values_list(
                    "name", flat=True
                )
            ),
            set(INDY_HUB_BEAT_SCHEDULE),
        )

        dispatcher = PeriodicTask.objects.select_related("crontab").get(
            name="indy-hub-dispatch-pending-industry-bulk-updates"
        )
        self.assertEqual(
            dispatcher.task,
            "indy_hub.tasks.industry.dispatch_pending_industry_bulk_updates",
        )
        self.assertEqual(dispatcher.crontab.minute, "*")
        self.assertEqual(dispatcher.crontab.hour, "*")
        self.assertTrue(dispatcher.enabled)

    def test_setup_is_idempotent(self) -> None:
        setup_periodic_tasks()

        result = setup_periodic_tasks()

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], len(INDY_HUB_BEAT_SCHEDULE))

    @patch("indy_hub.tasks.setup_periodic_tasks")
    def test_beat_start_reconciles_schedule(self, mock_setup) -> None:
        _reconcile_periodic_tasks_at_beat_start()

        mock_setup.assert_called_once_with()
