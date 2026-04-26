"""Compatibility shim for ESI HTTP exceptions.

Provides a stable set of names (``HTTPError``, ``HTTPBadGateway``,
``HTTPGatewayTimeout``, ``HTTPServiceUnavailable``) that work both with the
legacy Bravado/Swagger client (django-esi <= 8) and the new OpenAPI client
(django-esi >= 9, which removed ``bravado``).

Usage::

    from indy_hub.services._esi_compat import (
        HTTPError,
        HTTPBadGateway,
        HTTPGatewayTimeout,
        HTTPServiceUnavailable,
    )

When using ``HTTPError`` in an ``except`` clause it may be either an exception
class (Bravado) or a tuple of exception classes (django-esi 9), both of which
are accepted by Python's ``except``.

For type annotations, prefer ``from __future__ import annotations`` so that
the alias is treated as a string and remains valid at runtime.
"""

from __future__ import annotations

try:
    # django-esi <= 8 — Bravado is installed and shipped with these classes.
    # Third Party
    from bravado.exception import (
        HTTPBadGateway,
        HTTPError,
        HTTPGatewayTimeout,
        HTTPServiceUnavailable,
    )

    BRAVADO_AVAILABLE = True
except ImportError:  # pragma: no cover - django-esi >= 9 path
    BRAVADO_AVAILABLE = False

    try:
        # django-esi >= 9 — replaces bravado with esi.exceptions
        # Alliance Auth
        from esi.exceptions import HTTPClientError, HTTPServerError
    except ImportError:  # last resort fallback

        class HTTPClientError(Exception):
            """Fallback ESI 4xx error when neither bravado nor esi.exceptions is available."""

        class HTTPServerError(Exception):
            """Fallback ESI 5xx error when neither bravado nor esi.exceptions is available."""

    # ``HTTPError`` historically caught both 4xx and 5xx in Bravado. Use a
    # tuple so ``except HTTPError`` keeps the same coverage on django-esi 9.
    HTTPError = (HTTPClientError, HTTPServerError)

    # The 502/503/504 specific Bravado classes do not exist in django-esi 9;
    # alias them to the broader 5xx parent so Celery's ``autoretry_for`` and
    # ``except`` clauses still work. At runtime, this means we retry on any
    # 5xx, which is acceptable for these helpers.
    HTTPBadGateway = HTTPServerError
    HTTPGatewayTimeout = HTTPServerError
    HTTPServiceUnavailable = HTTPServerError


__all__ = [
    "BRAVADO_AVAILABLE",
    "HTTPBadGateway",
    "HTTPError",
    "HTTPGatewayTimeout",
    "HTTPServiceUnavailable",
]
