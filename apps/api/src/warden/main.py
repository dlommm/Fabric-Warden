"""Application factory and lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete

from warden import __version__
from warden.api import auth, controllers, inventory, modules, system, users
from warden.app_state import AppState
from warden.config import Settings, get_settings
from warden.db import Base, Database, utcnow
from warden.events import EventBus
from warden.integrations.unifi.sync import sync_job
from warden.jobs import JobContext, JobSpec, Scheduler
from warden.models import AuthSession, JobRun
from warden.modules.service import ModuleService
from warden.security.vault import Vault, load_master_key

log = logging.getLogger("warden")


async def housekeeping(ctx: JobContext) -> dict[str, Any]:
    now = utcnow()
    s = await ctx.db.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=1)))
    j = await ctx.db.execute(delete(JobRun).where(JobRun.started_at < now - timedelta(days=30)))
    return {"sessions_pruned": s.rowcount, "job_runs_pruned": j.rowcount}


def build_state(settings: Settings) -> AppState:
    db = Database(settings.database_url)
    bus = EventBus()
    state = AppState(
        settings=settings,
        db=db,
        vault=Vault(load_master_key(settings.secret_key, settings.data_dir)),
        bus=bus,
        modules=ModuleService(bus),
    )
    state.scheduler = Scheduler(db.sessionmaker, state, settings.redis_url)
    state.scheduler.register(JobSpec("core.housekeeping", "core", "Expire sessions and prune job history", housekeeping, 3600))
    state.scheduler.register(JobSpec("unifi-network.sync", "unifi-network", "Poll controllers and sync inventory", sync_job, 60))
    return state


async def migrate(state: AppState) -> None:
    if state.settings.database_url.startswith("sqlite") and state.settings.database_url.endswith(":memory:"):
        async with state.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    from warden.migrations import upgrade_head

    await upgrade_head(state.db.engine)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    state = build_state(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.run_migrations_on_start:
            await migrate(state)
        async with state.db.sessionmaker() as db:
            await state.modules.ensure_core(db)
            await db.commit()
        if settings.scheduler_enabled:
            state.scheduler.start(settings.scheduler_tick_seconds)
        log.info("Fabric Warden %s started", __version__)
        yield
        await state.scheduler.stop()
        await state.db.dispose()

    app = FastAPI(
        title="Fabric Warden API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.warden = state
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    for r in (auth.router, users.router, modules.router, controllers.router, inventory.router, system.router):
        app.include_router(r)
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("warden.main:create_app", factory=True, host="0.0.0.0", port=8000, proxy_headers=True)
