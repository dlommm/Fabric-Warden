"""Opt-in reachability probe for the WAN module.

The official UniFi API reports no WAN latency, loss or state, so Warden measures
for itself: a TCP connect from the Warden host to each configured target. This
is the path from wherever Warden runs, through whichever WAN is active. It is
not an ICMP ping (no raw sockets needed) and not a per-WAN measurement.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import TYPE_CHECKING, Any

from warden import metrics
from warden.models import Setting

if TYPE_CHECKING:
    from warden.jobs import JobContext

SETTING_KEY = "wan.probe"
DEFAULTS: dict[str, Any] = {"targets": [], "attempts": 3, "timeout_ms": 1500}


async def tcp_connect_ms(host: str, port: int, timeout: float) -> float | None:  # noqa: ASYNC109 - shared probe signature
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return None
    elapsed = (time.perf_counter() - start) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return elapsed


def target_id(t: dict[str, Any]) -> str:
    return f"{t['host']}:{t['port']}"


async def load_config(db: Any) -> dict[str, Any]:
    row = await db.get(Setting, SETTING_KEY)
    return {**DEFAULTS, **(row.value if row and isinstance(row.value, dict) else {})}


async def probe_job(ctx: JobContext) -> dict[str, Any]:
    cfg = await load_config(ctx.db)
    if not cfg["targets"]:
        return {"skipped": "no probe targets configured"}
    connect = ctx.state.probe_connect or tcp_connect_ms
    timeout = cfg["timeout_ms"] / 1000
    samples: list[metrics.Sample] = []
    out = []
    for t in cfg["targets"]:
        results = [await connect(t["host"], int(t["port"]), timeout) for _ in range(cfg["attempts"])]
        ok = [r for r in results if r is not None]
        loss = 100.0 * (len(results) - len(ok)) / len(results)
        label = t.get("label") or t["host"]
        samples.append(metrics.Sample("wan.loss_pct", "probe", target_id(t), label, loss))
        if ok:
            samples.append(metrics.Sample("wan.latency_ms", "probe", target_id(t), label, round(statistics.median(ok), 2)))
        out.append({"target": target_id(t), "loss_pct": loss, "latency_ms": round(statistics.median(ok), 2) if ok else None})
    await metrics.record(ctx.db, samples)
    return {"targets": out}
