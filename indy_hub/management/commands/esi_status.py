"""Inspect current local ESI protections and clear Indy Hub response validators."""

# Django
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.services.esi_client import shared_client
from indy_hub.services.market_prices import MARKET_PRICES_CACHE_KEY
from indy_hub.services.providers import esi_provider
from indy_hub.utils.eve import get_structure_rate_limit_cooldown_seconds

logger = get_extension_logger(__name__)


class Command(BaseCommand):
    help = "Inspect local ESI cooldowns without making ESI calls"

    def add_arguments(self, parser):
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument(
            "--clear-cache",
            action="store_true",
            help="Clear Indy Hub ETags and public market prices; preserve shared responses and protections",
        )
        actions.add_argument(
            "--reset-circuit-breakers",
            action="store_true",
            help="Obsolete: the current client does not use per-type/per-character circuit breakers",
        )

    def handle(self, *args, **options):
        if options["clear_cache"] and options["reset_circuit_breakers"]:
            raise CommandError("Choose only one cache action")
        if options["reset_circuit_breakers"]:
            raise CommandError(
                "--reset-circuit-breakers is obsolete: the current client uses ESI "
                "rate limits and cooldowns, which must be allowed to expire."
            )
        if options["clear_cache"]:
            delete_pattern = getattr(cache, "delete_pattern", None)
            if not callable(delete_pattern):
                raise CommandError(
                    "Clearing Indy Hub ETags requires a cache backend with delete_pattern support"
                )
            # django-esi namespaces ETags by the provider's application name;
            # response bodies use shared esi_<hash> keys and must be preserved.
            pattern = f"{slugify(esi_provider._ua_appname)}_etag_*"
            deleted = delete_pattern(pattern)
            cache.delete(MARKET_PRICES_CACHE_KEY)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cleared {deleted or 0} Indy Hub ETags and its public market price cache. "
                    "Shared ESI responses, cooldowns and task locks were preserved."
                )
            )
            logger.info("Indy Hub ETags and public market price cache cleared.")
            return

        remaining = shared_client._tranquility_cooldown_seconds_remaining()
        structure_delay = get_structure_rate_limit_cooldown_seconds()
        self.stdout.write("=== Indy Hub ESI status (local cache only) ===")
        self.stdout.write(f"Tranquility global cooldown: {remaining}s remaining")
        self.stdout.write(f"Structure lookup recorded retry delay: {structure_delay}s")
        self.stdout.write(
            "Type and character names use local SDE/Alliance Auth data. "
            "Legacy per-type/per-character circuit breakers are not used."
        )
        self.stdout.write(
            "This is not a live ESI health check; endpoint/token rate limits may also apply."
        )
