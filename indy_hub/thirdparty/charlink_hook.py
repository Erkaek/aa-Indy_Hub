"""CharLink integration for Indy Hub.

This module is imported by aa-charlink when that app is installed and the
``charlink`` hook is discovered through ``auth_hooks.py``.
"""

from __future__ import annotations

# Third Party
from charlink.app_imports.utils import AppImport, LoginImport

# Django
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token

from ..services.industry_skills import SKILLS_SCOPE
from ..tasks.industry import (
    BLUEPRINT_SCOPE,
    CORP_BLUEPRINT_SCOPE_SET,
    CORP_JOBS_SCOPE_SET,
    JOBS_SCOPE,
    MATERIAL_EXCHANGE_SCOPE_SET,
    ONLINE_SCOPE,
    STRUCTURE_SCOPE,
)

ASSETS_SCOPE = "esi-assets.read_assets.v1"

User = get_user_model()

# Keep this aligned with `views.user.ASSETS_SCOPE_SET` and the personal
# `authorize_*` flows: the in-app ESI page requests ONLINE_SCOPE alongside
# ASSETS_SCOPE, so charlink must request the same set or users will be
# prompted to re-authorize manually for `esi-location.read_online.v1`.
PERSONAL_SCOPE_SET = sorted(
    {
        BLUEPRINT_SCOPE,
        JOBS_SCOPE,
        ASSETS_SCOPE,
        STRUCTURE_SCOPE,
        SKILLS_SCOPE,
        ONLINE_SCOPE,
    }
)
CORPORATION_SCOPE_SET = sorted({*CORP_BLUEPRINT_SCOPE_SET, *CORP_JOBS_SCOPE_SET})
MATERIAL_HUB_SCOPE_SET = sorted(set(MATERIAL_EXCHANGE_SCOPE_SET))


def _token_management_cache_key(user_id: int) -> str:
    return f"indy_hub:token_management_live:{int(user_id)}"


def _character_name(character_id: int) -> str:
    character = EveCharacter.objects.filter(character_id=int(character_id)).first()
    if character and getattr(character, "character_name", None):
        return str(character.character_name)
    return str(character_id)


def _character_scope_coverage_queryset(*, scope_names: list[str], character_id_ref):
    return (
        Token.objects.filter(character_id=character_id_ref)
        .filter(scopes__name__in=scope_names)
        .values("character_id")
        .annotate(scope_count=Count("scopes__name", distinct=True))
        .filter(scope_count=len(scope_names))
    )


def _has_scope_coverage(character: EveCharacter, scope_names: list[str]) -> bool:
    owner_user_id = (
        CharacterOwnership.objects.filter(character=character)
        .values_list("user_id", flat=True)
        .first()
    )
    if not owner_user_id:
        return False

    return (
        Token.objects.filter(
            character_id=int(character.character_id),
            user_id=int(owner_user_id),
        )
        .require_valid()
        .filter(scopes__name__in=scope_names)
        .values("character_id")
        .annotate(scope_count=Count("scopes__name", distinct=True))
        .filter(scope_count=len(scope_names))
        .exists()
    )


def _users_with_permission(codename: str) -> QuerySet[User]:
    return User.objects.filter(
        Q(is_superuser=True)
        | Q(
            user_permissions__content_type__app_label="indy_hub",
            user_permissions__codename=codename,
        )
        | Q(
            groups__permissions__content_type__app_label="indy_hub",
            groups__permissions__codename=codename,
        )
    ).distinct()


def _clear_user_live_cache(user_id: int) -> None:
    # Django
    from django.core.cache import cache

    cache.delete(_token_management_cache_key(int(user_id)))


def _post_link_message(request, token: Token, message_template) -> None:
    _clear_user_live_cache(int(request.user.id))
    messages.success(
        request,
        message_template % {"character": _character_name(int(token.character_id))},
    )


def _add_personal_character(request, token: Token) -> None:
    _post_link_message(
        request,
        token,
        _(
            "Indy Hub personal scopes linked for %(character)s. Blueprints, jobs, assets, and skills will refresh automatically."
        ),
    )


def _add_corporation_character(request, token: Token) -> None:
    _post_link_message(
        request,
        token,
        _(
            "Indy Hub corporation scopes linked for %(character)s. Corporation blueprint and job access will be available after synchronization."
        ),
    )


def _add_material_exchange_character(request, token: Token) -> None:
    _post_link_message(
        request,
        token,
        _(
            "Indy Hub Material Exchange scopes linked for %(character)s. Structure, asset, and contract access will refresh automatically."
        ),
    )


app_import = AppImport(
    "indy_hub",
    [
        LoginImport(
            app_label="indy_hub",
            unique_id="personal",
            field_label=_("Indy Hub"),
            add_character=_add_personal_character,
            scopes=PERSONAL_SCOPE_SET,
            check_permissions=lambda user: user.has_perm(
                "indy_hub.can_access_indy_hub"
            ),
            is_character_added=lambda character: _has_scope_coverage(
                character, PERSONAL_SCOPE_SET
            ),
            is_character_added_annotation=Exists(
                _character_scope_coverage_queryset(
                    scope_names=PERSONAL_SCOPE_SET,
                    character_id_ref=OuterRef("pk"),
                )
            ),
            get_users_with_perms=lambda: _users_with_permission("can_access_indy_hub"),
        ),
        LoginImport(
            app_label="indy_hub",
            unique_id="corporation",
            field_label=_("Indy Hub Corporation Admin"),
            add_character=_add_corporation_character,
            scopes=CORPORATION_SCOPE_SET,
            check_permissions=lambda user: user.has_perm(
                "indy_hub.can_manage_corp_bp_requests"
            )
            and user.has_perm("indy_hub.can_access_indy_hub"),
            is_character_added=lambda character: _has_scope_coverage(
                character, CORPORATION_SCOPE_SET
            ),
            is_character_added_annotation=Exists(
                _character_scope_coverage_queryset(
                    scope_names=CORPORATION_SCOPE_SET,
                    character_id_ref=OuterRef("pk"),
                )
            ),
            get_users_with_perms=lambda: _users_with_permission(
                "can_manage_corp_bp_requests"
            ),
            default_initial_selection=False,
        ),
        LoginImport(
            app_label="indy_hub",
            unique_id="materialhub",
            field_label=_("Indy Hub Material Exchange"),
            add_character=_add_material_exchange_character,
            scopes=MATERIAL_HUB_SCOPE_SET,
            check_permissions=lambda user: user.has_perm(
                "indy_hub.can_manage_material_hub"
            )
            and user.has_perm("indy_hub.can_access_indy_hub"),
            is_character_added=lambda character: _has_scope_coverage(
                character, MATERIAL_HUB_SCOPE_SET
            ),
            is_character_added_annotation=Exists(
                _character_scope_coverage_queryset(
                    scope_names=MATERIAL_HUB_SCOPE_SET,
                    character_id_ref=OuterRef("pk"),
                )
            ),
            get_users_with_perms=lambda: _users_with_permission(
                "can_manage_material_hub"
            ),
            default_initial_selection=False,
        ),
    ],
)
