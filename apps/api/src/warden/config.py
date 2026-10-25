"""Application settings, loaded from environment variables prefixed with WARDEN_."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WARDEN_", env_file=".env", extra="ignore")

    environment: str = "production"
    database_url: str = "sqlite+aiosqlite:///./data/warden.db"
    redis_url: str | None = None
    data_dir: Path = Path("./data")

    # Master key for the credential vault. When unset, a key file is generated
    # inside data_dir on first start (see security.vault).
    secret_key: str | None = None

    session_cookie_name: str = "warden_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = True

    # Scheduler loop runs in-process; disable for tests and one-off CLI use.
    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = 5.0

    # Allows the built-in deterministic UniFi simulator to be connected as a
    # demo controller. Demo data is always labelled as such.
    demo_controller_enabled: bool = True

    cors_origins: list[str] = Field(default_factory=list)
    # Base URL people use to open Warden; alert notifications link back to it when set.
    public_url: str | None = None
    run_migrations_on_start: bool = True

    # Syslog receiver (Log Management). 0 disables it; 514 needs privileges, so the default compose maps 5514.
    syslog_port: int = 0
    syslog_bind: str = "0.0.0.0"  # noqa: S104 - a receiver must listen on the LAN; set 127.0.0.1 to restrict
    log_retention_days: int = 14
    log_max_rows: int = 2_000_000

    # Flow collector (Traffic & Flow Analytics): NetFlow v5, v9 and IPFIX over UDP. 0 disables it.
    flow_port: int = 0
    flow_bind: str = "0.0.0.0"  # noqa: S104 - a collector must listen on the LAN; set 127.0.0.1 to restrict
    # Exporter addresses or CIDRs accepted; empty accepts any sender.
    flow_allowed_exporters: list[str] = Field(default_factory=list)
    # Packet sampling rate to assume when an exporter does not report one (0 = treat its counts as unsampled).
    flow_sampling_rate: int = 0
    flow_retention_days: int = 7
    flow_max_rows: int = 5_000_000

    # Outbound connections to user-supplied addresses (see security/outbound.py).
    # Private and loopback ranges are allowed (equipment lives there); link-local and
    # metadata addresses are refused unless allowed here.
    outbound_allow_link_local: bool = False
    outbound_allowed_cidrs: list[str] = Field(default_factory=list)
    outbound_blocked_cidrs: list[str] = Field(default_factory=list)

    # Request body limits (bytes). Restoring a backup has its own, larger limit.
    max_request_bytes: int = 2 * 1024 * 1024
    max_restore_bytes: int = 512 * 1024 * 1024

    # Sign-in rate limits, per client address and per username (any address), on top
    # of the per-address-and-username limit of 5.
    login_ip_max_failures: int = 20
    login_user_max_failures: int = 50

    # How many trusted proxies append to X-Forwarded-For before the API. The web
    # container's /api proxy passes the header through unchanged and adds nothing, so:
    # 0 = use the socket peer (default; behind the web container every client shares its
    # address), 1 = a TLS reverse proxy in front of the web container sets or appends
    # the real client address. Never more than the real number: extra hops would trust
    # client-supplied addresses. The per-address sign-in limit applies only when > 0.
    forwarded_hops: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
