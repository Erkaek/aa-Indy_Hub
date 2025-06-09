from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from ...models import Blueprint, IndustryJob, batch_cache_type_names, batch_cache_character_names
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Pre-cache ESI data to improve Indy Hub performance'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            type=str,
            help='Specific user to cache data for (username)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Cache data for all users',
        )
        parser.add_argument(
            '--types-only',
            action='store_true',
            help='Only cache type names (not character names)',
        )
        parser.add_argument(
            '--characters-only',
            action='store_true',
            help='Only cache character names (not type names)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting ESI cache pre-loading...'))
        
        # Determine which users to process
        if options['user']:
            try:
                users = [User.objects.get(username=options['user'])]
                self.stdout.write(f"Processing user: {options['user']}")
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"User '{options['user']}' not found")
                )
                return
        elif options['all']:
            # Get all users who have blueprints or jobs
            user_ids = set()
            user_ids.update(Blueprint.objects.values_list('owner_user_id', flat=True).distinct())
            user_ids.update(IndustryJob.objects.values_list('owner_user_id', flat=True).distinct())
            users = User.objects.filter(id__in=user_ids)
            self.stdout.write(f"Processing {users.count()} users with data")
        else:
            self.stdout.write(
                self.style.ERROR('Please specify --user <username> or --all')
            )
            return

        total_type_ids = set()
        total_character_ids = set()

        # Collect all unique IDs
        for user in users:
            self.stdout.write(f"Collecting data for user: {user.username}")
            
            # Get blueprint data
            if not options['characters_only']:
                blueprints = Blueprint.objects.filter(owner_user=user)
                blueprint_type_ids = [bp.type_id for bp in blueprints if bp.type_id]
                total_type_ids.update(blueprint_type_ids)
                self.stdout.write(f"  Found {len(blueprint_type_ids)} blueprint type IDs")

            if not options['types_only']:
                blueprints = Blueprint.objects.filter(owner_user=user)
                blueprint_character_ids = [bp.character_id for bp in blueprints if bp.character_id]
                total_character_ids.update(blueprint_character_ids)

            # Get industry job data
            jobs = IndustryJob.objects.filter(owner_user=user)
            
            if not options['characters_only']:
                job_blueprint_type_ids = [job.blueprint_type_id for job in jobs if job.blueprint_type_id]
                job_product_type_ids = [job.product_type_id for job in jobs if job.product_type_id]
                total_type_ids.update(job_blueprint_type_ids)
                total_type_ids.update(job_product_type_ids)
                self.stdout.write(f"  Found {len(job_blueprint_type_ids)} job blueprint type IDs")
                self.stdout.write(f"  Found {len(job_product_type_ids)} job product type IDs")

            if not options['types_only']:
                job_character_ids = [job.character_id for job in jobs if job.character_id]
                total_character_ids.update(job_character_ids)

        # Cache the data
        if total_type_ids and not options['characters_only']:
            self.stdout.write(
                self.style.SUCCESS(f'Caching {len(total_type_ids)} unique type names...')
            )
            batch_cache_type_names(list(total_type_ids))

        if total_character_ids and not options['types_only']:
            self.stdout.write(
                self.style.SUCCESS(f'Caching {len(total_character_ids)} unique character names...')
            )
            batch_cache_character_names(list(total_character_ids))

        self.stdout.write(
            self.style.SUCCESS('ESI cache pre-loading completed successfully!')
        )
        self.stdout.write(
            self.style.SUCCESS(f'Cached {len(total_type_ids)} type names and {len(total_character_ids)} character names')
        )
