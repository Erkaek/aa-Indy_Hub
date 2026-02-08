"""Shared service providers for indy_hub."""

# Alliance Auth / django-esi
try:
    # Alliance Auth (OpenAPI)
    # Alliance Auth
    from esi.openapi_clients import ESIClientProvider
except ImportError as exc:  # pragma: no cover - enforce OpenAPI-only
    raise ImportError(
        "indy_hub requires django-esi OpenAPI clients. "
        "Upgrade django-esi to a version that provides esi.openapi_clients."
    ) from exc

# AA Example App
# Local
from indy_hub import __app_name_ua__, __title__, __url__, __version__
from indy_hub.app_settings import ESI_COMPATIBILITY_DATE

_SUPPORTS_COMPATIBILITY_DATE = True

DEFAULT_COMPATIBILITY_DATE = ESI_COMPATIBILITY_DATE
DEFAULT_ESI_OPERATIONS = [
    "GetCharactersCharacterIdBlueprints",
    "GetCharactersCharacterIdIndustryJobs",
    "GetCharactersCharacterIdOnline",
    "GetCorporationsCorporationIdBlueprints",
    "GetCorporationsCorporationIdIndustryJobs",
    "GetCharactersCharacterIdRoles",
    "GetUniverseStructuresStructureId",
    "GetUniverseStationsStationId",
    "PostUniverseNames",
    "GetCorporationsCorporationIdContracts",
    "GetCorporationsCorporationIdContractsContractIdItems",
    "GetCharactersCharacterIdContracts",
    "GetCharactersCharacterIdContractsContractIdItems",
    "GetCorporationsCorporationIdAssets",
    "GetCharactersCharacterIdAssets",
    "GetCorporationsCorporationIdStructures",
    "GetCorporationsCorporationIdDivisions",
    "GetCharactersCharacterId",
    "GetCorporationsCorporationId",
]
DEFAULT_ESI_TAGS = [
    "Assets",
    "Character",
    "Contracts",
    "Corporation",
    "Industry",
    "Universe",
]

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
