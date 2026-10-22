"""Owner-only encrypted backup and restore of application state (see warden.backup)."""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from warden import audit, backup
from warden.api.deps import DB, Allowed, State
from warden.security.crypto import verify_password
from warden.security.rbac import P

router = APIRouter(prefix="/api/backup", tags=["backup"])

Owner = Allowed(P.SYSTEM_MANAGE)


class ExportIn(BaseModel):
    passphrase: str = Field(min_length=backup.MIN_PASSPHRASE, max_length=1024)
    include_vault_key: bool = True


class RestoreIn(BaseModel):
    archive_b64: str = Field(min_length=1)
    passphrase: str = Field(min_length=1, max_length=1024)
    current_password: str = Field(min_length=1, max_length=1024)
    confirm: Literal["RESTORE"]


@router.get("")
async def backup_info(state: State, principal: Owner) -> dict:
    return {
        "format": backup.FORMAT_NAME,
        "format_version": backup.FORMAT_VERSION,
        "database": state.db.engine.dialect.name,
        "excluded_tables": sorted(backup.EXCLUDED_TABLES),
        "min_passphrase": backup.MIN_PASSPHRASE,
        "max_restore_bytes": state.settings.max_restore_bytes,
    }


@router.post("/export")
async def export_backup(body: ExportIn, db: DB, state: State, principal: Owner) -> Response:
    # Record the export first: the archive then contains its own audit entry.
    await audit.record(db, principal.actor, "backup.export", detail={"includes_vault_key": body.include_vault_key})
    await db.commit()
    try:
        blob, summary = await backup.create_backup(
            state.db.engine, body.passphrase, vault_key=state.vault.master_key if body.include_vault_key else None
        )
    except backup.BackupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    stamp = summary["created_at"][:19].replace(":", "").replace("-", "")
    return Response(
        blob,
        media_type="application/octet-stream",
        headers={
            "content-disposition": f'attachment; filename="fabric-warden-{stamp}.fwbk"',
            "x-warden-backup-rows": str(sum(summary["row_counts"].values())),
            "x-warden-backup-revision": str(summary["schema_revision"]),
        },
    )


@router.post("/restore")
async def restore_backup(body: RestoreIn, db: DB, state: State, principal: Owner) -> dict:
    if principal.via != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Restore can only be started from an interactive session.")
    if not verify_password(principal.user.password_hash, body.current_password):
        await audit.record(db, principal.actor, "backup.restore", outcome="denied", detail={"reason": "password"})
        await db.commit()  # keep the denial even though the request fails
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your current password is incorrect.")
    try:
        blob = base64.b64decode(body.archive_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The archive must be base64 encoded.") from None
    actor = principal.actor
    try:
        doc = backup.read_backup(blob, body.passphrase)
        await db.rollback()  # release this session's connection before replacing every table
        state.scheduler.paused = True
        try:
            counts = await backup.restore_state(state.db.engine, doc, current_key=state.vault.master_key)
        finally:
            state.scheduler.paused = False
    except backup.BackupError as exc:
        await audit.record(db, actor, "backup.restore", outcome="failure", detail={"reason": str(exc)[:300]})
        await db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    # The restored audit chain continues with the restore itself.
    await audit.record(
        db,
        actor,
        "backup.restore",
        detail={"created_at": doc.get("created_at"), "app_version": doc.get("app_version"), "rows": sum(counts.values())},
    )
    return {
        "ok": True,
        "restored_from": doc.get("created_at"),
        "rows": counts,
        "note": "Every session was signed out. Sign in with an account from the backup, then restart Fabric Warden.",
    }
