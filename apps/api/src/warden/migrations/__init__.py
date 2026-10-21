"""Alembic migrations, runnable programmatically at startup or via `warden-migrate`."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

HERE = Path(__file__).parent


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def upgrade_head(engine: AsyncEngine) -> None:
    def _run(conn) -> None:  # type: ignore[no-untyped-def]
        cfg = alembic_config()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")

    async with engine.begin() as conn:
        await conn.run_sync(_run)
