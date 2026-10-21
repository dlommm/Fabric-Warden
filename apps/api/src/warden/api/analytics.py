"""Metric queries and Analytics Studio dashboards.

`/api/metrics` serves any module that consumes metric history (dashboards,
alerts, WAN); `/api/analytics` holds the dashboards themselves.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from warden import audit, metrics
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.models import Dashboard, MetricSeries
from warden.security.rbac import P


async def _any_consumer(state: State, db: DB) -> None:
    enabled = await state.modules.enabled_ids(db)
    if not any(m in enabled for m in metrics.CONSUMERS):
        raise HTTPException(status.HTTP_409_CONFLICT, "metric history needs Analytics Studio, Monitoring or WAN enabled")


metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(_any_consumer)])
router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_module("analytics-studio"))])

Reader = Allowed(P.INVENTORY_READ)
Editor = Allowed(P.ANALYTICS_EDIT)
RangeKey = Literal["1h", "6h", "24h", "7d", "30d", "90d"]


def _metric(metric_id: str) -> metrics.MetricDef:
    m = metrics.METRICS.get(metric_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown metric {metric_id}")
    return m


def _def(m: metrics.MetricDef) -> dict[str, Any]:
    return {
        "id": m.id,
        "label": m.label,
        "unit": m.unit,
        "entity_type": m.entity_type,
        "description": m.description,
        "higher_is_worse": m.higher_is_worse,
    }


def _series(s: MetricSeries, now: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "entity_type": s.entity_type,
        "entity_id": s.entity_id,
        "label": s.label,
        "is_demo": s.is_demo,
        "last_value": s.last_value,
        "last_ts": s.last_ts,
        "stale": metrics.is_stale(s, now),
    }


@metrics_router.get("")
async def catalog(db: DB, principal: Reader) -> dict:
    now = utcnow()
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for s in await db.scalars(select(MetricSeries).order_by(MetricSeries.label)):
        by_metric.setdefault(s.metric, []).append(_series(s, now))
    return {
        "items": [{**_def(m), "series": by_metric.get(m.id, [])} for m in metrics.METRICS.values()],
        "ranges": list(metrics.RANGES),
        "retention": {"raw_hours": 48, "five_minute_days": 30, "hourly_days": 400},
    }


class Query(BaseModel):
    metric: str
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    range: RangeKey = "24h"
    limit: int = Field(5, ge=1, le=20)


async def run_query(db: Any, q: Query) -> dict[str, Any]:
    m = _metric(q.metric)
    now = utcnow()
    span, tier, _ = metrics.plan(q.range)
    stmt = select(MetricSeries).where(MetricSeries.metric == q.metric)
    if q.entity_ids:
        stmt = stmt.where(MetricSeries.entity_id.in_(q.entity_ids))
    rows = [s for s in await db.scalars(stmt) if s.last_ts and (now - s.last_ts).total_seconds() <= span]
    total = len(rows)
    # Worst first, so a "top 5" of a fleet shows what needs attention.
    rows.sort(key=lambda s: (-(s.last_value or 0) if m.higher_is_worse else (s.last_value or 0), s.label))
    rows = rows[: q.limit] if not q.entity_ids else rows
    pts, step = await metrics.points(db, [s.id for s in rows], q.range, now)
    series = []
    for s in rows:
        p = pts.get(s.id, [])
        avgs = [x[1] for x in p]
        series.append(
            {
                **_series(s, now),
                "points": p,
                "summary": {
                    "avg": round(sum(avgs) / len(avgs), 4) if avgs else None,
                    "min": min((x[2] for x in p), default=None),
                    "max": max((x[3] for x in p), default=None),
                },
            }
        )
    return {
        "metric": _def(m),
        "range": q.range,
        "tier": ["raw", "5m", "1h"][tier],
        "step": step,
        "from": int(now.timestamp()) - span,
        "to": int(now.timestamp()),
        "series": series,
        "total_series": total,
    }


@metrics_router.post("/query")
async def query(body: Query, db: DB, principal: Reader) -> dict:
    return await run_query(db, body)


# -- dashboards ----------------------------------------------------------------------


class Widget(BaseModel):
    id: str = Field(default_factory=lambda: secrets.token_hex(4), max_length=16)
    title: str = Field("", max_length=128)
    kind: Literal["line", "stat", "top"] = "line"
    metric: str
    entity_ids: list[str] = Field(default_factory=list, max_length=20)
    size: Literal["third", "half", "full"] = "half"
    limit: int = Field(5, ge=1, le=20)

    @field_validator("metric")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in metrics.METRICS:
            raise ValueError(f"unknown metric {v}")
        return v


def W(metric: str, kind: str = "line", size: str = "half", title: str = "", limit: int = 5) -> dict[str, Any]:  # noqa: N802
    return Widget(metric=metric, kind=kind, size=size, title=title, limit=limit).model_dump()  # type: ignore[arg-type]


TEMPLATES: dict[str, dict[str, Any]] = {
    "network": {
        "name": "Network health",
        "description": "Devices online, clients and controller reachability at a glance.",
        "widgets": lambda: [
            W("site.devices_offline", "stat", "third", "Devices offline"),
            W("site.clients", "stat", "third", "Clients"),
            W("controller.reachable", "stat", "third", "Controller"),
            W("site.devices_online", "line", "half", "Devices online"),
            W("site.clients", "line", "half", "Clients"),
            W("device.cpu_pct", "top", "half", "Busiest CPUs"),
            W("radio.tx_retries_pct", "top", "half", "Highest Wi-Fi retries"),
        ],
    },
    "devices": {
        "name": "Devices",
        "description": "CPU, memory, load and uplink rates for the busiest devices.",
        "widgets": lambda: [
            W("device.cpu_pct", "line", "half", "CPU"),
            W("device.mem_pct", "line", "half", "Memory"),
            W("device.uplink_rx_bps", "line", "half", "Uplink receive"),
            W("device.uplink_tx_bps", "line", "half", "Uplink transmit"),
            W("device.load_1m", "top", "full", "Load (1 min)", 10),
        ],
    },
    "clients": {
        "name": "Clients",
        "description": "Client counts by connection type, network and access point.",
        "widgets": lambda: [
            W("site.clients_wireless", "stat", "third", "Wireless"),
            W("site.clients_wired", "stat", "third", "Wired"),
            W("site.clients", "stat", "third", "All clients"),
            W("network.clients", "line", "full", "Clients per network", 8),
            W("device.clients", "top", "full", "Clients per device", 10),
        ],
    },
    "wifi": {
        "name": "Wi-Fi",
        "description": "Retry rates per radio and client load per access point.",
        "widgets": lambda: [
            W("radio.tx_retries_pct", "line", "full", "Transmit retries per radio", 8),
            W("device.clients", "top", "half", "Clients per access point"),
            W("site.clients_wireless", "line", "half", "Wireless clients"),
        ],
    },
    "wan": {
        "name": "WAN",
        "description": "Probe latency and loss with controller reachability.",
        "widgets": lambda: [
            W("wan.latency_ms", "line", "half", "Latency"),
            W("wan.loss_pct", "line", "half", "Probe loss"),
            W("controller.reachable", "line", "full", "Controller reachable"),
        ],
    },
}


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field("", max_length=2000)
    widgets: list[Widget] = Field(default_factory=list, max_length=24)
    default_range: RangeKey = "24h"


class DashboardCreate(BaseModel):
    template: str | None = None
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str = Field("", max_length=2000)
    widgets: list[Widget] = Field(default_factory=list, max_length=24)
    default_range: RangeKey = "24h"


class DashboardPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)
    widgets: list[Widget] | None = Field(None, max_length=24)
    default_range: RangeKey | None = None


def _dash(d: Dashboard, full: bool = True) -> dict[str, Any]:
    out = {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "default_range": d.default_range,
        "template": d.template,
        "widget_count": len(d.widgets),
        "created_by": d.created_by,
        "updated_at": d.updated_at,
    }
    if full:
        out["widgets"] = d.widgets
    return out


@router.get("/templates")
async def templates(principal: Reader) -> dict:
    return {
        "items": [{"id": k, "name": t["name"], "description": t["description"], "widgets": t["widgets"]()} for k, t in TEMPLATES.items()]
    }


@router.get("/dashboards")
async def list_dashboards(db: DB, principal: Reader) -> dict:
    rows = await db.scalars(select(Dashboard).order_by(Dashboard.name))
    return {"items": [_dash(d, full=False) for d in rows]}


async def _get(db: Any, dashboard_id: str) -> Dashboard:
    d = await db.get(Dashboard, dashboard_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dashboard not found")
    return d


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str, db: DB, principal: Reader) -> dict:
    return _dash(await _get(db, dashboard_id))


@router.post("/dashboards", status_code=status.HTTP_201_CREATED)
async def create_dashboard(body: DashboardCreate, db: DB, principal: Editor) -> dict:
    if body.template:
        t = TEMPLATES.get(body.template)
        if t is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown template {body.template}")
        d = Dashboard(
            name=body.name or t["name"],
            description=body.description or t["description"],
            widgets=t["widgets"](),
            default_range=body.default_range,
            template=body.template,
        )
    else:
        if not body.name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name: give the dashboard a name")
        d = Dashboard(
            name=body.name, description=body.description, widgets=[w.model_dump() for w in body.widgets], default_range=body.default_range
        )
    d.created_by = principal.user.username
    db.add(d)
    await db.flush()
    await audit.record(db, principal.actor, "analytics.dashboard.create", target_type="dashboard", target_id=d.id, detail={"name": d.name})
    return _dash(d)


@router.patch("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: str, body: DashboardPatch, db: DB, principal: Editor) -> dict:
    d = await _get(db, dashboard_id)
    changes = body.model_dump(exclude_unset=True)
    if "widgets" in changes and body.widgets is not None:
        d.widgets = [w.model_dump() for w in body.widgets]
    for k in ("name", "description", "default_range"):
        if k in changes and changes[k] is not None:
            setattr(d, k, changes[k])
    await db.flush()
    await audit.record(
        db, principal.actor, "analytics.dashboard.update", target_type="dashboard", target_id=d.id, detail={"fields": sorted(changes)}
    )
    return _dash(d)


@router.delete("/dashboards/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(dashboard_id: str, db: DB, principal: Editor) -> None:
    d = await _get(db, dashboard_id)
    await db.delete(d)
    await audit.record(
        db, principal.actor, "analytics.dashboard.delete", target_type="dashboard", target_id=dashboard_id, detail={"name": d.name}
    )
