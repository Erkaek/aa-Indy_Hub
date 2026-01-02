"""Cache helpers for corporation assets, structure names, and divisions."""

from __future__ import annotations

# Standard Library
import logging
from datetime import timedelta

# Django
from django.conf import settings
from django.db import transaction
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter
from esi.clients import EsiClientProvider
from esi.models import Token

# AA Example App
# Local
from indy_hub.models import (
    CachedCharacterAsset,
    CachedCorporationAsset,
    CachedCorporationDivision,
    CachedStructureName,
)
from indy_hub.services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    shared_client,
)

logger = logging.getLogger(__name__)
esi = EsiClientProvider()

ASSET_CACHE_MAX_AGE_MINUTES = getattr(
    settings, "INDY_HUB_ASSET_CACHE_MAX_AGE_MINUTES", 60
)
CHAR_ASSET_CACHE_MAX_AGE_MINUTES = getattr(
    settings, "INDY_HUB_CHAR_ASSET_CACHE_MAX_AGE_MINUTES", ASSET_CACHE_MAX_AGE_MINUTES
)
DIVISION_CACHE_MAX_AGE_MINUTES = getattr(
    settings, "INDY_HUB_DIVISION_CACHE_MAX_AGE_MINUTES", 1440
)


def make_managed_hangar_location_id(office_folder_item_id: int, division: int) -> int:
    """Return the corptools-style managed hangar location id.

    This encodes the office folder item id and corp hangar division into a single negative id:
    -(office_folder_item_id * 10 + division)
    """

    office_folder_item_id = int(office_folder_item_id)
    division = int(division)
    return -(office_folder_item_id * 10 + division)


def get_office_folder_item_id_from_assets(
    corp_assets: list[dict], *, structure_id: int
) -> int | None:
    """Extract the office folder item_id for a structure from corp assets.

    ESI corp assets represent the OfficeFolder itself as an asset where:
    - location_id == structure_id
    - location_flag == "OfficeFolder"
    - item_id is the office folder item id
    """

    try:
        structure_id_int = int(structure_id)
    except (TypeError, ValueError):
        return None

    for asset in corp_assets or []:
        try:
            if int(asset.get("location_id", 0) or 0) != structure_id_int:
                continue
        except (TypeError, ValueError):
            continue

        if str(asset.get("location_flag") or "") != "OfficeFolder":
            continue

        item_id = asset.get("item_id")
        if item_id is None:
            continue

        try:
            return int(item_id)
        except (TypeError, ValueError):
            return None

    return None


def _cache_corp_structure_names(corporation_id: int) -> dict[int, str]:
    """Cache all corp structure names using the corp structures endpoint."""

    try:
        character_id = _get_character_for_scope(
            int(corporation_id), "esi-corporations.read_structures.v1"
        )
    except ESITokenError:
        return {}

    try:
        structures = shared_client.fetch_corporation_structures(
            int(corporation_id), character_id=int(character_id)
        )
    except (ESIForbiddenError, ESITokenError):
        return {}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to cache corp structures for %s: %s", corporation_id, exc
        )
        return {}

    now = timezone.now()
    cached: dict[int, str] = {}
    for entry in structures:
        sid = entry.get("structure_id")
        name = entry.get("name")
        if not sid or not name:
            continue
        cached[int(sid)] = name
        CachedStructureName.objects.update_or_create(
            structure_id=int(sid),
            defaults={"name": name, "last_resolved": now},
        )

    return cached


def _get_character_for_scope(corporation_id: int, scope: str) -> int:
    """Find a character in the corporation with the required ESI scope."""

    character_ids = list(
        EveCharacter.objects.filter(corporation_id=corporation_id).values_list(
            "character_id", flat=True
        )
    )
    if not character_ids:
        raise ESITokenError(
            f"No characters found for corporation {corporation_id}. "
            "At least one corporation member must login to grant ESI scopes."
        )

    tokens = Token.objects.filter(character_id__in=character_ids)
    if not tokens.exists():
        raise ESITokenError(
            f"No tokens found for corporation {corporation_id}. "
            "At least one corporation member must login to grant ESI scopes."
        )

    for token in tokens:
        try:
            scope_names = list(token.scopes.values_list("name", flat=True))
            if scope in scope_names:
                return token.character_id
        except Exception:
            continue

    raise ESITokenError(
        f"No character in corporation {corporation_id} has scope '{scope}'. "
        "Ask a member to grant this scope."
    )


