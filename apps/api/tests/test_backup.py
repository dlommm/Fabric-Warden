"""Encrypted backup and restore of application state (SQLite)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from tests.conftest import PASSWORD
from warden import audit, backup
from warden.backup_cli import main as cli
from warden.config import Settings
from warden.main import create_app
from warden.models import AuditRecord, AuthSession, Controller, Device

PASS = "a long backup passphrase"


def _settings(path: Path) -> Settings:
    path.mkdir(parents=True, exist_ok=True)
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{path}/warden.db",
        data_dir=path,
        scheduler_enabled=False,
        cookie_secure=False,
    )


async def _export(client: httpx.AsyncClient, **extra) -> bytes:  # type: ignore[no-untyped-def]
    r = await client.post("/api/backup/export", json={"passphrase": PASS, **extra})
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith("attachment;")
    return r.content


async def _restore(client: httpx.AsyncClient, blob: bytes, **over) -> httpx.Response:  # type: ignore[no-untyped-def]
    body = {"archive_b64": base64.b64encode(blob).decode(), "passphrase": PASS, "current_password": PASSWORD, "confirm": "RESTORE"}
    return await client.post("/api/backup/restore", json={**body, **over})


async def _fresh_owner(tmp_path: Path, name: str):  # type: ignore[no-untyped-def]
    app = create_app(_settings(tmp_path / name))
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"})
    r = await client.post("/api/setup/owner", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 201
    return app, ctx, client


async def test_archive_is_encrypted_and_authenticated(app, demo, owner) -> None:  # type: ignore[no-untyped-def]
    blob = await _export(owner)
    assert blob.startswith(backup.MAGIC)
    assert b"owner" not in blob and b"Demo Home Lab" not in blob
    with pytest.raises(backup.BackupError, match="passphrase"):
        backup.read_backup(blob, "the wrong passphrase!")
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(backup.BackupError):
        backup.read_backup(tampered, PASS)
    doc = backup.read_backup(blob, PASS)
    assert doc["includes_vault_key"] is True and doc["schema_revision"]
    assert "auth_sessions" not in doc["tables"]
    r = await owner.post("/api/backup/export", json={"passphrase": "short"})
    assert r.status_code == 422


async def test_restore_onto_new_installation_with_different_key(app, demo, owner, tmp_path) -> None:  # type: ignore[no-untyped-def]
    blob = await _export(owner)
    async with app.state.warden.db.sessionmaker() as db:
        devices = await db.scalar(select(func.count()).select_from(Device))
    assert devices

    app2, ctx, c2 = await _fresh_owner(tmp_path, "second")
    try:
        assert app2.state.warden.vault.master_key != app.state.warden.vault.master_key
        r = await _restore(c2, blob)
        assert r.status_code == 200, r.text
        assert r.json()["rows"]["devices"] == devices
        # Every session was signed out by the restore.
        assert (await c2.get("/api/auth/me")).status_code == 401
        async with app2.state.warden.db.sessionmaker() as db:
            assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0
            ok, bad = await audit.verify_chain(db)
            assert ok, bad
            last = await db.scalar(select(AuditRecord).order_by(AuditRecord.seq.desc()).limit(1))
            assert last.action == "backup.restore" and last.outcome == "success"
            ctrl = await db.scalar(select(Controller))
            # Credentials were re-encrypted under the new installation's key.
            assert await app2.state.warden.vault.reveal(db, ctrl.credential_id)
        # The backup's owner signs in and the restored controller still syncs.
        r = await c2.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
        assert r.status_code == 200
        r = await c2.post(f"/api/controllers/{demo['id']}/sync")
        assert r.json()["status"] == "succeeded", r.text
    finally:
        await c2.aclose()
        await ctx.__aexit__(None, None, None)


async def test_restore_without_vault_key_needs_the_same_key(app, demo, owner, tmp_path) -> None:  # type: ignore[no-untyped-def]
    blob = await _export(owner, include_vault_key=False)
    assert backup.read_backup(blob, PASS)["vault_key"] is None
    app2, ctx, c2 = await _fresh_owner(tmp_path, "other")
    try:
        r = await _restore(c2, blob)
        assert r.status_code == 422 and "vault key" in r.json()["detail"]
        # Nothing was replaced.
        assert (await c2.get("/api/auth/me")).status_code == 200
    finally:
        await c2.aclose()
        await ctx.__aexit__(None, None, None)
    # Same installation (same key): fine.
    r = await _restore(owner, blob)
    assert r.status_code == 200, r.text


async def test_restore_guards(app, owner, tmp_path) -> None:  # type: ignore[no-untyped-def]
    blob = await _export(owner)
    assert (await _restore(owner, blob, current_password="not my password")).status_code == 403
    assert (await _restore(owner, blob, passphrase="wrong passphrase here")).status_code == 422
    assert (await _restore(owner, blob, confirm="yes")).status_code == 422
    assert (await _restore(owner, b"garbage")).status_code == 422

    doc = backup.read_backup(blob, PASS)
    doc["schema_revision"] = "0000"
    other = backup.encrypt(json.dumps(doc).encode(), PASS)
    r = await _restore(owner, other)
    assert r.status_code == 422 and "schema revision" in r.json()["detail"]

    r = await owner.post("/api/users", json={"username": "adm", "password": PASSWORD, "roles": ["admin"]})
    assert r.status_code == 201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        await c.post("/api/auth/login", json={"username": "adm", "password": PASSWORD})
        assert (await c.post("/api/backup/export", json={"passphrase": PASS})).status_code == 403
        assert (await _restore(c, blob)).status_code == 403

    tok = (await owner.post("/api/auth/tokens", json={"name": "backup"})).json()["token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"authorization": f"Bearer {tok}"}
    ) as c:
        # Tokens can export (scheduled backups) but never restore.
        assert (await c.post("/api/backup/export", json={"passphrase": PASS})).status_code == 200
        assert (await _restore(c, blob)).status_code == 403


async def test_cli_round_trip(app, demo, owner, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    src = app.state.warden.settings
    out = tmp_path / "cli.fwbk"
    monkeypatch.setenv("WARDEN_BACKUP_PASSPHRASE", PASS)
    monkeypatch.setenv("WARDEN_DATABASE_URL", src.database_url)
    monkeypatch.setenv("WARDEN_DATA_DIR", str(src.data_dir))
    from warden.config import get_settings

    get_settings.cache_clear()
    try:
        await asyncio.to_thread(cli, ["export", str(out)])
        assert out.stat().st_mode & 0o777 == 0o600
        target = tmp_path / "restored"
        target.mkdir()
        monkeypatch.setenv("WARDEN_DATABASE_URL", f"sqlite+aiosqlite:///{target}/warden.db")
        monkeypatch.setenv("WARDEN_DATA_DIR", str(target))
        get_settings.cache_clear()
        with pytest.raises(SystemExit):
            await asyncio.to_thread(cli, ["restore", str(out)])  # needs --yes
        await asyncio.to_thread(cli, ["restore", str(out), "--yes"])
    finally:
        get_settings.cache_clear()
    app3 = create_app(_settings(target))
    async with app3.router.lifespan_context(app3), app3.state.warden.db.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Device))
        ctrl = await db.scalar(select(Controller))
        assert await app3.state.warden.vault.reveal(db, ctrl.credential_id)
