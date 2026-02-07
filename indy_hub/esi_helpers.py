# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

from .services.esi_client import ESIClientError, ESITokenError, shared_client

logger = get_extension_logger(__name__)


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
    """Legacy wrapper that delegates to the shared ESI client."""
    try:
        return shared_client.fetch_character_assets(character_id=character_id)
    except (ESITokenError, ESIClientError) as exc:
        logger.error("Asset fetch failed for %s: %s", character_id, exc)
        raise
