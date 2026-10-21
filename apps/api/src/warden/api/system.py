"""Health, jobs, audit trail and live event stream."""

from __future__ import annotations

import asyncio
import json
import platform
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text

from warden import __version__, audit
from warden.api.deps import DB, Allowed, State
from warden.models import AuditRecord, EventRecord, JobRun
from warden.security.rbac import P

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(db: DB) -> dict:
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from exc
    return {"status": "ok"}


@router.get("/system/health")
async def system_health(db: DB, state: State, principal: Allowed(P.SYSTEM_READ)) -> dict:
    checks = []
    try:
        await db.execute(text("SELECT 1"))
        checks.append({"id": "database", "status": "ok", "detail": state.db.engine.dialect.name})
    except Exception as exc:  # pragma: no cover - exercised only on broken DBs
        checks.append({"id": "database", "status": "failed", "detail": str(exc)})
    if state.settings.redis_url:
        try:
            r = state.scheduler.locker._redis
            await r.ping()  # type: ignore[union-attr]
            checks.append({"id": "redis", "status": "ok", "detail": "job coordination"})
        except Exception as exc:
            checks.append({"id": "redis", "status": "degraded", "detail": f"unreachable: {exc}"})
    else:
        checks.append({"id": "redis", "status": "skipped", "detail": "not configured; single-instance locking"})
    running = state.scheduler._task is not None and not state.scheduler._task.done()
    checks.append({"id": "scheduler", "status": "ok" if running else "stopped", "detail": f"{len(state.scheduler.jobs)} jobs registered"})
    failed = await db.scalar(select(func.count()).select_from(JobRun).where(JobRun.status == "failed")) or 0
    ok, bad = await audit.verify_chain(db)
    checks.append(
        {"id": "audit_chain", "status": "ok" if ok else "failed", "detail": "hash chain intact" if ok else f"chain broken at record {bad}"}
    )
    return {
        "version": __version__,
        "python": platform.python_version(),
        "environment": state.settings.environment,
        "checks": checks,
        "failed_job_runs": failed,
    }


@router.get("/jobs")
async def list_jobs(db: DB, state: State, principal: Allowed(P.JOBS_READ)) -> dict:
    enabled = await state.modules.enabled_ids(db)
    jobs = []
    for spec in state.scheduler.jobs.values():
        last = await db.scalar(select(JobRun).where(JobRun.job_id == spec.id).order_by(JobRun.started_at.desc()).limit(1))
        jobs.append(
            {
                "id": spec.id,
                "title": spec.title,
                "module_id": spec.module_id,
                "module_enabled": spec.module_id in enabled,
                "interval_seconds": spec.interval_seconds,
                "next_due": state.scheduler.next_due.get(spec.id).isoformat() if spec.id in state.scheduler.next_due else None,
                "last_run": _run(last) if last else None,
            }
        )
    return {"jobs": jobs}


def _run(r: JobRun) -> dict:
    return {
        "id": r.id,
        "job_id": r.job_id,
        "trigger": r.trigger,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "summary": r.summary,
        "error": r.error,
    }


@router.get("/jobs/runs")
async def job_runs(db: DB, principal: Allowed(P.JOBS_READ), limit: Annotated[int, Query(le=200)] = 50) -> dict:
    rows = await db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit))
    return {"runs": [_run(r) for r in rows]}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str, db: DB, state: State, principal: Allowed(P.JOBS_RUN)) -> dict:
    if job_id not in state.scheduler.jobs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown job")
    await audit.record(db, principal.actor, "job.run", target_type="job", target_id=job_id)
    await db.commit()
    return _run(await state.scheduler.run(job_id, "manual"))


@router.get("/audit")
async def audit_log(
    db: DB,
    principal: Allowed(P.AUDIT_READ),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_seq: int | None = None,
    action: str | None = None,
    outcome: str | None = None,
) -> dict:
    q = select(AuditRecord).order_by(AuditRecord.seq.desc()).limit(limit)
    if before_seq:
        q = q.where(AuditRecord.seq < before_seq)
    if action:
        q = q.where(AuditRecord.action.like(f"{action}%"))
    if outcome:
        q = q.where(AuditRecord.outcome == outcome)
    rows = list(await db.scalars(q))
    ok, bad = await audit.verify_chain(db)
    return {
        "chain_intact": ok,
        "first_bad_seq": bad,
        "records": [
            {
                "seq": r.seq,
                "id": r.id,
                "occurred_at": r.occurred_at.isoformat(),
                "actor_type": r.actor_type,
                "actor_name": r.actor_name,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "outcome": r.outcome,
                "detail": r.detail,
                "ip_address": r.ip_address,
                "hash": r.hash,
            }
            for r in rows
        ],
    }


@router.get("/events")
async def list_events(
    db: DB, principal: Allowed(P.INVENTORY_READ), limit: Annotated[int, Query(le=500)] = 100, type: str | None = None
) -> dict:
    q = select(EventRecord).order_by(EventRecord.seq.desc()).limit(limit)
    if type:
        q = q.where(EventRecord.type.like(f"{type}%"))
    return {
        "events": [
            {
                "id": e.id,
                "type": e.type,
                "occurred_at": e.occurred_at.isoformat(),
                "source": e.source,
                "severity": e.severity,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "payload": e.payload,
            }
            for e in await db.scalars(q)
        ]
    }


@router.get("/events/stream")
async def event_stream(request: Request, state: State, principal: Allowed(P.INVENTORY_READ)) -> StreamingResponse:
    queue = state.bus.listen()

    async def gen():  # type: ignore[no-untyped-def]
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {event.type}\ndata: {json.dumps(event.as_dict())}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            state.bus.unlisten(queue)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
