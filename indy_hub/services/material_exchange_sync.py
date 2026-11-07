"""Publish Material Exchange refreshes after configuration changes commit."""

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)


def enqueue_material_exchange_sync() -> None:
    """Queue stock once; its worker implementation refreshes prices afterward."""
    # Local import keeps task registration independent of signal imports.
    # AA Example App
    from indy_hub.tasks.material_exchange import sync_material_exchange_stock

    try:
        sync_material_exchange_stock.apply_async(retry=False)
    except Exception:
        # The configuration is already committed. Never replace a failed
        # broker publication with synchronous ESI/Fuzzwork work in the request.
        logger.exception(
            "Could not queue Material Exchange stock/price refresh; "
            "the saved configuration was retained"
        )
