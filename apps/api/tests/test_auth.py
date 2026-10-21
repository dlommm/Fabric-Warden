import httpx
import pyotp
import pytest

from tests.conftest import PASSWORD
from warden.api import auth as auth_api


@pytest.fixture(autouse=True)
def _reset_throttle():
    auth_api.throttle._fails.clear()


async def test_setup_only_once(owner):
    r = await owner.post("/api/setup/owner", json={"username": "second", "password": PASSWORD})
    assert r.status_code == 409


async def test_setup_rejects_weak_password(anon):
    r = await anon.post("/api/setup/owner", json={"username": "owner", "password": "short"})
    assert r.status_code == 422


async def test_unauthenticated_rejected(anon):
    assert (await anon.get("/api/auth/me")).status_code == 401
    assert (await anon.get("/api/modules")).status_code == 401


async def test_login_logout(app, owner):
    await owner.post("/api/auth/logout")
    assert (await owner.get("/api/auth/me")).status_code == 401
    r = await owner.post("/api/auth/login", json={"username": "owner", "password": "wrong password!!"})
    assert r.status_code == 401
    r = await owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.json() == {"mfa_required": False}
    me = (await owner.get("/api/auth/me")).json()
    assert me["roles"] == ["owner"]
    assert "system:manage" in me["permissions"]


async def test_login_throttled_after_repeated_failures(owner):
    await owner.post("/api/auth/logout")
    for _ in range(5):
        await owner.post("/api/auth/login", json={"username": "owner", "password": "nope nope nope"})
    r = await owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 429


async def test_cookie_writes_require_csrf_header(app, owner):
    cookies = owner.cookies
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=cookies) as raw:
        r = await raw.post("/api/modules/presets/minimal/apply")
        assert r.status_code == 403
        assert "CSRF" in r.text
        assert (await raw.get("/api/modules")).status_code == 200


async def test_mfa_enrolment_and_login(owner):
    secret = (await owner.post("/api/auth/mfa/enroll")).json()["secret"]
    assert (await owner.post("/api/auth/mfa/activate", json={"code": "000000"})).status_code == 400
    assert (await owner.post("/api/auth/mfa/activate", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    await owner.post("/api/auth/logout")
    r = await owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.json() == {"mfa_required": True}
    # Pending session is not yet usable.
    assert (await owner.get("/api/auth/me")).status_code == 401
    assert (await owner.post("/api/auth/mfa/verify", json={"code": "123456"})).status_code == 401
    assert (await owner.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    assert (await owner.get("/api/auth/me")).json()["mfa_enabled"] is True


async def test_api_token_is_narrowed_and_revocable(app, owner):
    r = await owner.post("/api/auth/tokens", json={"name": "ci", "permissions": ["modules:read"]})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("wdn_")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"authorization": f"Bearer {token}"}
    ) as bot:
        assert (await bot.get("/api/modules")).status_code == 200
        assert (await bot.get("/api/audit")).status_code == 403
        # Bearer tokens cannot mint further tokens.
        assert (await bot.post("/api/auth/tokens", json={"name": "x"})).status_code == 403
        await owner.delete(f"/api/auth/tokens/{r.json()['id']}")
        assert (await bot.get("/api/modules")).status_code == 401


async def test_cannot_grant_permissions_not_held(app, owner):
    await owner.post("/api/users", json={"username": "vic", "password": PASSWORD, "roles": ["viewer"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as v:
        await v.post("/api/auth/login", json={"username": "vic", "password": PASSWORD})
        r = await v.post("/api/auth/tokens", json={"name": "x", "permissions": ["users:manage"]})
        assert r.status_code == 403
