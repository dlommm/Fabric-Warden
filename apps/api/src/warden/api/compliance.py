"""Policy & Compliance: built-in controls evaluated on demand, with evidence and audited waivers."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.compliance import BY_ID, CONTROLS
from warden.db import utcnow
from warden.models import ComplianceResult
from warden.security.rbac import P

router = APIRouter(prefix="/api/compliance", tags=["compliance"], dependencies=[Depends(require_module("compliance"))])

Reader = Allowed(P.INVENTORY_READ)
Manager = Allowed(P.COMPLIANCE_MANAGE)


async def evaluate(db: DB, state: State) -> list[dict[str, Any]]:
    now = utcnow()
    stored = {r.control_id: r for r in await db.scalars(select(ComplianceResult))}
    out = []
    for c in CONTROLS:
        status_, evidence, detail = await c.check(db, state)
        r = stored.get(c.id)
        if r is None:
            r = ComplianceResult(control_id=c.id, status=status_)
            db.add(r)
        if r.waived_until and r.waived_until < now:
            r.waived_by, r.waived_until, r.waiver_reason = None, None, ""
        if status_ == "fail" and r.failing_since is None:
            r.failing_since = now
        elif status_ != "fail":
            r.failing_since = None
        r.status, r.evidence, r.detail, r.evaluated_at, r.runs = status_, evidence, detail, now, (r.runs or 0) + 1
        effective = "waived" if status_ == "fail" and r.waived_by else status_
        out.append(
            {
                "id": c.id,
                "title": c.title,
                "category": c.category,
                "rationale": c.rationale,
                "status": effective,
                "raw_status": status_,
                "detail": detail,
                "evidence": evidence,
                "failing_since": r.failing_since,
                "waiver": {"by": r.waived_by, "reason": r.waiver_reason, "until": r.waived_until} if r.waived_by else None,
                "evaluated_at": now,
            }
        )
    await db.flush()
    return out


@router.get("")
async def controls(db: DB, state: State, principal: Reader) -> dict:
    items = await evaluate(db, state)
    tally = {s: sum(1 for i in items if i["status"] == s) for s in ("pass", "fail", "waived", "unknown")}
    return {"items": items, "tally": tally}


class Waiver(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    until: datetime


@router.post("/{control_id}/waive")
async def waive(control_id: str, body: Waiver, db: DB, state: State, principal: Manager) -> dict:
    if control_id not in BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown control")
    if body.until <= utcnow():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A waiver needs an expiry in the future")
    await evaluate(db, state)
    r = await db.scalar(select(ComplianceResult).where(ComplianceResult.control_id == control_id))
    assert r is not None
    r.waived_by, r.waiver_reason, r.waived_until = principal.user.username, body.reason, body.until
    await audit.record(
        db,
        principal.actor,
        "compliance.waive",
        target_type="control",
        target_id=control_id,
        detail={"reason": body.reason, "until": body.until.isoformat()},
    )
    return {"id": control_id, "waived_until": r.waived_until}


@router.delete("/{control_id}/waive", status_code=status.HTTP_204_NO_CONTENT)
async def unwaive(control_id: str, db: DB, principal: Manager) -> None:
    r = await db.scalar(select(ComplianceResult).where(ComplianceResult.control_id == control_id))
    if r is None or not r.waived_by:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No waiver on this control")
    r.waived_by, r.waiver_reason, r.waived_until = None, "", None
    await audit.record(db, principal.actor, "compliance.unwaive", target_type="control", target_id=control_id)


@router.get("/evidence.csv", response_class=PlainTextResponse)
async def evidence_csv(db: DB, state: State, principal: Reader) -> PlainTextResponse:
    items = await evaluate(db, state)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["control", "title", "status", "evaluated_at", "object", "fact", "waiver_reason", "waived_until"])
    for i in items:
        rows = i["evidence"] or [{"object": "", "fact": i["detail"]}]
        for e in rows:
            wv = i["waiver"] or {}
            w.writerow(
                [
                    i["id"],
                    i["title"],
                    i["status"],
                    i["evaluated_at"].isoformat(),
                    e.get("object", ""),
                    e.get("fact", ""),
                    wv.get("reason", ""),
                    wv.get("until") or "",
                ]
            )
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="compliance-evidence.csv"'}
    )
