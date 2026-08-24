"""Multi-org connection registry.

The analyser must work against any Salesforce org, not the one it was developed
against. That means no org identity may be baked into code: instance URLs,
credentials, editions, API limits and even which objects are queryable all vary
per org and must be supplied or discovered rather than assumed.

Two sources, checked in order:

  1. ``orgs.json`` in the repo root - a list of orgs, for real multi-org use.
  2. The ``SF_*`` variables in ``.env`` - a single org, which keeps the simple
     one-org case simple.

``orgs.json`` is gitignored: it holds client secrets.

    {
      "orgs": [
        {"alias": "acme-prod",  "instance_url": "https://acme.my.salesforce.com",
         "client_id": "...", "client_secret": "...", "api_version": "67.0"},
        {"alias": "acme-uat",   "instance_url": "https://acme--uat.sandbox.my.salesforce.com",
         "client_id": "...", "client_secret": "...", "auth_flow": "client_credentials"}
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from app.config import REPO_ROOT, Settings, get_settings

log = structlog.get_logger()

ORGS_FILE = REPO_ROOT / "orgs.json"


@dataclass
class OrgConnection:
    """Everything needed to reach one org. No defaults that imply an identity."""

    alias: str
    instance_url: str
    client_id: str
    client_secret: str
    api_version: str = "67.0"
    auth_flow: str = "client_credentials"
    # Populated by capability probing, never hardcoded: editions differ by an
    # order of magnitude in API allowance (Developer 15k vs Enterprise 100k+).
    org_id: str | None = None
    edition: str | None = None
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.instance_url = self.instance_url.rstrip("/")

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return f"<OrgConnection {self.alias} {self.instance_url}>"

    @property
    def token_url(self) -> str:
        # Client-credentials tokens are issued only from the org's own My Domain
        # host, never from login.salesforce.com.
        return f"{self.instance_url}/services/oauth2/token"

    def data_path(self, path: str) -> str:
        return f"/services/data/v{self.api_version}/{path.lstrip('/')}"

    @property
    def is_sandbox_like(self) -> bool:
        """Heuristic only, used for warnings - never for a safety decision.

        Deliberately not trusted: a scratch or developer org can carry a
        production-looking domain, and treating a guess as authoritative is how a
        tool ends up doing something irreversible in production.
        """
        host = self.instance_url.lower()
        return any(m in host for m in (".sandbox.", "--", "develop.my.salesforce",
                                       "scratch", "-dev-ed."))


class ConnectionRegistry:
    def __init__(self, connections: dict[str, OrgConnection]) -> None:
        self._conns = connections

    def __len__(self) -> int:
        return len(self._conns)

    @property
    def aliases(self) -> list[str]:
        return sorted(self._conns)

    def get(self, alias: str | None = None) -> OrgConnection:
        """Fetch a connection. With no alias, only unambiguous cases succeed."""
        if alias:
            if alias not in self._conns:
                raise KeyError(
                    f"No org connection named {alias!r}. "
                    f"Known: {', '.join(self.aliases) or '(none)'}"
                )
            return self._conns[alias]
        if len(self._conns) == 1:
            return next(iter(self._conns.values()))
        raise ValueError(
            f"{len(self._conns)} orgs are configured "
            f"({', '.join(self.aliases)}); specify which one to use. "
            "Refusing to guess - picking the wrong org would analyse, and "
            "potentially propose deletions against, the wrong system."
        )

    def all(self) -> list[OrgConnection]:
        return [self._conns[a] for a in self.aliases]


def load_registry(settings: Settings | None = None) -> ConnectionRegistry:
    s = settings or get_settings()
    conns: dict[str, OrgConnection] = {}

    if ORGS_FILE.exists():
        try:
            raw = json.loads(ORGS_FILE.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{ORGS_FILE.name} is not valid JSON: {e}") from e
        for entry in raw.get("orgs", []):
            missing = [k for k in ("alias", "instance_url", "client_id", "client_secret")
                       if not entry.get(k)]
            if missing:
                raise ValueError(
                    f"{ORGS_FILE.name}: org entry {entry.get('alias', '?')!r} "
                    f"is missing {', '.join(missing)}"
                )
            c = OrgConnection(
                alias=entry["alias"],
                instance_url=entry["instance_url"],
                client_id=entry["client_id"],
                client_secret=entry["client_secret"],
                api_version=str(entry.get("api_version", s.sf_api_version)),
                auth_flow=entry.get("auth_flow", "client_credentials"),
                notes=entry.get("notes", {}),
            )
            conns[c.alias] = c
        log.info("orgs_loaded_from_file", file=ORGS_FILE.name, count=len(conns))

    # The single-org .env path. Registered under a neutral alias derived from the
    # host, so no org name is ever implied by the code.
    if not conns and s.sf_client_id:
        alias = _alias_from_url(s.sf_instance_url)
        conns[alias] = OrgConnection(
            alias=alias,
            instance_url=s.sf_instance_url,
            client_id=s.sf_client_id,
            client_secret=s.sf_client_secret.get_secret_value(),
            api_version=s.sf_api_version,
            auth_flow=s.sf_auth_flow,
        )
        log.info("org_loaded_from_env", alias=alias)

    if not conns:
        raise ValueError(
            "No Salesforce org is configured. Provide SF_CLIENT_ID / "
            "SF_CLIENT_SECRET / SF_INSTANCE_URL in .env, or create orgs.json."
        )
    return ConnectionRegistry(conns)


def _alias_from_url(instance_url: str) -> str:
    """Derive a stable, readable alias from the host.

    Used only so the single-org path has *some* label; the authoritative
    identifier is always the org id returned by the org itself.
    """
    host = instance_url.split("://", 1)[-1].split("/", 1)[0]
    return host.split(".", 1)[0] or "default"
