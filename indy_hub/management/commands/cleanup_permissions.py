# Management command to clean up unwanted Django permissions
# Django
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)


class Command(BaseCommand):
    help = "Remove all default Django permissions (add/change/delete/view) for indy_hub models"

    def handle(self, *args, **options):
        # Get all indy_hub content types
        indy_hub_content_types = ContentType.objects.filter(app_label="indy_hub")

        logger.info("Starting indy_hub permission cleanup.")

        try:
            # Count existing permissions before deletion
            total_before = Permission.objects.filter(
                content_type__in=indy_hub_content_types
            ).count()

            self.stdout.write(
                f"Found {total_before} total permissions for indy_hub models"
            )
            logger.info("Found %s permissions for indy_hub models.", total_before)

            # Delete all default Django permissions
            deleted_count = 0
            for content_type in indy_hub_content_types:
                model_name = content_type.model

                # Default Django permissions
                default_perms = [
                    f"add_{model_name}",
                    f"change_{model_name}",
                    f"delete_{model_name}",
                    f"view_{model_name}",
                ]

                deleted = Permission.objects.filter(
                    content_type=content_type,
                    codename__in=default_perms,
                ).delete()

                if deleted[0] > 0:
                    deleted_count += deleted[0]
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Deleted {deleted[0]} default permissions for {content_type.model}"
                        )
                    )
                    logger.debug(
                        "Deleted %s default permissions for %s.",
                        deleted[0],
                        content_type.model,
                    )

            # Count remaining permissions
            total_after = Permission.objects.filter(
                content_type__in=indy_hub_content_types
            ).count()

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Deleted {deleted_count} default Django permissions"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Remaining permissions: {total_after} (should be 3: can_access_indy_hub, can_manage_corp_bp_requests, can_manage_material_hub)"
                )
            )
            logger.info(
                "Permission cleanup complete. Deleted=%s Remaining=%s",
                deleted_count,
                total_after,
            )

            # List remaining permissions for verification
            remaining_perms = Permission.objects.filter(
                content_type__in=indy_hub_content_types
            ).values_list("codename", "name")

            if remaining_perms:
                self.stdout.write("\nRemaining permissions:")
                for codename, name in remaining_perms:
                    self.stdout.write(f"  - {codename}: {name}")
        except Exception as exc:
            logger.exception("Permission cleanup failed: %s", exc)
            raise
