# Asynchronous tasks for the industry module (example)
# Copy industry-related tasks that were previously in tasks.py here.
# Place any industry-specific asynchronous tasks from tasks.py here when needed.

# Standard Library
import random
import time
from datetime import datetime, timedelta

# Third Party
from celery import shared_task

try:
    try:
        # Alliance Auth
        from esi.decorators import rate_limit_retry_task, wait_for_esi_errorlimit_reset
    except ImportError:  # pragma: no cover - older django-esi

        def rate_limit_retry_task(func):
            return func

        def wait_for_esi_errorlimit_reset(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

except ImportError:  # pragma: no cover - older django-esi

    def rate_limit_retry_task(func):
        return func

    def wait_for_esi_errorlimit_reset(*args, **kwargs):
        def decorator(func):
            return func

        return decorator


# Django
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.utils import OperationalError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger
from esi.models import Token

try:
    # Alliance Auth
    from esi.exceptions import HTTPNotModified
except ImportError:  # pragma: no cover - older django-esi
    HTTPNotModified = None

from ..app_settings import (
    BLUEPRINTS_BULK_WINDOW_MINUTES,
    BULK_UPDATE_WINDOW_MINUTES,
    ESI_TASK_STAGGER_THRESHOLD,
    ESI_TASK_TARGET_PER_MIN_BLUEPRINTS,
    ESI_TASK_TARGET_PER_MIN_JOBS,
    ESI_TASK_TARGET_PER_MIN_ROLES,
    ESI_TASK_TARGET_PER_MIN_SKILLS,
    INDUSTRY_JOBS_BULK_WINDOW_MINUTES,
    LOCATION_LOOKUP_BUDGET,
    MANUAL_REFRESH_COOLDOWN_SECONDS,
)
from ..models import (
    Blueprint,
    CharacterOnlineStatus,
    CharacterRoles,
    CharacterSettings,
    CorporationSharingSetting,
    IndustryJob,
    IndustrySkillSnapshot,
)
from ..services.esi_client import (
    ESIClientError,
    ESIForbiddenError,
    ESIRateLimitError,
    ESITokenError,
    ESIUnmodifiedError,
    get_retry_after_seconds,
    shared_client,
)
from ..services.location_population import populate_location_names
from ..utils.eve import (
    PLACEHOLDER_PREFIX,
    batch_cache_type_names,
    get_character_name,
    get_corporation_name,
    get_type_name,
    resolve_location_name,
)

logger = get_extension_logger(__name__)

BLUEPRINT_SCOPE = "esi-characters.read_blueprints.v1"
JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
CORP_BLUEPRINT_SCOPE = "esi-corporations.read_blueprints.v1"
CORP_JOBS_SCOPE = "esi-industry.read_corporation_jobs.v1"
CORP_ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"
CORP_STRUCTURES_SCOPE = "esi-corporations.read_structures.v1"
CORP_ASSETS_SCOPE = "esi-assets.read_corporation_assets.v1"
CORP_WALLET_SCOPE = "esi-wallet.read_corporation_wallets.v1"
SKILLS_SCOPE = "esi-skills.read_skills.v1"
ONLINE_SCOPE = "esi-location.read_online.v1"
SKILL_TYPE_IDS = {
    "mass_production": 3387,
    "advanced_mass_production": 3388,
    "laboratory_operation": 3406,
    "advanced_laboratory_operation": 24624,
    "mass_reactions": 45746,
    "advanced_mass_reactions": 45748,
}
CORP_BLUEPRINT_SCOPE_SET = [
    CORP_BLUEPRINT_SCOPE,
    STRUCTURE_SCOPE,
    CORP_ROLES_SCOPE,
    CORP_STRUCTURES_SCOPE,
    CORP_ASSETS_SCOPE,
]
CORP_JOBS_SCOPE_SET = [
    CORP_JOBS_SCOPE,
    STRUCTURE_SCOPE,
    CORP_ROLES_SCOPE,
    CORP_STRUCTURES_SCOPE,
    CORP_ASSETS_SCOPE,
]
MATERIAL_EXCHANGE_SCOPE_SET = [
    STRUCTURE_SCOPE,
    CORP_ROLES_SCOPE,
    CORP_STRUCTURES_SCOPE,
    CORP_ASSETS_SCOPE,
    "esi-corporations.read_divisions.v1",
    "esi-contracts.read_corporation_contracts.v1",
]


def _is_deadlock_error(exc: Exception) -> bool:
    if getattr(exc, "args", None):
        code = exc.args[0]
        if code == 1213:
            return True
    return "Deadlock found" in str(exc)


def _is_user_active(user: User, *, now: datetime | None = None) -> bool:
    if not user:
        return False
    now = now or timezone.now()
    cutoff = now - timedelta(days=ACTIVE_USER_DAYS)
    active_char_ids = list(
        CharacterOnlineStatus.objects.filter(
            owner_user=user,
            last_login__isnull=False,
            last_login__gte=cutoff,
        ).values_list("character_id", flat=True)
    )
    if not active_char_ids:
        return False
    return (
        Token.objects.filter(user=user, character_id__in=active_char_ids)
        .require_scopes([ONLINE_SCOPE])
        .require_valid()
        .exists()
    )


def _coerce_online_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_datetime(value)
        if dt is None:
            return None
    else:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _refresh_online_status_for_user(user: User, *, now: datetime | None = None) -> None:
    now = now or timezone.now()
    ownerships = CharacterOwnership.objects.filter(user=user).select_related(
        "character"
    )
    for ownership in ownerships:
        char_id = ownership.character.character_id
        if not char_id:
            continue
        token = (
            Token.objects.filter(user=user, character_id=int(char_id))
            .require_scopes([ONLINE_SCOPE])
            .require_valid()
            .order_by("-created")
            .first()
        )
        if not token:
            continue

        try:
            payload = shared_client.fetch_character_online_status(int(char_id))
        except ESIUnmodifiedError:
            CharacterOnlineStatus.objects.filter(character_id=int(char_id)).update(
                owner_user=user,
                last_updated=now,
            )
            continue
        except (
            ESITokenError,
            ESIForbiddenError,
            ESIRateLimitError,
            ESIClientError,
        ) as exc:
            logger.warning(
                "Failed to refresh online status for character %s: %s",
                char_id,
                exc,
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Unexpected error refreshing online status for character %s: %s",
                char_id,
                exc,
            )
            continue

        if not isinstance(payload, dict):
            logger.warning(
                "Unexpected online status payload for character %s: %s",
                char_id,
                type(payload),
            )
            continue

        CharacterOnlineStatus.objects.update_or_create(
            character_id=int(char_id),
            defaults={
                "owner_user": user,
                "online": bool(payload.get("online")),
                "last_login": _coerce_online_datetime(payload.get("last_login")),
                "last_logout": _coerce_online_datetime(payload.get("last_logout")),
                "logins": payload.get("logins"),
            },
        )


def _user_recent_blueprint_sync(user: User, *, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    return Blueprint.objects.filter(
        owner_user=user, last_updated__gte=now - BLUEPRINT_REFRESH_MIN_AGE
    ).exists()


def _user_recent_job_sync(user: User, *, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    return IndustryJob.objects.filter(
        owner_user=user, last_updated__gte=now - JOB_REFRESH_MIN_AGE
    ).exists()


def _update_or_create_with_deadlock_retry(
    model,
    *,
    lookup: dict[str, object],
    defaults: dict[str, object],
    max_attempts: int = 3,
) -> tuple[object, bool]:
    for attempt in range(1, max_attempts + 1):
        try:
            return model.objects.update_or_create(**lookup, defaults=defaults)
        except OperationalError as exc:
            if not _is_deadlock_error(exc) or attempt >= max_attempts:
                raise
            delay = 0.2 * attempt + random.random() * 0.2
            logger.warning(
                "Deadlock while writing %s; retrying (%s/%s) in %.2fs",
                model.__name__,
                attempt,
                max_attempts,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable: deadlock retry loop exhausted")


MANUAL_REFRESH_KIND_BLUEPRINTS = "blueprints"
MANUAL_REFRESH_KIND_JOBS = "jobs"

_MANUAL_REFRESH_CACHE_PREFIX = "indy_hub:manual_refresh"
_DEFAULT_BULK_WINDOWS = {
    MANUAL_REFRESH_KIND_BLUEPRINTS: BLUEPRINTS_BULK_WINDOW_MINUTES,
    MANUAL_REFRESH_KIND_JOBS: INDUSTRY_JOBS_BULK_WINDOW_MINUTES,
}

ACTIVE_USER_DAYS = 30
BLUEPRINT_REFRESH_MIN_AGE = timedelta(hours=1)
JOB_REFRESH_MIN_AGE = timedelta(hours=2)
SKILL_REFRESH_MIN_AGE = timedelta(days=1)

_OPTIONAL_CORPORATION_SCOPES = {STRUCTURE_SCOPE}
REQUIRED_CORPORATION_ROLES = {"DIRECTOR", "FACTORY_MANAGER"}
_CORPORATION_ROLE_CACHE: dict[int, set[str]] = {}
_SKILLS_OPERATION_UNAVAILABLE = False


def _normalized_roles(roles: list[str] | tuple[str, ...] | None) -> set[str]:
    if not roles:
        return set()
    return {str(role).upper() for role in roles if role}


def _coerce_mapping(payload: object) -> dict:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    for attr_name in ("model_dump", "dict", "to_dict"):
        func = getattr(payload, attr_name, None)
        if callable(func):
            try:
                data = func()
            except TypeError:
                data = func
            if isinstance(data, dict):
                return data
    try:
        return dict(payload)
    except Exception:
        return {}


def _fetch_character_skill_levels_with_token(
    token_obj: Token,
    *,
    force_refresh: bool = False,
) -> dict[int, int]:
    global _SKILLS_OPERATION_UNAVAILABLE
    client = shared_client.client
    skills_resource = getattr(client, "Skills", None)
    operation_fn = None
    if skills_resource is not None:
        operation_fn = getattr(
            skills_resource,
            "get_characters_character_id_skills",
            None,
        ) or getattr(skills_resource, "GetCharactersCharacterIdSkills", None)

    if not operation_fn:
        character_resource = client.Character
        operation_fn = getattr(
            character_resource,
            "get_characters_character_id_skills",
            None,
        ) or getattr(character_resource, "GetCharactersCharacterIdSkills", None)

    if not operation_fn:
        _SKILLS_OPERATION_UNAVAILABLE = True
        raise ESIClientError("ESI skills operation unavailable")

    try:
        request_kwargs = {}
        if force_refresh:
            request_kwargs["If-None-Match"] = ""
        payload = operation_fn(
            character_id=token_obj.character_id,
            token=token_obj,
            **request_kwargs,
        ).results()
    except HTTPNotModified as exc:
        raise ESIUnmodifiedError("ESI skills not modified") from exc
    except Exception as exc:
        if "GetCharactersCharacterIdSkills" in str(exc):
            _SKILLS_OPERATION_UNAVAILABLE = True
            raise ESIClientError("ESI skills operation unavailable") from exc
        if "is not of type 'string'" not in str(exc):
            raise
        access_token = token_obj.valid_access_token()
        request_kwargs = {}
        if force_refresh:
            request_kwargs["If-None-Match"] = ""
        try:
            payload = operation_fn(
                character_id=token_obj.character_id,
                token=access_token,
                **request_kwargs,
            ).results()
        except HTTPNotModified as nested_exc:
            raise ESIUnmodifiedError("ESI skills not modified") from nested_exc
        except Exception as nested_exc:
            if "GetCharactersCharacterIdSkills" in str(nested_exc):
                _SKILLS_OPERATION_UNAVAILABLE = True
                raise ESIClientError("ESI skills operation unavailable") from nested_exc
            raise

    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    payload = _coerce_mapping(payload)
    skills = payload.get("skills", []) if payload else []
    return {
        int(skill.get("skill_id")): int(
            skill.get("active_skill_level") or skill.get("trained_skill_level") or 0
        )
        for skill in skills
        if skill.get("skill_id")
    }


def _extract_role_payload(payload: dict) -> dict[str, list[str]]:
    def _coerce_list(value: object) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item]
        return []

    return {
        "roles": _coerce_list(payload.get("roles")),
        "roles_at_hq": _coerce_list(payload.get("roles_at_hq")),
        "roles_at_base": _coerce_list(payload.get("roles_at_base")),
        "roles_at_other": _coerce_list(payload.get("roles_at_other")),
    }


def _roles_from_snapshot(snapshot: CharacterRoles) -> set[str]:
    collected: set[str] = set()
    for key in ("roles", "roles_at_hq", "roles_at_base", "roles_at_other"):
        collected.update(_normalized_roles(getattr(snapshot, key, None)))
    return collected


def get_character_corporation_roles(character_id: int) -> set[str]:
    if character_id in _CORPORATION_ROLE_CACHE:
        return _CORPORATION_ROLE_CACHE[character_id]

    table_empty = not CharacterRoles.objects.exists()
    snapshot = CharacterRoles.objects.filter(character_id=character_id).first()
    try:
        payload = shared_client.fetch_character_corporation_roles(
            int(character_id),
            force_refresh=table_empty or snapshot is None,
        )
    except ESIUnmodifiedError:
        cached = _CORPORATION_ROLE_CACHE.get(character_id)
        if cached is not None:
            return cached
        if snapshot:
            roles = _roles_from_snapshot(snapshot)
            _CORPORATION_ROLE_CACHE[character_id] = roles
            return roles
        logger.debug(
            "Corporation roles not modified for character %s; no cache available",
            character_id,
        )
        return set()
    except ESIRateLimitError as exc:
        delay = get_retry_after_seconds(exc)
        logger.warning(
            "ESI rate limit hit while fetching roles for character %s; using cached roles when available (retry in %ss): %s",
            character_id,
            delay,
            exc,
        )
        cached = _CORPORATION_ROLE_CACHE.get(character_id)
        if cached is not None:
            return cached
        if snapshot:
            roles = _roles_from_snapshot(snapshot)
            _CORPORATION_ROLE_CACHE[character_id] = roles
            return roles
        return set()
    except (ESITokenError, ESIForbiddenError, ESIClientError) as exc:
        logger.warning(
            "Failed to fetch corporation roles for character %s: %s",
            character_id,
            exc,
        )
        return set()  # Return empty set on error instead of crashing

    if not isinstance(payload, dict):
        logger.debug(
            "Unexpected corporation roles payload type for character %s: %s",
            character_id,
            type(payload),
        )
        return set()

    role_payload = _extract_role_payload(payload)
    ownership = (
        CharacterOwnership.objects.filter(character__character_id=character_id)
        .select_related("character", "user")
        .first()
    )
    if ownership:
        CharacterRoles.objects.update_or_create(
            character_id=character_id,
            defaults={
                "owner_user": ownership.user,
                "corporation_id": getattr(ownership.character, "corporation_id", None),
                **role_payload,
            },
        )

    collected: set[str] = set()
    for key in ("roles", "roles_at_hq", "roles_at_base", "roles_at_other"):
        collected.update(_normalized_roles(payload.get(key)))

    _CORPORATION_ROLE_CACHE[character_id] = collected
    return collected


def _collect_corporation_contexts(
    user: User, required_scopes: list[str]
) -> dict[int, dict[str, int | str]]:
    """Return mapping of corporation id to token context for the user."""

    contexts: dict[int, dict[str, int | str]] = {}

    ownerships = CharacterOwnership.objects.filter(user=user).select_related(
        "character"
    )

    # Optimize: Bulk load corp settings instead of querying in get_or_create loop
    corp_settings = {
        setting.corporation_id: setting
        for setting in CorporationSharingSetting.objects.filter(user=user)
    }

    # Collect corporation IDs from ownerships to bulk fetch missing settings
    corp_ids_in_ownerships = {
        getattr(ownership.character, "corporation_id", None) for ownership in ownerships
    }
    corp_ids_in_ownerships.discard(None)
    missing_corp_ids = corp_ids_in_ownerships - set(corp_settings.keys())

    # Bulk create missing settings
    if missing_corp_ids:
        new_settings = [
            CorporationSharingSetting(
                user=user,
                corporation_id=corp_id,
                corporation_name=get_corporation_name(corp_id) or str(corp_id),
                share_scope=CharacterSettings.SCOPE_NONE,
                allow_copy_requests=False,
            )
            for corp_id in missing_corp_ids
        ]
        created_settings = CorporationSharingSetting.objects.bulk_create(
            new_settings, ignore_conflicts=True
        )
        # Reload to get the created settings (in case of conflicts)
        for created_setting in created_settings:
            corp_settings[created_setting.corporation_id] = created_setting
        # Also fetch any existing conflicting settings
        for corp_id in missing_corp_ids:
            if corp_id not in corp_settings:
                try:
                    corp_settings[corp_id] = CorporationSharingSetting.objects.get(
                        user=user, corporation_id=corp_id
                    )
                except CorporationSharingSetting.DoesNotExist:
                    pass

    try:
        # Alliance Auth
        from esi.models import Token
    except ImportError:  # pragma: no cover - defensive fallback
        logger.debug("ESI Token model unavailable; skipping corp context collection")
        return contexts

    for ownership in ownerships:
        corp_id = getattr(ownership.character, "corporation_id", None)
        if not corp_id:
            continue

        setting = corp_settings.get(corp_id)

        char_id = ownership.character.character_id
        base_qs = (
            Token.objects.filter(character_id=char_id, user=user)
            .require_valid()
            .order_by("-created")
        )

        scope_groups: list[list[str]]
        if required_scopes:
            base_scopes = list(required_scopes)
            scope_groups = [base_scopes]
            for optional_scope in _OPTIONAL_CORPORATION_SCOPES:
                if optional_scope in base_scopes:
                    reduced = [
                        scope for scope in base_scopes if scope != optional_scope
                    ]
                    if reduced not in scope_groups:
                        scope_groups.append(reduced)
        else:
            scope_groups = [[]]

        token_qs = None
        scopes_used: list[str] | None = None
        for scopes in scope_groups:
            candidate_qs = base_qs
            if scopes:
                candidate_qs = candidate_qs.require_scopes(scopes)
            if candidate_qs.exists():
                token_qs = candidate_qs
                scopes_used = scopes
                break

        if token_qs is None:
            continue

        if (
            setting
            and setting.restricts_characters
            and not setting.is_character_authorized(char_id)
        ):
            logger.debug(
                "Character %s skipped for corporation %s: not whitelisted in Indy Hub",
                char_id,
                corp_id,
            )
            continue

        if scopes_used is not None and scopes_used != required_scopes:
            logger.debug(
                "Character %s uses fallback scopes %s for corporation %s",
                char_id,
                scopes_used,
                corp_id,
            )

        try:
            roles = get_character_corporation_roles(char_id)
        except ESITokenError:
            logger.info(
                "Character %s lacks the required corporation roles scope for corporation %s",
                char_id,
                corp_id,
            )
            continue
        except ESIClientError as exc:
            logger.warning(
                "Unable to load corporation roles for %s (%s); skipping corp %s",
                char_id,
                exc,
                corp_id,
            )
            continue

        if not roles.intersection(REQUIRED_CORPORATION_ROLES):
            logger.info(
                "Character %s does not have roles %s for corporation %s",
                char_id,
                ", ".join(sorted(REQUIRED_CORPORATION_ROLES)),
                corp_id,
            )
            continue

        contexts.setdefault(
            corp_id,
            {
                "character_id": char_id,
                "character_name": get_character_name(char_id),
                "corporation_name": get_corporation_name(corp_id),
            },
        )

    return contexts


def _get_manual_refresh_cooldown_seconds() -> int:
    return max(int(MANUAL_REFRESH_COOLDOWN_SECONDS), 0)


def _get_bulk_window_minutes(kind: str) -> int:
    fallback = _DEFAULT_BULK_WINDOWS.get(kind, 720)
    try:
        fallback = int(BULK_UPDATE_WINDOW_MINUTES)
    except (TypeError, ValueError):
        fallback = _DEFAULT_BULK_WINDOWS.get(kind, 720)

    specific = fallback
    if kind == MANUAL_REFRESH_KIND_BLUEPRINTS:
        specific = BLUEPRINTS_BULK_WINDOW_MINUTES
    elif kind == MANUAL_REFRESH_KIND_JOBS:
        specific = INDUSTRY_JOBS_BULK_WINDOW_MINUTES

    try:
        minutes = int(specific)
    except (TypeError, ValueError):
        minutes = fallback
    return max(minutes, 0)


def _get_target_per_min(kind: str) -> int:
    if kind == "blueprints":
        return int(ESI_TASK_TARGET_PER_MIN_BLUEPRINTS)
    if kind == "industry_jobs":
        return int(ESI_TASK_TARGET_PER_MIN_JOBS)
    if kind == "skills":
        return int(ESI_TASK_TARGET_PER_MIN_SKILLS)
    if kind == "roles":
        return int(ESI_TASK_TARGET_PER_MIN_ROLES)
    return 0


def _get_adaptive_window_minutes(kind: str, total: int) -> int:
    if total <= 0:
        return 0
    threshold = int(ESI_TASK_STAGGER_THRESHOLD)
    target_per_min = _get_target_per_min(kind)
    if threshold > 0 and total <= threshold:
        return 0
    if target_per_min <= 0:
        return _get_bulk_window_minutes(kind)
    return max((total + target_per_min - 1) // target_per_min, 1)


def _manual_refresh_cache_key(kind: str, user_id: int, scope: str | None = None) -> str:
    scope_key = (scope or "").lower() or "default"
    return f"{_MANUAL_REFRESH_CACHE_PREFIX}:{kind}:{user_id}:{scope_key}"


def _queue_staggered_user_tasks(
    task, user_ids: list[int], *, window_minutes: int, priority: int | None = None
) -> int:
    if not user_ids:
        return 0

    total = len(user_ids)
    window_seconds = max(window_minutes * 60, 0)

    if total == 1 or window_seconds == 0:
        for user_id in user_ids:
            task.apply_async(args=(user_id,), priority=priority)
        return total

    spacing = window_seconds / total
    for index, user_id in enumerate(user_ids):
        countdown = int(round(index * spacing))
        task.apply_async(args=(user_id,), countdown=countdown, priority=priority)
    return total


def _record_manual_refresh(kind: str, user_id: int, scope: str | None = None) -> None:
    if user_id is None:
        return
    cooldown = _get_manual_refresh_cooldown_seconds()
    if cooldown <= 0:
        return
    now = timezone.now()
    cache.set(
        _manual_refresh_cache_key(kind, user_id, scope),
        now.timestamp(),
        timeout=cooldown,
    )


def _remaining_manual_cooldown(
    kind: str, user_id: int, scope: str | None = None
) -> timedelta | None:
    cached = cache.get(_manual_refresh_cache_key(kind, user_id, scope))
    if cached is None:
        return None
    try:
        last_trigger = datetime.fromtimestamp(float(cached), tz=timezone.utc)
    except (TypeError, ValueError):
        return None
    cooldown = _get_manual_refresh_cooldown_seconds()
    if cooldown <= 0:
        return None
    expiry = last_trigger + timedelta(seconds=cooldown)
    remaining = expiry - timezone.now()
    if remaining.total_seconds() <= 0:
        return None
    return remaining


def manual_refresh_allowed(
    kind: str, user_id: int, scope: str | None = None
) -> tuple[bool, timedelta | None]:
    remaining = _remaining_manual_cooldown(kind, user_id, scope)
    if remaining is None:
        return True, None
    return False, remaining


def reset_manual_refresh_cooldown(
    kind: str, user_id: int, scope: str | None = None
) -> None:
    cache.delete(_manual_refresh_cache_key(kind, user_id, scope))


def queue_blueprint_update_for_user(
    user_id: int,
    *,
    countdown: int = 0,
    priority: int | None = None,
    scope: str | None = None,
) -> None:
    kwargs = {}
    if scope:
        kwargs["scope"] = scope
    update_blueprints_for_user.apply_async(
        args=(user_id,), kwargs=kwargs, countdown=countdown, priority=priority
    )


def queue_industry_job_update_for_user(
    user_id: int,
    *,
    countdown: int = 0,
    priority: int | None = None,
    scope: str | None = None,
) -> None:
    kwargs = {}
    if scope:
        kwargs["scope"] = scope
    update_industry_jobs_for_user.apply_async(
        args=(user_id,), kwargs=kwargs, countdown=countdown, priority=priority
    )


def request_manual_refresh(
    kind: str,
    user_id: int,
    *,
    priority: int | None = None,
    scope: str | None = None,
    check_active: bool = True,
) -> tuple[bool, timedelta | None]:
    allowed, remaining = manual_refresh_allowed(kind, user_id, scope)
    if not allowed:
        return False, remaining

    if check_active:
        user = User.objects.filter(id=user_id).first()
        if not user:
            return False, None
        _refresh_online_status_for_user(user)
        if not _is_user_active(user):
            return False, None

    if kind == MANUAL_REFRESH_KIND_BLUEPRINTS:
        queue_blueprint_update_for_user(user_id, priority=priority, scope=scope)
    elif kind == MANUAL_REFRESH_KIND_JOBS:
        queue_industry_job_update_for_user(user_id, priority=priority, scope=scope)
    else:
        raise ValueError(f"Unknown manual refresh kind: {kind}")

    _record_manual_refresh(kind, user_id, scope)
    return True, None


def _get_location_lookup_budget() -> int:
    try:
        value = int(LOCATION_LOOKUP_BUDGET)
    except (TypeError, ValueError):
        value = 50
    return max(value, 0)


def _coerce_job_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_datetime(value)
        if dt is None:
            return None
    else:
        return None

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


@shared_task(bind=True, max_retries=3)
@rate_limit_retry_task
def update_blueprints_for_user(
    self,
    user_id,
    scope: str | None = None,
    character_id: int | None = None,
    force_refresh: bool = False,
):
    base_scopes = [BLUEPRINT_SCOPE]
    scope_preferences = [
        base_scopes + [STRUCTURE_SCOPE],
        base_scopes,
    ]
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist as exc:  # pragma: no cover - defensive guard
        logger.warning("User %s not found during blueprint synchronization", user_id)
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))

    now = timezone.now()
    _refresh_online_status_for_user(user, now=now)
    if not _is_user_active(user, now=now):
        logger.info("Skipping blueprint sync for inactive user %s", user.username)
        return {"success": True, "skipped": "inactive_user"}

    if (
        not force_refresh
        and character_id is None
        and _user_recent_blueprint_sync(user, now=now)
    ):
        logger.info(
            "Skipping blueprint sync for %s: updated within last %s minutes",
            user.username,
            int(BLUEPRINT_REFRESH_MIN_AGE.total_seconds() / 60),
        )
        return {"success": True, "skipped": "recent_sync"}

    normalized_scope = (scope or "").strip().lower()
    if normalized_scope not in {"character", "corporation"}:
        normalized_scope = None

    scope_label = normalized_scope or "all"
    logger.info(
        "Blueprint synchronization for %s (scope=%s)",
        user.username,
        scope_label,
    )
    updated_count = 0
    deleted_total = 0
    error_messages: list[str] = []
    corp_contexts: dict[int, dict[str, int | str]] = {}

    process_characters = normalized_scope in {None, "character"}
    process_corporations = normalized_scope in {None, "corporation"}

    if process_corporations and user.has_perm("indy_hub.can_manage_corp_bp_requests"):
        corp_contexts = _collect_corporation_contexts(user, CORP_BLUEPRINT_SCOPE_SET)
        logger.info(
            "Corporation context detected for %s: %s",
            user.username,
            ", ".join(str(key) for key in corp_contexts.keys()) or "none",
        )
        if not corp_contexts:
            logger.debug(
                "No corporation context available for %s during a scope=%s synchronization",
                user.username,
                scope_label,
            )
    elif normalized_scope == "corporation":
        message = (
            "Skipped corporate blueprint synchronization for %s: missing permission"
            % user.username
        )
        logger.info(message)
        error_messages.append(message)

    ownerships = (
        CharacterOwnership.objects.filter(user=user) if process_characters else []
    )
    if character_id and process_characters:
        ownerships = ownerships.filter(character__character_id=int(character_id))
    for ownership in ownerships:
        char_id = ownership.character.character_id
        character_name = get_character_name(char_id)
        try:
            # Alliance Auth
            from esi.models import Token

            token_qs = (
                Token.objects.filter(character_id=char_id, user=user)
                .require_valid()
                .select_related("user")
                .order_by("-created")
            )
            chosen_scopes: list[str] | None = None
            for scope_set in scope_preferences:
                candidate_qs = token_qs
                if scope_set:
                    candidate_qs = candidate_qs.require_scopes(scope_set)
                if candidate_qs.exists():
                    chosen_scopes = scope_set
                    break

            if chosen_scopes is None:
                message = (
                    f"{character_name} ({char_id}) has no token for scopes "
                    f"{', '.join(base_scopes)}"
                )
                logger.debug(message)
                continue

            if STRUCTURE_SCOPE not in chosen_scopes:
                logger.debug(
                    "Blueprint synchronization for %s using a token without the structure scope",
                    character_name,
                )

            blueprints = shared_client.fetch_character_blueprints(char_id)
        except ESIUnmodifiedError:
            logger.debug(
                "Blueprints not modified for %s (%s); skipping sync",
                character_name,
                char_id,
            )
            continue
        except ESITokenError as exc:
            message = f"Invalid token for {character_name} ({char_id}): {exc}"
            logger.warning(message)
            error_messages.append(message)
            continue
        except ESIRateLimitError as exc:
            delay = get_retry_after_seconds(exc)
            message = f"ESI rate limit hit for {character_name} ({char_id}); retrying in {delay}s: {exc}"
            logger.warning(message)
            raise self.retry(countdown=delay)
        except (ESIForbiddenError, ESIClientError) as exc:
            message = f"ESI error for {character_name} ({char_id}): {exc}"
            logger.error(message)
            error_messages.append(message)
            continue
        except Exception as exc:  # pragma: no cover - unexpected
            message = f"Unexpected error for {character_name} ({char_id}): {exc}"
            logger.exception(message)
            error_messages.append(message)
            continue

        esi_ids = set()
        with transaction.atomic():
            for bp in blueprints:
                item_id = bp.get("item_id")
                if item_id is None:
                    logger.debug(
                        "Blueprint without item_id ignored for %s (%s)",
                        character_name,
                        bp,
                    )
                    continue
                esi_ids.add(item_id)
                location_id = bp.get("location_id")
                location_name = resolve_location_name(
                    location_id,
                    character_id=char_id,
                    owner_user_id=user.id,
                )
                Blueprint.objects.update_or_create(
                    item_id=item_id,
                    defaults={
                        "owner_user": user,
                        "owner_kind": Blueprint.OwnerKind.CHARACTER,
                        "corporation_id": None,
                        "corporation_name": "",
                        "character_id": char_id,
                        "blueprint_id": bp.get("blueprint_id"),
                        "type_id": bp.get("type_id"),
                        "location_id": location_id,
                        "location_name": location_name,
                        "location_flag": bp.get("location_flag", ""),
                        "quantity": bp.get("quantity"),
                        "time_efficiency": bp.get("time_efficiency", 0),
                        "material_efficiency": bp.get("material_efficiency", 0),
                        "runs": bp.get("runs", 0),
                        "character_name": character_name,
                        "type_name": get_type_name(bp.get("type_id")),
                    },
                )

            deleted, _ = (
                Blueprint.objects.filter(
                    owner_user=user,
                    owner_kind=Blueprint.OwnerKind.CHARACTER,
                    character_id=char_id,
                )
                .exclude(item_id__in=esi_ids)
                .delete()
            )
        deleted_total += deleted
        updated_count += len(blueprints)
        logger.debug(
            "Blueprint synchronization finished for %s (%s updated, %s deleted)",
            character_name,
            len(blueprints),
            deleted,
        )

    if process_corporations and corp_contexts:
        for corp_id, context in corp_contexts.items():
            corp_char_id = context.get("character_id")
            corp_name = context.get("corporation_name") or str(corp_id)
            acting_character_name = context.get("character_name") or ""

            if not corp_char_id:
                logger.debug(
                    "Incomplete context for corporation %s (missing character_id)",
                    corp_id,
                )
                continue

            try:
                corp_blueprints = shared_client.fetch_corporation_blueprints(
                    int(corp_id), character_id=int(corp_char_id)
                )
            except ESIUnmodifiedError:
                logger.debug(
                    "Corporate blueprints not modified for %s (%s); skipping sync",
                    corp_name,
                    corp_id,
                )
                continue
            except ESITokenError as exc:
                message = f"Invalid token for corporation {corp_name} ({corp_id}) via {acting_character_name}: {exc}"
                logger.warning(message)
                error_messages.append(message)
                continue
            except ESIRateLimitError as exc:
                delay = get_retry_after_seconds(exc)
                message = (
                    "ESI rate limit hit for corporation "
                    f"{corp_name} ({corp_id}) via {acting_character_name}; retrying in {delay}s: {exc}"
                )
                logger.warning(message)
                raise self.retry(countdown=delay)
            except (ESIForbiddenError, ESIClientError) as exc:
                message = f"ESI error for corporation {corp_name} ({corp_id}) via {acting_character_name}: {exc}"
                logger.error(message)
                error_messages.append(message)
                continue
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Unexpected error for corporation {corp_name} ({corp_id}) via {acting_character_name}: {exc}"
                logger.exception(message)
                error_messages.append(message)
                continue

            corp_esi_ids: set[int] = set()
            with transaction.atomic():
                for bp in corp_blueprints:
                    item_id = bp.get("item_id")
                    if item_id is None:
                        logger.debug(
                            "Corporate blueprint without item_id ignored for %s (%s)",
                            corp_name,
                            bp,
                        )
                        continue
                    corp_esi_ids.add(item_id)
                    location_id = bp.get("location_id")
                    location_name = resolve_location_name(
                        location_id,
                        character_id=int(corp_char_id),
                        owner_user_id=user.id,
                    )
                    Blueprint.objects.update_or_create(
                        item_id=item_id,
                        defaults={
                            "owner_user": user,
                            "owner_kind": Blueprint.OwnerKind.CORPORATION,
                            "corporation_id": corp_id,
                            "corporation_name": corp_name,
                            "character_id": None,
                            "character_name": acting_character_name,
                            "blueprint_id": bp.get("blueprint_id"),
                            "type_id": bp.get("type_id"),
                            "location_id": location_id,
                            "location_name": location_name,
                            "location_flag": bp.get("location_flag", ""),
                            "quantity": bp.get("quantity"),
                            "time_efficiency": bp.get("time_efficiency", 0),
                            "material_efficiency": bp.get("material_efficiency", 0),
                            "runs": bp.get("runs", 0),
                            "type_name": get_type_name(bp.get("type_id")),
                        },
                    )

                deleted, _ = (
                    Blueprint.objects.filter(
                        owner_kind=Blueprint.OwnerKind.CORPORATION,
                        corporation_id=corp_id,
                    )
                    .exclude(item_id__in=corp_esi_ids)
                    .delete()
                )

            updated_count += len(corp_blueprints)
            deleted_total += deleted
            logger.debug(
                "Corporate blueprint synchronization finished for %s (%s updated, %s deleted)",
                corp_name,
                len(corp_blueprints),
                deleted,
            )

    logger.info(
        "Blueprints synchronized for %s: %s updated, %s deleted",
        user.username,
        updated_count,
        deleted_total,
    )
    if error_messages:
        logger.warning(
            "Issues during blueprint synchronization %s: %s",
            user.username,
            "; ".join(error_messages),
        )

    return {
        "success": True,
        "blueprints_updated": updated_count,
        "deleted": deleted_total,
        "errors": error_messages,
    }


@shared_task(bind=True, max_retries=3)
@rate_limit_retry_task
def update_industry_jobs_for_user(
    self,
    user_id,
    scope: str | None = None,
    character_id: int | None = None,
    force_refresh: bool = False,
):
    try:
        user = User.objects.get(id=user_id)
        now = timezone.now()
        _refresh_online_status_for_user(user, now=now)
        if not _is_user_active(user, now=now):
            logger.info(
                "Skipping industry jobs sync for inactive user %s", user.username
            )
            return {"success": True, "skipped": "inactive_user"}

        if (
            not force_refresh
            and character_id is None
            and _user_recent_job_sync(user, now=now)
        ):
            logger.info(
                "Skipping industry jobs sync for %s: updated within last %s minutes",
                user.username,
                int(JOB_REFRESH_MIN_AGE.total_seconds() / 60),
            )
            return {"success": True, "skipped": "recent_sync"}
        logger.info("Starting industry jobs update for user %s", user.username)
        updated_count = 0
        deleted_total = 0
        error_messages: list[str] = []
        location_cache: dict[int, str] = {}
        lookup_budget = _get_location_lookup_budget()
        lookup_budget_warned = False
        ownerships = CharacterOwnership.objects.filter(user=user)
        base_scopes = [JOBS_SCOPE]
        scope_preferences = [
            base_scopes + [STRUCTURE_SCOPE],
            base_scopes,
        ]
        corp_contexts: dict[int, dict[str, int | str]] = {}

        normalized_scope = (scope or "").strip().lower()
        if normalized_scope not in {"character", "corporation"}:
            normalized_scope = None
        scope_label = normalized_scope or "all"
        logger.debug(
            "Industry jobs update for %s using scope=%s",
            user.username,
            scope_label,
        )

        process_characters = normalized_scope in {None, "character"}
        process_corporations = normalized_scope in {None, "corporation"}

        if not process_characters:
            ownerships = []
        elif character_id:
            ownerships = ownerships.filter(character__character_id=int(character_id))

        if process_corporations and user.has_perm(
            "indy_hub.can_manage_corp_bp_requests"
        ):
            corp_contexts = _collect_corporation_contexts(user, CORP_JOBS_SCOPE_SET)
            logger.info(
                "Detected corporation context (jobs) for %s: %s",
                user.username,
                ", ".join(str(key) for key in corp_contexts.keys()) or "none",
            )
            if not corp_contexts:
                logger.debug(
                    "No corporate contexts found for %s during scope=%s job sync",
                    user.username,
                    scope_label,
                )
        elif normalized_scope == "corporation":
            message = (
                "Corporate industry job sync skipped for %s due to missing permission"
                % user.username
            )
            logger.info(message)
            error_messages.append(message)

        for ownership in ownerships:
            char_id = ownership.character.character_id
            character_name = get_character_name(char_id)
            try:
                token_qs = (
                    Token.objects.filter(character_id=char_id, user=user)
                    .require_valid()
                    .order_by("-created")
                )

                chosen_scopes: list[str] | None = None
                for scope_set in scope_preferences:
                    candidate_qs = token_qs
                    if scope_set:
                        candidate_qs = candidate_qs.require_scopes(scope_set)
                    if candidate_qs.exists():
                        chosen_scopes = scope_set
                        break

                if chosen_scopes is None:
                    scope_list = ", ".join(base_scopes)
                    message = f"{character_name} ({char_id}) missing token for scopes {scope_list}"
                    logger.debug(message)
                    continue

                if STRUCTURE_SCOPE not in chosen_scopes:
                    logger.debug(
                        "Job synchronization for %s using token without structure scope",
                        character_name,
                    )

                jobs = shared_client.fetch_character_industry_jobs(char_id)
                if not isinstance(jobs, list):
                    payload_type = type(jobs).__name__
                    logger.warning(
                        "Skipping industry jobs for %s (%s): unexpected payload type %s",
                        character_name,
                        char_id,
                        payload_type,
                    )
                    error_messages.append(
                        f"Unexpected industry jobs payload type for {character_name} ({char_id}): {payload_type}"
                    )
                    continue
            except ESIUnmodifiedError:
                logger.debug(
                    "Industry jobs not modified for %s (%s); skipping sync",
                    character_name,
                    char_id,
                )
                continue
            except ESITokenError as exc:
                message = f"Invalid token for {character_name} ({char_id}): {exc}"
                logger.warning(message)
                error_messages.append(message)
                continue
            except ESIRateLimitError as exc:
                delay = get_retry_after_seconds(exc)
                message = f"ESI rate limit hit for {character_name} ({char_id}); retrying in {delay}s: {exc}"
                logger.warning(message)
                raise self.retry(countdown=delay)
            except (ESIForbiddenError, ESIClientError) as exc:
                message = f"ESI error for {character_name} ({char_id}): {exc}"
                logger.error(message)
                error_messages.append(message)
                continue
            except Exception as exc:  # pragma: no cover - unexpected
                message = f"Unexpected error for {character_name} ({char_id}): {exc}"
                logger.exception(message)
                error_messages.append(message)
                continue

            esi_job_ids = set()
            with transaction.atomic():
                for job in jobs:
                    if not isinstance(job, dict):
                        logger.warning(
                            "Skipping industry job with unexpected payload type for %s: %s (%r)",
                            character_name,
                            type(job).__name__,
                            job,
                        )
                        continue
                    job_id = job.get("job_id")
                    if job_id is None:
                        logger.debug(
                            "Skipping job without identifier for %s: %s",
                            character_name,
                            job,
                        )
                        continue
                    esi_job_ids.add(job_id)
                    station_id = job.get("station_id") or job.get("facility_id")
                    location_name = ""
                    if station_id is not None:
                        try:
                            location_key = int(station_id)
                        except (TypeError, ValueError):
                            location_key = None

                        if location_key is not None:
                            cached_name = location_cache.get(location_key)
                            if cached_name is not None:
                                location_name = cached_name
                            elif lookup_budget > 0:
                                try:
                                    resolved_name = resolve_location_name(
                                        location_key,
                                        character_id=char_id,
                                        owner_user_id=user.id,
                                    )
                                except (
                                    Exception
                                ):  # pragma: no cover - defensive fallback
                                    logger.debug(
                                        "Location resolution failed for %s via %s",
                                        location_key,
                                        character_name,
                                        exc_info=True,
                                    )
                                    resolved_name = None

                                lookup_budget -= 1
                                location_name = (
                                    resolved_name
                                    if resolved_name
                                    else f"{PLACEHOLDER_PREFIX}{location_key}"
                                )
                                location_cache[location_key] = location_name
                            else:
                                if not lookup_budget_warned:
                                    logger.warning(
                                        "Location lookup budget exhausted while syncing industry jobs for %s; remaining locations will use placeholders.",
                                        user.username,
                                    )
                                    lookup_budget_warned = True
                                location_name = location_cache.setdefault(
                                    location_key,
                                    f"{PLACEHOLDER_PREFIX}{location_key}",
                                )
                    start_date = _coerce_job_datetime(job.get("start_date"))
                    end_date = _coerce_job_datetime(job.get("end_date"))
                    pause_date = _coerce_job_datetime(job.get("pause_date"))
                    completed_date = _coerce_job_datetime(job.get("completed_date"))

                    if start_date is None:
                        logger.warning(
                            "Skipping job %s for %s due to invalid start date %r",
                            job_id,
                            character_name,
                            job.get("start_date"),
                        )
                        continue

                    if end_date is None:
                        logger.warning(
                            "Job %s for %s missing end date; defaulting to start date.",
                            job_id,
                            character_name,
                        )
                        end_date = start_date

                    _update_or_create_with_deadlock_retry(
                        IndustryJob,
                        lookup={"job_id": job_id},
                        defaults={
                            "owner_user": user,
                            "owner_kind": Blueprint.OwnerKind.CHARACTER,
                            "corporation_id": None,
                            "corporation_name": "",
                            "character_id": char_id,
                            "installer_id": job.get("installer_id"),
                            "station_id": station_id,
                            "location_name": location_name,
                            "activity_id": job.get("activity_id"),
                            "blueprint_id": job.get("blueprint_id"),
                            "blueprint_type_id": job.get("blueprint_type_id"),
                            "runs": job.get("runs"),
                            "cost": job.get("cost"),
                            "licensed_runs": job.get("licensed_runs"),
                            "probability": job.get("probability"),
                            "product_type_id": job.get("product_type_id"),
                            "status": job.get("status"),
                            "duration": job.get("duration"),
                            "start_date": start_date,
                            "end_date": end_date,
                            "pause_date": pause_date,
                            "completed_date": completed_date,
                            "completed_character_id": job.get("completed_character_id"),
                            "successful_runs": job.get("successful_runs"),
                            "blueprint_type_name": get_type_name(
                                job.get("blueprint_type_id")
                            ),
                            "product_type_name": get_type_name(
                                job.get("product_type_id")
                            ),
                            "character_name": character_name,
                        },
                    )

                deleted, _ = (
                    IndustryJob.objects.filter(
                        owner_user=user,
                        owner_kind=Blueprint.OwnerKind.CHARACTER,
                        character_id=char_id,
                    )
                    .exclude(job_id__in=esi_job_ids)
                    .delete()
                )

            deleted_total += deleted
            updated_count += len(jobs)
            logger.debug(
                "Finished syncing jobs for %s (%s updated, %s removed)",
                character_name,
                len(jobs),
                deleted,
            )

        if process_corporations and corp_contexts:
            for corp_id, context in corp_contexts.items():
                corp_char_id = context.get("character_id")
                corp_name = context.get("corporation_name") or str(corp_id)
                acting_character_name = context.get("character_name") or ""

                if not corp_char_id:
                    logger.debug(
                        "Incomplete corporate job context for %s (missing character_id)",
                        corp_id,
                    )
                    continue

                try:
                    corp_jobs = shared_client.fetch_corporation_industry_jobs(
                        int(corp_id), character_id=int(corp_char_id)
                    )
                    if not isinstance(corp_jobs, list):
                        payload_type = type(corp_jobs).__name__
                        logger.warning(
                            "Skipping corporation jobs for %s (%s): unexpected payload type %s",
                            corp_name,
                            corp_id,
                            payload_type,
                        )
                        error_messages.append(
                            f"Unexpected corporation jobs payload type for {corp_name} ({corp_id}): {payload_type}"
                        )
                        continue
                except ESIUnmodifiedError:
                    logger.debug(
                        "Corporate jobs not modified for %s (%s); skipping sync",
                        corp_name,
                        corp_id,
                    )
                    continue
                except ESITokenError as exc:
                    message = f"Invalid token for corporation {corp_name} ({corp_id}) via {acting_character_name}: {exc}"
                    logger.warning(message)
                    error_messages.append(message)
                    continue
                except ESIRateLimitError as exc:
                    delay = get_retry_after_seconds(exc)
                    message = (
                        "ESI rate limit hit for corporation "
                        f"{corp_name} ({corp_id}) via {acting_character_name}; retrying in {delay}s: {exc}"
                    )
                    logger.warning(message)
                    raise self.retry(countdown=delay)
                except (ESIForbiddenError, ESIClientError) as exc:
                    message = f"ESI error for corporation {corp_name} ({corp_id}) via {acting_character_name}: {exc}"
                    logger.error(message)
                    error_messages.append(message)
                    continue
                except Exception as exc:  # pragma: no cover - unexpected
                    message = f"Unexpected error for corporation {corp_name} ({corp_id}) via {acting_character_name}: {exc}"
                    logger.exception(message)
                    error_messages.append(message)
                    continue

                corp_job_ids: set[int] = set()
                with transaction.atomic():
                    for job in corp_jobs:
                        if not isinstance(job, dict):
                            logger.warning(
                                "Skipping corporate job with unexpected payload type for %s: %s (%r)",
                                corp_name,
                                type(job).__name__,
                                job,
                            )
                            continue
                        job_id = job.get("job_id")
                        if job_id is None:
                            logger.debug(
                                "Skipping corporate job without identifier for %s: %s",
                                corp_name,
                                job,
                            )
                            continue

                        corp_job_ids.add(job_id)
                        station_id = job.get("station_id") or job.get("facility_id")
                        location_name = ""
                        if station_id is not None:
                            try:
                                location_key = int(station_id)
                            except (TypeError, ValueError):
                                location_key = None

                            if location_key is not None:
                                cached_name = location_cache.get(location_key)
                                if cached_name is not None:
                                    location_name = cached_name
                                elif lookup_budget > 0:
                                    try:
                                        resolved_name = resolve_location_name(
                                            location_key,
                                            character_id=int(corp_char_id),
                                            owner_user_id=user.id,
                                        )
                                    except Exception:  # pragma: no cover
                                        logger.debug(
                                            "Location resolution failed for %s via %s",
                                            location_key,
                                            acting_character_name,
                                            exc_info=True,
                                        )
                                        resolved_name = None

                                    lookup_budget -= 1
                                    location_name = (
                                        resolved_name
                                        if resolved_name
                                        else f"{PLACEHOLDER_PREFIX}{location_key}"
                                    )
                                    location_cache[location_key] = location_name
                                else:
                                    if not lookup_budget_warned:
                                        logger.warning(
                                            "Location lookup budget exhausted while syncing corporate jobs for %s; remaining locations will use placeholders.",
                                            corp_name,
                                        )
                                        lookup_budget_warned = True
                                    location_name = location_cache.setdefault(
                                        location_key,
                                        f"{PLACEHOLDER_PREFIX}{location_key}",
                                    )

                        start_date = _coerce_job_datetime(job.get("start_date"))
                        end_date = _coerce_job_datetime(job.get("end_date"))
                        pause_date = _coerce_job_datetime(job.get("pause_date"))
                        completed_date = _coerce_job_datetime(job.get("completed_date"))

                        if start_date is None:
                            logger.warning(
                                "Ignoring corporate job %s for %s due to invalid start date %r",
                                job_id,
                                corp_name,
                                job.get("start_date"),
                            )
                            continue

                        if end_date is None:
                            logger.warning(
                                "Corporate job %s for %s missing end date; defaulting to start date.",
                                job_id,
                                corp_name,
                            )
                            end_date = start_date

                        _update_or_create_with_deadlock_retry(
                            IndustryJob,
                            lookup={"job_id": job_id},
                            defaults={
                                "owner_user": user,
                                "owner_kind": Blueprint.OwnerKind.CORPORATION,
                                "corporation_id": corp_id,
                                "corporation_name": corp_name,
                                "character_id": None,
                                "character_name": acting_character_name,
                                "installer_id": job.get("installer_id"),
                                "station_id": station_id,
                                "location_name": location_name,
                                "activity_id": job.get("activity_id"),
                                "blueprint_id": job.get("blueprint_id"),
                                "blueprint_type_id": job.get("blueprint_type_id"),
                                "runs": job.get("runs"),
                                "cost": job.get("cost"),
                                "licensed_runs": job.get("licensed_runs"),
                                "probability": job.get("probability"),
                                "product_type_id": job.get("product_type_id"),
                                "status": job.get("status"),
                                "duration": job.get("duration"),
                                "start_date": start_date,
                                "end_date": end_date,
                                "pause_date": pause_date,
                                "completed_date": completed_date,
                                "completed_character_id": job.get(
                                    "completed_character_id"
                                ),
                                "successful_runs": job.get("successful_runs"),
                                "blueprint_type_name": get_type_name(
                                    job.get("blueprint_type_id")
                                ),
                                "product_type_name": get_type_name(
                                    job.get("product_type_id")
                                ),
                            },
                        )

                deleted, _ = (
                    IndustryJob.objects.filter(
                        owner_kind=Blueprint.OwnerKind.CORPORATION,
                        corporation_id=corp_id,
                    )
                    .exclude(job_id__in=corp_job_ids)
                    .delete()
                )

                updated_count += len(corp_jobs)
                deleted_total += deleted
                logger.debug(
                    "Finished syncing corporate jobs for %s (%s updated, %s removed)",
                    corp_name,
                    len(corp_jobs),
                    deleted,
                )

        logger.info(
            "Jobs synced for %s: %s updated, %s removed",
            user.username,
            updated_count,
            deleted_total,
        )
        if error_messages:
            logger.warning(
                "Issues occurred while syncing jobs for %s: %s",
                user.username,
                "; ".join(error_messages),
            )
        return {
            "success": True,
            "jobs_updated": updated_count,
            "deleted": deleted_total,
            "errors": error_messages,
        }
    except Exception as e:
        logger.error(f"Error updating jobs for user {user_id}: {e}")
        # Error tracking removed in unified settings
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@shared_task
def cleanup_old_jobs():
    """
    Delete only orphaned jobs:
    - jobs whose owner_user no longer exists
    - jobs whose character_id does not match any CharacterOwnership
    - jobs whose ESI token no longer exists for the user/character pair
    """
    # Alliance Auth
    from allianceauth.authentication.models import CharacterOwnership
    from esi.models import Token

    # Jobs sans user
    jobs_no_user = IndustryJob.objects.filter(owner_user__isnull=True)
    count_no_user = jobs_no_user.count()
    jobs_no_user.delete()

    # Jobs without character ownership (applies only to character-related jobs)
    char_ids = set(
        CharacterOwnership.objects.values_list("character__character_id", flat=True)
    )
    jobs_no_char = IndustryJob.objects.filter(character_id__isnull=False).exclude(
        character_id__in=char_ids
    )
    count_no_char = jobs_no_char.count()
    jobs_no_char.delete()

    # Jobs without a valid token (applies only to character-related jobs)
    token_pairs = {
        (user_id, character_id)
        for user_id, character_id in Token.objects.values_list(
            "user_id", "character_id"
        )
    }

    orphan_job_ids: list[int] = []
    for job_id, owner_user_id, character_id in IndustryJob.objects.filter(
        character_id__isnull=False,
        owner_user__isnull=False,
    ).values_list("id", "owner_user_id", "character_id"):
        if (owner_user_id, character_id) not in token_pairs:
            orphan_job_ids.append(job_id)

    deleted_tokenless = 0
    if orphan_job_ids:
        deleted_tokenless = IndustryJob.objects.filter(id__in=orphan_job_ids).delete()[
            0
        ]

    total_deleted = count_no_user + count_no_char + deleted_tokenless
    logger.info(
        f"Cleaned up {total_deleted} orphaned industry jobs (no user: {count_no_user}, no char: {count_no_char}, no token: {deleted_tokenless})"
    )
    return {
        "deleted_jobs": total_deleted,
        "no_user": count_no_user,
        "no_char": count_no_char,
        "no_token": deleted_tokenless,
    }


@shared_task
def update_type_names():
    blueprints_without_names = Blueprint.objects.filter(type_name="")
    type_ids = list(blueprints_without_names.values_list("type_id", flat=True))
    if type_ids:
        batch_cache_type_names(type_ids)
        for bp in blueprints_without_names:
            bp.refresh_from_db()
    jobs_without_names = IndustryJob.objects.filter(blueprint_type_name="")
    job_type_ids = list(jobs_without_names.values_list("blueprint_type_id", flat=True))
    product_type_ids = list(
        jobs_without_names.exclude(product_type_id__isnull=True).values_list(
            "product_type_id", flat=True
        )
    )
    all_type_ids = list(set(job_type_ids + product_type_ids))
    if all_type_ids:
        batch_cache_type_names(all_type_ids)
        for job in jobs_without_names:
            job.refresh_from_db()
    logger.info("Updated type names for blueprints and jobs")


@shared_task(bind=True, max_retries=0)
@rate_limit_retry_task
def populate_location_names_async(
    self, location_ids=None, force_refresh=False, dry_run=False
):
    """Populate location names for blueprints and industry jobs asynchronously."""

    logger.info(
        "Starting async population job for location names%s",
        f" (limited to {len(location_ids)} IDs)" if location_ids else "",
    )

    normalized_ids = None
    if location_ids is not None:
        normalized_ids = [int(value) for value in location_ids if value]
        if not normalized_ids:
            logger.info("No valid location IDs supplied; skipping population job")
            return {"blueprints": 0, "jobs": 0, "locations": 0}

    summary = populate_location_names(
        location_ids=normalized_ids,
        force_refresh=force_refresh,
        dry_run=dry_run,
        schedule_async=not force_refresh,
    )

    logger.info(
        "Location population completed: %s blueprints, %s jobs (%s locations)",
        summary.get("blueprints", 0),
        summary.get("jobs", 0),
        summary.get("locations", 0),
    )
    return summary


@shared_task
def update_all_blueprints():
    """
    Update blueprints for all users - scheduled via Celery beat
    """
    logger.info("Starting bulk blueprint update for all users")

    user_ids = list(
        Token.objects.all()
        .require_scopes([ONLINE_SCOPE])
        .require_valid()
        .values_list("user_id", flat=True)
        .distinct()
    )
    random.shuffle(user_ids)

    window_minutes = _get_adaptive_window_minutes("blueprints", len(user_ids))
    queued = _queue_staggered_user_tasks(
        update_blueprints_for_user,
        user_ids,
        window_minutes=window_minutes,
        priority=7,
    )

    logger.info(
        "Queued blueprint updates for %s users across a %s minute window",
        queued,
        window_minutes,
    )
    return {"users_queued": queued, "window_minutes": window_minutes}


@shared_task
def update_all_industry_jobs():
    """
    Update industry jobs for all users - scheduled via Celery beat
    """
    logger.info("Starting bulk industry jobs update for all users")

    user_ids = list(
        Token.objects.all()
        .require_scopes([ONLINE_SCOPE])
        .require_valid()
        .values_list("user_id", flat=True)
        .distinct()
    )
    random.shuffle(user_ids)

    window_minutes = _get_adaptive_window_minutes("industry_jobs", len(user_ids))
    queued = _queue_staggered_user_tasks(
        update_industry_jobs_for_user,
        user_ids,
        window_minutes=window_minutes,
        priority=7,
    )

    logger.info(
        "Queued industry job updates for %s users across a %s minute window",
        queued,
        window_minutes,
    )
    return {"users_queued": queued, "window_minutes": window_minutes}


@shared_task
def update_all_skill_snapshots() -> dict[str, int]:
    """Queue per-user skill snapshot refresh tasks."""
    if _SKILLS_OPERATION_UNAVAILABLE:
        logger.warning(
            "Skipping skill snapshot refresh: ESI skills operation unavailable"
        )
        return {"queued": 0, "skipped": 0}

    user_ids = list(
        Token.objects.all()
        .require_scopes([SKILLS_SCOPE])
        .require_valid()
        .values_list("user_id", flat=True)
        .distinct()
    )
    random.shuffle(user_ids)

    window_minutes = _get_adaptive_window_minutes("skills", len(user_ids))
    queued = _queue_staggered_user_tasks(
        update_user_skill_snapshots,
        user_ids,
        window_minutes=window_minutes,
        priority=7,
    )

    logger.info(
        "Queued skill snapshot updates for %s users across a %s minute window",
        queued,
        window_minutes,
    )
    return {"queued": queued, "skipped": 0, "window_minutes": window_minutes}


@shared_task
def update_character_skill_snapshot_for_character(
    user_id: int,
    character_id: int,
) -> dict[str, object]:
    """Refresh skill snapshot for a single character."""
    if _SKILLS_OPERATION_UNAVAILABLE:
        return {"status": "skipped", "reason": "operation_unavailable"}

    ownership = (
        CharacterOwnership.objects.filter(
            user_id=user_id, character__character_id=character_id
        )
        .select_related("character", "user")
        .first()
    )
    if not ownership:
        return {"status": "skipped", "reason": "ownership_missing"}

    if not _is_user_active(ownership.user):
        return {"status": "skipped", "reason": "user_inactive"}

    token = (
        Token.objects.filter(user=ownership.user, character_id=character_id)
        .require_scopes([SKILLS_SCOPE])
        .require_valid()
        .order_by("-created")
        .first()
    )
    if not token:
        return {"status": "skipped", "reason": "token_missing"}

    snapshot = IndustrySkillSnapshot.objects.filter(
        character_id=int(character_id)
    ).first()
    table_empty = not IndustrySkillSnapshot.objects.exists()

    try:
        levels = _fetch_character_skill_levels_with_token(
            token,
            force_refresh=table_empty or snapshot is None,
        )
    except ESIUnmodifiedError:
        return {"status": "skipped", "reason": "not_modified"}
    except ESIRateLimitError as exc:
        delay = get_retry_after_seconds(exc)
        logger.warning(
            "ESI rate limit hit while refreshing skills for character %s; retrying in %ss: %s",
            character_id,
            delay,
            exc,
        )
        update_character_skill_snapshot_for_character.apply_async(
            args=(int(user_id), int(character_id)),
            countdown=delay,
        )
        return {"status": "rate_limited", "retry_in": delay}
    except (ESITokenError, ESIForbiddenError) as exc:
        logger.warning(
            "Failed to refresh skills for character %s: %s",
            character_id,
            exc,
        )
        return {"status": "failed", "reason": str(exc)}
    except ESIClientError as exc:
        if "skills operation unavailable" in str(exc):
            return {"status": "skipped", "reason": "operation_unavailable"}
        logger.warning(
            "Failed to refresh skills for character %s: %s",
            character_id,
            exc,
        )
        return {"status": "failed", "reason": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Unexpected error refreshing skills for character %s: %s",
            character_id,
            exc,
        )
        return {"status": "failed", "reason": str(exc)}

    defaults = {
        "mass_production_level": levels.get(SKILL_TYPE_IDS["mass_production"], 0),
        "advanced_mass_production_level": levels.get(
            SKILL_TYPE_IDS["advanced_mass_production"], 0
        ),
        "laboratory_operation_level": levels.get(
            SKILL_TYPE_IDS["laboratory_operation"], 0
        ),
        "advanced_laboratory_operation_level": levels.get(
            SKILL_TYPE_IDS["advanced_laboratory_operation"], 0
        ),
        "mass_reactions_level": levels.get(SKILL_TYPE_IDS["mass_reactions"], 0),
        "advanced_mass_reactions_level": levels.get(
            SKILL_TYPE_IDS["advanced_mass_reactions"], 0
        ),
    }
    _update_or_create_with_deadlock_retry(
        IndustrySkillSnapshot,
        lookup={
            "owner_user": ownership.user,
            "character_id": int(character_id),
        },
        defaults=defaults,
    )
    return {"status": "updated"}


@shared_task
def update_user_skill_snapshots(user_id: int) -> dict[str, int]:
    """Refresh skill snapshots for all characters of a user with skills scope."""
    if _SKILLS_OPERATION_UNAVAILABLE:
        return {"updated": 0, "skipped": 0, "failures": 0}

    user = User.objects.filter(id=user_id).first()
    if not user or not _is_user_active(user):
        return {"updated": 0, "skipped": 1, "failures": 0}

    tokens = (
        Token.objects.filter(user=user)
        .require_scopes([SKILLS_SCOPE])
        .require_valid()
        .values_list("character_id", flat=True)
        .distinct()
    )
    updated = 0
    skipped = 0
    failures = 0
    for character_id in tokens:
        if not character_id:
            skipped += 1
            continue
        result = update_character_skill_snapshot_for_character(
            int(user_id),
            int(character_id),
        )
        status = result.get("status") if isinstance(result, dict) else None
        if status == "updated":
            updated += 1
        elif status == "failed":
            failures += 1
        else:
            skipped += 1

    return {"updated": updated, "skipped": skipped, "failures": failures}
