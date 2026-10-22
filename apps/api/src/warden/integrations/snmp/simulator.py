"""Deterministic simulated SNMP agents for the demo devices.

Each agent answers GET and walk from an OID table built from the demo
device's ports, so the real collector parses it exactly as it would a live
agent. Octet counters are the integral of a smooth per-port rate, so they only
ever increase and two polls a minute apart yield a plausible throughput.
One PoE switch port accumulates input errors (a bad cable story) and the
aggregation uplink drops a few packets. Never presented as live data.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Any

from warden.integrations.snmp.client import COLUMNS, SYS_DESCR, SYS_NAME, SYS_UPTIME, Credentials, SnmpError

SIM_USER = "fabricwarden-demo"
SIM_PASSWORD = "demo-snmp-passphrase"  # noqa: S105 - simulator fixture
# Counters run from this simulated boot time (2026-09-21), so they are large and monotonic.
SIM_BOOT = 1_790_000_000
PERIOD = 900.0


@dataclass
class SnmpSimState:
    """Mutable so tests can move the clock forward between polls."""

    offset: float = 0.0
    # Tests pin the clock so counter steps are exact.
    frozen: float | None = None

    def now(self) -> float:
        return (self.frozen if self.frozen is not None else time.time()) + self.offset


def _frac(*parts: Any) -> float:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def _octets(base_bps: float, phase: float, t: float) -> int:
    """Bytes transferred by t at rate base·(1 + 0.6·sin(2πt/P + φ)) bits/s."""
    amp = 0.6 * base_bps
    w = 2 * math.pi / PERIOD
    bits = base_bps * t + amp / w * (math.cos(phase) - math.cos(w * t + phase))
    return int(bits / 8)


class SimulatedAgent:
    def __init__(self, sim: SnmpSimState, device: dict[str, Any], creds: Credentials) -> None:
        self.sim, self.device, self.creds = sim, device, creds

    def now(self) -> float:
        return self.sim.now()

    def _check(self) -> None:
        c = self.creds
        ok = (c.version == "v3" and c.username == SIM_USER and c.auth_key == SIM_PASSWORD and c.priv_key == SIM_PASSWORD) or (
            c.version == "v2c" and c.community == SIM_PASSWORD
        )
        if not ok:
            raise SnmpError("The agent refused the credentials (Unknown USM user or wrong digest)", kind="auth")

    def _table(self) -> dict[str, Any]:
        t = self.now() - SIM_BOOT
        d = self.device
        out: dict[str, Any] = {
            SYS_DESCR: f"Linux {d['name']} 5.4.213-ui-ipq5322 #5.4.213 SMP aarch64 (simulated)".encode(),
            SYS_NAME: d["name"].encode(),
            SYS_UPTIME: int(t * 100) % 2**32,
        }
        rows: list[dict[str, Any]] = []
        for p in d["ports"]:
            up = p["state"] == "UP" and p.get("speed_mbps")
            speed = p.get("speed_mbps") or p["max_speed_mbps"]
            # Most ports idle along at a few percent; SFP uplinks carry more.
            util = 0.005 + 0.08 * _frac(d["name"], p["idx"], "u")
            if p["connector"].startswith("SFP"):
                util += 0.12
            base = speed * 1e6 * util if up else 0.0
            phase = 2 * math.pi * _frac(d["name"], p["idx"], "p")
            rx = _octets(base, phase, t)
            tx = _octets(base * (0.3 + 0.6 * _frac(d["name"], p["idx"], "r")), phase + 1.1, t)
            in_err = int(t / 30) if (d["model"] == "USW Pro Max 16 PoE" and p["idx"] == 9) else 0
            in_disc = int(t / 20) if (d["model"] == "USW Pro Aggregation" and p["idx"] == 25) else 0
            rows.append(
                {
                    "idx": p["idx"],
                    "descr": f"Port {p['idx']}",
                    "name": f"Port {p['idx']}",
                    "alias": "",
                    "type": 6,
                    "mac": bytes.fromhex(d["mac_address"].replace(":", ""))[:5] + bytes([p["idx"]]),
                    "admin": 1,
                    "oper": 1 if up else 2,
                    "high_speed": speed if up else 0,
                    "in_octets": rx,
                    "out_octets": tx,
                    "in_errors": in_err,
                    "out_errors": 0,
                    "in_discards": in_disc,
                    "out_discards": 0,
                }
            )
        lo = {"idx": 1000, "descr": "lo", "name": "lo", "alias": "", "type": 24, "mac": b"", "admin": 1, "oper": 1, "high_speed": 0}
        lo |= {k: int(t * 900) for k in ("in_octets", "out_octets")} | {
            k: 0 for k in ("in_errors", "out_errors", "in_discards", "out_discards")
        }
        rows.append(lo)
        for key, oid in COLUMNS.items():
            for r in rows:
                v = r[key]
                out[f"{oid}.{r['idx']}"] = v.encode() if isinstance(v, str) else v
        return out

    async def get(self, oids: list[str]) -> dict[str, Any]:
        self._check()
        table = self._table()
        return {o: table.get(o) for o in oids}

    async def walk(self, oid: str) -> dict[str, Any]:
        self._check()
        prefix = oid + "."
        table = self._table()
        return {k: v for k, v in sorted(table.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]) if k.startswith(prefix)}
