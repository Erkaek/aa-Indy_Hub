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
from django.db.models import Count, Exists, OuterRef, Q, QuerySet, Subquery
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token

from .. import authorization
from ..utils.token_status import invalidate_token_status_cache

ASSETS_SCOPE = authorization.ASSETS_SCOPE

User = get_user_model()

_PERSONAL_PROFILE = authorization.AUTHORIZATION_PROFILES["charlink_personal"]
_CORPORATION_PROFILE = authorization.AUTHORIZATION_PROFILES["charlink_corporation"]
_MATERIAL_PROFILE = authorization.AUTHORIZATION_PROFILES["charlink_materialhub"]
# Compatibility copies; LoginImport receives its own mutable list below.
PERSONAL_SCOPE_SET = _PERSONAL_PROFILE.scope_list()
CORPORATION_SCOPE_SET = _CORPORATION_PROFILE.scope_list()
MATERIAL_HUB_SCOPE_SET = _MATERIAL_PROFILE.scope_list()


def _character_name(character_id: int) -> str:
    character = EveCharacter.objects.filter(character_id=int(character_id)).first()
    if character and getattr(character, "character_name", None):
        return str(character.character_name)
    return str(character_id)


def _character_scope_coverage_queryset(*, scope_names: list[str], character_id_ref):
    # Group by token pk (not character_id) so EXISTS + GROUP BY don't collide on
    # the correlated column, which causes Django to silently return no rows.
    # require_valid() is intentionally omitted: the listing page shows whether a
    # character has been linked (has the scopes), not whether the token is
    # currently active. django-esi auto-refreshes via refresh_token on next use.
    # The nested correlation uses the Token character_id to find its AA owner,
    # matching the Python predicate even for historical foreign-user grants.
    owner = CharacterOwnership.objects.filter(
        character__character_id=OuterRef("character_id")
    ).values("user_id")[:1]
    return (
        Token.objects.filter(character_id=character_id_ref, user_id=Subquery(owner))
        .filter(scopes__name__in=scope_names)
        .values("pk")
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
        .filter(scopes__name__in=scope_names)
        .values("pk")
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
    invalidate_token_status_cache(user_id)


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
            "Indy Hub personal scopes linked for %(character)s. Eligible synchronization tasks will use this authorization."
        ),
    )


def _add_corporation_character(request, token: Token) -> None:
    _post_link_message(
        request,
        token,
        _(
            "Indy Hub corporation scopes linked for %(character)s. Operations still require corporation permissions, authorized character selection, and current roles."
        ),
    )


def _add_material_exchange_character(request, token: Token) -> None:
    _post_link_message(
        request,
        token,
        _(
            "Indy Hub Mat Exchange scopes linked for %(character)s. Operations still require the applicable permissions, scopes, and corporation roles."
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
            scopes=_PERSONAL_PROFILE.scope_list(),
            check_permissions=lambda user: user.has_perms(
                _PERSONAL_PROFILE.permissions
            ),
            is_character_added=lambda character: _has_scope_coverage(
                character, _PERSONAL_PROFILE.scope_list()
            ),
            is_character_added_annotation=Exists(
                _character_scope_coverage_queryset(
                    scope_names=_PERSONAL_PROFILE.scope_list(),
                    character_id_ref=OuterRef("character_id"),
                )
            ),
            get_users_with_perms=lambda: _users_with_permission(
                _PERSONAL_PROFILE.permissions[-1].split(".", 1)[1]
            ),
            default_initial_selection=_PERSONAL_PROFILE.default_initial_selection,
        ),
        LoginImport(
            app_label="indy_hub",
            unique_id="corporation",
            field_label=_("Indy Hub Corp Admin"),
            add_character=_add_corporation_character,
            scopes=_CORPORATION_PROFILE.scope_list(),
            check_permissions=lambda user: user.has_perms(
                _CORPORATION_PROFILE.permissions
            ),
            is_character_added=lambda character: _has_scope_coverage(
                character, _CORPORATION_PROFILE.scope_list()
            ),
            is_character_added_annotation=Exists(
                _character_scope_coverage_queryset(
                    scope_names=_CORPORATION_PROFILE.scope_list(),
                    character_id_ref=OuterRef("character_id"),
                )
            ),
            get_users_with_perms=lambda: _users_with_permission(
                _CORPORATION_PROFILE.permissions[-1].split(".", 1)[1]
            ),
            default_initial_selection=_CORPORATION_PROFILE.default_initial_selection,
        ),
        LoginImport(
            app_label="indy_hub",
            unique_id="materialhub",
            field_label=_("Indy Hub Mat Exchange"),
            add_character=_add_material_exchange_character,
            scopes=_MATERIAL_PROFILE.scope_list(),
            check_permissions=lambda user: user.has_perms(
                _MATERIAL_PROFILE.permissions
            ),
            is_character_added=lambda character: _has_scope_coverage(
                character, _MATERIAL_PROFILE.scope_list()
            ),
            is_character_added_annotation=Exists(
                _character_scope_coverage_queryset(
                    scope_names=_MATERIAL_PROFILE.scope_list(),
                    character_id_ref=OuterRef("character_id"),
                )
            ),
            get_users_with_perms=lambda: _users_with_permission(
                _MATERIAL_PROFILE.permissions[-1].split(".", 1)[1]
            ),
            default_initial_selection=_MATERIAL_PROFILE.default_initial_selection,
        ),
    ],
)
