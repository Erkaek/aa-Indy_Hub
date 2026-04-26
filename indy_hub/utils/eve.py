"""Helper utilities for retrieving EVE Online metadata."""

from __future__ import annotations

# Standard Library
import time
from collections.abc import Iterable, Mapping
from datetime import timedelta
from uuid import uuid4

# Third Party
from bravado.exception import HTTPError

# Django
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import AppRegistryNotReady
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

from ..services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    rate_limit_wait_seconds,
    shared_client,
)
from ..services.providers import esi_provider


def _is_eve_sde_installed() -> bool:
    installed_apps = getattr(settings, "INSTALLED_APPS", ())
    return any(
        app == "eve_sde" or str(app).startswith("eve_sde.") for app in installed_apps
    )


if (
    getattr(settings, "configured", False) and _is_eve_sde_installed()
):  # pragma: no branch
    try:  # pragma: no cover - eve_sde is optional in unit tests
        # Alliance Auth (External Libs)
        from eve_sde.models import ItemType as EveType
    except ImportError:  # pragma: no cover - fallback when eve_sde is not installed
        EveType = None

    try:
        from ..models import SDEBlueprintActivityProduct as EveIndustryActivityProduct
    except ImportError:  # pragma: no cover - model import can fail during app loading
        EveIndustryActivityProduct = None
else:  # pragma: no cover - eve_sde app not installed
    EveType = None
    EveIndustryActivityProduct = None

logger = get_extension_logger(__name__)

_TYPE_NAME_CACHE: dict[int, str] = {}
_CHAR_NAME_CACHE: dict[int, str] = {}
_CORP_NAME_CACHE: dict[int, str] = {}
_CORP_TICKER_CACHE: dict[int, str] = {}
_BP_PRODUCT_CACHE: dict[int, int | None] = {}
_REACTION_CACHE: dict[int, bool] = {}
_LOCATION_NAME_CACHE: dict[int, str] = {}
PLACEHOLDER_PREFIX = "Structure "
_STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
_FALLBACK_STRUCTURE_TOKEN_IDS: list[int] | None = None
_OWNER_STRUCTURE_TOKEN_CACHE: dict[int, list[int]] = {}
_STATION_ID_MAX = 100_000_000
_MAX_STRUCTURE_LOOKUPS = 3
_STRUCTURE_LOOKUP_PAUSE_UNTIL: float = 0.0
STRUCTURE_FORBIDDEN_RETRY_DELAY = timedelta(days=15)
STRUCTURE_TRIED_CHARACTER_RETRY_DELAY = timedelta(days=7)
_STRUCTURE_FORBIDDEN_COOLDOWN_CACHE_NAMESPACE = uuid4().hex
_STRUCTURE_TRIED_CHARACTER_CACHE_NAMESPACE = uuid4().hex