def _refresh_corp_assets(corporation_id: int) -> tuple[list[dict], bool]:
    """Fetch corporation assets from ESI and refresh the cache."""

    assets_scope_missing = False
    try:
        character_id = _get_character_for_scope(
            corporation_id, "esi-assets.read_corporation_assets.v1"
        )
        assets = shared_client.fetch_corporation_assets(
            corporation_id=int(corporation_id),
            character_id=int(character_id),
        )
        now = timezone.now()
        rows: list[CachedCorporationAsset] = []
        for asset in assets:
            rows.append(
                CachedCorporationAsset(
                    corporation_id=int(corporation_id),
                    item_id=(
                        int(asset.get("item_id"))
                        if asset.get("item_id") is not None
                        else None
                    ),
                    location_id=int(asset.get("location_id", 0) or 0),
                    location_flag=str(asset.get("location_flag", "") or ""),
                    type_id=int(asset.get("type_id", 0) or 0),
                    quantity=int(asset.get("quantity", 0) or 0),
                    is_singleton=bool(asset.get("is_singleton", False)),
                    is_blueprint=bool(asset.get("is_blueprint", False)),
                    synced_at=now,
                )
            )

        with transaction.atomic():
            CachedCorporationAsset.objects.filter(
                corporation_id=corporation_id
            ).delete()
            if rows:
                CachedCorporationAsset.objects.bulk_create(rows, batch_size=1000)

        # Cache all corp structure names while we have a valid corp token
        _cache_corp_structure_names(int(corporation_id))

        return assets, assets_scope_missing

    except ESITokenError:
        assets_scope_missing = True
    except (ESIRateLimitError, ESIClientError) as exc:
        logger.warning("ESI assets lookup failed for corp %s: %s", corporation_id, exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Unexpected error refreshing corp assets for %s: %s", corporation_id, exc
        )

    return [], assets_scope_missing


def get_corp_assets_cached(
    corporation_id: int,
    *,
    allow_refresh: bool = True,
    max_age_minutes: int | None = None,
) -> tuple[list[dict], bool]:
    """Return cached corp assets; refresh from ESI when stale/empty if allowed."""

    max_age = max_age_minutes or ASSET_CACHE_MAX_AGE_MINUTES
    qs = CachedCorporationAsset.objects.filter(corporation_id=corporation_id)

    latest = qs.order_by("-synced_at").values_list("synced_at", flat=True).first()
    assets_scope_missing = False
    if latest and timezone.now() - latest <= timedelta(minutes=max_age):
        assets = [
            {
                "item_id": row.item_id,
                "location_id": row.location_id,
                "location_flag": row.location_flag,
                "type_id": row.type_id,
                "quantity": row.quantity,
                "is_singleton": row.is_singleton,
                "is_blueprint": row.is_blueprint,
            }
            for row in qs
        ]
        return assets, assets_scope_missing

    if allow_refresh:
        refreshed_assets, assets_scope_missing = _refresh_corp_assets(corporation_id)
        if refreshed_assets:
            return refreshed_assets, assets_scope_missing

    # Fallback to whatever is in cache even if stale
    assets = [
        {
            "item_id": row.item_id,
            "location_id": row.location_id,
            "location_flag": row.location_flag,
            "type_id": row.type_id,
            "quantity": row.quantity,
            "is_singleton": row.is_singleton,
            "is_blueprint": row.is_blueprint,
        }
        for row in qs
    ]
    return assets, assets_scope_missing


