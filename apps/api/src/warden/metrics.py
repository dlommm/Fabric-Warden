"""Metric history store: record, roll up, prune and query time series.

The official UniFi API only exposes *latest* statistics, so history exists
only from the moment Warden starts sampling. Every inventory sync records one
raw sample per series; `core.metrics` folds raw samples into 5-minute and
hourly rollups and prunes each tier to its retention.
"""

from __future__ import annotations

import ipaddress
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.db import utcnow
from warden.models import Client, Controller, Device, MetricSample, MetricSeries, Network, Radio, Setting, Site

RAW, FIVE_MIN, HOURLY = 0, 1, 2
TIER_SECONDS = {RAW: 60, FIVE_MIN: 300, HOURLY: 3600}
RETENTION = {RAW: timedelta(hours=48), FIVE_MIN: timedelta(days=30), HOURLY: timedelta(days=400)}
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400}
MAX_POINTS = 240
STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400]
# A series whose newest sample is older than this is treated as "no data".
STALE_AFTER = timedelta(minutes=5)
WATERMARK_KEY = "metrics.rollup"
# Modules that consume metric history; sync records samples while any is enabled.
CONSUMERS = ("analytics-studio", "monitoring", "wan")


@dataclass(frozen=True)
class MetricDef:
    id: str
    label: str
    unit: str  # pct | bps | count | ms | bool | load
    entity_type: str
    description: str
    # Larger is worse; drives sort order of "top" widgets.
    higher_is_worse: bool = True
    # Any value above zero is a problem (a count of bad things), so readings carry state.
    alarm: bool = False


METRICS: dict[str, MetricDef] = {
    m.id: m
    for m in [
        MetricDef("device.online", "Online", "bool", "device", "1 while the controller reports the device online.", False),
        MetricDef("device.cpu_pct", "CPU", "pct", "device", "CPU utilisation from the controller's latest statistics."),
        MetricDef("device.mem_pct", "Memory", "pct", "device", "Memory utilisation from the controller's latest statistics."),
        MetricDef("device.load_1m", "Load (1 min)", "load", "device", "One-minute load average."),
        MetricDef("device.uplink_tx_bps", "Uplink transmit", "bps", "device", "Transmit rate on the device's uplink."),
        MetricDef("device.uplink_rx_bps", "Uplink receive", "bps", "device", "Receive rate on the device's uplink."),
        MetricDef("device.clients", "Attached clients", "count", "device", "Clients whose uplink is this device."),
        MetricDef("radio.tx_retries_pct", "Wi-Fi retries", "pct", "radio", "Transmit retry rate per radio."),
        MetricDef("site.clients", "Clients", "count", "site", "Connected clients."),
        MetricDef("site.clients_wired", "Wired clients", "count", "site", "Clients on a wired connection.", False),
        MetricDef("site.clients_wireless", "Wireless clients", "count", "site", "Clients on Wi-Fi.", False),
        MetricDef("site.devices_online", "Devices online", "count", "site", "Adopted devices reported online.", False),
        MetricDef("site.devices_offline", "Devices offline", "count", "site", "Adopted devices not online.", alarm=True),
        MetricDef("network.clients", "Clients per network", "count", "network", "Clients whose address falls in the network's subnet."),
        MetricDef("controller.reachable", "Controller reachable", "bool", "controller", "1 when the last sync succeeded.", False),
        MetricDef("wan.latency_ms", "Latency", "ms", "probe", "TCP connect time from the Warden host to a probe target."),
        MetricDef("wan.loss_pct", "Probe loss", "pct", "probe", "Share of probe attempts that failed."),
        MetricDef("host.cpu_pct", "Host CPU", "pct", "host", "CPU utilisation reported by Proxmox, Unraid or node_exporter."),
        MetricDef("host.mem_pct", "Host memory", "pct", "host", "Memory in use on a server or hypervisor node."),
        MetricDef("host.load_1m", "Host load (1 min)", "load", "host", "One-minute load average on a server."),
    ]
}


@dataclass(frozen=True)
class Sample:
    metric: str
    entity_type: str
    entity_id: str
    label: str
    value: float
    site_id: str | None = None
    is_demo: bool = False


