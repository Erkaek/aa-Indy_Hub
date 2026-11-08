"""Explicit authorization requests, independent of Django, tasks and CharLink.

Capabilities describe business scope requirements. Profiles describe consent
requests; their coverage groups describe when a native Indy Hub button may skip
consent. CharLink instead checks a single, owner-bound recorded grant, passively.
django-esi remains responsible for SSO, token validity and callbacks.
"""

# Standard Library
from dataclasses import dataclass
from types import MappingProxyType

ACCESS_PERMISSION = "indy_hub.can_access_indy_hub"
CORPORATION_PERMISSION = "indy_hub.can_manage_corp_bp_requests"
MATERIAL_PERMISSION = "indy_hub.can_manage_material_hub"

BLUEPRINT_SCOPE = "esi-characters.read_blueprints.v1"
JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
ASSETS_SCOPE = "esi-assets.read_assets.v1"
STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
SKILLS_SCOPE = "esi-skills.read_skills.v1"
CORP_BLUEPRINT_SCOPE = "esi-corporations.read_blueprints.v1"
CORP_JOBS_SCOPE = "esi-industry.read_corporation_jobs.v1"
CORP_ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"
CORP_STRUCTURES_SCOPE = "esi-corporations.read_structures.v1"
CORP_ASSETS_SCOPE = "esi-assets.read_corporation_assets.v1"
CORP_DIVISIONS_SCOPE = "esi-corporations.read_divisions.v1"
CORP_CONTRACTS_SCOPE = "esi-contracts.read_corporation_contracts.v1"

BLUEPRINT_SCOPES = frozenset({BLUEPRINT_SCOPE, STRUCTURE_SCOPE})
JOBS_SCOPES = frozenset({JOBS_SCOPE, STRUCTURE_SCOPE})
ASSETS_SCOPES = frozenset({ASSETS_SCOPE})
SKILLS_SCOPES = frozenset({SKILLS_SCOPE})
CORPORATION_BLUEPRINT_SCOPES = frozenset(
    {
        CORP_BLUEPRINT_SCOPE,
        STRUCTURE_SCOPE,
        CORP_ROLES_SCOPE,
        CORP_STRUCTURES_SCOPE,
        CORP_ASSETS_SCOPE,
    }
)
CORPORATION_JOBS_SCOPES = frozenset(
    {
        CORP_JOBS_SCOPE,
        STRUCTURE_SCOPE,
        CORP_ROLES_SCOPE,
        CORP_STRUCTURES_SCOPE,
        CORP_ASSETS_SCOPE,
    }
)
MATERIAL_EXCHANGE_SCOPES = frozenset(
    {
        STRUCTURE_SCOPE,
        CORP_ROLES_SCOPE,
        CORP_STRUCTURES_SCOPE,
        CORP_ASSETS_SCOPE,
        CORP_DIVISIONS_SCOPE,
        CORP_CONTRACTS_SCOPE,
    }
)
PERSONAL_SCOPES = BLUEPRINT_SCOPES | JOBS_SCOPES | ASSETS_SCOPES | SKILLS_SCOPES


@dataclass(frozen=True)
class AuthorizationProfile:
    scopes: frozenset[str]
    permissions: tuple[str, ...]
    return_to: str = "indy_hub:token_management"
    # Every owned character must have each group on one valid token. Different
    # groups may be covered by different tokens; this is not a full-grant test.
    coverage_groups: tuple[frozenset[str], ...] = ()
    allow_force: bool = False
    default_initial_selection: bool = False

    def scope_list(self) -> list[str]:
        """Fresh list for APIs such as LoginImport; never share mutable scopes."""
        return sorted(self.scopes)


AUTHORIZATION_PROFILES = MappingProxyType(
    {
        "authorize_assets": AuthorizationProfile(
            ASSETS_SCOPES, (ACCESS_PERMISSION,), coverage_groups=(ASSETS_SCOPES,)
        ),
        "authorize_blueprints": AuthorizationProfile(
            BLUEPRINT_SCOPES, (ACCESS_PERMISSION,), coverage_groups=(BLUEPRINT_SCOPES,)
        ),
        "authorize_jobs": AuthorizationProfile(
            JOBS_SCOPES, (ACCESS_PERMISSION,), coverage_groups=(JOBS_SCOPES,)
        ),
        # Roles are requested here, but historically are not a skip criterion.
        "authorize_all": AuthorizationProfile(
            PERSONAL_SCOPES | {CORP_ROLES_SCOPE},
            (ACCESS_PERMISSION,),
            coverage_groups=(
                BLUEPRINT_SCOPES,
                JOBS_SCOPES,
                ASSETS_SCOPES,
                SKILLS_SCOPES,
            ),
            allow_force=True,
        ),
        "authorize_corp_blueprints": AuthorizationProfile(
            CORPORATION_BLUEPRINT_SCOPES, (ACCESS_PERMISSION, CORPORATION_PERMISSION)
        ),
        "authorize_corp_jobs": AuthorizationProfile(
            CORPORATION_JOBS_SCOPES, (ACCESS_PERMISSION, CORPORATION_PERMISSION)
        ),
        "authorize_corp_all": AuthorizationProfile(
            CORPORATION_BLUEPRINT_SCOPES
            | CORPORATION_JOBS_SCOPES
            | MATERIAL_EXCHANGE_SCOPES,
            (ACCESS_PERMISSION, CORPORATION_PERMISSION),
        ),
        "authorize_material_exchange": AuthorizationProfile(
            MATERIAL_EXCHANGE_SCOPES, (ACCESS_PERMISSION, MATERIAL_PERMISSION)
        ),
        # Configuration has historically required the management permission
        # alone. Its four-scope request is intentionally narrower than the hub.
        "material_exchange_request_assets_token": AuthorizationProfile(
            frozenset({CORP_ASSETS_SCOPE}),
            (MATERIAL_PERMISSION,),
            return_to="indy_hub:material_exchange_config",
        ),
        "material_exchange_request_divisions_token": AuthorizationProfile(
            frozenset({CORP_DIVISIONS_SCOPE}),
            (MATERIAL_PERMISSION,),
            return_to="indy_hub:material_exchange_config",
        ),
        "material_exchange_request_contracts_scope": AuthorizationProfile(
            frozenset({CORP_CONTRACTS_SCOPE}),
            (MATERIAL_PERMISSION,),
            return_to="indy_hub:material_exchange_config",
        ),
        "material_exchange_request_all_scopes": AuthorizationProfile(
            frozenset(
                {
                    CORP_ASSETS_SCOPE,
                    CORP_DIVISIONS_SCOPE,
                    CORP_CONTRACTS_SCOPE,
                    STRUCTURE_SCOPE,
                }
            ),
            (MATERIAL_PERMISSION,),
            return_to="indy_hub:material_exchange_config",
        ),
        "charlink_personal": AuthorizationProfile(
            PERSONAL_SCOPES,
            (ACCESS_PERMISSION,),
            return_to="charlink:index",
            default_initial_selection=True,
        ),
        "charlink_corporation": AuthorizationProfile(
            CORPORATION_BLUEPRINT_SCOPES | CORPORATION_JOBS_SCOPES,
            (ACCESS_PERMISSION, CORPORATION_PERMISSION),
            return_to="charlink:index",
        ),
        "charlink_materialhub": AuthorizationProfile(
            MATERIAL_EXCHANGE_SCOPES,
            (ACCESS_PERMISSION, MATERIAL_PERMISSION),
            return_to="charlink:index",
        ),
    }
)
