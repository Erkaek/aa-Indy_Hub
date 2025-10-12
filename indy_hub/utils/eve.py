"""Helper utilities for retrieving EVE Online metadata."""

from __future__ import annotations

# Standard Library
import logging
from collections.abc import Iterable, Mapping

# Third Party
import requests

# Django
from django.apps import apps
from django.conf import settings
from django.core.exceptions import AppRegistryNotReady

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter
from esi.models import Token

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
PLACEHOLDER_PREFIX = "Structure "
_STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
_FALLBACK_STRUCTURE_TOKEN_IDS: list[int] | None = None


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


def _get_structure_scope_token_ids() -> list[int]:
    global _FALLBACK_STRUCTURE_TOKEN_IDS

    if _FALLBACK_STRUCTURE_TOKEN_IDS is not None:
        return _FALLBACK_STRUCTURE_TOKEN_IDS

    try:
        qs = Token.objects.all().require_scopes(_STRUCTURE_SCOPE)
        token_ids = list(qs.values_list("character_id", flat=True).distinct())
    except Exception:  # pragma: no cover - defensive fallback when DB unavailable
        logger.debug("Unable to load structure scope tokens", exc_info=True)
        token_ids = []

    _FALLBACK_STRUCTURE_TOKEN_IDS = [int(char_id) for char_id in token_ids]
    return _FALLBACK_STRUCTURE_TOKEN_IDS


def _invalidate_structure_scope_token_cache() -> None:
    global _FALLBACK_STRUCTURE_TOKEN_IDS
    _FALLBACK_STRUCTURE_TOKEN_IDS = None


def _lookup_location_name_in_db(structure_id: int) -> str | None:
    """Return a previously stored location name for the given ID when present."""

    model_specs = (
        ("indy_hub", "Blueprint", "location_id", "location_name"),
        ("indy_hub", "IndustryJob", "station_id", "location_name"),
    )

    for app_label, model_name, id_field, name_field in model_specs:
        try:
            model = apps.get_model(app_label, model_name)
        except (LookupError, AppRegistryNotReady):
            continue

        if model is None:
            continue

        filter_kwargs = {id_field: structure_id}

        try:
            qs = (
                model.objects.filter(**filter_kwargs)
                .exclude(**{f"{name_field}__isnull": True})
                .exclude(**{name_field: ""})
                .exclude(**{f"{name_field}__startswith": PLACEHOLDER_PREFIX})
            )
            existing = qs.values_list(name_field, flat=True).first()
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "Unable to reuse stored location for %s via %s.%s",
                structure_id,
                app_label,
                model_name,
                exc_info=True,
            )
            existing = None

        if existing:
            return str(existing)

    return None


def resolve_location_name(
    structure_id: int | None,
    *,
    character_id: int | None = None,
    force_refresh: bool = False,
) -> str:
    """Resolve a structure or station name using ESI lookups with caching.

    When ``force_refresh`` is True, cached placeholder values (``Structure <id>``)
    are ignored so that a fresh lookup can populate the real name if available.
    """

    if not structure_id:
        return ""

    structure_id = int(structure_id)
    placeholder_value = f"{PLACEHOLDER_PREFIX}{structure_id}"

    cached = _LOCATION_NAME_CACHE.get(structure_id)
    if cached is not None:
        if not force_refresh or cached != placeholder_value:
            return cached

    if not force_refresh:
        db_name = _lookup_location_name_in_db(structure_id)
        if db_name:
            _LOCATION_NAME_CACHE[structure_id] = db_name
            return db_name

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

    if not name:
        for fallback_character_id in _get_structure_scope_token_ids():
            if fallback_character_id == character_id:
                continue
            try:
                name = shared_client.fetch_structure_name(
                    structure_id, fallback_character_id
                )
            except ESITokenError:
                logger.debug(
                    "Token for fallback character %s invalid when resolving %s",
                    fallback_character_id,
                    structure_id,
                )
                _invalidate_structure_scope_token_cache()
                continue
            except ESIClientError as exc:  # pragma: no cover
                logger.debug(
                    "Fallback structure lookup failed for %s via %s: %s",
                    structure_id,
                    fallback_character_id,
                    exc,
                )
                continue

            if name:
                break

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
        name = placeholder_value

    _LOCATION_NAME_CACHE[structure_id] = name
    return name
