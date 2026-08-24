"""Signed session tokens.

A minimal JWT-shaped token: base64 payload, HMAC-SHA256 signature, no algorithm
field. Omitting the algorithm header is deliberate -- "alg" is the source of the
classic JWT confusion attacks, and there is nothing to negotiate when both ends
are this application.

The token carries identity and role so an authenticated request costs no
database round trip. The cost is that a role change or deactivation only takes
effect when the token expires, so the lifetime is kept short and the user
lookup still runs for anything that grants access to others.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import structlog

from app.config import get_settings

log = structlog.get_logger()

_ephemeral: str | None = None


def _secret() -> str:
    """The signing key, or a per-process random one with a loud warning.

    Falling back keeps development frictionless. In production it means every
    restart silently logs everyone out, so deploy.sh refuses to start without
    AUTH_SECRET_KEY set.
    """
    global _ephemeral
    configured = get_settings().auth_secret_key
    if configured:
        return configured.get_secret_value()
    if _ephemeral is None:
        _ephemeral = secrets.token_urlsafe(48)
        log.warning("auth_secret_key_missing",
                    detail="using a random per-process key; sessions will not "
                           "survive a restart. Set AUTH_SECRET_KEY.")
    return _ephemeral


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(payload: dict[str, Any], ttl_seconds: int) -> str:
    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(_secret().encode(), raw.encode(), hashlib.sha256).digest()
    return f"{raw}.{_b64(sig)}"


def verify(token: str | None) -> dict[str, Any] | None:
    """Return the payload, or None. Never raises and never explains why."""
    if not token or "." not in token:
        return None
    raw, _, sig = token.partition(".")
    expected = hmac.new(_secret().encode(), raw.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(sig), expected):
            return None
        payload = json.loads(_unb64(raw))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload
