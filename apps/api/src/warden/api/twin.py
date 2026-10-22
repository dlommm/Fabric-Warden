"""Network Digital Twin: what a proposed change would touch, from the observed inventory graph. Read-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from warden import twin
from warden.api.deps import DB, Allowed, require_module
from warden.models import ChangeBatch, ChangeRequest
from warden.security.rbac import P

router = APIRouter(prefix="/api/twin", tags=["digital-twin"], dependencies=[Depends(require_module("digital-twin"))])

Reader = Allowed(P.INVENTORY_READ)


@router.get("/changes/{cid}/impact")
async def change_impact(cid: str, db: DB, principal: Reader) -> dict:
    cr = await db.get(ChangeRequest, cid)
    if cr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change not found")
    return {"change_id": cr.id, **await twin.change_impact(db, cr)}


@router.get("/batches/{bid}/impact")
async def batch_impact(bid: str, db: DB, principal: Reader) -> dict:
    b = await db.get(ChangeBatch, bid)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    rows = list(await db.scalars(select(ChangeRequest).where(ChangeRequest.batch_id == b.id).order_by(ChangeRequest.batch_position)))
    return {"batch_id": b.id, **await twin.batch_impact(db, rows)}
