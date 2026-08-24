"""Salesforce authentication.

Python owns the token. This is not a preference - it is the only option:

  * ``sf org display --json`` returns ``"[REDACTED]"`` for accessToken.
  * ``sf org auth show-access-token`` requires an interactive confirmation
    prompt and cannot be scripted.

Both were verified against this org. Where the CLI is needed (metadata retrieve,
destructive dry-run) it is *handed* a token via ``sf org login access-token``
rather than scraped for one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

from app.config import Settings, get_settings

log = structlog.get_logger()

# Refresh this far before nominal expiry so an in-flight request never races the
# boundary. Client-credentials tokens carry no expires_in, so this is applied to
# a conservative assumed lifetime instead.
_SKEW_SECONDS = 300
_ASSUMED_LIFETIME_SECONDS = 3600


@dataclass(frozen=True)
class AccessToken:
    value: str
    instance_url: str
    issued_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at

    def __repr__(self) -> str:  # never let a token reach a log or traceback
        return f"<AccessToken instance={self.instance_url} expires_in={self.ttl:.0f}s>"

    @property
    def ttl(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())


class TokenProvider(Protocol):
    """Auth strategy. Swapping client-credentials for JWT is a config change."""

    async def token(self, *, force_refresh: bool = False) -> AccessToken: ...


class ClientCredentialsProvider:
    """OAuth 2.0 client-credentials flow.

    Requires two switches on the Connected App, and the failure modes are
    indistinguishable without them:

      * "Enable Client Credentials Flow"      -> otherwise unsupported_grant_type
      * a "Run As" user under Edit Policies   -> otherwise invalid_grant

    The Run As user's permissions bound what the analysis can see. A restricted
    user silently narrows coverage, and narrowed coverage is indistinguishable
    from "this component is unused" - so the identity is recorded on every run.
    """

    #: Maps Salesforce's terse OAuth errors onto the actual misconfiguration.
    _HINTS = {
        "unsupported_grant_type": (
            "'Enable Client Credentials Flow' is not checked on the Connected App"
        ),
        "invalid_grant": (
            "No 'Run As' user assigned (App Manager > Manage > Edit Policies > "
            "Client Credentials Flow)"
        ),
        "invalid_client": (
            "Consumer Key/Secret mismatch, or the app has not finished "
            "propagating (allow ~10 minutes after creation)"
        ),
        "invalid_client_id": "Consumer Key is not valid for this org",
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._cached: AccessToken | None = None
        # Single-flight: without this, twenty workers hitting a 401 at once
        # would stampede the token endpoint. Losers wait and re-read the cache.
        self._lock = asyncio.Lock()

    async def token(self, *, force_refresh: bool = False) -> AccessToken:
        if not force_refresh and self._cached and not self._cached.expired:
            return self._cached

        async with self._lock:
            # Re-check: another coroutine may have refreshed while we waited.
            if not force_refresh and self._cached and not self._cached.expired:
                return self._cached
            self._cached = await self._fetch()
            return self._cached

    async def _fetch(self) -> AccessToken:
        form = {
            "grant_type": "client_credentials",
            "client_id": self._s.sf_client_id,
            "client_secret": self._s.sf_client_secret.get_secret_value(),
        }
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                self._s.sf_token_url,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if resp.status_code != 200:
            code = ""
            try:
                code = resp.json().get("error", "")
            except Exception:
                pass
            hint = self._HINTS.get(code)
            log.error("sf_auth_failed", status=resp.status_code, error=code, hint=hint)
            raise AuthError(
                f"Salesforce token request failed ({resp.status_code} {code})"
                + (f": {hint}" if hint else ""),
                error_code=code,
            )

        body = resp.json()
        now = time.monotonic()
        # Salesforce omits expires_in on this grant, so assume a conservative
        # lifetime and refresh early rather than discovering expiry via a 401.
        lifetime = int(body.get("expires_in", _ASSUMED_LIFETIME_SECONDS))
        token = AccessToken(
            value=body["access_token"],
            instance_url=body.get("instance_url", self._s.sf_instance_url).rstrip("/"),
            issued_at=now,
            expires_at=now + max(60, lifetime - _SKEW_SECONDS),
        )
        log.info("sf_auth_ok", instance=token.instance_url, ttl_s=int(token.ttl))
        return token


class AuthError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code


def build_token_provider(settings: Settings | None = None) -> TokenProvider:
    s = settings or get_settings()
    flow = s.sf_auth_flow.lower()
    if flow == "client_credentials":
        return ClientCredentialsProvider(s)
    if flow == "jwt":
        raise NotImplementedError(
            "JWT bearer flow is not implemented yet. It is the preferred path "
            "for deployment (no long-lived secret at rest); client_credentials "
            "is fine for local work."
        )
    raise ValueError(f"Unknown SF_AUTH_FLOW: {s.sf_auth_flow!r}")