def _normalized_location_aliases(
    location_id: int | None, *, max_depth: int = 3
) -> tuple[int, ...]:
    """Return ordered alias IDs that may refer to the same physical location.

    This helps bridge ESI inconsistencies where a location may be represented as:
    - the canonical station / structure ID
    - a signed / unsigned int64 variant
    - an office-folder or container item ID seen in cached assets
    """

    if not location_id:
        return ()

    try:
        root_id = int(location_id)
    except (TypeError, ValueError):
        return ()

    aliases: list[int] = []
    seen: set[int] = set()

    def _push(candidate: int | None, *, frontier: list[int] | None = None) -> None:
        if candidate is None:
            return
        try:
            normalized = int(candidate)
        except (TypeError, ValueError):
            return
        if normalized == 0 or normalized in seen:
            return
        seen.add(normalized)
        aliases.append(normalized)
        if frontier is not None:
            frontier.append(normalized)

    frontier: list[int] = []
    _push(root_id, frontier=frontier)

    if root_id > 0 and root_id > 9_223_372_036_854_775_807:
        _push(root_id - 18_446_744_073_709_551_616, frontier=frontier)
    elif root_id < 0:
        _push(root_id + 18_446_744_073_709_551_616, frontier=frontier)

    try:
        cached_corp_asset_model = apps.get_model("indy_hub", "CachedCorporationAsset")
    except Exception:
        cached_corp_asset_model = None

    try:
        cached_char_asset_model = apps.get_model("indy_hub", "CachedCharacterAsset")
    except Exception:
        cached_char_asset_model = None

    for _depth in range(max(int(max_depth), 0)):
        if not frontier:
            break

        current_frontier = frontier
        frontier = []

        for current_id in current_frontier:
            if cached_corp_asset_model is not None:
                try:
                    corp_locations = (
                        cached_corp_asset_model.objects.filter(item_id=current_id)
                        .exclude(location_id__isnull=True)
                        .values_list("location_id", flat=True)
                        .distinct()
                    )
                    for related_id in corp_locations:
                        _push(related_id, frontier=frontier)
                except Exception:  # pragma: no cover - defensive fallback
                    logger.debug(
                        "Unable to inspect cached corporation asset aliases for %s",
                        current_id,
                        exc_info=True,
                    )

            if cached_char_asset_model is not None:
                try:
                    char_location_rows = (
                        cached_char_asset_model.objects.filter(item_id=current_id)
                        .exclude(location_id__isnull=True)
                        .values_list("location_id", flat=True)
                        .distinct()
                    )
                    for related_id in char_location_rows:
                        _push(related_id, frontier=frontier)

                    char_root_rows = (
                        cached_char_asset_model.objects.filter(
                            raw_location_id=current_id
                        )
                        .exclude(location_id__isnull=True)
                        .values_list("location_id", flat=True)
                        .distinct()
                    )
                    for related_id in char_root_rows:
                        _push(related_id, frontier=frontier)
                except Exception:  # pragma: no cover - defensive fallback
                    logger.debug(
                        "Unable to inspect cached character asset aliases for %s",
                        current_id,
                        exc_info=True,
                    )

    return tuple(aliases)


def _get_item_type_model():
    """Return eve_sde ItemType model when available, without relying only on import-time globals."""

    if EveType is not None:
        return EveType

    try:
        return apps.get_model("eve_sde", "ItemType")
    except Exception:
        return None


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


def _rate_limited_public_results(
    operation,
    *,
    description: str,
    max_attempts: int = 3,
):
    """Perform a public ESI operation honouring the shared rate limit pause."""

    last_response = None
    for attempt in range(1, max_attempts + 1):
        _wait_for_structure_rate_limit_window()

        try:
            if hasattr(operation, "request_config"):
                operation.request_config.also_return_response = True
            result = operation.results(use_etag=False)
            if isinstance(result, tuple) and len(result) == 2:
                payload, response = result
            else:
                payload, response = result, None
            return payload, response
        except HTTPError as exc:
            response = getattr(exc, "response", None)
            last_response = response
            status_code = getattr(exc, "status_code", None) or getattr(
                response, "status_code", None
            )
            if status_code == 420 and response is not None:
                sleep_for, remaining = rate_limit_wait_seconds(
                    response, shared_client.backoff_factor * (2 ** (attempt - 1))
                )
                logger.warning(
                    "ESI rate limit reached for %s (public), attempt %s/%s (remaining=%s).",
                    description,
                    attempt,
                    max_attempts,
                    remaining,
                )
                _schedule_structure_rate_limit_pause(sleep_for)
                if attempt >= max_attempts:
                    break
                continue
            return None, response
        except Exception as exc:  # pragma: no cover - defensive
            if attempt >= max_attempts:
                logger.debug(
                    "Public lookup %s failed on attempt %s/%s: %s",
                    description,
                    attempt,
                    max_attempts,
                    exc,
                )
                break
            sleep_for = shared_client.backoff_factor * (2 ** (attempt - 1))
            logger.warning(
                "Public lookup error for %s, retry %s/%s in %.1fs",
                description,
                attempt,
                max_attempts,
                sleep_for,
            )
            time.sleep(sleep_for)
            continue

    return None, last_response


def reset_forbidden_structure_lookup_cache() -> None:
    """Clear the cache of structure lookup attempts used to skip recent failures."""

    global _STRUCTURE_FORBIDDEN_COOLDOWN_CACHE_NAMESPACE
    global _STRUCTURE_TRIED_CHARACTER_CACHE_NAMESPACE
    _STRUCTURE_FORBIDDEN_COOLDOWN_CACHE_NAMESPACE = uuid4().hex
    _STRUCTURE_TRIED_CHARACTER_CACHE_NAMESPACE = uuid4().hex


