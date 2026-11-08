"""Minimal local OpenAPI contracts for the django-esi 10.1 facade tests.

Paths, tags, body/path parameters and pagination match the installed public
stubs. Schemas deliberately include only fields consumed by these regressions.
No remote specification or authorization server is used.
"""

# Standard Library
import copy

BLUEPRINT_SCOPE = "esi-characters.read_blueprints.v1"
SKILLS_SCOPE = "esi-skills.read_skills.v1"

# operationId, tag, path, scope, paginated, response schema
OPERATIONS = (
    (
        "GetCharactersCharacterIdBlueprints",
        "Character",
        "/characters/{character_id}/blueprints",
        BLUEPRINT_SCOPE,
        True,
        "Blueprints",
    ),
    (
        "GetCorporationsCorporationIdBlueprints",
        "Corporation",
        "/corporations/{corporation_id}/blueprints",
        "esi-corporations.read_blueprints.v1",
        True,
        "Blueprints",
    ),
    (
        "GetCharactersCharacterIdIndustryJobs",
        "Industry",
        "/characters/{character_id}/industry/jobs",
        "esi-industry.read_character_jobs.v1",
        False,
        "Jobs",
    ),
    (
        "GetCorporationsCorporationIdIndustryJobs",
        "Industry",
        "/corporations/{corporation_id}/industry/jobs",
        "esi-industry.read_corporation_jobs.v1",
        True,
        "Jobs",
    ),
    (
        "GetCharactersCharacterIdSkills",
        "Skills",
        "/characters/{character_id}/skills",
        SKILLS_SCOPE,
        False,
        "Skills",
    ),
    (
        "GetCharactersCharacterIdRoles",
        "Character",
        "/characters/{character_id}/roles",
        "esi-characters.read_corporation_roles.v1",
        False,
        "Roles",
    ),
    (
        "GetCharactersCharacterIdAssets",
        "Assets",
        "/characters/{character_id}/assets",
        "esi-assets.read_assets.v1",
        True,
        "Assets",
    ),
    (
        "GetCorporationsCorporationIdAssets",
        "Assets",
        "/corporations/{corporation_id}/assets",
        "esi-assets.read_corporation_assets.v1",
        True,
        "Assets",
    ),
    (
        "GetCorporationsCorporationIdDivisions",
        "Corporation",
        "/corporations/{corporation_id}/divisions",
        "esi-corporations.read_divisions.v1",
        False,
        "Divisions",
    ),
    (
        "GetCharactersCharacterIdContracts",
        "Contracts",
        "/characters/{character_id}/contracts",
        "esi-contracts.read_character_contracts.v1",
        True,
        "Contracts",
    ),
    (
        "GetCharactersCharacterIdContractsContractIdItems",
        "Contracts",
        "/characters/{character_id}/contracts/{contract_id}/items",
        "esi-contracts.read_character_contracts.v1",
        False,
        "ContractItems",
    ),
    (
        "GetCorporationsCorporationIdContracts",
        "Contracts",
        "/corporations/{corporation_id}/contracts",
        "esi-contracts.read_corporation_contracts.v1",
        True,
        "Contracts",
    ),
    (
        "GetCorporationsCorporationIdContractsContractIdItems",
        "Contracts",
        "/corporations/{corporation_id}/contracts/{contract_id}/items",
        "esi-contracts.read_corporation_contracts.v1",
        False,
        "ContractItems",
    ),
    (
        "GetCorporationsCorporationIdStructures",
        "Corporation",
        "/corporations/{corporation_id}/structures",
        "esi-corporations.read_structures.v1",
        True,
        "Structures",
    ),
    (
        "GetUniverseStructuresStructureId",
        "Universe",
        "/universe/structures/{structure_id}",
        "esi-universe.read_structures.v1",
        False,
        "Name",
    ),
    (
        "GetUniverseStationsStationId",
        "Universe",
        "/universe/stations/{station_id}",
        None,
        False,
        "Name",
    ),
    ("PostUniverseNames", "Universe", "/universe/names", None, False, "Names"),
    ("GetIndustrySystems", "Industry", "/industry/systems", None, False, "Systems"),
    ("GetMarketsPrices", "Market", "/markets/prices", None, False, "Prices"),
)


def _object(**properties):
    return {"type": "object", "properties": properties, "additionalProperties": True}


def _array(item):
    return {"type": "array", "items": item}


