"""Rebuild the local read model for the admin-users listing."""

# Django
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

# AA Example App
from indy_hub.services.admin_user_status import rebuild_admin_user_statuses

User = get_user_model()


class Command(BaseCommand):
    help = "Rebuild local AdminUserStatus rows without contacting ESI."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        user_id = options.get("user_id")
        batch_size = max(25, min(int(options["batch_size"]), 1000))
        rebuilt_total = 0

        users = User.objects.order_by("id").values_list("id", flat=True)
        if user_id is not None:
            users = users.filter(id=int(user_id))

        after_user_id = 0
        while True:
            batch = list(users.filter(id__gt=after_user_id)[:batch_size])
            if not batch:
                break
            rebuilt_total += rebuild_admin_user_statuses(
                [int(current_user_id) for current_user_id in batch]
            )
            after_user_id = int(batch[-1])

        self.stdout.write(
            self.style.SUCCESS(f"Rebuilt {rebuilt_total} admin-user status row(s).")
        )
