"""Shared service providers for indy_hub."""

# Alliance Auth / django-esi
try:
    # Django
    from django.conf import settings
except Exception:  # pragma: no cover - settings might be unavailable in tests
    settings = None

try:
    # Alliance Auth
    from esi.openapi_clients import ESIClientProvider

    _SUPPORTS_COMPATIBILITY_DATE = True
except ImportError:  # pragma: no cover - older django-esi
    # Alliance Auth
    from esi.clients import EsiClientProvider as ESIClientProvider

    _SUPPORTS_COMPATIBILITY_DATE = False

# AA Example App
# Local
from indy_hub import (
    __app_name_ua__,
    __esi_compatibility_date__,
    __title__,
    __url__,
    __version__,
)

DEFAULT_COMPATIBILITY_DATE = __esi_compatibility_date__
DEFAULT_ESI_OPERATIONS = [
    "get_characters_character_id_blueprints",
    "get_characters_character_id_industry_jobs",
    "get_corporations_corporation_id_blueprints",
    "get_corporations_corporation_id_industry_jobs",
    "get_characters_character_id_roles",
    "get_universe_structures_structure_id",
    "get_universe_stations_station_id",
    "post_universe_names",
    "get_corporations_corporation_id_contracts",
    "get_corporations_corporation_id_contracts_contract_id_items",
    "get_characters_character_id_contracts",
    "get_characters_character_id_contracts_contract_id_items",
    "get_corporations_corporation_id_assets",
    "get_characters_character_id_assets",
    "get_corporations_corporation_id_structures",
    "get_corporations_corporation_id_divisions",
    "get_characters_character_id",
    "get_corporations_corporation_id",
]
DEFAULT_ESI_TAGS = [
    "Assets",
    "Character",
    "Contracts",
    "Corporation",
    "Industry",
    "Universe",
]

if settings is not None:
    _compat_date = getattr(
        settings,
        "INDY_HUB_ESI_COMPATIBILITY_DATE",
        DEFAULT_COMPATIBILITY_DATE,
    )
else:  # pragma: no cover - running without Django settings
    _compat_date = DEFAULT_COMPATIBILITY_DATE

_provider_kwargs = {
    "ua_appname": __app_name_ua__ or __title__,
    "ua_version": __version__,
    "ua_url": __url__,
}
if _SUPPORTS_COMPATIBILITY_DATE:
    _provider_kwargs["compatibility_date"] = _compat_date
    _provider_kwargs["operations"] = DEFAULT_ESI_OPERATIONS
    _provider_kwargs["tags"] = DEFAULT_ESI_TAGS

esi_provider = ESIClientProvider(**_provider_kwargs)
