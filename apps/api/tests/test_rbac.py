from contextlib import asynccontextmanager

import httpx
import pytest

from tests.conftest import PASSWORD
from warden.security.rbac import ROLES, P


@asynccontextmanager
async def _login(app, username):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        r = await c.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        yield c


@pytest.mark.parametrize(
    ("role", "method", "path", "expected"),
    [
        ("viewer", "GET", "/api/inventory/devices", 200),
        ("viewer", "GET", "/api/audit", 403),
        ("viewer", "POST", "/api/modules/presets/minimal/apply", 403),
        ("viewer", "POST", "/api/controllers/demo", 403),
        ("viewer", "GET", "/api/users", 403),
        ("auditor", "GET", "/api/audit", 200),
        ("auditor", "POST", "/api/users", 403),
        ("operator", "POST", "/api/jobs/core.housekeeping/run", 200),
        ("operator", "POST", "/api/modules/ipam/enable", 403),
        ("admin", "POST", "/api/modules/inventory/disable", 200),
    ],
)
async def test_role_matrix(app, demo, owner, role, method, path, expected):
    r = await owner.post("/api/users", json={"username": f"u-{role}", "password": PASSWORD, "roles": [role]})
    assert r.status_code == 201, r.text
    async with _login(app, f"u-{role}") as c:
        r = await c.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == expected, (role, path, r.text)


async def test_admin_cannot_create_owner(app, owner):
    await owner.post("/api/users", json={"username": "adm", "password": PASSWORD, "roles": ["admin"]})
    async with _login(app, "adm") as c:
        r = await c.post("/api/users", json={"username": "evil", "password": PASSWORD, "roles": ["owner"]})
        assert r.status_code == 403


async def test_last_owner_protected(owner):
    me = (await owner.get("/api/auth/me")).json()
    r = await owner.patch(f"/api/users/{me['id']}", json={"roles": ["admin"]})
    assert r.status_code == 409


async def test_deactivated_user_sessions_revoked(app, owner):
    uid = (await owner.post("/api/users", json={"username": "temp", "password": PASSWORD, "roles": ["viewer"]})).json()["id"]
    async with _login(app, "temp") as c:
        assert (await c.get("/api/auth/me")).status_code == 200
        await owner.patch(f"/api/users/{uid}", json={"is_active": False})
        assert (await c.get("/api/auth/me")).status_code == 401


def test_role_hierarchy_is_monotonic():
    assert ROLES["viewer"] <= ROLES["operator"] <= ROLES["admin"] <= ROLES["owner"]
    assert P.SYSTEM_MANAGE not in ROLES["admin"]
    assert all(p.value.count(":") == 1 for p in P)
