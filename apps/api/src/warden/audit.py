"""Hash-chained audit trail.

Records are only ever inserted. The chain is tamper-evident, not tamper-proof:
someone with database write access can rewrite the whole chain, so export
or ship records off-host when stronger guarantees are needed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.db import new_id, utcnow
from warden.models import AuditRecord

GENESIS = "0" * 64


@dataclass(frozen=True)
class Actor:
    type: str  # user | token | system | agent
    id: str | None
    name: str
    ip_address: str | None = None

    @classmethod
    def system(cls, name: str = "system") -> Actor:
        return cls("system", None, name)


def _digest(rec: AuditRecord) -> str:
    body = json.dumps(
        {
            "id": rec.id,
            "occurred_at": rec.occurred_at.isoformat(),
            "actor": [rec.actor_type, rec.actor_id, rec.actor_name],
            "action": rec.action,
            "target": [rec.target_type, rec.target_id],
            "outcome": rec.outcome,
            "detail": rec.detail,
            "prev": rec.prev_hash,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def record(
    db: AsyncSession,
    actor: Actor,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
) -> AuditRecord:
    prev = await db.scalar(select(AuditRecord.hash).order_by(AuditRecord.seq.desc()).limit(1))
    rec = AuditRecord(
        id=new_id(),
        occurred_at=utcnow(),
        actor_type=actor.type,
        actor_id=actor.id,
        actor_name=actor.name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        detail=detail or {},
        ip_address=actor.ip_address,
        prev_hash=prev or GENESIS,
        hash="",
    )
    rec.hash = _digest(rec)
    db.add(rec)
    await db.flush()
    return rec


async def verify_chain(db: AsyncSession) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_seq)."""
    prev = GENESIS
    for rec in await db.scalars(select(AuditRecord).order_by(AuditRecord.seq)):
        if rec.prev_hash != prev or _digest(rec) != rec.hash:
            return False, rec.seq
        prev = rec.hash
    return True, None
