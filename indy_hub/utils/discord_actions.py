"""Utilities for Discord-driven blueprint copy actions."""

# Standard Library
import json
from urllib.parse import urlencode

# Django
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse

from ..notifications import build_site_url

_ACTION_TOKEN_SALT = "indy_hub.discord_action"
_DEFAULT_TOKEN_MAX_AGE = getattr(
    settings,
    "INDY_HUB_DISCORD_ACTION_TOKEN_MAX_AGE",
    72 * 60 * 60,  # three days
)

_MATERIAL_EXCHANGE_ACTION_TOKEN_SALT = "indy_hub.material_exchange_action"
_MATERIAL_EXCHANGE_ACTION_TOKEN_MAX_AGE = getattr(
    settings,
    "INDY_HUB_DISCORD_MATEX_ACTION_TOKEN_MAX_AGE",
    24 * 60 * 60,  # one day
)


def _get_signer() -> TimestampSigner:
    return TimestampSigner(salt=_ACTION_TOKEN_SALT)


def _get_material_exchange_signer() -> TimestampSigner:
    return TimestampSigner(salt=_MATERIAL_EXCHANGE_ACTION_TOKEN_SALT)


def generate_action_token(
    *,
    user_id: int | None,
    request_id: int,
    action: str,
) -> str:
    payload = {"r": request_id, "a": action}
    if user_id is not None:
        payload["u"] = user_id
    return _get_signer().sign(json.dumps(payload))


def decode_action_token(token: str, *, max_age: int | None = None) -> dict:
    raw = _get_signer().unsign(token, max_age=max_age or _DEFAULT_TOKEN_MAX_AGE)
    return json.loads(raw)


def build_action_link(*, action: str, request_id: int, user_id: int) -> str | None:
    token = generate_action_token(user_id=user_id, request_id=request_id, action=action)
    query = urlencode({"token": token})
    path = f"{reverse('indy_hub:bp_discord_action')}?{query}"
    return build_site_url(path)


def build_action_link_any(*, action: str, request_id: int) -> str | None:
    token = generate_action_token(user_id=None, request_id=request_id, action=action)
    query = urlencode({"token": token})
    path = f"{reverse('indy_hub:bp_discord_action')}?{query}"
    return build_site_url(path)


def generate_material_exchange_action_token(
    *,
    user_id: int | None,
    order_id: int,
    action: str,
) -> str:
    payload = {"o": order_id, "a": action}
    if user_id is not None:
        payload["u"] = user_id
    return _get_material_exchange_signer().sign(json.dumps(payload))


def decode_material_exchange_action_token(
    token: str, *, max_age: int | None = None
) -> dict:
    raw = _get_material_exchange_signer().unsign(
        token,
        max_age=max_age or _MATERIAL_EXCHANGE_ACTION_TOKEN_MAX_AGE,
    )
    return json.loads(raw)


def build_material_exchange_action_link_any(
    *,
    action: str,
    order_id: int,
) -> str | None:
    token = generate_material_exchange_action_token(
        user_id=None,
        order_id=order_id,
        action=action,
    )
    query = urlencode({"token": token})
    path = f"{reverse('indy_hub:material_exchange_discord_action')}?{query}"
    return build_site_url(path)


__all__ = [
    "BadSignature",
    "SignatureExpired",
    "generate_action_token",
    "generate_material_exchange_action_token",
    "decode_action_token",
    "decode_material_exchange_action_token",
    "build_action_link",
    "build_action_link_any",
    "build_material_exchange_action_link_any",
    "_DEFAULT_TOKEN_MAX_AGE",
]
