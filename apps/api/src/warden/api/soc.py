"""Security Operations: one queue of signals from every module, and cases to work them.

Signals are computed live from what the platform already knows: firewall
findings, failing compliance controls, firing alerts, and log patterns
(IDS events, repeated authentication failures, blocked inbound traffic).
They are not stored; a case records the human decision about one.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from warden import audit, firewall_analysis
from warden.api.deps import DB, Allowed, State, require_module
from warden.compliance import CONTROLS
from warden.db import utcnow
from warden.models import Alert, LogEvent, SocCase
from warden.security.rbac import P

router = APIRouter(prefix="/api/soc", tags=["soc"], dependencies=[Depends(require_module("soc"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.SOC_MANAGE)
Severity = Literal["low", "medium", "high", "critical"]
AUTH_FAIL_THRESHOLD = 5
SCAN_PORTS = 20  # distinct destination ports from one source within an hour
NOISY_BLOCKS = 200  # blocks of one source within an hour
RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _sig(
    key: str,
    kind: str,
    severity: str,
    title: str,
    detail: str,
    source: str,
    entities: list[dict[str, Any]],
    seen_at: Any = None,
    demo: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail,
        "source": source,
        "entities": entities,
        "seen_at": seen_at,
        "is_demo": demo,
    }


async def signals(db: DB, state: State) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    enabled = await state.modules.enabled_ids(db)
    if "firewall" in enabled:
        for f in await firewall_analysis.findings(db):
            if f["kind"] in ("shadowed", "permissive_inbound"):
                out.append(
                    _sig(
                        f"fw:{f['key']}",
                        "firewall",
                        "high" if f["kind"] == "permissive_inbound" else "medium",
                        f["title"],
                        f.get("detail", ""),
                        "Firewall",
                        [{"type": "policy", "id": f.get("policy_id"), "label": f.get("subject")}],
                    )
                )
    if "compliance" in enabled:
        for c in CONTROLS:
            st, ev, detail = await c.check(db, state)
            if st == "fail":
                out.append(
                    _sig(
                        f"compliance:{c.id}",
                        "compliance",
                        "medium",
                        f"Control failing: {c.title}",
                        detail,
                        "Compliance",
                        [{"type": "control", "id": c.id, "label": c.title}],
                    )
                )
    if "monitoring" in enabled:
        for a in await db.scalars(select(Alert).where(Alert.state == "firing")):
            sev = {"critical": "high", "warning": "medium"}.get(a.severity, "low")
            out.append(
                _sig(
                    f"alert:{a.id}",
                    "alert",
                    sev,
                    f"Alert firing: {a.label}",
                    f"Value {a.value} against threshold {a.threshold}",
                    "Monitoring",
                    [{"type": a.entity_type, "id": a.entity_id, "label": a.label}],
                    a.fired_at,
                    a.is_demo,
                )
            )
    if "logs" in enabled:
        since = utcnow() - timedelta(hours=24)
        recent = list(
            await db.scalars(select(LogEvent).where(LogEvent.received_at >= since, LogEvent.category.in_(("ids", "auth", "firewall"))))
        )
        ids = [e for e in recent if e.category == "ids"]
        by_src = Counter(str(e.fields.get("src") or e.source_ip) for e in ids)
        for src, n in by_src.most_common(10):
            sample = next(e for e in ids if str(e.fields.get("src") or e.source_ip) == src)
            out.append(
                _sig(
                    f"ids:{src}",
                    "ids",
                    "high" if sample.severity <= 3 else "medium",
                    f"IDS: {n} event{'s' if n > 1 else ''} involving {src}",
                    sample.message[:200],
                    "Logs",
                    [{"type": "ip", "id": src, "label": src}],
                    sample.received_at,
                    sample.is_demo,
                )
            )
        fails = [e for e in recent if e.category == "auth" and ("fail" in e.message.lower() or "invalid" in e.message.lower())]
        by_ip = Counter(m.group(1) for e in fails if (m := re.search(r"from (\d{1,3}(?:\.\d{1,3}){3})", e.message)))
        for ip, n in by_ip.items():
            if n >= AUTH_FAIL_THRESHOLD:
                sample = next(e for e in fails if ip in e.message)
                out.append(
                    _sig(
                        f"authfail:{ip}",
                        "auth",
                        "high",
                        f"{n} failed logins from {ip} in 24 h",
                        f"On {sample.host or sample.source_ip}: {sample.message[:160]}",
                        "Logs",
                        [{"type": "ip", "id": ip, "label": ip}],
                        sample.received_at,
                        sample.is_demo,
                    )
                )
        fw = list(
            await db.scalars(select(LogEvent).where(LogEvent.received_at >= utcnow() - timedelta(hours=1), LogEvent.category == "firewall"))
        )
        targets: dict[str, set[tuple[str, str]]] = {}
        blocked: Counter[str] = Counter()
        for e in fw:
            src = e.fields.get("src")
            if not src:
                continue
            if e.fields.get("dpt"):
                targets.setdefault(src, set()).add((e.fields.get("dst", ""), e.fields["dpt"]))
            if e.fields.get("action") == "block":
                blocked[src] += 1
        for src, t in targets.items():
            ports = {p for _, p in t}
            if len(ports) >= SCAN_PORTS:
                out.append(
                    _sig(
                        f"scan:{src}",
                        "scan",
                        "high",
                        f"Possible port scan: {src} tried {len(ports)} ports in the last hour",
                        f"{len(t)} address/port pairs, for example {', '.join(sorted(ports)[:6])}.",
                        "Logs",
                        [{"type": "ip", "id": src, "label": src}],
                    )
                )
        for src, n in blocked.items():
            if n >= NOISY_BLOCKS:
                out.append(
                    _sig(
                        f"noisy:{src}",
                        "noisy",
                        "low",
                        f"{src} was blocked {n} times in the last hour",
                        "Often a device retrying something a policy forbids (discovery, telemetry). "
                        "Check whether the policy or the device is wrong.",
                        "Logs",
                        [{"type": "ip", "id": src, "label": src}],
                    )
                )
    open_keys = {c.signal_key: c for c in await db.scalars(select(SocCase).where(SocCase.signal_key.is_not(None)))}
    for s in out:
        c = open_keys.get(s["key"])
        s["case"] = {"id": c.id, "status": c.status, "title": c.title} if c else None
    return sorted(
        out,
        key=lambda s: (s["case"] is not None and s["case"]["status"] in ("resolved", "false_positive"), RANK[s["severity"]], s["title"]),
    )


def _case(c: SocCase) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "severity": c.severity,
        "status": c.status,
        "assignee": c.assignee,
        "summary": c.summary,
        "entities": c.entities or [],
        "signal_key": c.signal_key,
        "timeline": c.timeline or [],
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "resolved_at": c.resolved_at,
        "is_demo": c.is_demo,
    }


@router.get("")
async def overview(db: DB, state: State, principal: Reader) -> dict:
    sigs = await signals(db, state)
    cases = list(await db.scalars(select(SocCase).order_by(SocCase.updated_at.desc())))
    return {
        "signals": sigs,
        "cases": [_case(c) for c in cases],
        "counts": {
            "signals": sum(1 for s in sigs if s["case"] is None),
            "open_cases": sum(1 for c in cases if c.status in ("open", "investigating")),
        },
    }


class CaseIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    severity: Severity = "medium"
    summary: str = Field(default="", max_length=20_000)
    entities: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    signal_key: str | None = Field(default=None, max_length=160)


@router.post("/cases", status_code=status.HTTP_201_CREATED)
async def open_case(body: CaseIn, db: DB, principal: Manager) -> dict:
    who = principal.user.username
    c = SocCase(
        **body.model_dump(), assignee=who, timeline=[{"at": utcnow().isoformat(), "author": who, "kind": "status", "text": "Opened"}]
    )
    db.add(c)
    await db.flush()
    await audit.record(
        db, principal.actor, "soc.case.open", target_type="soc_case", target_id=c.id, detail={"title": c.title, "signal": c.signal_key}
    )
    return _case(c)


class CasePatch(BaseModel):
    status: Literal["open", "investigating", "resolved", "false_positive"] | None = None
    severity: Severity | None = None
    assignee: str | None = Field(default=None, max_length=64)
    summary: str | None = Field(default=None, max_length=20_000)
    note: str | None = Field(default=None, max_length=5000)


@router.patch("/cases/{case_id}")
async def update_case(case_id: str, body: CasePatch, db: DB, principal: Manager) -> dict:
    c = await db.get(SocCase, case_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    who, now = principal.user.username, utcnow().isoformat()
    entries = list(c.timeline or [])
    if body.status and body.status != c.status:
        entries.append(
            {"at": now, "author": who, "kind": "status", "text": f"{c.status.replace('_', ' ')} → {body.status.replace('_', ' ')}"}
        )
        c.status = body.status
        c.resolved_at = utcnow() if body.status in ("resolved", "false_positive") else None
    if body.assignee is not None and body.assignee != c.assignee:
        entries.append({"at": now, "author": who, "kind": "assign", "text": f"Assigned to {body.assignee or 'nobody'}"})
        c.assignee = body.assignee or None
    if body.severity:
        c.severity = body.severity
    if body.summary is not None:
        c.summary = body.summary
    if body.note:
        entries.append({"at": now, "author": who, "kind": "note", "text": body.note})
    c.timeline = entries
    await audit.record(
        db,
        principal.actor,
        "soc.case.update",
        target_type="soc_case",
        target_id=c.id,
        detail=body.model_dump(exclude_unset=True, exclude={"note"}) | ({"note": True} if body.note else {}),
    )
    return _case(c)