def _build_structure_tried_character_cache_key(structure_id: int) -> str:
    return (
        "indy_hub:structure-tried-character:"
        f"{_STRUCTURE_TRIED_CHARACTER_CACHE_NAMESPACE}:{int(structure_id)}"
    )


def _get_structure_tried_characters(structure_id: int | None) -> set[int]:
    if not structure_id:
        return set()

    tried_characters: set[int] = set()
    alias_ids = _normalized_location_aliases(structure_id) or (int(structure_id),)
    for alias_id in alias_ids:
        cached = cache.get(
            _build_structure_tried_character_cache_key(int(alias_id)), []
        )
        for candidate in cached or []:
            try:
                tried_characters.add(int(candidate))
            except (TypeError, ValueError):  # pragma: no cover - defensive parsing
                logger.debug(
                    "Unable to coerce tried character id %s for structure %s",
                    candidate,
                    alias_id,
                )
    return tried_characters


def _is_structure_character_tried(
    structure_id: int | None, character_id: int | None
) -> bool:
    if not structure_id or not character_id:
        return False
    try:
        return int(character_id) in _get_structure_tried_characters(int(structure_id))
    except (TypeError, ValueError):  # pragma: no cover - defensive parsing
        logger.debug(
            "Unable to coerce character id %s while checking tried cache",
            character_id,
        )
        return False


def _mark_structure_character_tried(
    structure_id: int | None,
    character_id: int | None,
    *,
    duration: timedelta = STRUCTURE_TRIED_CHARACTER_RETRY_DELAY,
) -> None:
    if not structure_id or not character_id:
        return

    try:
        normalized_character_id = int(character_id)
        alias_ids = _normalized_location_aliases(structure_id) or (int(structure_id),)
        tried_characters = _get_structure_tried_characters(int(structure_id))
        tried_characters.add(normalized_character_id)
        timeout = max(int(duration.total_seconds()), 1)
        payload = sorted(tried_characters)
        for alias_id in alias_ids:
            cache.set(
                _build_structure_tried_character_cache_key(int(alias_id)),
                payload,
                timeout=timeout,
            )
    except (TypeError, ValueError):  # pragma: no cover - defensive parsing
        logger.debug(
            "Unable to record tried character id %s for structure %s",
            character_id,
            structure_id,
        )


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

    try:
        type_id = int(type_id)
    except (TypeError, ValueError):
        return str(type_id)

    if type_id in _TYPE_NAME_CACHE:
        cached_value = _TYPE_NAME_CACHE[type_id]
        if cached_value and cached_value != str(type_id):
            return cached_value

    item_type_model = _get_item_type_model()

    if item_type_model is None:
        value = str(type_id)
    else:
        try:
            value = item_type_model.objects.only("name").get(id=type_id).name
        except item_type_model.DoesNotExist:  # type: ignore[attr-defined]
            logger.debug(
                "EveType %s introuvable, retour de l'identifiant brut", type_id
            )
            value = str(type_id)

    if value != str(type_id):
        _TYPE_NAME_CACHE[type_id] = value
    else:
        _TYPE_NAME_CACHE.pop(type_id, None)
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


