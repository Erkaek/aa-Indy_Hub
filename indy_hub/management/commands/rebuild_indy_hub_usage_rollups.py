"""Rebuild prepared usage analytics from retained source JSON."""

# Django
from django.core.management.base import BaseCommand, CommandError

# Local
from indy_hub.models import IndyHubUserUsage
from indy_hub.services.user_usage_rollups import (
    ROLLUP_REBUILD_BATCH_SIZE,
    ROLLUP_REBUILD_MAX_BATCH_SIZE,
    rebuild_indy_hub_usage_rollups,
)


class Command(BaseCommand):
    help = "Rebuild Indy Hub daily usage rollups without contacting ESI."

    def add_arguments(self, parser):
        parser.add_argument("--usage-id", type=int)
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--batch-size", type=int, default=ROLLUP_REBUILD_BATCH_SIZE)

    def handle(self, *args, **options):
        usage_id = options.get("usage_id")
        user_id = options.get("user_id")
        if usage_id is not None and user_id is not None:
            raise CommandError("Use either --usage-id or --user-id, not both.")

        batch_size = max(
            5,
            min(int(options["batch_size"]), ROLLUP_REBUILD_MAX_BATCH_SIZE),
        )
        usages = IndyHubUserUsage.objects.order_by("id").values_list("id", flat=True)
        if usage_id is not None:
            usages = usages.filter(id=int(usage_id))
        elif user_id is not None:
            usages = usages.filter(user_id=int(user_id))

        after_usage_id = 0
        rebuilt_users = 0
        rebuilt_rows = 0
        while True:
            batch = list(usages.filter(id__gt=after_usage_id)[:batch_size])
            if not batch:
                break
            users_count, rows_count = rebuild_indy_hub_usage_rollups(batch)
            rebuilt_users += users_count
            rebuilt_rows += rows_count
            after_usage_id = int(batch[-1])

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt {rebuilt_rows} rollup row(s) for "
                f"{rebuilt_users} usage record(s)."
            )
        )
