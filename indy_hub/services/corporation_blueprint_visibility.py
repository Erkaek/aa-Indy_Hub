"""Helpers for read-only access to corporation blueprint catalogs."""

from __future__ import annotations

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter

from ..models import CharacterSettings, CorporationSharingSetting


def _get_accessible_corporation_ids_for_scope(user, scope_field_name: str) -> set[int]:
    if not getattr(user, "is_authenticated", False):
        return set()

    accessible_ids: set[int] = set()
    if user.has_perm("indy_hub.can_manage_corp_bp_requests"):
        accessible_ids.update(get_managed_corporation_ids(user))

    viewer_affiliations = _collect_user_affiliations(user)
    configured_settings = list(
        CorporationSharingSetting.objects.exclude(
            **{scope_field_name: CharacterSettings.SCOPE_NONE}
        ).values_list("corporation_id", scope_field_name)
    )
    configured_corp_ids = {
        int(corporation_id)
        for corporation_id, _scope in configured_settings
        if corporation_id
    }
    alliance_map = _collect_corporation_alliances(configured_corp_ids)

    for corporation_id, scope in configured_settings:
        if not corporation_id:
            continue
        normalized_corp_id = int(corporation_id)
        if scope == CharacterSettings.SCOPE_CORPORATION:
            if normalized_corp_id in viewer_affiliations["corp_ids"]:
                accessible_ids.add(normalized_corp_id)
        elif scope == CharacterSettings.SCOPE_ALLIANCE:
            if normalized_corp_id in viewer_affiliations["corp_ids"]:
                accessible_ids.add(normalized_corp_id)
                continue
            if (
                alliance_map.get(normalized_corp_id, set())
                & viewer_affiliations["alliance_ids"]
            ):
                accessible_ids.add(normalized_corp_id)
        elif scope == CharacterSettings.SCOPE_EVERYONE:
            if user.has_perm("indy_hub.can_access_indy_hub"):
                accessible_ids.add(normalized_corp_id)

    return accessible_ids


def get_managed_corporation_ids(user) -> set[int]:
    if not getattr(user, "is_authenticated", False):
        return set()

    return {
        int(corporation_id)
        for corporation_id in CharacterOwnership.objects.filter(user=user)
        .exclude(character__corporation_id__isnull=True)
        .values_list("character__corporation_id", flat=True)
        .distinct()
        if corporation_id
    }


def _collect_user_affiliations(user) -> dict[str, set[int]]:
    corp_ids: set[int] = set()
    alliance_ids: set[int] = set()

    if not getattr(user, "is_authenticated", False):
        return {"corp_ids": corp_ids, "alliance_ids": alliance_ids}

    rows = EveCharacter.objects.filter(character_ownership__user=user).values(
        "corporation_id",
        "alliance_id",
    )
    for row in rows:
        corporation_id = row.get("corporation_id")
        alliance_id = row.get("alliance_id")
        if corporation_id:
            corp_ids.add(int(corporation_id))
        if alliance_id:
            alliance_ids.add(int(alliance_id))

    return {"corp_ids": corp_ids, "alliance_ids": alliance_ids}


def _collect_corporation_alliances(corporation_ids: set[int]) -> dict[int, set[int]]:
    alliance_map = {int(corporation_id): set() for corporation_id in corporation_ids}
    if not corporation_ids:
        return alliance_map

    rows = (
        EveCharacter.objects.filter(corporation_id__in=corporation_ids)
        .exclude(alliance_id__isnull=True)
        .values_list("corporation_id", "alliance_id")
        .distinct()
    )
    for corporation_id, alliance_id in rows:
        if corporation_id and alliance_id:
            alliance_map.setdefault(int(corporation_id), set()).add(int(alliance_id))
    return alliance_map


def get_viewable_corporation_ids(user) -> set[int]:
    return _get_accessible_corporation_ids_for_scope(user, "blueprint_catalog_scope")


def get_viewable_corporation_job_ids(user) -> set[int]:
    return _get_accessible_corporation_ids_for_scope(user, "job_catalog_scope")


def can_view_corporation_blueprints(user) -> bool:
    return bool(get_viewable_corporation_ids(user))


def can_view_corporation_jobs(user) -> bool:
    return bool(get_viewable_corporation_job_ids(user))
