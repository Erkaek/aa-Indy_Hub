"""Helper utilities for retrieving EVE Online metadata."""

from __future__ import annotations

# Standard Library
import logging
from collections.abc import Iterable, Mapping

# Third Party
import requests

# Django
from django.conf import settings

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter

from ..services.esi_client import (
    ESI_BASE_URL,
    ESIClientError,
    ESITokenError,
    shared_client,
)

if getattr(settings, "configured", False) and "eveuniverse" in getattr(
    settings, "INSTALLED_APPS", ()
):  # pragma: no branch
    try:  # pragma: no cover - EveUniverse is optional
        # Alliance Auth (External Libs)
        from eveuniverse.models import EveIndustryActivityProduct, EveType
    except ImportError:  # pragma: no cover - fallback when EveUniverse is not installed
        EveType = None
        EveIndustryActivityProduct = None
else:  # pragma: no cover - EveUniverse app not installed
    EveType = None
    EveIndustryActivityProduct = None

logger = logging.getLogger(__name__)

_TYPE_NAME_CACHE: dict[int, str] = {}
_CHAR_NAME_CACHE: dict[int, str] = {}
_BP_PRODUCT_CACHE: dict[int, int | None] = {}
_REACTION_CACHE: dict[int, bool] = {}
_LOCATION_NAME_CACHE: dict[int, str] = {}


def get_type_name(type_id: int | None) -> str:
    """Return the display name for a type ID, falling back to the ID string."""
    if not type_id:
        return ""

    if type_id in _TYPE_NAME_CACHE:
        return _TYPE_NAME_CACHE[type_id]

    if EveType is None:
        value = str(type_id)
    else:
        try:
            value = EveType.objects.only("name").get(id=type_id).name
        except EveType.DoesNotExist:  # type: ignore[attr-defined]
            logger.debug(
                "EveType %s introuvable, retour de l'identifiant brut", type_id
            )
            value = str(type_id)

    _TYPE_NAME_CACHE[type_id] = value
    return value


def get_character_name(character_id: int | None) -> str:
    """Return the pilot name for a character ID, falling back to the ID string."""
    if not character_id:
        return ""

    if character_id in _CHAR_NAME_CACHE:
        return _CHAR_NAME_CACHE[character_id]

    try:
        value = (
            EveCharacter.objects.only("character_name")
            .get(character_id=character_id)
            .character_name
        )
    except EveCharacter.DoesNotExist:
        logger.debug(
            "EveCharacter %s introuvable, retour de l'identifiant brut",
            character_id,
        )
        value = str(character_id)

    _CHAR_NAME_CACHE[character_id] = value
    return value


def batch_cache_type_names(type_ids: Iterable[int]) -> Mapping[int, str]:
    """Fetch and cache type names in batch, returning the mapping."""
    ids = {int(pk) for pk in type_ids if pk}
    if not ids:
        return {}

    if EveType is None:
        return {pk: str(pk) for pk in ids}

    result: dict[int, str] = {}
    for eve_type in EveType.objects.filter(id__in=ids).only("id", "name"):
        _TYPE_NAME_CACHE[eve_type.id] = eve_type.name
        result[eve_type.id] = eve_type.name

    missing = ids - result.keys()
    for pk in missing:
        value = str(pk)
        _TYPE_NAME_CACHE[pk] = value
        result[pk] = value

    return result


def get_blueprint_product_type_id(blueprint_type_id: int | None) -> int | None:
    """Resolve the manufactured product type for a blueprint when possible."""
    if not blueprint_type_id:
        return None

    blueprint_type_id = int(blueprint_type_id)
    if blueprint_type_id in _BP_PRODUCT_CACHE:
        return _BP_PRODUCT_CACHE[blueprint_type_id]

    product_id: int | None = None

    if EveIndustryActivityProduct is not None:
        try:
            qs = EveIndustryActivityProduct.objects.filter(
                eve_type_id=blueprint_type_id
            )
            if qs.exists():
                product = qs.filter(activity_id=1).first() or qs.first()
                if product:
                    product_id = product.product_eve_type_id
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "Impossible de résoudre le produit pour le blueprint %s via ESI Universe",
                blueprint_type_id,
                exc_info=True,
            )

    _BP_PRODUCT_CACHE[blueprint_type_id] = product_id
    return product_id


def is_reaction_blueprint(blueprint_type_id: int | None) -> bool:
    """Return True when the blueprint is associated with a reaction activity."""
    if not blueprint_type_id:
        return False

    blueprint_type_id = int(blueprint_type_id)
    if blueprint_type_id in _REACTION_CACHE:
        return _REACTION_CACHE[blueprint_type_id]

    if EveIndustryActivityProduct is None:
        value = False
    else:
        try:
            value = EveIndustryActivityProduct.objects.filter(
                eve_type_id=blueprint_type_id, activity_id__in=[9, 11]
            ).exists()
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "Impossible de déterminer l'activité pour le blueprint %s",
                blueprint_type_id,
                exc_info=True,
            )
            value = False

    _REACTION_CACHE[blueprint_type_id] = value
    return value


def resolve_location_name(
    structure_id: int | None,
    *,
    character_id: int | None = None,
) -> str:
    """Resolve a structure or station name using ESI lookups with caching."""

    if not structure_id:
        return ""

    structure_id = int(structure_id)
    if structure_id in _LOCATION_NAME_CACHE:
        return _LOCATION_NAME_CACHE[structure_id]

    name: str | None = None

    if character_id:
        try:
            name = shared_client.fetch_structure_name(structure_id, character_id)
        except ESITokenError:
            logger.debug(
                "Character %s lacks esi-universe.read_structures scope for %s",
                character_id,
                structure_id,
            )
        except ESIClientError as exc:  # pragma: no cover - defensive fallback
            logger.debug(
                "Authenticated structure lookup failed for %s: %s",
                structure_id,
                exc,
            )

    params = {"datasource": "tranquility"}

    if not name:
        try:
            response = requests.get(
                f"{ESI_BASE_URL}/universe/structures/{structure_id}/",
                params=params,
                timeout=15,
            )
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                name = payload.get("name")
        except requests.RequestException as exc:  # pragma: no cover - network failures
            logger.debug(
                "Unauthenticated structure lookup failed for %s: %s",
                structure_id,
                exc,
            )

    if not name:
        try:
            response = requests.get(
                f"{ESI_BASE_URL}/universe/stations/{structure_id}/",
                params=params,
                timeout=15,
            )
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                name = payload.get("name")
        except requests.RequestException as exc:  # pragma: no cover - network failures
            logger.debug(
                "Station lookup failed for %s: %s",
                structure_id,
                exc,
            )

    if not name:
        name = f"Structure {structure_id}"

    _LOCATION_NAME_CACHE[structure_id] = name
    return name
