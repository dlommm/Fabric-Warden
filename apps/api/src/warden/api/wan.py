"""WAN: interfaces as the controller lists them, gateway health and Warden's own probe."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from warden import audit, metrics
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.integrations import probe
from warden.models import Device, MetricSeries, Setting, Site, Wan
from warden.security.rbac import P

router = APIRouter(prefix="/api/wan", tags=["wan"], dependencies=[Depends(require_module("wan"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.MONITORING_MANAGE)
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9-]{1,63})(\.[A-Za-z0-9-]{1,63})*$")


class Target(BaseModel):
    host: str
    port: int = Field(443, ge=1, le=65535)
    label: str = Field("", max_length=64)

    @field_validator("host")
    @classmethod
    def host_ok(cls, v: str) -> str:
        v = v.strip()
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            pass
        if not HOST_RE.match(v):
            raise ValueError("enter a hostname or IP address, without a scheme or path")
        return v.lower()


class ProbeConfig(BaseModel):
    targets: list[Target] = Field(default_factory=list, max_length=5)
    attempts: int = Field(3, ge=1, le=10)
    timeout_ms: int = Field(1500, ge=200, le=5000)


@router.get("")
async def overview(db: DB, principal: Reader) -> dict:
    now = utcnow()
    sites = {s.id: s.name for s in await db.scalars(select(Site))}
    wans = [
        {"id": w.id, "name": w.name, "site": sites.get(w.site_id), "observed_at": w.observed_at, "present": w.present, "is_demo": w.is_demo}
        for w in await db.scalars(select(Wan).order_by(Wan.name))
    ]
    gateways = []
    for d in await db.scalars(select(Device).where(Device.present.is_(True))):
        if "gateway" not in d.features:
            continue
        st = d.stats or {}
        gateways.append(
            {
                "id": d.id,
                "name": d.name,
                "model": d.model,
                "state": d.state,
                "ip_address": d.ip_address,
                "firmware_version": d.firmware_version,
                "cpu_pct": st.get("cpuUtilizationPct"),
                "mem_pct": st.get("memoryUtilizationPct"),
                "uptime_sec": st.get("uptimeSec"),
                "stats_observed_at": d.stats_observed_at,
                "is_demo": d.is_demo,
            }
        )
    cfg = await probe.load_config(db)
    series = {
        (s.metric, s.entity_id): s
        for s in await db.scalars(select(MetricSeries).where(MetricSeries.metric.in_(["wan.latency_ms", "wan.loss_pct"])))
    }
    results = []
    for t in cfg["targets"]:
        tid = probe.target_id(t)
        lat, loss = series.get(("wan.latency_ms", tid)), series.get(("wan.loss_pct", tid))
        results.append(
            {
                "target": tid,
                "host": t["host"],
                "port": t["port"],
                "label": t.get("label") or t["host"],
                "latency_ms": lat.last_value if lat and not metrics.is_stale(lat, now) else None,
                "loss_pct": loss.last_value if loss and not metrics.is_stale(loss, now) else None,
                "last_ts": loss.last_ts if loss else None,
                "stale": loss is None or metrics.is_stale(loss, now),
            }
        )
    return {"wans": wans, "gateways": gateways, "probe": {**cfg, "results": results}}


@router.put("/probe")
async def set_probe(body: ProbeConfig, db: DB, principal: Manager) -> dict:
    value = body.model_dump()
    keys = [probe.target_id(t) for t in value["targets"]]
    if len(set(keys)) != len(keys):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "targets: each host and port can appear once")
    row = await db.get(Setting, probe.SETTING_KEY)
    if row is None:
        db.add(Setting(key=probe.SETTING_KEY, value=value))
    else:
        row.value = value
    await audit.record(
        db, principal.actor, "wan.probe.configure", target_type="setting", target_id=probe.SETTING_KEY, detail={"targets": keys}
    )
    return value


@router.post("/probe/run")
async def run_probe(state: State, db: DB, principal: Manager) -> dict[str, Any]:
    await db.commit()  # the job runs in its own session and must see the saved config
    run = await state.scheduler.run("wan.probe", "manual")
    return {"status": run.status, "summary": run.summary, "error": run.error}
