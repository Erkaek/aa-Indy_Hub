"""Helpers for syncing industry structures from corporation ESI."""

from __future__ import annotations

# Django
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

# AA Example App
from indy_hub.models import IndustryStructure
from indy_hub.services.esi_client import (
    ESITokenError,
    ESIUnmodifiedError,
    shared_client,
)
from indy_hub.services.industry_structures import (
    is_supported_industry_structure_type,
    resolve_item_type_reference,
    resolve_solar_system_location_reference,
    resolve_solar_system_reference,
)
from indy_hub.tasks.industry import CORP_STRUCTURES_SCOPE
from indy_hub.utils.eve import get_type_name

logger = get_extension_logger(__name__)

STRUCTURE_SYNC_NAME_MAX_LENGTH = 255
ONLINE_STRUCTURE_SERVICE_STATE = "online"

ONLINE_INDUSTRY_SERVICE_ACTIVITY_FLAGS = {
    "material efficiency research": "enable_research",
    "blueprint copying": "enable_research",
    "time efficiency research": "enable_research",
    "composite reactions": "enable_composite_reactions",
    "biochemical reactions": "enable_biochemical_reactions",
    "hybrid reactions": "enable_hybrid_reactions",
    "invention": "enable_invention",
    "manufacturing (standard)": "enable_manufacturing",
    "manufacturing (capitals)": "enable_manufacturing_capitals",
    "manufacturing (super capitals)": "enable_manufacturing_super_capitals",
}

DEFAULT_DISABLED_ACTIVITY_FLAGS = {
    "enable_manufacturing": False,
    "enable_manufacturing_capitals": False,
    "enable_manufacturing_super_capitals": False,
    "enable_research": False,
    "enable_invention": False,
    "enable_biochemical_reactions": False,
    "enable_hybrid_reactions": False,
    "enable_composite_reactions": False,
}


def _truncate_structure_sync_name(value: str) -> str:
    return str(value or "")[:STRUCTURE_SYNC_NAME_MAX_LENGTH]


def _build_synced_structure_name(
    *,
    base_name: str,
    owner_corporation_name: str,
    external_structure_id: int,
    current_structure: IndustryStructure | None = None,
) -> str:
    trimmed_base_name = (
        base_name or ""
    ).strip() or f"Structure {external_structure_id}"
    conflict_qs = IndustryStructure.objects.all()
    if current_structure is not None:
        conflict_qs = conflict_qs.exclude(pk=current_structure.pk)

    candidate_names = [_truncate_structure_sync_name(trimmed_base_name)]
    if owner_corporation_name:
        candidate_names.append(
            _truncate_structure_sync_name(
                f"{trimmed_base_name} [{owner_corporation_name}]"
            )
        )
    candidate_names.append(
        _truncate_structure_sync_name(f"{trimmed_base_name} [{external_structure_id}]")
    )
    if owner_corporation_name:
        candidate_names.append(
            _truncate_structure_sync_name(
                f"{trimmed_base_name} [{owner_corporation_name} #{external_structure_id}]"
            )
        )

    for candidate_name in candidate_names:
        if not conflict_qs.filter(name=candidate_name).exists():
            return candidate_name

    suffix = 2
    while True:
        candidate_name = _truncate_structure_sync_name(
            f"{trimmed_base_name} [{external_structure_id}-{suffix}]"
        )
        if not conflict_qs.filter(name=candidate_name).exists():
            return candidate_name
        suffix += 1