def resolve_structure_names(
    structure_ids: list[int],
    character_id: int | None = None,
    corporation_id: int | None = None,
) -> dict[int, str]:
    """Return a mapping of structure_id -> name using cache, corp structures, and ESI lookups."""

    if not structure_ids:
        return {}

    requested_ids = [int(sid) for sid in structure_ids]

    # Managed hangar ids are negative ids derived from an office folder item id + division.
    managed_ids = [sid for sid in requested_ids if sid < 0]

    managed_mapping: dict[int, tuple[int, int]] = {}
    managed_base_structure_ids: set[int] = set()
    if managed_ids and corporation_id:
        office_folder_item_ids: set[int] = set()
        for mid in managed_ids:
            raw = abs(int(mid))
            division = raw % 10
            office_folder_item_id = raw // 10
            if office_folder_item_id <= 0 or division not in range(1, 8):
                continue
            managed_mapping[int(mid)] = (int(office_folder_item_id), int(division))
            office_folder_item_ids.add(int(office_folder_item_id))

        if office_folder_item_ids:
            folder_rows = (
                CachedCorporationAsset.objects.filter(
                    corporation_id=int(corporation_id),
                    item_id__in=list(office_folder_item_ids),
                    location_flag="OfficeFolder",
                )
                .values_list("item_id", "location_id")
                .distinct()
            )
            folder_to_structure = {
                int(item_id): int(location_id) for item_id, location_id in folder_rows
            }
            for mid, (folder_item_id, _division) in managed_mapping.items():
                structure_id = folder_to_structure.get(int(folder_item_id))
                if structure_id:
                    managed_base_structure_ids.add(int(structure_id))

    all_ids_for_cache = list(set(requested_ids + list(managed_base_structure_ids)))
    known = {
        obj.structure_id: obj.name
        for obj in CachedStructureName.objects.filter(
            structure_id__in=all_ids_for_cache
        )
    }
    missing = [sid for sid in all_ids_for_cache if sid not in known]

    # Try corporation structures endpoint first (returns names) when corp_id is available
    # Only applies to real (positive) structure ids.
    if any(sid > 0 for sid in missing) and corporation_id:
        cached = _cache_corp_structure_names(int(corporation_id))
        for sid, name in cached.items():
            if sid in missing:
                known[sid] = name
        missing = [
            sid
            for sid in all_ids_for_cache
            if sid not in known or str(known.get(sid, "")).startswith("Structure ")
        ]

    # Try direct structure lookups with the provided character first, then fall back to any corp token with the universe scope
    candidate_characters: list[int] = []
    if character_id:
        candidate_characters.append(int(character_id))

    if corporation_id:
        try:
            extra_chars = list(
                Token.objects.filter(character__corporation_id=int(corporation_id))
                .require_scopes(["esi-universe.read_structures.v1"])
                .require_valid()
                .values_list("character_id", flat=True)
            )
            for cid in extra_chars:
                if cid not in candidate_characters:
                    candidate_characters.append(int(cid))
        except Exception:  # pragma: no cover - defensive
            pass

    # Also try any characters (alts) that have assets in these locations and a universe scope token
    try:
        asset_chars = list(
            CachedCharacterAsset.objects.filter(location_id__in=missing)
            .values_list("character_id", flat=True)
            .distinct()
        )
        if asset_chars:
            alt_chars = list(
                Token.objects.filter(character_id__in=asset_chars)
                .require_scopes(["esi-universe.read_structures.v1"])
                .require_valid()
                .values_list("character_id", flat=True)
            )
            for cid in alt_chars:
                if cid and cid not in candidate_characters:
                    candidate_characters.append(int(cid))
    except Exception:  # pragma: no cover - defensive
        pass

    # As a last resort, try any valid token with universe.read_structures scope (alts in other corps/alliances)
    try:
        global_chars = list(
            Token.objects.all()
            .require_scopes(["esi-universe.read_structures.v1"])
            .require_valid()
            .values_list("character_id", flat=True)
        )
        for cid in global_chars:
            if cid and cid not in candidate_characters:
                candidate_characters.append(int(cid))
    except Exception:  # pragma: no cover - defensive
        pass

    # Only attempt direct structure lookups for positive ids.
    for structure_id in [sid for sid in list(missing) if sid > 0]:
        resolved = False
        for cid in candidate_characters:
            try:
                name = shared_client.fetch_structure_name(structure_id, cid)
            except ESIForbiddenError:
                logger.info(
                    "Structure lookup forbidden for %s with character %s",
                    structure_id,
                    cid,
                )
                continue
            except ESITokenError:
                logger.info(
                    "Structure lookup missing/invalid token for %s with character %s",
                    structure_id,
                    cid,
                )
                continue

            if not name:
                continue
            known[structure_id] = name
            CachedStructureName.objects.update_or_create(
                structure_id=structure_id,
                defaults={"name": name, "last_resolved": timezone.now()},
            )
            resolved = True
            break

        if resolved:
            missing.remove(structure_id)

    # Resolve managed hangar ids to "<structure name> > <division name>".
    if managed_ids and corporation_id:
        div_map, _ = get_corp_divisions_cached(int(corporation_id), allow_refresh=True)

        # Rebuild folder->structure map now that base assets/cache may have been refreshed.
        office_folder_item_ids = {v[0] for v in managed_mapping.values()}
        folder_rows = (
            CachedCorporationAsset.objects.filter(
                corporation_id=int(corporation_id),
                item_id__in=list(office_folder_item_ids),
                location_flag="OfficeFolder",
            )
            .values_list("item_id", "location_id")
            .distinct()
        )
        folder_to_structure = {
            int(item_id): int(loc_id) for item_id, loc_id in folder_rows
        }

        now = timezone.now()
        for mid, (folder_item_id, division) in managed_mapping.items():
            if mid in known:
                continue
            structure_id = folder_to_structure.get(int(folder_item_id))
            if not structure_id:
                continue
            base_name = known.get(int(structure_id)) or f"Structure {structure_id}"
            if " > " in base_name:
                base_name = base_name.split(" > ")[0]
            if base_name.startswith("Structure "):
                continue

            division_name = div_map.get(int(division)) or f"Hangar Division {division}"
            combined = f"{base_name} > {division_name}"
            known[int(mid)] = combined
            CachedStructureName.objects.update_or_create(
                structure_id=int(mid),
                defaults={"name": combined, "last_resolved": now},
            )

    # Return only requested ids
    return {sid: known[sid] for sid in requested_ids if sid in known}


