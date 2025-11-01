"""ESI client abstraction with retry-aware helpers."""

from __future__ import annotations

# Standard Library
import logging
import time

# Third Party
import requests
from requests import Response
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    # Django
    from django.conf import settings
except Exception:  # pragma: no cover - settings might be unavailable in tests
    settings = None

# Alliance Auth
from esi.models import Token

logger = logging.getLogger(__name__)

ESI_BASE_URL = "https://esi.evetech.net/latest"
DEFAULT_COMPATIBILITY_DATE = "2025-09-30"


class ESIClientError(Exception):
    """Base error raised when the ESI client fails."""


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


class ESIRateLimitError(ESIClientError):
    """Raised when ESI signals that the error limit has been exceeded."""

    def __init__(
        self,
        message: str = "ESI rate limit exceeded",
        *,
        retry_after: float | None = None,
        remaining: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.remaining = remaining


def rate_limit_wait_seconds(
    response: Response, fallback: float
) -> tuple[float, int | None]:
    """Return the recommended pause in seconds from ESI headers."""

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

    wait = fallback
    if wait_candidates:
        positive = [value for value in wait_candidates if value > 0]
        if positive:
            wait = max(max(positive), fallback)

    remaining_header = response.headers.get("X-Esi-Error-Limit-Remain")
    remaining: int | None = None
    if remaining_header is not None:
        try:
            remaining = int(remaining_header)
        except (TypeError, ValueError):
            remaining = None

    return wait, remaining


class ESIClient:
    """Small helper around requests with retry/backoff logic for ESI endpoints."""

    def __init__(
        self,
        base_url: str = ESI_BASE_URL,
        timeout: int = 20,
        max_attempts: int = 3,
        backoff_factor: float = 0.75,
        compatibility_date: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_factor = backoff_factor
        self.compatibility_date = (compatibility_date or "").strip() or None
        self.session = requests.Session()
        retry = Retry(
            total=max_attempts,
            read=max_attempts,
            connect=max_attempts,
            status=max_attempts,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._default_headers: dict[str, str] = {"Accept": "application/json"}
        if self.compatibility_date:
            self._default_headers["X-Compatibility-Date"] = self.compatibility_date

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        merged = dict(self._default_headers)
        if headers:
            merged.update(headers)
        return merged

    def fetch_character_blueprints(self, character_id: int) -> list[dict]:
        """Return the list of blueprints for a character."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-characters.read_blueprints.v1",
            endpoint=f"/characters/{character_id}/blueprints/",
        )

    def fetch_character_industry_jobs(self, character_id: int) -> list[dict]:
        """Return the list of industry jobs for a character."""
        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-industry.read_character_jobs.v1",
            endpoint=f"/characters/{character_id}/industry/jobs/",
        )

    def fetch_corporation_blueprints(
        self, corporation_id: int, *, character_id: int
    ) -> list[dict]:
        """Return the list of blueprints owned by a corporation."""

        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-corporations.read_blueprints.v1",
            endpoint=f"/corporations/{corporation_id}/blueprints/",
        )

    def fetch_corporation_industry_jobs(
        self, corporation_id: int, *, character_id: int
    ) -> list[dict]:
        """Return the list of industry jobs owned by a corporation."""

        return self._fetch_paginated(
            character_id=character_id,
            scope="esi-industry.read_corporation_jobs.v1",
            endpoint=f"/corporations/{corporation_id}/industry/jobs/",
        )

    def fetch_character_corporation_roles(self, character_id: int) -> dict:
        """Return the corporation roles assigned to a character."""

        access_token = self._get_access_token(
            character_id, "esi-characters.read_corporation_roles.v1"
        )
        url = f"{self.base_url}/characters/{character_id}/roles/"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"datasource": "tranquility"}
        response = self._request("GET", url, headers=headers, params=params)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ESIClientError(
                f"ESI {url} returned an unexpected payload type: {type(payload)}"
            )
        return payload

    def fetch_structure_name(
        self, structure_id: int, character_id: int | None = None
    ) -> str | None:
        """Attempt to resolve a structure name via the authenticated endpoint."""

        if not structure_id:
            return None

        url = f"{self.base_url}/universe/structures/{int(structure_id)}/"
        params = {"datasource": "tranquility"}
        headers: dict[str, str] | None = None

        if character_id:
            try:
                access_token = self._get_access_token(
                    int(character_id), "esi-universe.read_structures.v1"
                )
                headers = {"Authorization": f"Bearer {access_token}"}
            except ESITokenError:
                logger.debug(
                    "No valid universe.read_structures token for character %s",
                    character_id,
                )

        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            request_headers = self._merge_headers(headers)
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=request_headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_attempts:
                    logger.debug(
                        "Request error while fetching structure %s: %s",
                        structure_id,
                        exc,
                    )
                    return None
                sleep_for = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "Structure lookup request failed (%s), retry %s/%s in %.1fs",
                    exc,
                    attempt,
                    self.max_attempts,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    logger.warning(
                        "Invalid JSON returned for structure %s", structure_id
                    )
                    return None
                return payload.get("name")

            if response.status_code == 420:
                sleep_for, remaining = rate_limit_wait_seconds(
                    response, self.backoff_factor * (2 ** (attempt - 1))
                )
                message = (
                    "ESI rate limit reached while fetching structure %s (remaining=%s)."
                )
                logger.warning(message, structure_id, remaining)
                if attempt >= self.max_attempts:
                    raise ESIRateLimitError(
                        retry_after=sleep_for,
                        remaining=remaining,
                    )
                time.sleep(sleep_for)
                continue

            if response.status_code == 403 and character_id is not None:
                raise ESIForbiddenError(
                    "Structure lookup forbidden",
                    character_id=int(character_id),
                    structure_id=int(structure_id),
                )

            if response.status_code in (401, 403):
                logger.debug(
                    "Structure %s requires auth or token invalid (status %s)",
                    structure_id,
                    response.status_code,
                )
                return None

            if response.status_code == 404:
                logger.debug("Structure %s not found via ESI", structure_id)
                return None

            logger.warning(
                "Unexpected status %s when fetching structure %s",
                response.status_code,
                structure_id,
            )
            return None

        return None

    def _fetch_paginated(
        self,
        *,
        character_id: int,
        scope: str,
        endpoint: str,
    ) -> list[dict]:
        token_obj = self._get_token(character_id, scope)
        try:
            access_token = token_obj.valid_access_token()
        except Exception as exc:
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc
        url = f"{self.base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"datasource": "tranquility", "page": 1}

        aggregated: list[dict] = []
        while True:
            try:
                response = self._request("GET", url, headers=headers, params=params)
            except ESITokenError as exc:
                if exc.status_code == 403:
                    self._handle_forbidden_token(
                        token_obj,
                        scope=scope,
                        endpoint=endpoint,
                    )
                    raise ESIForbiddenError(
                        f"Access denied for {endpoint}",
                        character_id=int(character_id),
                    ) from exc
                raise
            payload = response.json()
            if not isinstance(payload, list):
                raise ESIClientError(
                    f"ESI {endpoint} returned an unexpected payload type: {type(payload)}"
                )
            aggregated.extend(payload)

            total_pages = int(response.headers.get("X-Pages", 1))
            if params["page"] >= total_pages:
                break
            params["page"] += 1
        return aggregated

    def _get_token(self, character_id: int, scope: str) -> Token:
        try:
            return Token.get_token(character_id, scope)
        except Exception as exc:  # pragma: no cover - Alliance Auth handles details
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc

    def _get_access_token(self, character_id: int, scope: str) -> str:
        token = self._get_token(character_id, scope)
        try:
            return token.valid_access_token()
        except Exception as exc:  # pragma: no cover - Alliance Auth handles details
            raise ESITokenError(
                f"No valid token for character {character_id} and scope {scope}"
            ) from exc

    def _request(self, method: str, url: str, **kwargs) -> Response:
        attempt = 0
        while True:
            attempt += 1
            headers = kwargs.pop("headers", None)
            kwargs["headers"] = self._merge_headers(headers)
            try:
                response = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                if attempt >= self.max_attempts:
                    raise ESIClientError(
                        f"ESI request {method} {url} failed after {attempt} attempts"
                    ) from exc
                sleep_for = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "ESI request failed (%s %s), attempt %s/%s, retrying in %.1fs",
                    method,
                    url,
                    attempt,
                    self.max_attempts,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            if response.status_code in (401, 403):
                raise ESITokenError(
                    f"Invalid token for {url} (status {response.status_code})",
                    status_code=response.status_code,
                )
            if response.status_code == 420:
                sleep_for, remaining = rate_limit_wait_seconds(
                    response, self.backoff_factor * (2 ** (attempt - 1))
                )
                logger.warning(
                    "ESI rate limit hit for %s, attempt %s/%s (remaining=%s). Waiting %.1fs",
                    url,
                    attempt,
                    self.max_attempts,
                    remaining,
                    sleep_for,
                )
                if attempt >= self.max_attempts:
                    raise ESIRateLimitError(
                        retry_after=sleep_for,
                        remaining=remaining,
                    )
                time.sleep(sleep_for)
                continue
            if response.status_code >= 400:
                if attempt >= self.max_attempts:
                    raise ESIClientError(
                        f"ESI returned {response.status_code} for {url}: {response.text}"
                    )
                sleep_for = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "Status %s received for %s, attempt %s/%s, retrying in %.1fs",
                    response.status_code,
                    url,
                    attempt,
                    self.max_attempts,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            return response

    def _handle_forbidden_token(
        self, token: Token, *, scope: str, endpoint: str
    ) -> None:
        character_id = getattr(token, "character_id", None)
        user_repr = None
        try:
            user_repr = token.user.username  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - username optional
            user_repr = getattr(token, "user_id", None)

        logger.warning(
            "ESI returned 403 for %s (%s) through character %s (user %s). Token will be deleted.",
            endpoint,
            scope,
            character_id,
            user_repr,
        )
        try:
            token.delete()
        except Exception:  # pragma: no cover - defensive guard
            logger.exception(
                "Impossible de supprimer le jeton ESI %s pour le personnage %s",
                token.id,
                character_id,
            )


# Module level singleton to avoid re-creating sessions
if settings is not None:
    _compat_date = getattr(
        settings,
        "INDY_HUB_ESI_COMPATIBILITY_DATE",
        DEFAULT_COMPATIBILITY_DATE,
    )
else:  # pragma: no cover - running without Django settings
    _compat_date = DEFAULT_COMPATIBILITY_DATE

shared_client = ESIClient(compatibility_date=_compat_date)