def _get_character_for_scope(
    corporation_id: int,
    *,
    owner_user: User | None = None,
) -> int:
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter

    character_ids = list(
        EveCharacter.objects.filter(corporation_id=corporation_id).values_list(
            "character_id", flat=True
        )
    )
    character_ids_set = {int(cid) for cid in character_ids if cid is not None}

    try:
        tokens = Token.objects.all()
        if owner_user is not None:
            tokens = tokens.filter(user=owner_user)
        if character_ids:
            tokens = tokens.filter(character_id__in=character_ids)
        if hasattr(tokens, "require_valid"):
            tokens = tokens.require_valid()
    except Exception:
        logger.warning(
            "Unable to load corporation structure tokens for corporation %s",
            corporation_id,
            exc_info=True,
        )
        raise ESITokenError(f"Unable to load tokens for corporation {corporation_id}.")

    if not tokens.exists():
        raise ESITokenError(f"No valid token found for corporation {corporation_id}.")

    def _character_matches_corporation(token) -> bool:
        try:
            if character_ids_set and int(token.character_id or 0) in character_ids_set:
                return True
        except Exception:
            pass
        try:
            stored = EveCharacter.objects.get_character_by_id(int(token.character_id))
            if stored is None:
                stored = EveCharacter.objects.create_character(int(token.character_id))
            return bool(
                stored
                and getattr(stored, "corporation_id", None) is not None
                and int(stored.corporation_id) == int(corporation_id)
            )
        except Exception:
            return False

    for token in tokens:
        try:
            scope_names = list(token.scopes.values_list("name", flat=True))
            if CORP_STRUCTURES_SCOPE in scope_names and _character_matches_corporation(
                token
            ):
                return int(token.character_id)
        except Exception:
            continue

    raise ESITokenError(
        f"No character in corporation {corporation_id} has scope '{CORP_STRUCTURES_SCOPE}'."
    )


def _resolve_corporation_identity(
    corporation_id: int, fallback_name: str = ""
) -> tuple[str, str]:
    # Alliance Auth
    from allianceauth.eveonline.models import EveCorporationInfo

    corporation_name = fallback_name or ""
    corporation_ticker = ""
    try:
        corporation = EveCorporationInfo.objects.filter(
            corporation_id=corporation_id
        ).first()
        if corporation is None:
            corporation = EveCorporationInfo.objects.create_corporation(corporation_id)
    except Exception:
        corporation = None

    if corporation is not None:
        corporation_name = (
            corporation_name
            or getattr(corporation, "corporation_name", "")
            or f"Corp {corporation_id}"
        )
        corporation_ticker = getattr(corporation, "corporation_ticker", "") or ""

    if not corporation_name:
        corporation_name = f"Corp {corporation_id}"

    return corporation_name, corporation_ticker


def _normalize_structure_service_entry(raw_service) -> tuple[str, str]:
    if isinstance(raw_service, dict):
        service_name = str(raw_service.get("name") or "").strip()
        service_state = str(raw_service.get("state") or "").strip().lower()
        return service_name, service_state

    service_name = str(getattr(raw_service, "name", "") or "").strip()
    service_state = str(getattr(raw_service, "state", "") or "").strip().lower()
    return service_name, service_state


def _get_online_industry_activity_flags(payload: dict[str, object]) -> dict[str, bool]:
    enabled_flags = dict(DEFAULT_DISABLED_ACTIVITY_FLAGS)

    for raw_service in payload.get("services") or []:
        service_name, service_state = _normalize_structure_service_entry(raw_service)
        if not service_name or service_state != ONLINE_STRUCTURE_SERVICE_STATE:
            continue

        flag_name = ONLINE_INDUSTRY_SERVICE_ACTIVITY_FLAGS.get(service_name.lower())
        if flag_name:
            enabled_flags[flag_name] = True

    return enabled_flags


def _has_online_industry_service(payload: dict[str, object]) -> bool:
    return any(_get_online_industry_activity_flags(payload).values())


def _iter_syncable_corporations(tokens) -> list[dict[str, object]]:
    # Alliance Auth
    from allianceauth.eveonline.models import EveCharacter

    corporations_by_id: dict[int, dict[str, object]] = {}
    for token in tokens:
        character_id = getattr(token, "character_id", None)
        if not character_id:
            continue
        try:
            character = EveCharacter.objects.get_character_by_id(int(character_id))
            if character is None:
                character = EveCharacter.objects.create_character(int(character_id))
        except Exception:
            logger.debug(
                "Unable to resolve character %s while listing structure sync corporations",
                character_id,
                exc_info=True,
            )
            continue

        corporation_id = getattr(character, "corporation_id", None)
        if not corporation_id:
            continue
        corporation_id = int(corporation_id)
        if corporation_id in corporations_by_id:
            continue

        corporation_name = getattr(character, "corporation_name", "") or ""
        corporation_ticker = getattr(character, "corporation_ticker", "") or ""
        if not corporation_name:
            corporation_name, corporation_ticker = _resolve_corporation_identity(
                corporation_id,
                fallback_name=corporation_name,
            )

        corporations_by_id[corporation_id] = {
            "id": corporation_id,
            "name": corporation_name,
            "ticker": corporation_ticker,
            "character_id": int(character_id),
        }

    return sorted(corporations_by_id.values(), key=lambda row: str(row["name"]).lower())