def openapi_spec(*, compatibility_date="2026-07-21"):
    integer = {"type": "integer", "format": "int64"}
    string = {"type": "string"}
    boolean = {"type": "boolean"}
    schemas = {
        "Error": _object(error=string),
        "Blueprints": _array(
            _object(
                item_id=integer,
                type_id=integer,
                location_id=integer,
                location_flag=string,
                quantity=integer,
                material_efficiency=integer,
                time_efficiency=integer,
                runs=integer,
            )
        ),
        "Jobs": _array(
            _object(
                job_id=integer,
                installer_id=integer,
                station_id=integer,
                activity_id=integer,
                blueprint_id=integer,
                blueprint_type_id=integer,
                product_type_id=integer,
                runs=integer,
                status=string,
                duration=integer,
                start_date={"type": "string", "format": "date-time"},
                end_date={"type": "string", "format": "date-time"},
            )
        ),
        "Skills": _object(
            skills=_array(
                _object(
                    skill_id=integer,
                    active_skill_level=integer,
                    trained_skill_level=integer,
                    skillpoints_in_skill=integer,
                )
            ),
            total_sp=integer,
        ),
        "Roles": _object(roles=_array(string)),
        "Assets": _array(
            _object(
                item_id=integer,
                type_id=integer,
                location_id=integer,
                quantity=integer,
                is_singleton=boolean,
                location_flag=string,
                location_type=string,
            )
        ),
        "Divisions": _object(
            hangar=_array(_object(division=integer, name=string)),
            wallet=_array(_object(division=integer, name=string)),
        ),
        "Contracts": _array(_object(contract_id=integer, status=string)),
        "ContractItems": _array(
            _object(
                record_id=integer,
                type_id=integer,
                quantity=integer,
                is_included=boolean,
            )
        ),
        "Structures": _array(
            _object(structure_id=integer, name=string, type_id=integer)
        ),
        "Name": _object(name=string),
        "Names": _array(_object(id=integer, name=string, category=string)),
        "Systems": _array(
            _object(
                solar_system_id=integer,
                cost_indices=_array(
                    _object(activity=string, cost_index={"type": "number"})
                ),
            )
        ),
        "Prices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type_id": integer,
                    "adjusted_price": {"type": "number", "nullable": True},
                    "average_price": {"type": "number", "nullable": True},
                },
                "required": ["type_id"],
            },
        },
    }
    paths = {}
    for operation, tag, path, scope, paginated, schema in OPERATIONS:
        params = [
            {"name": segment[1:-1], "in": "path", "required": True, "schema": integer}
            for segment in path.split("/")
            if segment.startswith("{")
        ]
        params.append({"name": "If-None-Match", "in": "header", "schema": string})
        params.append(
            {
                "name": "X-Compatibility-Date",
                "in": "header",
                "schema": {"type": "string", "default": compatibility_date},
            }
        )
        if paginated:
            params.append(
                {
                    "name": "page",
                    "in": "query",
                    "schema": {"type": "integer", "default": 1},
                }
            )
        method = "post" if operation.startswith("Post") else "get"
        definition = {
            "operationId": operation,
            "tags": [tag],
            "parameters": params,
            "x-offline-contract": True,  # Native operations expect an extensions mapping.
            "responses": {
                "200": {
                    "description": "Local response",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/" + schema}
                        }
                    },
                },
                **{
                    str(status): {
                        "description": "Simulated error",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    }
                    for status in [304, 401, 403, 404, 420, 429, 500, 503]
                },
            },
        }
        if scope:
            definition["security"] = [{"OAuth2": [scope]}]
        if method == "post":
            definition["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _array(integer)}},
            }
        paths[path] = {method: definition}
    paths["/unrelated"] = {
        "get": {
            "tags": ["Status"],
            "operationId": "GetStatus",
            "responses": {"200": {"description": "Excluded"}},
        }
    }
    return copy.deepcopy(
        {
            "openapi": "3.0.3",
            "info": {"title": "Indy Hub offline ESI contract", "version": "1"},
            "servers": [{"url": "https://esi.invalid"}],
            "paths": paths,
            "components": {
                "schemas": schemas,
                "securitySchemes": {
                    "OAuth2": {
                        "type": "oauth2",
                        "flows": {
                            "authorizationCode": {
                                "authorizationUrl": "https://sso.invalid/authorize",
                                "tokenUrl": "https://sso.invalid/token",
                                "scopes": {
                                    scope: scope
                                    for _, _, _, scope, _, _ in OPERATIONS
                                    if scope
                                },
                            }
                        },
                    }
                },
            },
        }
    )
