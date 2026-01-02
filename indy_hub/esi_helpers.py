# Standard Library
import logging

from .services.esi_client import ESIClientError, ESITokenError, shared_client

logger = logging.getLogger(__name__)

ESI_BASE_URL = "https://esi.evetech.net/latest"


def fetch_character_blueprints(character_id):
    """Legacy wrapper that delegates to the shared ESI client."""
    try:
        return shared_client.fetch_character_blueprints(character_id)
    except (ESITokenError, ESIClientError) as exc:
        logger.error("Blueprint fetch failed for %s: %s", character_id, exc)
        raise


def fetch_character_industry_jobs(character_id):
    """Legacy wrapper that delegates to the shared ESI client."""
    try:
        return shared_client.fetch_character_industry_jobs(character_id)
    except (ESITokenError, ESIClientError) as exc:
        logger.error("Industry job fetch failed for %s: %s", character_id, exc)
        raise


def fetch_character_assets(character_id):
    """Legacy helper no longer implemented."""
    raise NotImplementedError(
        "fetch_character_assets is obsolete. Use indy_hub.services.esi_client to implement this functionality."
    )