def _get_structure_scope_tokens(*, user: User | None = None):
    tokens = Token.objects.filter()
    if user is not None:
        tokens = tokens.filter(user=user)
    if hasattr(tokens, "require_scopes"):
        tokens = tokens.require_scopes([CORP_STRUCTURES_SCOPE])
    if hasattr(tokens, "require_valid"):
        tokens = tokens.require_valid()
    return tokens


def get_syncable_corporations_for_user(user: User) -> list[dict[str, object]]:
    try:
        tokens = _get_structure_scope_tokens(user=user)
    except Exception:
        logger.warning(
            "Unable to determine corporation structure tokens for user %s",
            user.username,
            exc_info=True,
        )
        return []

    return _iter_syncable_corporations(tokens)


def get_available_structure_sync_targets() -> list[dict[str, object]]:
    try:
        tokens = _get_structure_scope_tokens()
    except Exception:
        logger.warning(
            "Unable to determine globally syncable corporation structure tokens",
            exc_info=True,
        )
        return []

    return _iter_syncable_corporations(tokens)


def sync_corporation_structure_targets(
    sync_targets: list[dict[str, object]],
    *,
    force_refresh: bool = True,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "corporations": len(sync_targets),
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_unsupported": 0,
        "deleted": 0,
        "errors": [],
    }
    now = timezone.now()

    for corporation in sync_targets:
        corporation_id = int(corporation["id"])
        corporation_name = str(corporation.get("name") or corporation_id)
        character_id = int(corporation["character_id"])
        try:
            corporation_structures = shared_client.fetch_corporation_structures(
                corporation_id,
                character_id=character_id,
                force_refresh=force_refresh,
            )
        except ESIUnmodifiedError:
            logger.debug(
                "Corporation structures not modified for corporation %s",
                corporation_id,
            )
            continue
        except Exception as exc:
            logger.warning(
                "Unable to sync structures for corporation %s: %s",
                corporation_id,
                exc,
            )
            summary["errors"].append(f"{corporation_name}: {exc}")
            continue

        with transaction.atomic():
            retained_structure_ids: set[int] = set()
            for payload in corporation_structures or []:
                raw_structure_id = payload.get("structure_id")
                if raw_structure_id is None:
                    continue
                structure_id = int(raw_structure_id)
                structure_name = str(payload.get("name") or f"Structure {structure_id}")
                structure_type_id = (
                    int(payload["type_id"])
                    if payload.get("type_id") is not None
                    else None
                )
                if not is_supported_industry_structure_type(structure_type_id):
                    summary["skipped_unsupported"] += 1
                    continue
                enabled_activity_flags = _get_online_industry_activity_flags(payload)
                if not any(enabled_activity_flags.values()):
                    continue
                solar_system_id = (
                    int(payload["solar_system_id"])
                    if payload.get("solar_system_id") is not None
                    else None
                )
                retained_structure_ids.add(structure_id)

                structure_type_reference = (
                    resolve_item_type_reference(item_type_id=structure_type_id)
                    if structure_type_id
                    else None
                )
                structure_type_name = (
                    structure_type_reference[1]
                    if structure_type_reference is not None
                    else (get_type_name(structure_type_id) if structure_type_id else "")
                ) or ""

                solar_system_reference = (
                    resolve_solar_system_reference(solar_system_id=solar_system_id)
                    if solar_system_id
                    else None
                )
                solar_system_name = (
                    solar_system_reference[1]
                    if solar_system_reference is not None
                    else ""
                )
                system_security_band = (
                    solar_system_reference[2]
                    if solar_system_reference is not None
                    else IndustryStructure.SecurityBand.HIGHSEC
                )
                solar_system_location_reference = (
                    resolve_solar_system_location_reference(
                        solar_system_id=solar_system_id
                    )
                    if solar_system_id
                    else None
                )

                existing_structure = IndustryStructure.objects.filter(
                    owner_corporation_id=corporation_id,
                    external_structure_id=structure_id,
                ).first()

                synced_name = _build_synced_structure_name(
                    base_name=structure_name,
                    owner_corporation_name=corporation_name,
                    external_structure_id=structure_id,
                    current_structure=existing_structure,
                )

                if existing_structure is None:
                    IndustryStructure.objects.create(
                        name=synced_name,
                        structure_type_id=structure_type_id,
                        structure_type_name=structure_type_name,
                        solar_system_id=solar_system_id,
                        solar_system_name=solar_system_name,
                        constellation_id=(
                            solar_system_location_reference["constellation_id"]
                            if solar_system_location_reference is not None
                            else None
                        ),
                        constellation_name=(
                            str(solar_system_location_reference["constellation_name"])
                            if solar_system_location_reference is not None
                            else ""
                        ),
                        region_id=(
                            solar_system_location_reference["region_id"]
                            if solar_system_location_reference is not None
                            else None
                        ),
                        region_name=(
                            str(solar_system_location_reference["region_name"])
                            if solar_system_location_reference is not None
                            else ""
                        ),
                        system_security_band=system_security_band,
                        external_structure_id=structure_id,
                        owner_corporation_id=corporation_id,
                        owner_corporation_name=corporation_name,
                        sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
                        last_synced_at=now,
                        **enabled_activity_flags,
                    )
                    summary["created"] += 1
                    continue

                changed_fields: list[str] = []
                synced_field_values = {
                    "name": synced_name,
                    "structure_type_id": structure_type_id,
                    "structure_type_name": structure_type_name,
                    "solar_system_id": solar_system_id,
                    "solar_system_name": solar_system_name,
                    "constellation_id": (
                        solar_system_location_reference["constellation_id"]
                        if solar_system_location_reference is not None
                        else None
                    ),
                    "constellation_name": (
                        str(solar_system_location_reference["constellation_name"])
                        if solar_system_location_reference is not None
                        else ""
                    ),
                    "region_id": (
                        solar_system_location_reference["region_id"]
                        if solar_system_location_reference is not None
                        else None
                    ),
                    "region_name": (
                        str(solar_system_location_reference["region_name"])
                        if solar_system_location_reference is not None
                        else ""
                    ),
                    "system_security_band": system_security_band,
                    "external_structure_id": structure_id,
                    "owner_corporation_id": corporation_id,
                    "owner_corporation_name": corporation_name,
                    "sync_source": IndustryStructure.SyncSource.ESI_CORPORATION,
                    "last_synced_at": now,
                    **enabled_activity_flags,
                }
                for field_name, field_value in synced_field_values.items():
                    if getattr(existing_structure, field_name) != field_value:
                        setattr(existing_structure, field_name, field_value)
                        changed_fields.append(field_name)

                if changed_fields:
                    existing_structure.save(
                        update_fields=[*changed_fields, "updated_at"]
                    )
                    summary["updated"] += 1
                else:
                    summary["unchanged"] += 1

            deleted_count, _deleted_detail = (
                IndustryStructure.objects.filter(
                    sync_source=IndustryStructure.SyncSource.ESI_CORPORATION,
                    owner_corporation_id=corporation_id,
                )
                .exclude(external_structure_id__in=retained_structure_ids)
                .delete()
            )
            summary["deleted"] += int(deleted_count)

    return summary


def sync_user_industry_structures(
    user: User,
    *,
    force_refresh: bool = True,
) -> dict[str, object]:
    return sync_corporation_structure_targets(
        get_syncable_corporations_for_user(user),
        force_refresh=force_refresh,
    )


def sync_persisted_industry_structures(
    *,
    force_refresh: bool = True,
) -> dict[str, object]:
    return sync_corporation_structure_targets(
        get_available_structure_sync_targets(),
        force_refresh=force_refresh,
    )
