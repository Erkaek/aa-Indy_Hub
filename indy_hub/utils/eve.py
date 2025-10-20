"""Helper utilities for retrieving EVE Online metadata."""

from __future__ import annotations

# Standard Library
import logging
import time
from collections.abc import Iterable, Mapping
from typing import Any

# Third Party
import requests

# Django
from django.apps import apps
from django.conf import settings
from django.core.exceptions import AppRegistryNotReady

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from esi.models import Token

from ..services.esi_client import (
    ESI_BASE_URL,
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    rate_limit_wait_seconds,
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
_CORP_NAME_CACHE: dict[int, str] = {}
_BP_PRODUCT_CACHE: dict[int, int | None] = {}
_REACTION_CACHE: dict[int, bool] = {}
_LOCATION_NAME_CACHE: dict[int, str] = {}
PLACEHOLDER_PREFIX = "Structure "
_STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
_FALLBACK_STRUCTURE_TOKEN_IDS: list[int] | None = None
_OWNER_STRUCTURE_TOKEN_CACHE: dict[int, list[int]] = {}
_STATION_ID_MAX = 100_000_000
_MAX_STRUCTURE_LOOKUPS = 3
_FORBIDDEN_STRUCTURE_CHARACTERS: set[int] = set()
_STRUCTURE_LOOKUP_PAUSE_UNTIL: float = 0.0


def _schedule_structure_rate_limit_pause(duration: float | None) -> None:
    """Record a future time when structure lookups may resume."""

    if not duration or duration <= 0:
        return

    global _STRUCTURE_LOOKUP_PAUSE_UNTIL
    resume_at = time.monotonic() + float(duration)
    _STRUCTURE_LOOKUP_PAUSE_UNTIL = max(_STRUCTURE_LOOKUP_PAUSE_UNTIL, resume_at)


def _wait_for_structure_rate_limit_window() -> None:
    """Sleep until the recorded rate limit pause elapses, if necessary."""

    remaining = _STRUCTURE_LOOKUP_PAUSE_UNTIL - time.monotonic()
    if remaining > 0:
        logger.info(
            "Throttling structure lookups for %.1fs to respect ESI rate limit",
            remaining,
        )
        time.sleep(remaining)


def _rate_limited_public_get(
    url: str,
    *,
    params: Mapping[str, Any],
    timeout: int = 15,
    max_attempts: int = 3,
) -> requests.Response | None:
    """Perform a GET request honouring the shared rate limit pause."""

    response: requests.Response | None = None
    for attempt in range(1, max_attempts + 1):
        _wait_for_structure_rate_limit_window()

        try:
            response = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            if attempt >= max_attempts:
                logger.debug(
                    "Unauthenticated lookup %s failed on attempt %s/%s: %s",
                    url,
                    attempt,
                    max_attempts,
                    exc,
                )
                return None
            sleep_for = shared_client.backoff_factor * (2 ** (attempt - 1))
            logger.warning(
                "Request error for %s, retry %s/%s in %.1fs",
                url,
                attempt,
                max_attempts,
                sleep_for,
            )
            time.sleep(sleep_for)
            continue

        if response.status_code == 420:
            sleep_for, remaining = rate_limit_wait_seconds(
                response, shared_client.backoff_factor * (2 ** (attempt - 1))
            )
            logger.warning(
                "ESI rate limit reached for %s (public), attempt %s/%s (remaining=%s).",
                url,
                attempt,
                max_attempts,
                remaining,
            )
            _schedule_structure_rate_limit_pause(sleep_for)
            if attempt >= max_attempts:
                break
            continue

        return response

    return response


def reset_forbidden_structure_lookup_cache() -> None:
    """Clear the in-memory cache of characters forbidden from structure lookups."""

    _FORBIDDEN_STRUCTURE_CHARACTERS.clear()


def _is_structure_character_forbidden(character_id: int | None) -> bool:
    if not character_id:
        return False
    try:
        return int(character_id) in _FORBIDDEN_STRUCTURE_CHARACTERS
    except (TypeError, ValueError):  # pragma: no cover - defensive parsing
        logger.debug(
            "Unable to coerce character id %s while checking forbidden cache",
            character_id,
        )
        return False


def _mark_structure_character_forbidden(character_id: int | None) -> None:
    if not character_id:
        return
    try:
        _FORBIDDEN_STRUCTURE_CHARACTERS.add(int(character_id))
    except (TypeError, ValueError):  # pragma: no cover - defensive parsing
        logger.debug("Unable to record forbidden character id %s", character_id)


def is_station_id(location_id: int | None) -> bool:
    """Return True when the identifier belongs to an NPC station."""

    if location_id is None:
        return False

    try:
        return int(location_id) < _STATION_ID_MAX
    except (TypeError, ValueError):  # pragma: no cover - defensive parsing
        logger.debug("Unable to coerce %s into an integer station id", location_id)
        return False


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


def get_corporation_name(corporation_id: int | None) -> str:
    """Return the display name for a corporation."""

    if not corporation_id:
        return ""

    try:
        corp_id = int(corporation_id)
    except (TypeError, ValueError):
        logger.debug("Unable to coerce corporation id %s", corporation_id)
        return str(corporation_id)

    if corp_id in _CORP_NAME_CACHE:
        return _CORP_NAME_CACHE[corp_id]

    try:
        corp = EveCorporationInfo.objects.only("corporation_name").get(
            corporation_id=corp_id
        )
        name = corp.corporation_name
    except AppRegistryNotReady:
        logger.debug("Corporation %s not available (app registry not ready)", corp_id)
        name = str(corp_id)
    except EveCorporationInfo.DoesNotExist:
        record = (
            EveCharacter.objects.filter(corporation_id=corp_id)
            .values("corporation_name")
            .order_by("corporation_name")
            .first()
        )
        if record and record.get("corporation_name"):
            name = record["corporation_name"]
        else:
            logger.debug(
                "Corporation %s missing from EveCorporationInfo and EveCharacter cache",
                corp_id,
            )
            name = str(corp_id)

    _CORP_NAME_CACHE[corp_id] = name
    return name


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


def _get_owner_structure_token_ids(owner_user_id: int | None) -> list[int]:
    if not owner_user_id:
        return []

    owner_user_id = int(owner_user_id)
    if owner_user_id in _OWNER_STRUCTURE_TOKEN_CACHE:
        return _OWNER_STRUCTURE_TOKEN_CACHE[owner_user_id]

    try:
        qs = (
            Token.objects.filter(user_id=owner_user_id)
            .require_scopes(_STRUCTURE_SCOPE)
            .values_list("character_id", flat=True)
            .distinct()
        )
        token_ids = [int(char_id) for char_id in qs]
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug(
            "Unable to load owner structure tokens for user %s",
            owner_user_id,
            exc_info=True,
        )
        token_ids = []

    _OWNER_STRUCTURE_TOKEN_CACHE[owner_user_id] = token_ids
    return token_ids


def _invalidate_owner_structure_tokens(owner_user_id: int | None) -> None:
    if not owner_user_id:
        return
    owner_user_id = int(owner_user_id)
    _OWNER_STRUCTURE_TOKEN_CACHE.pop(owner_user_id, None)


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
    owner_user_id: int | None = None,
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
    is_station = is_station_id(structure_id)

    attempted_characters: set[int] = set()
    remaining_attempts = _MAX_STRUCTURE_LOOKUPS

    def try_structure_lookup(
        candidate_character_id: int | None,
        *,
        invalidate_owner: bool = False,
        invalidate_fallback: bool = False,
    ) -> str | None:
        nonlocal remaining_attempts
        if not candidate_character_id or remaining_attempts <= 0:
            return None

        candidate_character_id = int(candidate_character_id)
        if _is_structure_character_forbidden(candidate_character_id):
            logger.debug(
                "Skipping structure lookup for character %s due to prior 403",
                candidate_character_id,
            )
            return None
        if candidate_character_id in attempted_characters:
            return None

        attempted_characters.add(candidate_character_id)
        remaining_attempts -= 1

        _wait_for_structure_rate_limit_window()

        try:
            return shared_client.fetch_structure_name(
                structure_id, candidate_character_id
            )
        except ESIForbiddenError:
            _mark_structure_character_forbidden(candidate_character_id)
            logger.info(
                "Character %s forbidden from fetching structure %s; future attempts will be skipped",
                candidate_character_id,
                structure_id,
            )
            if invalidate_owner:
                _invalidate_owner_structure_tokens(owner_user_id)
            if invalidate_fallback:
                _invalidate_structure_scope_token_cache()
            return None
        except ESITokenError:
            logger.debug(
                "Character %s lacks esi-universe.read_structures scope for %s",
                candidate_character_id,
                structure_id,
            )
            if invalidate_owner:
                _invalidate_owner_structure_tokens(owner_user_id)
            if invalidate_fallback:
                _invalidate_structure_scope_token_cache()
            return None
        except ESIRateLimitError as exc:
            pause = exc.retry_after or shared_client.backoff_factor * (
                2 ** max(len(attempted_characters) - 1, 0)
            )
            _schedule_structure_rate_limit_pause(pause)
            logger.warning(
                "ESI rate limit reached while fetching structure %s via %s (remaining=%s). Pausing for %.1fs",
                structure_id,
                candidate_character_id,
                exc.remaining,
                pause,
            )
            return None
        except ESIClientError as exc:  # pragma: no cover - defensive fallback
            logger.debug(
                "Authenticated structure lookup failed for %s via %s: %s",
                structure_id,
                candidate_character_id,
                exc,
            )
            return None

    if not is_station:
        name = try_structure_lookup(character_id)

        if not name and owner_user_id:
            for owner_character_id in _get_owner_structure_token_ids(owner_user_id):
                if remaining_attempts <= 0:
                    break
                result = try_structure_lookup(owner_character_id, invalidate_owner=True)
                if result:
                    name = result
                    break

        if not name and remaining_attempts > 0:
            for fallback_character_id in _get_structure_scope_token_ids():
                if fallback_character_id == character_id:
                    continue
                if remaining_attempts <= 0:
                    break
                result = try_structure_lookup(
                    fallback_character_id, invalidate_fallback=True
                )
                if result:
                    name = result
                    break

    params = {"datasource": "tranquility"}

    if not name and not is_station:
        response = _rate_limited_public_get(
            f"{ESI_BASE_URL}/universe/structures/{structure_id}/",
            params=params,
        )
        if response is not None and response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            name = payload.get("name")
        elif response is not None and response.status_code == 404:
            logger.debug("Structure %s not found via public ESI", structure_id)

    if not name:
        response = _rate_limited_public_get(
            f"{ESI_BASE_URL}/universe/stations/{structure_id}/",
            params=params,
        )
        if response is not None and response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            name = payload.get("name")

    if not name:
        name = placeholder_value

    _LOCATION_NAME_CACHE[structure_id] = name
    return name