def _refresh_corp_divisions(corporation_id: int) -> tuple[dict[int, str], bool]:
    """Fetch corp hangar divisions from ESI and refresh the cache."""

    scope_missing = False
    try:
        character_id = _get_character_for_scope(
            corporation_id, "esi-corporations.read_divisions.v1"
        )
        token_obj = Token.get_token(character_id, "esi-corporations.read_divisions.v1")
        divisions_data = (
            esi.client.Corporation.get_corporations_corporation_id_divisions(
                corporation_id=corporation_id,
                token=token_obj.valid_access_token(),
            ).results()
        )
        hangar_divisions = divisions_data.get("hangar", []) if divisions_data else []

        now = timezone.now()
        divisions: dict[int, str] = {}
        for info in hangar_divisions:
            division_num = info.get("division")
            division_name = info.get("name")
            if division_num:
                divisions[int(division_num)] = (
                    division_name or f"Hangar Division {division_num}"
                )

        with transaction.atomic():
            CachedCorporationDivision.objects.filter(
                corporation_id=corporation_id
            ).delete()
            if divisions:
                CachedCorporationDivision.objects.bulk_create(
                    [
                        CachedCorporationDivision(
                            corporation_id=corporation_id,
                            division=div_num,
                            name=div_name,
                            synced_at=now,
                        )
                        for div_num, div_name in divisions.items()
                    ],
                    batch_size=20,
                )
        return divisions, scope_missing

    except ESITokenError:
        scope_missing = True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Error refreshing corp divisions for %s: %s", corporation_id, exc
        )

    return {}, scope_missing


def get_corp_divisions_cached(
    corporation_id: int,
    *,
    allow_refresh: bool = True,
    max_age_minutes: int | None = None,
) -> tuple[dict[int, str], bool]:
    """Return cached hangar division names; refresh from ESI when stale if allowed."""

    max_age = max_age_minutes or DIVISION_CACHE_MAX_AGE_MINUTES
    qs = CachedCorporationDivision.objects.filter(corporation_id=corporation_id)
    latest = qs.order_by("-synced_at").values_list("synced_at", flat=True).first()
    scope_missing = False

    if latest and timezone.now() - latest <= timedelta(minutes=max_age):
        return {obj.division: obj.name for obj in qs}, scope_missing

    if allow_refresh:
        divisions, scope_missing = _refresh_corp_divisions(corporation_id)
        if divisions:
            return divisions, scope_missing

    return {obj.division: obj.name for obj in qs}, scope_missing


