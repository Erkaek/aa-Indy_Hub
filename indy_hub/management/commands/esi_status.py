# indy_hub/management/commands/esi_status.py

# Django
from django.core.cache import cache
from django.core.management.base import BaseCommand

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# AA Example App
from indy_hub.utils import clear_esi_cache, get_esi_status

logger = get_extension_logger(__name__)


class Command(BaseCommand):
    help = "Monitor ESI status and manage cache"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear-cache",
            action="store_true",
            help="Clear ESI cache data",
        )
        parser.add_argument(
            "--reset-circuit-breakers",
            action="store_true",
            help="Reset all circuit breakers",
        )

    def handle(self, *args, **options):
        if options["clear_cache"]:
            clear_esi_cache()
            self.stdout.write(self.style.SUCCESS("ESI cache cleared successfully"))
            logger.info("ESI cache cleared via esi_status command.")
            return

        if options["reset_circuit_breakers"]:
            # Clear circuit breaker states
            cache.delete_pattern("esi_circuit_breaker_*")
            cache.delete_pattern("esi_error_count_*")
            cache.delete_pattern("esi_backoff_*")
            self.stdout.write(self.style.SUCCESS("Circuit breakers reset successfully"))
            logger.info("ESI circuit breakers reset via esi_status command.")
            return

        # Show current status
        try:
            status = get_esi_status()
        except Exception as exc:
            logger.exception("Failed to retrieve ESI status: %s", exc)
            raise
        logger.info(
            "ESI status: type_cb=%s char_cb=%s type_backoff=%s char_backoff=%s type_errors=%s char_errors=%s",
            status.get("type_circuit_breaker"),
            status.get("character_circuit_breaker"),
            status.get("type_backoff"),
            status.get("character_backoff"),
            status.get("type_errors"),
            status.get("character_errors"),
        )

        self.stdout.write("=== ESI Protection Status ===")

        # Circuit breakers
        type_cb = "OPEN" if not status["type_circuit_breaker"] else "CLOSED"
        char_cb = "OPEN" if not status["character_circuit_breaker"] else "CLOSED"

        self.stdout.write(f"Type Circuit Breaker: {type_cb}")
        self.stdout.write(f"Character Circuit Breaker: {char_cb}")

        # Backoff status
        type_backoff = "ACTIVE" if status["type_backoff"] else "INACTIVE"
        char_backoff = "ACTIVE" if status["character_backoff"] else "INACTIVE"

        self.stdout.write(f"Type Backoff: {type_backoff}")
        self.stdout.write(f"Character Backoff: {char_backoff}")

        # Error counts
        self.stdout.write(f"Type Errors: {status['type_errors']}")
        self.stdout.write(f"Character Errors: {status['character_errors']}")

        # Recommendations
        if (
            not status["type_circuit_breaker"]
            or not status["character_circuit_breaker"]
        ):
            self.stdout.write(
                self.style.WARNING(
                    "\nWARNING: Circuit breakers are open! ESI calls are being blocked."
                )
            )
            self.stdout.write(
                "Use --reset-circuit-breakers to reset them if the issue is resolved."
            )

        if status["type_backoff"] or status["character_backoff"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nINFO: Backoff is active. ESI calls are being delayed."
                )
            )