async def record(db: AsyncSession, samples: list[Sample], ts: datetime | None = None) -> int:
    """Append raw samples, creating series on first sight."""
    if not samples:
        return 0
    ts = ts or utcnow()
    metrics = {s.metric for s in samples}
    existing = {
        (s.metric, s.entity_type, s.entity_id): s for s in await db.scalars(select(MetricSeries).where(MetricSeries.metric.in_(metrics)))
    }
    for s in samples:
        if s.value is None or (isinstance(s.value, float) and math.isnan(s.value)):
            continue
        series = existing.get((s.metric, s.entity_type, s.entity_id))
        if series is None:
            series = MetricSeries(metric=s.metric, entity_type=s.entity_type, entity_id=s.entity_id, label=s.label)
            db.add(series)
            await db.flush()
            existing[(s.metric, s.entity_type, s.entity_id)] = series
        series.label, series.site_id, series.is_demo = s.label, s.site_id, s.is_demo
        series.last_value, series.last_ts = float(s.value), ts
        db.add(MetricSample(series_id=series.id, tier=RAW, ts=ts, avg=s.value, min=s.value, max=s.value, count=1))
    await db.flush()
    return len(samples)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) else None


async def site_samples(db: AsyncSession, site: Site) -> list[Sample]:
    """Derive one snapshot of samples from a freshly synced site."""
    out: list[Sample] = []
    demo = site.is_demo

    def add(metric: str, etype: str, eid: str, label: str, value: float | None) -> None:
        if value is not None:
            out.append(Sample(metric, etype, eid, label, value, site.id, demo))

    devices = list(await db.scalars(select(Device).where(Device.site_id == site.id, Device.present.is_(True))))
    clients = list(await db.scalars(select(Client).where(Client.site_id == site.id, Client.present.is_(True))))
    attached: dict[str, int] = defaultdict(int)
    for c in clients:
        if c.uplink_device_external_id:
            attached[c.uplink_device_external_id] += 1
    radios = {
        (r.device_id, r.frequency_ghz): r for r in await db.scalars(select(Radio).where(Radio.device_id.in_([d.id for d in devices])))
    }

    for d in devices:
        online = d.state == "ONLINE"
        add("device.online", "device", d.id, d.name, 1.0 if online else 0.0)
        if "accessPoint" in d.features or attached.get(d.external_id):
            add("device.clients", "device", d.id, d.name, float(attached.get(d.external_id, 0)))
        if not online or not d.stats:
            continue
        st = d.stats
        add("device.cpu_pct", "device", d.id, d.name, _num(st.get("cpuUtilizationPct")))
        add("device.mem_pct", "device", d.id, d.name, _num(st.get("memoryUtilizationPct")))
        add("device.load_1m", "device", d.id, d.name, _num(st.get("loadAverage1Min")))
        up = st.get("uplink") or {}
        add("device.uplink_tx_bps", "device", d.id, d.name, _num(up.get("txRateBps")))
        add("device.uplink_rx_bps", "device", d.id, d.name, _num(up.get("rxRateBps")))
        for r in (st.get("interfaces") or {}).get("radios") or []:
            radio = radios.get((d.id, r.get("frequencyGHz")))
            if radio is not None:
                add("radio.tx_retries_pct", "radio", radio.id, f"{d.name} · {r['frequencyGHz']:g} GHz", _num(r.get("txRetriesPct")))

    online_n = sum(d.state == "ONLINE" for d in devices)
    add("site.devices_online", "site", site.id, site.name, float(online_n))
    add("site.devices_offline", "site", site.id, site.name, float(len(devices) - online_n))
    add("site.clients", "site", site.id, site.name, float(len(clients)))
    add("site.clients_wired", "site", site.id, site.name, float(sum(c.type == "WIRED" for c in clients)))
    add("site.clients_wireless", "site", site.id, site.name, float(sum(c.type == "WIRELESS" for c in clients)))

    for n in await db.scalars(select(Network).where(Network.site_id == site.id, Network.present.is_(True))):
        cfg = n.ipv4 or {}
        if not (cfg.get("hostIpAddress") and cfg.get("prefixLength")):
            continue
        net = ipaddress.ip_network(f"{cfg['hostIpAddress']}/{cfg['prefixLength']}", strict=False)
        count = 0
        for c in clients:
            try:
                count += bool(c.ip_address and ipaddress.ip_address(c.ip_address) in net)
            except ValueError:
                continue
        add("network.clients", "network", n.id, n.name, float(count))
    return out


def controller_sample(controller: Controller) -> Sample:
    return Sample(
        "controller.reachable",
        "controller",
        controller.id,
        controller.name,
        1.0 if controller.status == "ok" else 0.0,
        None,
        controller.is_demo,
    )


# -- rollups and retention --------------------------------------------------------


def _floor(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=ts.tzinfo)