def force_refresh_corp_assets(corporation_id: int) -> tuple[list[dict], bool]:
    """Force refresh of corp assets cache regardless of staleness."""

    return _refresh_corp_assets(corporation_id)


def force_refresh_corp_divisions(corporation_id: int) -> tuple[dict[int, str], bool]:
    """Force refresh of corp division cache regardless of staleness."""

    return _refresh_corp_divisions(corporation_id)


def _refresh_character_assets(user) -> tuple[list[dict], bool]:
    """Fetch character assets for a user from ESI and refresh the cache."""

    asset_scope = "esi-assets.read_assets.v1"
    tokens = (
        Token.objects.filter(user=user).require_scopes([asset_scope]).require_valid()
    )
    if not tokens.exists():
        return [], True

    assets_scope_missing = False
    rows: list[CachedCharacterAsset] = []
    all_assets: list[dict] = []
    now = timezone.now()

    corp_ids: set[int] = set()

    for token in tokens:
        character_id = getattr(token, "character_id", None)
        try:
            corp_id = getattr(token.character, "corporation_id", None)
            if corp_id:
                corp_ids.add(int(corp_id))
        except Exception:
            pass
        if not character_id:
            continue
        try:
            assets = shared_client.fetch_character_assets(
                character_id=int(character_id)
            )
        except (ESITokenError, ESIRateLimitError, ESIClientError) as exc:
            logger.warning(
                "Failed to load assets for character %s: %s", character_id, exc
            )
            continue

        for asset in assets:
            row = CachedCharacterAsset(
                user=user,
                character_id=int(character_id),
                location_id=int(asset.get("location_id", 0) or 0),
                location_flag=str(asset.get("location_flag", "") or ""),
                type_id=int(asset.get("type_id", 0) or 0),
                quantity=int(asset.get("quantity", 0) or 0),
                is_singleton=bool(asset.get("is_singleton", False)),
                is_blueprint=bool(asset.get("is_blueprint", False)),
                synced_at=now,
            )
            rows.append(row)
            all_assets.append(
                {
                    "character_id": int(character_id),
                    "location_id": row.location_id,
                    "location_flag": row.location_flag,
                    "type_id": row.type_id,
                    "quantity": row.quantity,
                    "is_singleton": row.is_singleton,
                    "is_blueprint": row.is_blueprint,
                }
            )

    for corp_id in corp_ids:
        _cache_corp_structure_names(corp_id)

    with transaction.atomic():
        CachedCharacterAsset.objects.filter(user=user).delete()
        if rows:
            CachedCharacterAsset.objects.bulk_create(rows, batch_size=1000)

    return all_assets, assets_scope_missing


def get_user_assets_cached(
    user, *, allow_refresh: bool = True, max_age_minutes: int | None = None
) -> tuple[list[dict], bool]:
    """Return cached character assets for a user; refresh from ESI when stale/empty if allowed."""

    max_age = max_age_minutes or CHAR_ASSET_CACHE_MAX_AGE_MINUTES
    qs = CachedCharacterAsset.objects.filter(user=user)
    latest = qs.order_by("-synced_at").values_list("synced_at", flat=True).first()
    assets_scope_missing = False

    if latest and timezone.now() - latest <= timedelta(minutes=max_age):
        assets = [
            {
                "character_id": row.character_id,
                "location_id": row.location_id,
                "location_flag": row.location_flag,
                "type_id": row.type_id,
                "quantity": row.quantity,
                "is_singleton": row.is_singleton,
                "is_blueprint": row.is_blueprint,
            }
            for row in qs
        ]
        return assets, assets_scope_missing

    if allow_refresh:
        refreshed_assets, assets_scope_missing = _refresh_character_assets(user)
        if refreshed_assets:
            return refreshed_assets, assets_scope_missing

    assets = [
        {
            "character_id": row.character_id,
            "location_id": row.location_id,
            "location_flag": row.location_flag,
            "type_id": row.type_id,
            "quantity": row.quantity,
            "is_singleton": row.is_singleton,
            "is_blueprint": row.is_blueprint,
        }
        for row in qs
    ]
    return assets, assets_scope_missing
