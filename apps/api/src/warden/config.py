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
    run_migrations_on_start: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
