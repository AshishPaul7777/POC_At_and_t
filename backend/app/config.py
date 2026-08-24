"""Typed configuration loaded from .env.

Everything the analysis depends on is declared here rather than read ad hoc, so
a run's behaviour is reproducible from its recorded settings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8-sig",  # tolerate a BOM from Windows editors
        extra="ignore",
        case_sensitive=False,
    )

    # -- Salesforce connection -------------------------------------------------
    sf_auth_flow: str = "client_credentials"
    sf_client_id: str
    sf_client_secret: SecretStr
    sf_instance_url: str
    sf_api_version: str = "67.0"
    # `sf` is not reliably on PATH and two installs on this machine have already
    # drifted (2.146.3 vs 2.140.6), so the binary is pinned explicitly and its
    # version recorded in run metadata.
    sf_cli_path: str = "sf"

    # -- Authentication --------------------------------------------------------
    # The superuser is provisioned from the environment so a freshly deployed
    # VM has a way in before any user exists. It is reconciled on every boot,
    # which also makes "change the env and restart" a working password reset.
    auth_superuser_email: str = ""
    auth_superuser_password: SecretStr | None = None
    # Signs session cookies. Unset means a random per-process key, so every
    # restart logs everyone out -- fine locally, checked by deploy.sh for prod.
    auth_secret_key: SecretStr | None = None
    # How long a session lasts. Short enough that a forgotten browser on a
    # shared machine expires on its own; long enough for a working day.
    auth_session_hours: int = 12

    # -- Rate-limit governor ---------------------------------------------------
    sf_api_safety_floor: int = 1500
    # The 5-concurrent ceiling applies only to requests exceeding 20s. Capping
    # every ordinary call at 15s keeps it out of that class entirely, so short
    # calls draw from a generous pool and only declared-long operations contend.
    sf_client_timeout_seconds: float = 15.0
    sf_max_concurrent_short: int = 10
    sf_max_concurrent_long: int = 4  # of 5; one slot left for interactive use
    sf_composite_batch_size: int = 25
    # MEASURED against this org, not assumed. Widely-repeated documentation
    # claims a composite call counts as ONE request; it does not. Each
    # subrequest costs ~0.3-0.5 calls, making real compression ~3x rather than
    # 25x. Re-measured per org at connect time; this is only the fallback.
    sf_composite_compression_default: float = 3.0

    # -- LLM -------------------------------------------------------------------
    llm_enabled: bool = True
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5"
    # Temperature 0 is deliberate: the model narrates a verdict the rule engine
    # has already decided. It must not be creative, and it never classifies.
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4000
    llm_max_concurrency: int = 5
    llm_timeout_seconds: float = 120.0
    # Optional gateway/proxy in front of the provider. Read from the standard
    # provider env vars (ANTHROPIC_BASE_URL / OPENAI_BASE_URL) so an existing
    # shell environment works without duplicating config here.
    anthropic_base_url: str | None = None
    anthropic_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    llm_base_url: str | None = None      # explicit override, any provider

    # -- Infrastructure --------------------------------------------------------
    database_url: str
    # Redis is unavailable (Docker Desktop is blocked by org policy), so event
    # fan-out uses Postgres LISTEN/NOTIFY. Notifications are wake-ups only; the
    # events table remains the source of truth, so a dropped one costs nothing.
    event_channel: str = "sfc_events"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    apexd_url: str = "http://127.0.0.1:7000"
    # Where retrieved metadata lands. A relative value is resolved against the
    # repo root: an absolute POSIX path like `/workspace` (correct inside a
    # container) resolves to the DRIVE ROOT on Windows, which silently scatters
    # a retrieved org across C:\.
    workspace_dir: Path = Path(".cache/workspace")
    cors_origins: str = "http://localhost:5173"

    # -- Analysis tuning -------------------------------------------------------
    # Half-built features look exactly like dead ones, so anything touched
    # recently is capped at NEEDS_REVIEW.
    analysis_recent_change_days: int = 90
    analysis_exclude_namespaced: bool = True
    analysis_enable_delete_rehearsal: bool = True
    analysis_rehearsal_batch_size: int = 50
    # Graph Engine is Developer Preview, OOM-prone, and blind to declarative
    # callers. Off by default; corroboration only, never a primary signal.
    analysis_enable_sfge: bool = False
    sfge_max_heap: str = "4g"
    sfge_timeout_seconds: int = 900
    # Fraction of required collectors that must succeed before UNUSED is
    # permitted. 1.0 means all of them: absence of evidence is not evidence of
    # absence. Lowering this trades safety for a shorter review queue.
    analysis_completeness_threshold: float = Field(default=1.0, ge=0.0, le=1.0)

    # -- Runtime ---------------------------------------------------------------
    log_level: str = "INFO"
    sse_heartbeat_seconds: int = 15

    @field_validator("sf_instance_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver "
                "(postgresql+asyncpg://...); the sync driver would block the "
                "event loop that the SSE stream runs on"
            )
        return v

    @property
    def workspace(self) -> Path:
        """Absolute workspace path, resolved against the repo root if relative."""
        p = Path(self.workspace_dir)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sf_token_url(self) -> str:
        # Client-credentials tokens are issued only from the My Domain host,
        # never from login.salesforce.com.
        return f"{self.sf_instance_url}/services/oauth2/token"

    def sf_data_url(self, path: str) -> str:
        return f"{self.sf_instance_url}/services/data/v{self.sf_api_version}/{path.lstrip('/')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
