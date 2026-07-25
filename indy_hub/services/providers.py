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

ESI_REQUIRED_OPERATIONS = [
    "GetCharactersCharacterIdAssets",
    "GetCharactersCharacterIdBlueprints",
    "GetCharactersCharacterIdContracts",
    "GetCharactersCharacterIdContractsContractIdItems",
    "GetCharactersCharacterIdIndustryJobs",
    "GetCharactersCharacterIdRoles",
    "GetCharactersCharacterIdSkills",
    "GetCorporationsCorporationIdAssets",
    "GetCorporationsCorporationIdBlueprints",
    "GetCorporationsCorporationIdContracts",
    "GetCorporationsCorporationIdContractsContractIdItems",
    "GetCorporationsCorporationIdIndustryJobs",
    "GetCorporationsCorporationIdStructures",
    "GetIndustrySystems",
    "GetUniverseStationsStationId",
    "GetUniverseStructuresStructureId",
    "PostUniverseNames",
]

esi_provider = ESIClientProvider(
    ua_appname=__app_name_ua__ or __title__,
    ua_version=__version__,
    ua_url=__url__,
    compatibility_date=ESI_COMPATIBILITY_DATE,
    operations=ESI_REQUIRED_OPERATIONS,
)
