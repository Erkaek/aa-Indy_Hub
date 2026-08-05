"""Visibility rules for user-level Indy Hub admin features.

This module centralizes access and scope rules so future admin views can reuse
the same backend logic.
"""

from __future__ import annotations

# Django
from django.contrib.auth import get_user_model

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

User = get_user_model()

INDY_HUB_CORP_ADMIN_PERMISSION = "indy_hub.can_manage_corp_bp_requests"


def can_access_indy_hub_user_admin_scope(user) -> bool:
    """Return whether the user can access Indy Hub user-admin scope data."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_perm(INDY_HUB_CORP_ADMIN_PERMISSION)


def get_managed_corporation_ids_for_user_admin_scope(user) -> set[int]:
    """Return corporation IDs managed by a non-superuser Indy Hub admin."""
    if not getattr(user, "is_authenticated", False):
        return set()
    if not user.has_perm(INDY_HUB_CORP_ADMIN_PERMISSION):
        return set()

    return {
        int(corporation_id)
        for corporation_id in CharacterOwnership.objects.filter(user=user)
        .exclude(character__corporation_id__isnull=True)
        .values_list("character__corporation_id", flat=True)
        .distinct()
        if corporation_id
    }


def get_visible_indy_hub_users_for_admin_scope(user, queryset=None):
    """Return the queryset of users visible in Indy Hub user-admin scope.

    Rules:
    - Superuser can view all users.
    - Non-superuser must have the Indy Hub corp admin permission.
    - Non-superuser visibility is restricted to users who share at least one
                corporation managed by the requesting admin.
    """
    qs = queryset if queryset is not None else User.objects.all()

    if not can_access_indy_hub_user_admin_scope(user):
        return qs.none()

    if getattr(user, "is_superuser", False):
        return qs.distinct()

    managed_corp_ids = get_managed_corporation_ids_for_user_admin_scope(user)
    if not managed_corp_ids:
        return qs.none()

    return qs.filter(
        character_ownerships__character__corporation_id__in=sorted(managed_corp_ids)
    ).distinct()


def get_visible_indy_hub_user_ids_for_admin_scope(user) -> set[int]:
    """Return visible user IDs for Indy Hub user-admin scope."""
    return set(
        get_visible_indy_hub_users_for_admin_scope(user).values_list("id", flat=True)
    )
