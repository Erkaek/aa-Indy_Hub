"""ESI client abstraction powered by django-esi."""

from __future__ import annotations

# Standard Library
import math
import time

try:
    # Celery
    # Third Party
    from celery import current_task
except ImportError:  # pragma: no cover - celery always available in runtime
    current_task = None

# Django
from django.core.cache import cache

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger
from esi import app_settings as esi_app_settings
from esi.errors import TokenError
from esi.exceptions import (
    ESIBucketLimitException,
    ESIErrorLimitException,
    HTTPClientError,
    HTTPNotModified,
    HTTPServerError,
)
from esi.models import Token

# Alliance Auth (External Libs)
from app_utils.helpers import chunks

# AA Example App
# Local
from indy_hub.app_settings import (
    ESI_TASK_TARGET_PER_MIN_BLUEPRINTS,
    ESI_TASK_TARGET_PER_MIN_JOBS,
    ESI_TASK_TARGET_PER_MIN_ROLES,
    ESI_TASK_TARGET_PER_MIN_SKILLS,
)
from indy_hub.services.providers import esi_provider

logger = get_extension_logger(__name__)

_SCOPE_THROTTLE_PREFIX = "indy_hub:esi_task_scope_budget"
_SCOPE_THROTTLE_TIMEOUT_SECONDS = 120
_SCOPE_THROTTLE_HIT_PREFIX = "indy_hub:esi_task_scope_budget_hits"
_SCOPE_THROTTLE_HIT_TIMEOUT_SECONDS = 120
_RATE_LIMIT_META_PREFIX = "indy_hub:esi_rate_limit_meta"
_RATE_LIMIT_COOLDOWN_PREFIX = "indy_hub:esi_rate_limit_cooldown"
_RATE_LIMIT_META_TTL_SECONDS = 24 * 60 * 60
_RATE_LIMIT_REQUIRED_TOKENS = 2
_TRANQUILITY_COOLDOWN_CACHE_KEY = "indy_hub:esi_tranquility_global_cooldown_until"
_TRANQUILITY_COOLDOWN_FALLBACK_SECONDS = 60
_HTTP_ERROR_TYPES = (HTTPClientError, HTTPServerError)
_DJANGO_ESI_RATE_LIMIT_ERRORS = (ESIBucketLimitException, ESIErrorLimitException)