async def _watermarks(db: AsyncSession) -> dict[str, str]:
    row = await db.get(Setting, WATERMARK_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def rollup(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Fold completed periods into the next tier, then prune every tier."""
    now = now or utcnow()
    marks = await _watermarks(db)
    out: dict[str, int] = {}
    for src, dst in ((RAW, FIVE_MIN), (FIVE_MIN, HOURLY)):
        width = TIER_SECONDS[dst]
        end = _floor(now, width)
        start = datetime.fromisoformat(marks[str(dst)]) if str(dst) in marks else now - RETENTION[src]
        start = max(_floor(start, width), _floor(now - RETENTION[src], width))
        if start >= end:
            out[f"tier{dst}"] = 0
            continue
        rows = await db.execute(
            select(MetricSample.series_id, MetricSample.ts, MetricSample.avg, MetricSample.min, MetricSample.max, MetricSample.count).where(
                MetricSample.tier == src, MetricSample.ts >= start, MetricSample.ts < end
            )
        )
        buckets: dict[tuple[str, datetime], list[float]] = {}
        for sid, ts, avg, mn, mx, n in rows:
            b = buckets.setdefault((sid, _floor(ts, width)), [0.0, math.inf, -math.inf, 0])
            b[0] += avg * n
            b[1] = min(b[1], mn)
            b[2] = max(b[2], mx)
            b[3] += n
        for (sid, ts), (total, mn, mx, n) in buckets.items():
            db.add(MetricSample(series_id=sid, tier=dst, ts=ts, avg=total / n, min=mn, max=mx, count=int(n)))
        marks[str(dst)] = end.isoformat()
        out[f"tier{dst}"] = len(buckets)
        await db.flush()
    for tier, keep in RETENTION.items():
        res = await db.execute(delete(MetricSample).where(MetricSample.tier == tier, MetricSample.ts < now - keep))
        out[f"pruned{tier}"] = res.rowcount or 0
    setting = await db.get(Setting, WATERMARK_KEY)
    if setting is None:
        db.add(Setting(key=WATERMARK_KEY, value=marks))
    else:
        setting.value = marks
    return out


# -- queries -----------------------------------------------------------------------


def plan(range_key: str) -> tuple[int, int, int]:
    """(span seconds, tier, step seconds) for a named range."""
    span = RANGES[range_key]
    tier = RAW if span <= 48 * 3600 else FIVE_MIN if span <= 30 * 86400 else HOURLY
    want = max(TIER_SECONDS[tier], span // MAX_POINTS)
    step = next((s for s in STEPS if s >= want), STEPS[-1])
    return span, tier, step


async def points(
    db: AsyncSession, series_ids: list[str], range_key: str, now: datetime | None = None
) -> tuple[dict[str, list[list[Any]]], int]:
    """Bucketed [ts, avg, min, max] per series. Finer tiers fill the period not yet rolled up."""
    now = now or utcnow()
    span, tier, step = plan(range_key)
    start = now - timedelta(seconds=span)
    raw: dict[str, list[tuple[datetime, float, float, float, int]]] = defaultdict(list)
    for sid in series_ids:
        cursor = start
        for t in range(tier, -1, -1):
            rows = (
                await db.execute(
                    select(MetricSample.ts, MetricSample.avg, MetricSample.min, MetricSample.max, MetricSample.count)
                    .where(MetricSample.series_id == sid, MetricSample.tier == t, MetricSample.ts >= cursor, MetricSample.ts <= now)
                    .order_by(MetricSample.ts)
                )
            ).all()
            if rows:
                raw[sid].extend(tuple(r) for r in rows)  # type: ignore[misc]
                cursor = rows[-1][0] + timedelta(seconds=TIER_SECONDS[t] if t else 1)
    out: dict[str, list[list[Any]]] = {}
    for sid in series_ids:
        buckets: dict[int, list[float]] = {}
        for ts, avg, mn, mx, n in raw.get(sid, []):
            key = int(ts.timestamp()) // step * step
            b = buckets.setdefault(key, [0.0, math.inf, -math.inf, 0])
            b[0] += avg * n
            b[1] = min(b[1], mn)
            b[2] = max(b[2], mx)
            b[3] += n
        out[sid] = [[k, round(t / n, 4), round(mn, 4), round(mx, 4)] for k, (t, mn, mx, n) in sorted(buckets.items())]
    return out, step


def is_stale(series: MetricSeries, now: datetime | None = None) -> bool:
    return series.last_ts is None or series.last_ts < (now or utcnow()) - STALE_AFTER
