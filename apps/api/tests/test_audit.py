from sqlalchemy import update

from warden import audit
from warden.audit import Actor
from warden.models import AuditRecord


async def test_chain_detects_tampering(app):
    async with app.state.warden.db.sessionmaker() as db:
        for i in range(3):
            await audit.record(db, Actor.system(), f"test.{i}")
        await db.commit()
        assert await audit.verify_chain(db) == (True, None)
        await db.execute(update(AuditRecord).where(AuditRecord.action == "test.1").values(outcome="denied"))
        await db.commit()
        ok, bad = await audit.verify_chain(db)
        assert not ok and bad is not None


async def test_audit_api_reports_chain_and_login_failures(owner):
    await owner.post("/api/auth/logout")
    await owner.post("/api/auth/login", json={"username": "ghost", "password": "whatever-123"})
    from tests.conftest import PASSWORD

    await owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    data = (await owner.get("/api/audit?action=auth.login")).json()
    assert data["chain_intact"] is True
    assert any(r["outcome"] == "denied" for r in data["records"])


async def test_system_health(owner):
    h = (await owner.get("/api/system/health")).json()
    ids = {c["id"]: c["status"] for c in h["checks"]}
    assert ids["database"] == "ok" and ids["audit_chain"] == "ok" and ids["redis"] == "skipped"
