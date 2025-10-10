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

# Alliance Auth
from esi.models import Token

logger = logging.getLogger(__name__)

ESI_BASE_URL = "https://esi.evetech.net/latest"


class ESIClientError(Exception):
    """Base error raised when the ESI client fails."""


class ESITokenError(ESIClientError):
    """Raised when a valid access token cannot be retrieved."""


class ESIClient:
    """Small helper around requests with retry/backoff logic for ESI endpoints."""

    def __init__(
        self,
        base_url: str = ESI_BASE_URL,
        timeout: int = 20,
        max_attempts: int = 3,
        backoff_factor: float = 0.75,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_factor = backoff_factor
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

    def _fetch_paginated(
        self,
        *,
        character_id: int,
        scope: str,
        endpoint: str,
    ) -> list[dict]:
        access_token = self._get_access_token(character_id, scope)
        url = f"{self.base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"datasource": "tranquility", "page": 1}

        aggregated: list[dict] = []
        while True:
            response = self._request("GET", url, headers=headers, params=params)
            payload = response.json()
            if not isinstance(payload, list):
                raise ESIClientError(
                    f"ESI {endpoint} a retourné un format inattendu: {type(payload)}"
                )
            aggregated.extend(payload)

            total_pages = int(response.headers.get("X-Pages", 1))
            if params["page"] >= total_pages:
                break
            params["page"] += 1
        return aggregated

    def _get_access_token(self, character_id: int, scope: str) -> str:
        try:
            token = Token.get_token(character_id, scope)
            return token.valid_access_token()
        except Exception as exc:  # pragma: no cover - Alliance Auth handles details
            raise ESITokenError(
                f"Aucun jeton valide pour le personnage {character_id} et le scope {scope}"
            ) from exc

    def _request(self, method: str, url: str, **kwargs) -> Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                if attempt >= self.max_attempts:
                    raise ESIClientError(
                        f"Echec de la requête ESI {method} {url} après {attempt} tentatives"
                    ) from exc
                sleep_for = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "Requête ESI en échec (%s %s), tentative %s/%s, nouvel essai dans %.1fs",
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
                    f"Jeton invalide pour {url} (statut {response.status_code})"
                )
            if response.status_code >= 400:
                if attempt >= self.max_attempts:
                    raise ESIClientError(
                        f"ESI a retourné {response.status_code} pour {url}: {response.text}"
                    )
                sleep_for = self.backoff_factor * (2 ** (attempt - 1))
                logger.warning(
                    "Statut %s reçu pour %s, tentative %s/%s, nouvel essai dans %.1fs",
                    response.status_code,
                    url,
                    attempt,
                    self.max_attempts,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            return response


# Module level singleton to avoid re-creating sessions
shared_client = ESIClient()
