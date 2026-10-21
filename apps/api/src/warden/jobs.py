"""In-process job scheduler.

Jobs belong to modules; a job only runs while its module is enabled. When a
Redis URL is configured, a short-lived lock per job keeps multiple API replicas
from running the same scheduled job concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from warden.db import utcnow
from warden.models import JobRun

if TYPE_CHECKING:
    from warden.app_state import AppState

log = logging.getLogger(__name__)


@dataclass
class JobContext:
    db: AsyncSession
    state: AppState
    trigger: str
    params: dict[str, Any] = field(default_factory=dict)


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any]]]


@dataclass
class JobSpec:
    id: str
    module_id: str
    title: str
    handler: JobHandler
    interval_seconds: int | None = None


class Locker:
    """Per-job mutual exclusion; Redis-backed when available."""

    def __init__(self, redis_url: str | None) -> None:
        self._local: set[str] = set()
        self._redis = None
        if redis_url:
            import redis.asyncio as redis

            self._redis = redis.from_url(redis_url)

    async def acquire(self, key: str, ttl: int) -> bool:
        if key in self._local:
            return False
        if self._redis is not None:
            ok = await self._redis.set(f"warden:lock:{key}", "1", nx=True, ex=max(ttl, 5))
            if not ok:
                return False
        self._local.add(key)
        return True

    async def release(self, key: str) -> None:
        self._local.discard(key)
        if self._redis is not None:
            await self._redis.delete(f"warden:lock:{key}")


class Scheduler:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], state: AppState, redis_url: str | None = None):
        self.sessionmaker = sessionmaker
        self.state = state
        self.jobs: dict[str, JobSpec] = {}
        self.next_due: dict[str, datetime] = {}
        self.locker = Locker(redis_url)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def register(self, spec: JobSpec) -> None:
        self.jobs[spec.id] = spec
        self.next_due[spec.id] = utcnow()

    async def run(self, job_id: str, trigger: str = "manual", params: dict[str, Any] | None = None) -> JobRun:
        spec = self.jobs[job_id]
        async with self.sessionmaker() as db:
            run = JobRun(job_id=job_id, module_id=spec.module_id, trigger=trigger, status="running")
            if not await self.state.modules.is_enabled(db, spec.module_id):
                run.status = "skipped"
                run.finished_at = utcnow()
                run.summary = {"reason": f"module {spec.module_id} is disabled"}
                db.add(run)
                await db.commit()
                return run
            if not await self.locker.acquire(job_id, spec.interval_seconds or 300):
                run.status = "skipped"
                run.finished_at = utcnow()
                run.summary = {"reason": "already running"}
                db.add(run)
                await db.commit()
                return run
            db.add(run)
            await db.commit()
        try:
            async with self.sessionmaker() as db:
                summary = await spec.handler(JobContext(db=db, state=self.state, trigger=trigger, params=params or {}))
                await db.commit()
            status, error = "succeeded", None
        except Exception as exc:
            log.exception("job %s failed", job_id)
            summary, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"
        finally:
            await self.locker.release(job_id)
        async with self.sessionmaker() as db:
            run = await db.get(JobRun, run.id)  # type: ignore[assignment]
            run.status = status
            run.summary = summary
            run.error = error
            run.finished_at = utcnow()
            await db.commit()
        return run

    async def tick(self) -> None:
        now = utcnow()
        for job_id, spec in self.jobs.items():
            if spec.interval_seconds is None or self.next_due[job_id] > now:
                continue
            self.next_due[job_id] = now + timedelta(seconds=spec.interval_seconds)
            asyncio.create_task(self.run(job_id, "schedule"))

    def start(self, tick_seconds: float) -> None:
        async def loop() -> None:
            while not self._stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("scheduler tick failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=tick_seconds)
                except TimeoutError:
                    pass

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
