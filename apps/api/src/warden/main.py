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

from warden import __version__, metrics, monitoring
from warden import automation as automation_engine
from warden.api import (
    ai,
    alerts,
    analytics,
    assets,
    auth,
    automation,
    backup,
    batches,
    cabling,
    changes,
    clients,
    compliance,
    config_history,
    controllers,
    dcim,
    demo_infra,
    docs,
    firewall,
    home_assistant,
    inventory,
    investigate,
    ipam,
    logs,
    mcp,
    modules,
    notifications,
    power,
    protect,
    servers,
    snmp,
    soc,
    system,
    topology,
    twin,
    users,
    wan,
)
from warden.app_state import AppState
from warden.changes.batches import advance_job as bulk_advance_job
from warden.changes.engine import verify_job as changes_verify_job
from warden.config import Settings, get_settings
from warden.db import Base, Database, utcnow
from warden.events import EventBus
from warden.integrations.ecosystem import home_assistant_sync_job, protect_sync_job
from warden.integrations.hosts.sync import sync_job as servers_sync_job
from warden.integrations.probe import probe_job
from warden.integrations.snmp.sync import poll_job as snmp_poll_job
from warden.integrations.unifi.sync import sync_job
from warden.jobs import JobContext, JobSpec, Scheduler
from warden.models import AuthSession, JobRun, NotificationDelivery
from warden.modules.service import ModuleService
from warden.security import outbound
from warden.security.limits import BodySizeLimit, SecurityHeaders
from warden.security.redact import install_log_filter
from warden.security.vault import Vault, load_master_key
from warden.syslog import Ingest, retention_job

log = logging.getLogger("warden")


async def housekeeping(ctx: JobContext) -> dict[str, Any]:
    now = utcnow()
    s = await ctx.db.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=1)))
    j = await ctx.db.execute(delete(JobRun).where(JobRun.started_at < now - timedelta(days=30)))
    n = await ctx.db.execute(delete(NotificationDelivery).where(NotificationDelivery.created_at < now - timedelta(days=30)))
    a = await monitoring.prune(ctx.db, now - timedelta(days=365))
    return {"sessions_pruned": s.rowcount, "job_runs_pruned": j.rowcount, "deliveries_pruned": n.rowcount, "alerts_pruned": a}


async def metrics_job(ctx: JobContext) -> dict[str, Any]:
    return await metrics.rollup(ctx.db)


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
    state.scheduler.register(JobSpec("core.metrics", "core", "Roll up and prune metric history", metrics_job, 900))
    state.scheduler.register(JobSpec("unifi-network.sync", "unifi-network", "Poll controllers and sync inventory", sync_job, 60))
    state.scheduler.register(JobSpec("monitoring.evaluate", "monitoring", "Evaluate alert rules", monitoring.evaluate_job, 60))
    state.scheduler.register(JobSpec("wan.probe", "wan", "Probe latency and loss", probe_job, 60))
    state.syslog = Ingest(state)
    state.scheduler.register(JobSpec("logs.retention", "logs", "Expire old log events", retention_job, 3600))
    state.scheduler.register(JobSpec("servers.sync", "servers", "Poll servers and hypervisors", servers_sync_job, 60))
    state.scheduler.register(JobSpec("unifi-protect.sync", "unifi-protect", "Poll UniFi Protect", protect_sync_job, 60))
    state.scheduler.register(JobSpec("home-assistant.sync", "home-assistant", "Poll Home Assistant", home_assistant_sync_job, 60))
    state.scheduler.register(JobSpec("snmp.poll", "snmp", "Poll SNMP interface counters", snmp_poll_job, 60))
    state.scheduler.register(
        JobSpec("changes.verify", "change-engine", "Verify applied changes and expire stale proposals", changes_verify_job, 30)
    )
    state.scheduler.register(JobSpec("bulk-ops.advance", "bulk-ops", "Continue running bulk operations", bulk_advance_job, 30))
    state.scheduler.register(
        JobSpec("automation.evaluate", "automation", "Evaluate automation rules against new events", automation_engine.evaluate_job, 30)
    )
    return state


async def migrate(state: AppState) -> None:
    if state.settings.database_url.startswith("sqlite") and state.settings.database_url.endswith(":memory:"):
        async with state.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    from warden.migrations import upgrade_head

    await upgrade_head(state.db.engine)


RESTORE_PATH = "/api/backup/restore"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    outbound.set_policy(outbound.OutboundPolicy.from_settings(settings))
    auth.throttle.clear()
    install_log_filter()
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
        if settings.syslog_port:
            await state.syslog.start(settings.syslog_bind, settings.syslog_port)
        log.info("Fabric Warden %s started", __version__)
        yield
        await state.scheduler.stop()
        await state.syslog.stop()
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

    app.add_middleware(SecurityHeaders)
    # Outermost: refuse oversized bodies before anything parses them.
    app.add_middleware(BodySizeLimit, limit=settings.max_request_bytes, overrides={RESTORE_PATH: settings.max_restore_bytes})

    for r in (
        auth.router,
        users.router,
        modules.router,
        controllers.router,
        inventory.router,
        clients.router,
        ipam.router,
        analytics.metrics_router,
        analytics.router,
        alerts.router,
        notifications.router,
        wan.router,
        dcim.router,
        cabling.router,
        power.router,
        assets.router,
        topology.router,
        docs.router,
        demo_infra.router,
        servers.router,
        snmp.router,
        protect.router,
        home_assistant.router,
        mcp.router,
        changes.router,
        batches.router,
        automation.router,
        twin.router,
        ai.providers_router,
        ai.agents_router,
        firewall.router,
        config_history.router,
        compliance.router,
        logs.router,
        soc.router,
        investigate.router,
        system.router,
        backup.router,
    ):
        app.include_router(r)
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    # Client addresses come from WARDEN_FORWARDED_HOPS (api.deps.client_ip), not uvicorn's proxy headers.
    uvicorn.run("warden.main:create_app", factory=True, host="0.0.0.0", port=8000, proxy_headers=False)