def get_corporation_ticker(corporation_id: int | None) -> str:
    """Return the ticker for a corporation, falling back to an empty string."""

    if not corporation_id:
        return ""

    try:
        corp_id = int(corporation_id)
    except (TypeError, ValueError):
        logger.debug(
            "Unable to coerce corporation id %s for ticker lookup", corporation_id
        )
        return ""

    if corp_id in _CORP_TICKER_CACHE:
        return _CORP_TICKER_CACHE[corp_id]

    ticker = ""

    try:
        corp = EveCorporationInfo.objects.only("corporation_ticker").get(
            corporation_id=corp_id
        )
        ticker = getattr(corp, "corporation_ticker", "") or ""
    except AppRegistryNotReady:
        logger.debug(
            "Corporation %s ticker not available (app registry not ready)", corp_id
        )
    except EveCorporationInfo.DoesNotExist:
        record = (
            EveCharacter.objects.filter(corporation_id=corp_id)
            .values("corporation_ticker")
            .order_by("corporation_ticker")
            .first()
        )
        if record:
            ticker = record.get("corporation_ticker", "") or ""

    _CORP_TICKER_CACHE[corp_id] = ticker
    return ticker


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

    item_type_model = _get_item_type_model()

    if item_type_model is None:
        return {pk: str(pk) for pk in ids}

    result: dict[int, str] = {}
    for eve_type in item_type_model.objects.filter(id__in=ids, published=True).only(
        "id", "name"
    ):
        _TYPE_NAME_CACHE[eve_type.id] = eve_type.name
        result[eve_type.id] = eve_type.name

    missing = ids - result.keys()
    for pk in missing:
        result[pk] = str(pk)

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
                eve_type_id=blueprint_type_id,
                eve_type__published=True,
                product_eve_type__published=True,
            )
            if qs.exists():
                product = qs.filter(activity_id=1).first() or qs.first()
                if product:
                    product_id = product.product_eve_type_id
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "Unable to resolve the product for blueprint %s via ESI Universe",
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
                eve_type_id=blueprint_type_id,
                activity_id__in=[9, 11],
                eve_type__published=True,
                product_eve_type__published=True,
            ).exists()
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "Unable to determine the activity for blueprint %s",
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

    alias_ids = _normalized_location_aliases(structure_id) or (int(structure_id),)

    # Prefer the shared persistent structure-name cache when available.
    # This allows different processes/workers to converge on the same resolved name,
    # and prevents returning a long-lived in-memory placeholder when DB already
    # contains the real name.
    try:
        cached_model = apps.get_model("indy_hub", "CachedStructureName")
    except Exception:
        cached_model = None

    if cached_model is not None:
        try:
            cached_rows = {
                int(row[0]): str(row[1])
                for row in cached_model.objects.filter(
                    structure_id__in=alias_ids
                ).values_list("structure_id", "name")
            }
        except Exception:  # pragma: no cover - defensive fallback
            cached_rows = {}

        for alias_id in alias_ids:
            cached_name = cached_rows.get(int(alias_id))
            if cached_name and not cached_name.startswith(PLACEHOLDER_PREFIX):
                return cached_name

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

        try:
            qs = (
                model.objects.filter(**{f"{id_field}__in": alias_ids})
                .exclude(**{f"{name_field}__isnull": True})
                .exclude(**{name_field: ""})
                .exclude(**{f"{name_field}__startswith": PLACEHOLDER_PREFIX})
            )
            existing_rows = {
                int(row[0]): str(row[1]) for row in qs.values_list(id_field, name_field)
            }
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug(
                "Unable to reuse stored location for %s via %s.%s",
                structure_id,
                app_label,
                model_name,
                exc_info=True,
            )
            existing_rows = {}

        for alias_id in alias_ids:
            existing = existing_rows.get(int(alias_id))
            if existing:
                return str(existing)

    return None


def build_structure_forbidden_cooldown_cache_key(structure_id: int) -> str:
    return (
        "indy_hub:structure-forbidden:"
        f"{_STRUCTURE_FORBIDDEN_COOLDOWN_CACHE_NAMESPACE}:{int(structure_id)}"
    )


def has_structure_forbidden_cooldown(structure_id: int | None) -> bool:
    if not structure_id:
        return False

    alias_ids = _normalized_location_aliases(structure_id) or (int(structure_id),)
    for alias_id in alias_ids:
        if cache.get(
            build_structure_forbidden_cooldown_cache_key(int(alias_id)), False
        ):
            return True
    return False


def set_structure_forbidden_cooldown(
    structure_id: int | None,
    *,
    duration: timedelta = STRUCTURE_FORBIDDEN_RETRY_DELAY,
) -> None:
    if not structure_id:
        return

    timeout = max(int(duration.total_seconds()), 1)
    cache.set(
        build_structure_forbidden_cooldown_cache_key(int(structure_id)),
        True,
        timeout=timeout,
    )


def _store_location_name_in_db(
    structure_id: int,
    name: str,
) -> None:
    """Persist a resolved/placeholder location name into CachedStructureName."""

    try:
        cached_model = apps.get_model("indy_hub", "CachedStructureName")
    except Exception:
        return

    if cached_model is None:
        return

    try:
        existing = (
            cached_model.objects.filter(structure_id=int(structure_id))
            .values_list("name", flat=True)
            .first()
        )
    except Exception:  # pragma: no cover - defensive fallback
        existing = None

    if existing is not None and str(existing) == str(name):
        return

    try:
        cached_model.objects.update_or_create(
            structure_id=int(structure_id),
            defaults={
                "name": str(name),
                "last_resolved": timezone.now(),
            },
        )
    except Exception:  # pragma: no cover - defensive fallback
        logger.debug(
            "Unable to persist structure name for %s",
            structure_id,
            exc_info=True,
        )


