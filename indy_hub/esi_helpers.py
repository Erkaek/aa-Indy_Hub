# Standard Library
import logging

# Third Party
import requests

# Alliance Auth
from esi.models import Token

# settings import removed as unused


logger = logging.getLogger(__name__)

ESI_BASE_URL = "https://esi.evetech.net/latest"


def fetch_character_blueprints(character_id):
    """
    Fetch all blueprints for a character from ESI, handling pagination and token refresh.
    Returns a list of blueprint dicts or raises an exception on error.
    """
    SCOPE = "esi-characters.read_blueprints.v1"
    try:
        token = Token.get_token(character_id, SCOPE)
        access_token = token.valid_access_token()
    except Exception as e:
        logger.warning(f"No valid token for char {character_id} and scope {SCOPE}: {e}")
        raise Exception("ESI: No valid token for blueprints")
    url = f"{ESI_BASE_URL}/characters/{character_id}/blueprints/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"datasource": "tranquility", "page": 1}
    all_blueprints = []
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code == 200:
            all_blueprints.extend(resp.json())
            total_pages = int(resp.headers.get("X-Pages", 1))
            for page in range(2, total_pages + 1):
                params["page"] = page
                resp = requests.get(url, headers=headers, params=params, timeout=20)
                if resp.status_code == 200:
                    all_blueprints.extend(resp.json())
                else:
                    raise Exception(
                        f"ESI error {resp.status_code} on page {page}: {resp.text}"
                    )
            return all_blueprints
        elif resp.status_code == 403:
            raise Exception("ESI: Forbidden (token revoked or missing scope)")
        elif resp.status_code == 401:
            raise Exception("ESI: Unauthorized (token expired)")
        else:
            raise Exception(f"ESI error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Failed to fetch blueprints for character {character_id}: {e}")
        raise


def fetch_character_industry_jobs(character_id):
    """
    Fetch all industry jobs for a character from ESI, handling pagination and token refresh.
    Returns a list of job dicts or raises an exception on error.
    """
    SCOPE = "esi-industry.read_character_jobs.v1"
    try:
        token = Token.get_token(character_id, SCOPE)
        access_token = token.valid_access_token()
    except Exception as e:
        logger.warning(f"No valid token for char {character_id} and scope {SCOPE}: {e}")
        raise Exception("ESI: No valid token for industry jobs")
    url = f"{ESI_BASE_URL}/characters/{character_id}/industry/jobs/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"datasource": "tranquility", "page": 1}
    all_jobs = []
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code == 200:
            all_jobs.extend(resp.json())
            total_pages = int(resp.headers.get("X-Pages", 1))
            for page in range(2, total_pages + 1):
                params["page"] = page
                resp = requests.get(url, headers=headers, params=params, timeout=20)
                if resp.status_code == 200:
                    all_jobs.extend(resp.json())
                else:
                    raise Exception(
                        f"ESI error {resp.status_code} on page {page}: {resp.text}"
                    )
            return all_jobs
        elif resp.status_code == 403:
            raise Exception("ESI: Forbidden (token revoked or missing scope)")
        elif resp.status_code == 401:
            raise Exception("ESI: Unauthorized (token expired)")
        else:
            raise Exception(f"ESI error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Failed to fetch industry jobs for character {character_id}: {e}")
        raise


def fetch_character_assets(character_id):
    """
    Fetch all assets for a character from ESI, handling pagination and token refresh.
    Returns a list of asset dicts or raises an exception on error.
    """
    SCOPE = "esi-assets.read_assets.v1"
    try:
        token = Token.get_token(character_id, SCOPE)
        access_token = token.valid_access_token()
    except Exception as e:
        logger.warning(f"No valid token for char {character_id} and scope {SCOPE}: {e}")
        raise Exception("ESI: No valid token for assets")
    url = f"{ESI_BASE_URL}/characters/{character_id}/assets/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"datasource": "tranquility", "page": 1}
    all_assets = []
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code == 200:
            all_assets.extend(resp.json())
            total_pages = int(resp.headers.get("X-Pages", 1))
            for page in range(2, total_pages + 1):
                params["page"] = page
                resp = requests.get(url, headers=headers, params=params, timeout=20)
                if resp.status_code == 200:
                    all_assets.extend(resp.json())
                else:
                    raise Exception(
                        f"ESI error {resp.status_code} on page {page}: {resp.text}"
                    )
            return all_assets
        elif resp.status_code == 403:
            raise Exception("ESI: Forbidden (token revoked or missing scope)")
        elif resp.status_code == 401:
            raise Exception("ESI: Unauthorized (token expired)")
        else:
            raise Exception(f"ESI error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Failed to fetch assets for character {character_id}: {e}")
        raise
