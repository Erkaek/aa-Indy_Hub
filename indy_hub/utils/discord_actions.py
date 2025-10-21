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


def _get_signer() -> TimestampSigner:
    return TimestampSigner(salt=_ACTION_TOKEN_SALT)


def generate_action_token(*, user_id: int, request_id: int, action: str) -> str:
    payload = json.dumps({"u": user_id, "r": request_id, "a": action})
    return _get_signer().sign(payload)


def decode_action_token(token: str, *, max_age: int | None = None) -> dict:
    raw = _get_signer().unsign(token, max_age=max_age or _DEFAULT_TOKEN_MAX_AGE)
    return json.loads(raw)


def build_action_link(*, action: str, request_id: int, user_id: int) -> str | None:
    token = generate_action_token(user_id=user_id, request_id=request_id, action=action)
    query = urlencode({"token": token})
    path = f"{reverse('indy_hub:bp_discord_action')}?{query}"
    return build_site_url(path)


__all__ = [
    "BadSignature",
    "SignatureExpired",
    "generate_action_token",
    "decode_action_token",
    "build_action_link",
    "_DEFAULT_TOKEN_MAX_AGE",
]