def resolve_location_name(
    structure_id: int | None,
    *,
    character_id: int | None = None,
    owner_user_id: int | None = None,
    force_refresh: bool = False,
    allow_public: bool = True,
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
    if cached is not None and cached != placeholder_value:
        _store_location_name_in_db(structure_id, cached)
        return cached

    if has_structure_forbidden_cooldown(structure_id):
        db_name = _lookup_location_name_in_db(structure_id)
        if not db_name:
            _LOCATION_NAME_CACHE[structure_id] = placeholder_value
            _store_location_name_in_db(structure_id, placeholder_value)
            return placeholder_value

    # If we have a cached placeholder but aren't forcing a refresh, still allow
    # cheap DB cache reuse to replace the placeholder (no ESI calls).
    if cached == placeholder_value and not force_refresh:
        db_name = _lookup_location_name_in_db(structure_id)
        if db_name:
            _LOCATION_NAME_CACHE[structure_id] = db_name
            _store_location_name_in_db(structure_id, db_name)
            return db_name
        _store_location_name_in_db(structure_id, cached)
        return cached

    if not force_refresh:
        db_name = _lookup_location_name_in_db(structure_id)
        if db_name:
            _LOCATION_NAME_CACHE[structure_id] = db_name
            _store_location_name_in_db(structure_id, db_name)
            return db_name

    name: str | None = None
    is_station = is_station_id(structure_id)
    attempted_characters: set[int] = set()
    remaining_attempts = _MAX_STRUCTURE_LOOKUPS

    def _extract_public_name(payload) -> str | None:
        if isinstance(payload, list):
            if not payload:
                return None
            payload = payload[0]
        if isinstance(payload, Mapping):
            value = payload.get("name")
            return str(value) if value else None
        if payload is not None:
            value = getattr(payload, "name", None)
            return str(value) if value else None
        return None

    def _get_universe_operation(op_name: str, **kwargs):
        universe_client = getattr(esi_provider.client, "Universe", None)
        if universe_client is None:
            return None
        operation = getattr(universe_client, op_name, None)
        if operation is None:
            camel = "".join(part.capitalize() for part in op_name.split("_"))
            operation = getattr(universe_client, camel, None)
        if operation is None:
            return None
        try:
            return operation(**kwargs)
        except TypeError:
            return None

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
        if _is_structure_character_tried(structure_id, candidate_character_id):
            logger.debug(
                "Skipping structure lookup for character %s on structure %s due to a recent failed attempt",
                candidate_character_id,
                structure_id,
            )
            return None
        if candidate_character_id in attempted_characters:
            return None

        attempted_characters.add(candidate_character_id)
        remaining_attempts -= 1

        _wait_for_structure_rate_limit_window()

        try:
            name = shared_client.fetch_structure_name(
                structure_id, candidate_character_id
            )
            if not name:
                _mark_structure_character_tried(structure_id, candidate_character_id)
            return name
        except ESIForbiddenError:
            _mark_structure_character_tried(structure_id, candidate_character_id)
            set_structure_forbidden_cooldown(structure_id)
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
            _mark_structure_character_tried(structure_id, candidate_character_id)
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

    if not is_station and structure_id > 2_147_483_647:
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

    if allow_public and not name:
        if not is_station:
            if structure_id <= 2_147_483_647:
                public_names = shared_client.resolve_ids_to_names([structure_id])
                name = public_names.get(structure_id)
        else:
            operation = _get_universe_operation(
                "get_universe_stations_station_id",
                station_id=structure_id,
            )
            if operation is not None:
                payload, response = _rate_limited_public_results(
                    operation,
                    description=f"/universe/stations/{structure_id}/",
                )
                name = _extract_public_name(payload)

    if not name:
        name = placeholder_value

    _LOCATION_NAME_CACHE[structure_id] = name
    _store_location_name_in_db(structure_id, name)
    return name