class ESIClientError(Exception):
    """Base error raised when the ESI client fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class ESITokenError(ESIClientError):
    """Raised when a valid access token cannot be retrieved."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ESIForbiddenError(ESIClientError):
    """Raised when ESI returns HTTP 403 for an authenticated lookup."""

    def __init__(
        self,
        message: str,
        *,
        character_id: int | None = None,
        structure_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.character_id = character_id
        self.structure_id = structure_id


class ESIUnmodifiedError(ESIClientError):
    """Raised when ESI responds with HTTP 304 (Not Modified)."""


def rate_limit_wait_seconds(response) -> tuple[float | None, int | None]:
    """Return pause seconds from ESI error-limit headers when available."""

    wait_candidates: list[float] = []
    retry_after_header = response.headers.get("Retry-After")
    reset_header = response.headers.get("X-Esi-Error-Limit-Reset")

    for raw_value in (retry_after_header, reset_header):
        if raw_value is None:
            continue
        try:
            wait_candidates.append(float(raw_value))
        except (TypeError, ValueError):
            continue

    wait: float | None = None
    if wait_candidates:
        positive = [value for value in wait_candidates if value > 0]
        if positive:
            wait = max(positive)

    remaining_header = response.headers.get("X-Esi-Error-Limit-Remain")
    remaining: int | None = None
    if remaining_header is not None:
        try:
            remaining = int(remaining_header)
        except (TypeError, ValueError):
            remaining = None

    return wait, remaining


def token_rate_limit_wait_seconds(
    response,
) -> tuple[float | None, int | None]:
    """Return pause seconds from token-rate headers when available."""

    retry_after_header = response.headers.get("Retry-After")
    reset_header = response.headers.get("X-Ratelimit-Reset")
    remaining_header = response.headers.get("X-Ratelimit-Remaining")

    wait_candidates: list[float] = []
    for raw_value in (retry_after_header, reset_header):
        if raw_value is None:
            continue
        try:
            wait_candidates.append(float(raw_value))
        except (TypeError, ValueError):
            continue

    wait: float | None = None
    if wait_candidates:
        positive = [value for value in wait_candidates if value > 0]
        if positive:
            wait = max(positive)

    remaining: int | None = None
    if remaining_header is not None:
        try:
            remaining = int(remaining_header)
        except (TypeError, ValueError):
            remaining = None

    return wait, remaining


def get_rate_limit_reset_seconds(
    exc: Exception,
    *,
    fallback: int = 1,
    minimum: int = 1,
) -> int:
    """Normalize retry delay from django-esi rate limit exceptions."""

    raw_delay = getattr(exc, "reset", None)
    if raw_delay is None:
        raw_delay = getattr(exc, "retry_after", None)
    delay = 0
    if raw_delay is not None:
        try:
            delay = int(float(raw_delay))
        except (TypeError, ValueError):
            delay = 0

    if delay <= 0:
        delay = int(fallback)

    return max(delay, int(minimum))


def get_retry_after_seconds(
    exc: Exception,
    *,
    fallback: int = 1,
    minimum: int = 1,
    maximum: int = 15 * 60,
) -> int:
    """Best-effort Retry-After extraction for transient ESI failures."""

    raw_delay = getattr(exc, "retry_after", None)
    response = getattr(exc, "response", None)
    if raw_delay is None and response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            for header in (
                "Retry-After",
                "X-Esi-Error-Limit-Reset",
                "X-Ratelimit-Reset",
            ):
                raw_delay = headers.get(header)
                if raw_delay is not None:
                    break

    delay = 0
    if raw_delay is not None:
        try:
            delay = int(math.ceil(float(raw_delay)))
        except (TypeError, ValueError):
            delay = 0

    if delay <= 0:
        delay = int(fallback)

    bounded = max(int(minimum), delay)
    return min(bounded, int(maximum))


class ESIClient:
    """Small helper around django-esi OpenAPI client with AA-friendly errors."""

    def __init__(self) -> None:
        self.provider = esi_provider

    @property
    def client(self):
        """Lazily materialize the django-esi OpenAPI client on first access."""
        return self.provider.client

    @staticmethod
    def _is_running_in_task() -> bool:
        if current_task is None:
            return False
        try:
            request = getattr(current_task, "request", None)
            return bool(request and getattr(request, "id", None))
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def _seconds_to_next_minute() -> int:
        return max(1, 60 - (int(time.time()) % 60))

    @staticmethod
    def _parse_rate_limit_limit(
        limit_header: str | None,
    ) -> tuple[int | None, int | None]:
        """Parse X-Ratelimit-Limit header like '150/15m' into tokens/window."""
        if not limit_header:
            return None, None

        raw = str(limit_header).strip().lower()
        if "/" not in raw:
            return None, None

        tokens_raw, window_raw = raw.split("/", 1)
        try:
            max_tokens = int(tokens_raw.strip())
        except (TypeError, ValueError):
            return None, None

        window_raw = window_raw.strip()
        if not window_raw:
            return None, None

        unit = window_raw[-1]
        try:
            magnitude = int(window_raw[:-1])
        except (TypeError, ValueError):
            return None, None

        if magnitude <= 0 or max_tokens <= 0:
            return None, None

        if unit == "m":
            window_seconds = magnitude * 60
        elif unit == "h":
            window_seconds = magnitude * 60 * 60
        else:
            return None, None

        return max_tokens, window_seconds

    @staticmethod
    def _rate_limit_meta_cache_key(endpoint: str) -> str:
        return f"{_RATE_LIMIT_META_PREFIX}:{endpoint}"

    @staticmethod
    def _tranquility_cooldown_seconds_remaining() -> int:
        blocked_until = cache.get(_TRANQUILITY_COOLDOWN_CACHE_KEY)
        if blocked_until is None:
            return 0
        try:
            remaining = int(math.ceil(float(blocked_until) - time.time()))
        except (TypeError, ValueError):
            return 0
        return max(remaining, 0)

    def _set_tranquility_cooldown(self, cooldown_seconds: int) -> int:
        cooldown = max(int(cooldown_seconds), 1)
        cache.set(
            _TRANQUILITY_COOLDOWN_CACHE_KEY,
            time.time() + cooldown,
            timeout=cooldown,
        )
        return cooldown

    def _record_tranquility_outage(self, exc: Exception) -> int:
        cooldown = get_retry_after_seconds(
            exc,
            fallback=_TRANQUILITY_COOLDOWN_FALLBACK_SECONDS,
            minimum=5,
            maximum=15 * 60,
        )
        applied = self._set_tranquility_cooldown(cooldown)
        logger.warning(
            "Set global Tranquility outage cooldown to %ss after transient ESI failure",
            applied,
        )
        return applied

    def _enforce_tranquility_cooldown(self, endpoint: str | None) -> None:
        remaining = self._tranquility_cooldown_seconds_remaining()
        if remaining <= 0:
            return
        raise ESIErrorLimitException(
            reset=remaining,
            message=(
                "Global Tranquility outage cooldown active"
                + (f" for {endpoint}" if endpoint else "")
                + f" (retry in {remaining}s)"
            ),
        )

    @staticmethod
    def _rate_limit_cooldown_cache_key(group: str, key_suffix: str) -> str:
        return f"{_RATE_LIMIT_COOLDOWN_PREFIX}:{group}:{key_suffix}"

    def _store_rate_limit_meta(
        self,
        *,
        endpoint: str | None,
        group: str,
        max_tokens: int,
        window_seconds: int,
    ) -> None:
        if not endpoint:
            return
        cache.set(
            self._rate_limit_meta_cache_key(endpoint),
            {
                "group": group,
                "max_tokens": int(max_tokens),
                "window_seconds": int(window_seconds),
            },
            timeout=_RATE_LIMIT_META_TTL_SECONDS,
        )

    def _read_rate_limit_meta(self, endpoint: str | None) -> dict | None:
        if not endpoint:
            return None
        value = cache.get(self._rate_limit_meta_cache_key(endpoint))
        if isinstance(value, dict):
            return value
        return None

    def _set_rate_limit_cooldown(
        self,
        *,
        group: str,
        key_suffix: str,
        cooldown_seconds: int,
    ) -> None:
        cooldown = max(int(cooldown_seconds), 1)
        cache.set(
            self._rate_limit_cooldown_cache_key(group, key_suffix),
            time.time() + cooldown,
            timeout=cooldown,
        )

    def _enforce_rate_limit_cooldown(
        self,
        *,
        endpoint: str | None,
        throttle_key: str | None,
    ) -> None:
        key_suffix = (throttle_key or "").strip()
        if not endpoint or not key_suffix:
            return

        meta = self._read_rate_limit_meta(endpoint)
        if not meta:
            return

        group = str(meta.get("group") or "").strip()
        if not group:
            return

        blocked_until = cache.get(
            self._rate_limit_cooldown_cache_key(group, key_suffix)
        )
        if blocked_until is None:
            return

        try:
            wait_seconds = int(math.ceil(float(blocked_until) - time.time()))
        except (TypeError, ValueError):
            wait_seconds = 0
        if wait_seconds <= 0:
            return

        raise ESIErrorLimitException(
            reset=max(wait_seconds, 1),
            message=(
                "Local ESI endpoint cooldown active for "
                f"group {group} key={key_suffix} on {endpoint}"
            ),
        )

    def _update_rate_limit_state_from_response(
        self,
        *,
        response,
        endpoint: str | None,
        throttle_key: str | None,
    ) -> None:
        if response is None:
            return
        headers = getattr(response, "headers", None)
        if headers is None:
            return

        group = str(headers.get("X-Ratelimit-Group") or "").strip()
        limit_raw = headers.get("X-Ratelimit-Limit")
        remaining_raw = headers.get("X-Ratelimit-Remaining")
        if not group:
            return

        max_tokens, window_seconds = self._parse_rate_limit_limit(limit_raw)
        if max_tokens and window_seconds:
            self._store_rate_limit_meta(
                endpoint=endpoint,
                group=group,
                max_tokens=max_tokens,
                window_seconds=window_seconds,
            )

        if not throttle_key or not max_tokens or not window_seconds:
            return

        try:
            remaining = int(remaining_raw)
        except (TypeError, ValueError):
            return

        refill_per_token_seconds = float(window_seconds) / float(max_tokens)
        missing_tokens = _RATE_LIMIT_REQUIRED_TOKENS - remaining
        if missing_tokens <= 0:
            return

        cooldown_seconds = int(
            max(1, math.ceil(float(missing_tokens) * refill_per_token_seconds))
        )
        self._set_rate_limit_cooldown(
            group=group,
            key_suffix=str(throttle_key).strip(),
            cooldown_seconds=cooldown_seconds,
        )

    def _record_scope_throttle_hit(
        self,
        *,
        scope_key: str,
        key_suffix: str | None,
        minute_bucket: int,
    ) -> int:
        subject_key = key_suffix or "global"
        hit_key = (
            f"{_SCOPE_THROTTLE_HIT_PREFIX}:{scope_key}:{subject_key}:{minute_bucket}"
        )
        if cache.add(hit_key, 1, timeout=_SCOPE_THROTTLE_HIT_TIMEOUT_SECONDS):
            return 1

        try:
            return int(cache.incr(hit_key))
        except Exception:  # pragma: no cover - cache backend edge case
            return 1

    @staticmethod
    def _target_per_min_for_scope(scope: str | None) -> int:
        if not scope:
            return 0

        if scope in {
            "esi-industry.read_character_jobs.v1",
            "esi-industry.read_corporation_jobs.v1",
        }:
            return int(ESI_TASK_TARGET_PER_MIN_JOBS)

        if scope in {
            "esi-characters.read_blueprints.v1",
            "esi-corporations.read_blueprints.v1",
        }:
            return int(ESI_TASK_TARGET_PER_MIN_BLUEPRINTS)

        if scope in {
            "esi-skills.read_skills.v1",
        }:
            return int(ESI_TASK_TARGET_PER_MIN_SKILLS)

        if scope in {
            "esi-characters.read_corporation_roles.v1",
            "esi-assets.read_assets.v1",
            "esi-assets.read_corporation_assets.v1",
            "esi-contracts.read_character_contracts.v1",
            "esi-contracts.read_corporation_contracts.v1",
            "esi-corporations.read_structures.v1",
            "esi-universe.read_structures.v1",
        }:
            return int(ESI_TASK_TARGET_PER_MIN_ROLES)

        # Conservative fallback for authenticated endpoints not explicitly mapped.
        return int(ESI_TASK_TARGET_PER_MIN_ROLES)

    def _enforce_task_scope_budget(
        self,
        scope: str | None,
        endpoint: str | None,
        *,
        throttle_key: str | None = None,
    ) -> None:
        self._enforce_rate_limit_cooldown(
            endpoint=endpoint,
            throttle_key=throttle_key,
        )

        if not self._is_running_in_task():
            return

        target_per_min = self._target_per_min_for_scope(scope)
        if target_per_min <= 0:
            return

        scope_key = (scope or "unknown").strip() or "unknown"
        key_suffix = (throttle_key or "").strip() or None
        minute_bucket = int(time.time() // 60)
        if key_suffix:
            cache_key = (
                f"{_SCOPE_THROTTLE_PREFIX}:{scope_key}:{key_suffix}:{minute_bucket}"
            )
        else:
            cache_key = f"{_SCOPE_THROTTLE_PREFIX}:{scope_key}:{minute_bucket}"

        if cache.add(cache_key, 1, timeout=_SCOPE_THROTTLE_TIMEOUT_SECONDS):
            return

        try:
            used = int(cache.incr(cache_key))
        except Exception as exc:  # pragma: no cover - cache backend edge case
            logger.warning(
                "Task scope throttle counter failed for scope %s on %s; applying conservative backoff: %s",
                scope_key,
                endpoint or "unknown-endpoint",
                exc,
            )
            raise ESIErrorLimitException(
                reset=self._seconds_to_next_minute(),
                message=(
                    "Local task ESI throttle unavailable for scope "
                    f"{scope_key}; backing off to avoid burst traffic"
                ),
            ) from exc

        if used <= target_per_min:
            return

        hit_count = self._record_scope_throttle_hit(
            scope_key=scope_key,
            key_suffix=key_suffix,
            minute_bucket=minute_bucket,
        )

        raise ESIErrorLimitException(
            reset=self._seconds_to_next_minute(),
            message=(
                "Local task ESI throttle hit for scope "
                f"{scope_key} ({used}/{target_per_min} req/min)"
                + (f" key={key_suffix}" if key_suffix else "")
                + f" hits={hit_count}/min"
                + (f" on {endpoint}" if endpoint else "")
            ),
        )

    def enforce_task_scope_budget(
        self,
        *,
        scope: str | None,
        endpoint: str | None = None,
        throttle_key: str | None = None,
    ) -> None:
        """Public wrapper for task-side scope throttling."""
        self._enforce_task_scope_budget(
            scope,
            endpoint,
            throttle_key=throttle_key,
        )

    def fetch_character_blueprints(
        self, character_id: int, *, force_refresh: bool = False
    ) -> list[dict]:
        """Return the list of blueprints for a character."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-characters.read_blueprints.v1",
            endpoint=f"/characters/{character_id}/blueprints/",
            resource="Character",
            operation="get_characters_character_id_blueprints",
            params={"character_id": character_id},
            force_refresh=force_refresh,
        )

    def fetch_character_industry_jobs(
        self, character_id: int, *, force_refresh: bool = False
    ) -> list[dict]:
        """Return the list of industry jobs for a character."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-industry.read_character_jobs.v1",
            endpoint=f"/characters/{character_id}/industry/jobs/",
            resource="Industry",
            operation="get_characters_character_id_industry_jobs",
            params={"character_id": character_id},
            throttle_key=f"char:{int(character_id)}",
            force_refresh=force_refresh,
        )

    def fetch_character_skills(
        self,
        character_id: int,
        *,
        force_refresh: bool = False,
        token_obj: Token | None = None,
    ) -> dict:
        """Return the skill payload for a character."""
        token_obj = token_obj or self._get_token(
            character_id, "esi-skills.read_skills.v1"
        )

        skills_resource = getattr(self.client, "Skills", None)
        operation_fn = None
        if skills_resource is not None:
            operation_fn = getattr(
                skills_resource,
                "get_characters_character_id_skills",
                None,
            ) or getattr(skills_resource, "GetCharactersCharacterIdSkills", None)

        if operation_fn is None:
            character_resource = getattr(self.client, "Character", None)
            if character_resource is not None:
                operation_fn = getattr(
                    character_resource,
                    "get_characters_character_id_skills",
                    None,
                ) or getattr(
                    character_resource,
                    "GetCharactersCharacterIdSkills",
                    None,
                )

        if not callable(operation_fn):
            raise ESIClientError("ESI skills operation unavailable")

        request_kwargs = {"If-None-Match": ""} if force_refresh else {}
        results_kwargs = (
            {"use_etag": False, "force_refresh": True} if force_refresh else None
        )

        payload = self._call_authed(
            token_obj,
            character_id=int(character_id),
            endpoint=f"/characters/{int(character_id)}/skills/",
            scope="esi-skills.read_skills.v1",
            throttle_key=f"char:{int(character_id)}",
            results_kwargs=results_kwargs,
            operation=lambda token: operation_fn(
                character_id=int(character_id),
                token=token,
                **request_kwargs,
            ),
        )

        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if isinstance(payload, dict):
            return payload
        coerced = self._coerce_mapping(payload)
        if isinstance(coerced, dict):
            return coerced
        raise ESIClientError(
            "ESI /characters/{character_id}/skills returned an unexpected payload"
        )

    def fetch_corporation_blueprints(
        self,
        corporation_id: int,
        *,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Return the list of blueprints owned by a corporation."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-corporations.read_blueprints.v1",
            endpoint=f"/corporations/{corporation_id}/blueprints/",
            resource="Corporation",
            operation="get_corporations_corporation_id_blueprints",
            params={"corporation_id": corporation_id},
            force_refresh=force_refresh,
        )

    def fetch_corporation_industry_jobs(
        self,
        corporation_id: int,
        *,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Return the list of industry jobs owned by a corporation."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-industry.read_corporation_jobs.v1",
            endpoint=f"/corporations/{corporation_id}/industry/jobs/",
            resource="Industry",
            operation="get_corporations_corporation_id_industry_jobs",
            params={"corporation_id": corporation_id},
            throttle_key=f"char:{int(character_id)}",
            force_refresh=force_refresh,
        )

    def fetch_character_corporation_roles(
        self,
        character_id: int,
        *,
        force_refresh: bool = False,
        token_obj: Token | None = None,
    ) -> dict:
        """Return the corporation roles assigned to a character."""
        token_obj = token_obj or self._get_token(
            character_id, "esi-characters.read_corporation_roles.v1"
        )
        operation_fn = self._resolve_operation(
            "Character", "get_characters_character_id_roles"
        )
        request_kwargs = {}
        if force_refresh:
            request_kwargs["If-None-Match"] = ""

        results_kwargs = None
        if force_refresh:
            results_kwargs = {"use_etag": False, "force_refresh": True}
        results_kwargs = None
        if force_refresh:
            # django-esi can return cached results without hitting ESI; when a caller
            # explicitly requests a refresh (e.g. after DB cache reset), bypass its
            # caching/etag behavior.
            results_kwargs = {"use_etag": False, "force_refresh": True}
        payload = self._call_authed(
            token_obj,
            character_id=character_id,
            endpoint=f"/characters/{character_id}/roles/",
            scope="esi-characters.read_corporation_roles.v1",
            results_kwargs=results_kwargs,
            operation=lambda token: operation_fn(
                character_id=character_id,
                token=token,
                **request_kwargs,
            ),
        )
        if isinstance(payload, list):
            if not payload:
                raise ESIClientError(
                    "ESI /characters/{character_id}/roles returned an empty payload"
                )
            payload = payload[0]
        if isinstance(payload, dict):
            return payload
        coerced = self._coerce_mapping(payload)
        if isinstance(coerced, dict):
            return coerced
        raise ESIClientError(
            "ESI /characters/{character_id}/roles returned an unexpected payload"
        )

    def fetch_corporation_divisions(
        self,
        corporation_id: int,
        *,
        character_id: int,
        force_refresh: bool = False,
        token_obj: Token | None = None,
    ) -> dict:
        """Return corporation division payload for a corporation."""
        token_obj = token_obj or self._get_token(
            int(character_id),
            "esi-corporations.read_divisions.v1",
        )
        operation_fn = self._resolve_operation(
            "Corporation", "get_corporations_corporation_id_divisions"
        )
        request_kwargs = {"If-None-Match": ""} if force_refresh else {}
        results_kwargs = (
            {"use_etag": False, "force_refresh": True} if force_refresh else None
        )

        payload = self._call_authed(
            token_obj,
            character_id=int(character_id),
            endpoint=f"/corporations/{int(corporation_id)}/divisions/",
            scope="esi-corporations.read_divisions.v1",
            throttle_key=f"char:{int(character_id)}",
            results_kwargs=results_kwargs,
            operation=lambda token: operation_fn(
                corporation_id=int(corporation_id),
                token=token,
                **request_kwargs,
            ),
        )

        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if isinstance(payload, dict):
            return payload
        coerced = self._coerce_mapping(payload)
        if isinstance(coerced, dict):
            return coerced
        raise ESIClientError(
            "ESI /corporations/{corporation_id}/divisions returned an unexpected payload"
        )

    def fetch_structure_name(
        self, structure_id: int, character_id: int | None = None
    ) -> str | None:
        """Attempt to resolve a structure name via the authenticated endpoint."""
        if not structure_id:
            return None

        if not character_id:
            return None

        token_obj = None
        try:
            token_obj = self._get_token(
                int(character_id), "esi-universe.read_structures.v1"
            )
        except ESITokenError:
            logger.debug(
                "No valid universe.read_structures token for character %s",
                character_id,
            )
            return None

        try:
            operation_fn = self._resolve_operation(
                "Universe", "get_universe_structures_structure_id"
            )
            payload = self._call_authed(
                token_obj,
                character_id=int(character_id),
                structure_id=int(structure_id),
                endpoint=f"/universe/structures/{int(structure_id)}/",
                scope="esi-universe.read_structures.v1",
                results_kwargs={"use_etag": False},
                operation=lambda token: operation_fn(
                    structure_id=int(structure_id),
                    token=token,
                ),
            )
        except ESIUnmodifiedError:
            try:
                payload = self._call_authed(
                    token_obj,
                    character_id=int(character_id),
                    structure_id=int(structure_id),
                    endpoint=f"/universe/structures/{int(structure_id)}/",
                    scope="esi-universe.read_structures.v1",
                    results_kwargs={"use_etag": False, "force_refresh": True},
                    operation=lambda token: operation_fn(
                        structure_id=int(structure_id),
                        token=token,
                        **{"If-None-Match": ""},
                    ),
                )
            except ESIForbiddenError:
                raise
            except ESITokenError:
                return None
            except ESIClientError:
                return None
        except ESIForbiddenError:
            raise
        except ESITokenError:
            return None
        except ESIClientError:
            return None

        if isinstance(payload, list):
            if not payload:
                return None
            payload = payload[0]
        if isinstance(payload, dict):
            return payload.get("name")
        coerced = self._coerce_mapping(payload)
        if isinstance(coerced, dict):
            return coerced.get("name")
        if payload is not None:
            name = getattr(payload, "name", None)
            if name:
                return str(name)
        return None

    def fetch_industry_systems(self, *, force_refresh: bool = False) -> list[dict]:
        """Fetch public industry system cost indices from ESI."""
        self._enforce_tranquility_cooldown("/industry/systems/")
        try:
            operation_fn = self._resolve_operation("Industry", "get_industry_systems")
        except AttributeError as exc:
            raise ESIClientError(
                "ESI operation Industry.get_industry_systems is not available"
            ) from exc

        results_kwargs = None
        if force_refresh:
            results_kwargs = {"use_etag": False, "force_refresh": True}

        try:
            operation_call = operation_fn()
            if results_kwargs is None:
                payload = operation_call.results()
            else:
                payload = operation_call.results(**results_kwargs)
        except HTTPNotModified:
            return []
        except _HTTP_ERROR_TYPES as exc:
            self._handle_http_error(exc, endpoint="/industry/systems/")
            raise
        except Exception as exc:
            raise ESIClientError(
                f"ESI request failed for /industry/systems/: {exc}"
            ) from exc

        if not isinstance(payload, list):
            raise ESIClientError(
                f"ESI /industry/systems/ returned an unexpected payload type: {type(payload)}"
            )
        return [self._coerce_mapping(item) for item in payload]

    def _fetch_paginated(
        self,
        *,
        character_id: int,
        scope: str,
        endpoint: str,
        resource: str,
        operation: str,
        params: dict,
        throttle_key: str | None = None,
        force_refresh: bool = False,
    ) -> list[dict]:
        if throttle_key is None and character_id:
            throttle_key = f"char:{int(character_id)}"
        self._enforce_task_scope_budget(
            scope,
            endpoint,
            throttle_key=throttle_key,
        )
        token_obj = self._get_token(character_id, scope)
        try:
            token_obj.valid_access_token()
        except Exception as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc

        try:
            operation_fn = self._resolve_operation(resource, operation)
        except AttributeError as exc:
            raise ESIClientError(
                f"ESI operation {resource}.{operation} is not available"
            ) from exc

        request_kwargs = {}
        if force_refresh:
            request_kwargs["If-None-Match"] = ""

        results_kwargs = None
        if force_refresh:
            results_kwargs = {"use_etag": False, "force_refresh": True}

        last_response = None
        try:
            operation_call = operation_fn(**params, token=token_obj, **request_kwargs)
            if results_kwargs is None:
                payload, last_response = operation_call.results(return_response=True)
            else:
                payload, last_response = operation_call.results(
                    return_response=True,
                    **results_kwargs,
                )
        except HTTPNotModified as exc:
            raise ESIUnmodifiedError(f"ESI returned 304 for {endpoint}") from exc
        except _HTTP_ERROR_TYPES as exc:
            self._handle_http_error(
                exc,
                character_id=character_id,
                endpoint=endpoint,
                token_obj=token_obj,
                scope=scope,
                throttle_key=throttle_key,
            )
            raise
        except TokenError as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc
        except Exception as exc:
            if "is not of type 'string'" not in str(exc):
                raise ESIClientError(
                    f"ESI request failed for {endpoint}: {exc}"
                ) from exc

            access_token = token_obj.valid_access_token()
            try:
                operation_call = operation_fn(
                    **params, token=access_token, **request_kwargs
                )
                if results_kwargs is None:
                    payload = operation_call.results()
                else:
                    payload = operation_call.results(**results_kwargs)
            except HTTPNotModified as retry_exc:
                raise ESIUnmodifiedError(
                    f"ESI returned 304 for {endpoint}"
                ) from retry_exc
            except _HTTP_ERROR_TYPES as retry_exc:
                self._handle_http_error(
                    retry_exc,
                    character_id=character_id,
                    endpoint=endpoint,
                    token_obj=token_obj,
                    scope=scope,
                    throttle_key=throttle_key,
                )
                raise
            except TokenError as retry_exc:
                raise ESITokenError(
                    f"No valid token for character {character_id} and scope {scope}"
                ) from retry_exc
            except Exception as retry_exc:
                raise ESIClientError(
                    f"ESI request failed for {endpoint}: {retry_exc}"
                ) from retry_exc

        if not isinstance(payload, list):
            raise ESIClientError(
                f"ESI {endpoint} returned an unexpected payload type: {type(payload)}"
            )

        self._update_rate_limit_state_from_response(
            response=last_response,
            endpoint=endpoint,
            throttle_key=throttle_key,
        )

        self._validate_paginated_last_modified(
            operation_fn=operation_fn,
            params=params,
            token_obj=token_obj,
            request_kwargs=request_kwargs,
            endpoint=endpoint,
            force_refresh=force_refresh,
            last_response=last_response,
        )

        return [self._coerce_mapping(item) for item in payload]

    def _validate_paginated_last_modified(
        self,
        *,
        operation_fn,
        params: dict,
        token_obj: Token,
        request_kwargs: dict,
        endpoint: str,
        force_refresh: bool,
        last_response,
    ) -> None:
        """Verify Last-Modified consistency across paged resources.

        ESI recommends that paginated pages for one resource share the same
        Last-Modified value to avoid mixed snapshots.
        """
        if force_refresh:
            # Manual refresh can bypass etag/cache on purpose; avoid issuing extra
            # live requests for validation in this mode.
            return

        if not last_response:
            return

        try:
            total_pages = int(last_response.headers.get("X-Pages", 1))
        except (TypeError, ValueError):
            total_pages = 1

        if total_pages <= 1:
            return

        if not getattr(esi_app_settings, "ESI_CACHE_RESPONSE", True):
            logger.debug(
                "Skipping Last-Modified pagination validation for %s because ESI cache is disabled.",
                endpoint,
            )
            return

        baseline_last_modified = last_response.headers.get("Last-Modified")
        if not baseline_last_modified:
            return

        for page in range(1, total_pages + 1):
            try:
                operation_call = operation_fn(
                    **params,
                    token=token_obj,
                    page=page,
                    **request_kwargs,
                )
                _, page_response = operation_call.result(
                    return_response=True,
                    use_etag=False,
                    use_cache=True,
                )
            except HTTPNotModified:
                # Should not happen with use_etag=False, but a 304 still means
                # content is unchanged for that page.
                continue
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.debug(
                    "Failed Last-Modified pagination probe for %s page=%s: %s",
                    endpoint,
                    page,
                    exc,
                )
                continue

            page_last_modified = None
            if page_response is not None:
                page_last_modified = page_response.headers.get("Last-Modified")

            if page_last_modified and page_last_modified != baseline_last_modified:
                raise ESIClientError(
                    "ESI returned inconsistent Last-Modified values across pages "
                    f"for {endpoint}; refusing mixed snapshot"
                )

    @staticmethod
    def _coerce_mapping(item):
        if isinstance(item, dict):
            return item
        for attr in ("model_dump", "dict", "to_dict"):
            converter = getattr(item, attr, None)
            if callable(converter):
                try:
                    result = converter()
                except Exception:
                    result = None
                if isinstance(result, dict):
                    return result
        return item

    def _get_token(self, character_id: int, scope: str) -> Token:
        token = (
            Token.objects.filter(character_id=int(character_id))
            .require_scopes([scope])
            .require_valid()
            .order_by("-created")
            .first()
        )
        if not token:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            )
        return token

    def fetch_corporation_contracts(
        self,
        corporation_id: int,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch all contracts for a corporation using character's token."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-contracts.read_corporation_contracts.v1",
            endpoint=f"/corporations/{corporation_id}/contracts/",
            resource="Contracts",
            operation="get_corporations_corporation_id_contracts",
            params={"corporation_id": corporation_id},
            force_refresh=force_refresh,
        )

    def fetch_corporation_contract_items(
        self,
        corporation_id: int,
        contract_id: int,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch items for a specific corporation contract."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-contracts.read_corporation_contracts.v1",
            endpoint=f"/corporations/{corporation_id}/contracts/{contract_id}/items/",
            resource="Contracts",
            operation="get_corporations_corporation_id_contracts_contract_id_items",
            params={"corporation_id": corporation_id, "contract_id": contract_id},
            force_refresh=force_refresh,
        )

    def fetch_character_contracts(self, character_id: int) -> list[dict]:
        """Fetch all contracts for a character using their token."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-contracts.read_character_contracts.v1",
            endpoint=f"/characters/{character_id}/contracts/",
            resource="Contracts",
            operation="get_characters_character_id_contracts",
            params={"character_id": character_id},
        )

    def fetch_character_contract_items(
        self,
        character_id: int,
        contract_id: int,
    ) -> list[dict]:
        """Fetch items for a specific character contract."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-contracts.read_character_contracts.v1",
            endpoint=f"/characters/{character_id}/contracts/{contract_id}/items/",
            resource="Contracts",
            operation="get_characters_character_id_contracts_contract_id_items",
            params={"character_id": character_id, "contract_id": contract_id},
        )

    def fetch_corporation_assets(
        self,
        corporation_id: int,
        *,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch all corporation assets for the given corp using a character token."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-assets.read_corporation_assets.v1",
            endpoint=f"/corporations/{corporation_id}/assets/",
            resource="Assets",
            operation="get_corporations_corporation_id_assets",
            params={"corporation_id": corporation_id},
            force_refresh=force_refresh,
        )

    def fetch_character_assets(
        self,
        *,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch all assets for a character using their token."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-assets.read_assets.v1",
            endpoint=f"/characters/{character_id}/assets/",
            resource="Assets",
            operation="get_characters_character_id_assets",
            params={"character_id": character_id},
            force_refresh=force_refresh,
        )

    def fetch_corporation_structures(
        self,
        corporation_id: int,
        *,
        character_id: int,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch corporation structures (includes names) using corp structures scope."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-corporations.read_structures.v1",
            endpoint=f"/corporations/{corporation_id}/structures/",
            resource="Corporation",
            operation="get_corporations_corporation_id_structures",
            params={"corporation_id": corporation_id},
            force_refresh=force_refresh,
        )

    def fetch_station_name(self, station_id: int) -> str | None:
        """Resolve an NPC station name via the public /universe/stations/ endpoint (no auth)."""
        if not station_id:
            return None
        endpoint = f"/universe/stations/{int(station_id)}/"
        throttle_key = "public-station"
        try:
            operation_fn = self._resolve_operation(
                "Universe", "get_universe_stations_station_id"
            )
        except AttributeError:
            return None
        self._enforce_task_scope_budget(None, endpoint, throttle_key=throttle_key)
        try:
            payload, response = operation_fn(station_id=int(station_id)).results(
                return_response=True,
                use_etag=False,
            )
            self._update_rate_limit_state_from_response(
                response=response,
                endpoint=endpoint,
                throttle_key=throttle_key,
            )
        except HTTPNotModified:
            # django-esi cache was stale; retry bypassing ETags entirely
            try:
                payload, response = operation_fn(station_id=int(station_id)).results(
                    return_response=True,
                    use_etag=False,
                    force_refresh=True,
                )
                self._update_rate_limit_state_from_response(
                    response=response,
                    endpoint=endpoint,
                    throttle_key=throttle_key,
                )
            except Exception as exc:
                logger.debug(
                    "fetch_station_name retry after 304 failed for %s: %s",
                    station_id,
                    exc,
                )
                return None
        except _DJANGO_ESI_RATE_LIMIT_ERRORS as exc:
            logger.warning(
                "fetch_station_name rate limited for %s: %s", station_id, exc
            )
            return None
        except _HTTP_ERROR_TYPES as exc:
            logger.debug("fetch_station_name HTTP error for %s: %s", station_id, exc)
            return None
        except Exception as exc:
            logger.debug("fetch_station_name failed for %s: %s", station_id, exc)
            return None
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if isinstance(payload, dict):
            return str(payload.get("name") or "") or None
        coerced = self._coerce_mapping(payload) if payload is not None else None
        if isinstance(coerced, dict):
            return str(coerced.get("name") or "") or None
        if payload is not None:
            name = getattr(payload, "name", None)
            if name:
                return str(name)
        return None

    def resolve_ids_to_names(self, ids: list[int]) -> dict[int, str]:
        """Resolve a list of IDs to names via the public /universe/names/ endpoint.

        This endpoint doesn't require authentication and can resolve stations, structures,
        systems, regions, etc.

        Returns a dict mapping ID -> name for successfully resolved IDs.
        """
        if not ids:
            return {}

        self._enforce_tranquility_cooldown("/universe/names/")

        # ESI accepts max 1000 IDs per request
        result: dict[int, str] = {}
        try:
            operation_fn = self._resolve_operation("Universe", "post_universe_names")
        except AttributeError:
            return result
        for batch in chunks(ids, 1000):
            try:
                payload = operation_fn(ids=batch).results()
            except _DJANGO_ESI_RATE_LIMIT_ERRORS as exc:
                logger.warning(
                    "resolve_ids_to_names hit django-esi rate limit: %s",
                    exc,
                )
                break
            except _HTTP_ERROR_TYPES as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code is None:
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                if status_code == 404:
                    logger.debug(
                        "resolve_ids_to_names skipped invalid IDs batch (size=%s)",
                        len(batch),
                    )
                    continue
                self._handle_http_error(
                    exc,
                    endpoint="/universe/names/",
                )
                continue
            except Exception:
                try:
                    payload = operation_fn(body=batch).results()
                except _DJANGO_ESI_RATE_LIMIT_ERRORS as exc2:
                    logger.warning(
                        "resolve_ids_to_names hit django-esi rate limit: %s",
                        exc2,
                    )
                    break
                except _HTTP_ERROR_TYPES as exc2:
                    status_code = getattr(exc2, "status_code", None)
                    if status_code is None:
                        response = getattr(exc2, "response", None)
                        status_code = getattr(response, "status_code", None)
                    if status_code == 404:
                        logger.debug(
                            "resolve_ids_to_names skipped invalid IDs batch (size=%s)",
                            len(batch),
                        )
                        continue
                    self._handle_http_error(
                        exc2,
                        endpoint="/universe/names/",
                    )
                    continue
                except Exception as exc2:
                    logger.warning("Resolve IDs request failed: %s", exc2)
                    continue

            try:
                for item in payload or []:
                    if "id" in item and "name" in item:
                        result[int(item["id"])] = str(item["name"])
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning("Invalid payload from /universe/names/: %s", exc)

        return result

    def _call_authed(
        self,
        token_obj: Token,
        *,
        character_id: int | None = None,
        structure_id: int | None = None,
        endpoint: str | None = None,
        scope: str | None = None,
        throttle_key: str | None = None,
        operation=None,
        results_kwargs: dict | None = None,
    ):
        if operation is None:
            raise ESIClientError("No ESI operation provided")
        self._enforce_tranquility_cooldown(endpoint)
        if throttle_key is None and character_id:
            throttle_key = f"char:{int(character_id)}"
        self._enforce_task_scope_budget(scope, endpoint, throttle_key=throttle_key)
        try:
            access_token = token_obj.valid_access_token()
        except Exception as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc

        def _execute(token_value):
            try:
                if results_kwargs is None:
                    return operation(token_value).results(return_response=True)
                return operation(token_value).results(
                    return_response=True,
                    **results_kwargs,
                )
            except TypeError:
                if results_kwargs is None:
                    payload = operation(token_value).results()
                else:
                    payload = operation(token_value).results(**results_kwargs)
                return payload, None

        try:
            payload, response = _execute(token_obj)
            self._update_rate_limit_state_from_response(
                response=response,
                endpoint=endpoint,
                throttle_key=throttle_key,
            )
            return payload
        except HTTPNotModified as exc:
            raise ESIUnmodifiedError(
                f"ESI returned 304 for {endpoint or 'request'}"
            ) from exc
        except _HTTP_ERROR_TYPES as exc:
            self._handle_http_error(
                exc,
                character_id=character_id,
                structure_id=structure_id,
                endpoint=endpoint,
                token_obj=token_obj,
                scope=scope,
                throttle_key=throttle_key,
            )
            raise
        except TokenError as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc
        except Exception as exc:
            if "is not of type 'string'" not in str(exc):
                raise ESIClientError(
                    f"ESI request failed for {endpoint}: {exc}"
                ) from exc

        try:
            payload, response = _execute(access_token)
            self._update_rate_limit_state_from_response(
                response=response,
                endpoint=endpoint,
                throttle_key=throttle_key,
            )
            return payload
        except HTTPNotModified as exc:
            raise ESIUnmodifiedError(
                f"ESI returned 304 for {endpoint or 'request'}"
            ) from exc
        except _HTTP_ERROR_TYPES as exc:
            self._handle_http_error(
                exc,
                character_id=character_id,
                structure_id=structure_id,
                endpoint=endpoint,
                token_obj=token_obj,
                scope=scope,
                throttle_key=throttle_key,
            )
            raise
        except TokenError as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc
        except Exception as exc:
            if "is not of type 'string'" not in str(exc):
                raise ESIClientError(
                    f"ESI request failed for {endpoint}: {exc}"
                ) from exc

        try:
            payload, response = _execute(access_token)
            self._update_rate_limit_state_from_response(
                response=response,
                endpoint=endpoint,
                throttle_key=throttle_key,
            )
            return payload
        except HTTPNotModified as exc:
            raise ESIUnmodifiedError(
                f"ESI returned 304 for {endpoint or 'request'}"
            ) from exc
        except _HTTP_ERROR_TYPES as exc:
            self._handle_http_error(
                exc,
                character_id=character_id,
                structure_id=structure_id,
                endpoint=endpoint,
                token_obj=token_obj,
                scope=scope,
                throttle_key=throttle_key,
            )
            raise
        except TokenError as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc
        except Exception as exc:
            raise ESIClientError(f"ESI request failed for {endpoint}: {exc}") from exc

    def _resolve_operation(self, resource: str, operation: str):
        """Resolve an ESI operation name for OpenAPI clients."""
        resource_obj = getattr(self.client, resource)
        if hasattr(resource_obj, operation):
            return getattr(resource_obj, operation)

        camel = "".join(part.capitalize() for part in operation.split("_"))
        if hasattr(resource_obj, camel):
            return getattr(resource_obj, camel)

        raise AttributeError(f"{resource}.{operation}")

    def _handle_http_error(
        self,
        exc: HTTPClientError | HTTPServerError,
        *,
        character_id: int | None = None,
        structure_id: int | None = None,
        endpoint: str | None = None,
        token_obj: Token | None = None,
        scope: str | None = None,
        throttle_key: str | None = None,
    ) -> None:
        status_code = getattr(exc, "status_code", None) or getattr(
            exc.response, "status_code", None
        )
        if status_code == 420:
            sleep_for, remaining = rate_limit_wait_seconds(exc.response)
            raise ESIErrorLimitException(
                reset=sleep_for or 1,
                message=f"ESI error limit reached (remaining={remaining})",
            ) from exc

        if status_code == 429:
            sleep_for, remaining = token_rate_limit_wait_seconds(exc.response)
            self._update_rate_limit_state_from_response(
                response=getattr(exc, "response", None),
                endpoint=endpoint,
                throttle_key=throttle_key,
            )
            if sleep_for and throttle_key:
                response = getattr(exc, "response", None)
                headers = getattr(response, "headers", None)
                group = ""
                if headers is not None:
                    group = str(headers.get("X-Ratelimit-Group") or "").strip()
                if group:
                    self._set_rate_limit_cooldown(
                        group=group,
                        key_suffix=str(throttle_key).strip(),
                        cooldown_seconds=max(int(sleep_for), 1),
                    )
            raise ESIBucketLimitException(
                bucket="esi-bucket",
                reset=sleep_for or 1,
                message=f"ESI bucket limit reached (remaining={remaining})",
            ) from exc

        if status_code == 403 and character_id is not None:
            if token_obj and scope and endpoint:
                self._handle_forbidden_token(
                    token_obj,
                    scope=scope,
                    endpoint=endpoint,
                )
            raise ESIForbiddenError(
                "ESI access forbidden",
                character_id=character_id,
                structure_id=structure_id,
            ) from exc

        if status_code in (401, 403):
            raise ESITokenError(
                f"Invalid token for {endpoint or 'ESI'} (status {status_code})",
                status_code=status_code,
            ) from exc

        if status_code is not None and 500 <= int(status_code) < 600:
            retry_after = self._record_tranquility_outage(exc)
            raise ESIClientError(
                f"ESI returned {status_code} for {endpoint or 'request'}: {exc}",
                status_code=status_code,
                retry_after=retry_after,
            ) from exc

        raise ESIClientError(
            f"ESI returned {status_code} for {endpoint or 'request'}: {exc}",
            status_code=status_code,
            retry_after=(
                get_retry_after_seconds(exc, fallback=5, minimum=1, maximum=15 * 60)
                if status_code is not None and 500 <= int(status_code) < 600
                else None
            ),
        ) from exc

    def _handle_forbidden_token(
        self, token: Token, *, scope: str, endpoint: str
    ) -> None:
        # A 403 on a single endpoint (typically a per-structure lookup) is not a
        # token invalidation: the token is shared with the rest of Alliance Auth
        # and other modules continue to rely on it. Only AA's own refresh flow
        # should ever delete the Token row. See GH-107.
        character_id = getattr(token, "character_id", None)
        user_repr = None
        try:
            user_repr = token.user.username  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - username optional
            user_repr = getattr(token, "user_id", None)

        logger.warning(
            "ESI returned 403 for %s (%s) through character %s (user %s); "
            "token is kept (per-endpoint forbidden, not an invalid token).",
            endpoint,
            scope,
            character_id,
            user_repr,
        )


# Module level singleton to avoid re-creating sessions
shared_client = ESIClient()
