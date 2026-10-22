"""Phase 10 security hardening regressions: authz coverage, CSRF, rate limits,
body limits, SSRF guards, secret redaction, security headers."""

from __future__ import annotations

import ipaddress
import logging

import httpx
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from tests.conftest import PASSWORD
from warden import audit
from warden.audit import Actor
from warden.config import Settings
from warden.main import create_app
from warden.models import AuditRecord
from warden.security import outbound
from warden.security.outbound import GuardedTransport, OutboundBlocked, OutboundPolicy, check_host, check_url
from warden.security.redact import REDACTED, SecretFilter, redact, redact_text

# Endpoints that are deliberately reachable without a session or token.
PUBLIC = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/mfa/verify"),  # authenticates the pending session itself
    ("POST", "/api/auth/logout"),
    ("GET", "/api/setup/status"),
    ("POST", "/api/setup/owner"),  # only while no user exists
    ("GET", "/api/health/live"),
    ("GET", "/api/health/ready"),
    ("POST", "/api/mcp"),  # authenticates bearer tokens itself
    ("GET", "/api/mcp"),  # 405 stub
    ("DELETE", "/api/mcp"),  # 405 stub
}
# Routers that belong to the always-on core (no module to disable).
CORE_PREFIXES = (
    "/api/auth",
    "/api/setup",
    "/api/users",
    "/api/modules",
    "/api/controllers",
    "/api/metrics",
    "/api/health",
    "/api/system",
    "/api/jobs",
    "/api/audit",
    "/api/events",
    "/api/backup",
)


def _routes(app):  # type: ignore[no-untyped-def]
    def walk(rs, parents=()):  # type: ignore[no-untyped-def]
        for x in rs:
            if hasattr(x, "original_router"):
                yield from walk(x.original_router.routes, (*parents, x.original_router))
            elif isinstance(x, APIRoute):
                yield x, parents

    def deps(d, names: set[str]) -> None:  # type: ignore[no-untyped-def]
        if d.call is not None:
            names.add(getattr(d.call, "__qualname__", ""))
        for s in d.dependencies:
            deps(s, names)

    for route, parents in walk(app.routes):
        names: set[str] = set()
        deps(route.dependant, names)
        for p in parents:
            for dd in p.dependencies:
                names.add(getattr(dd.dependency, "__qualname__", ""))
        for method in route.methods:
            yield method, route.path, names


def test_every_route_is_authenticated_and_module_gated(settings: Settings) -> None:
    app = create_app(settings)
    unauth, ungated = [], []
    for method, path, names in _routes(app):
        authed = any(n.startswith("require.") or n == "current_principal" for n in names)
        if not authed and (method, path) not in PUBLIC:
            unauth.append(f"{method} {path}")
        gated = any(n.startswith("require_module.") for n in names)
        if not gated and not path.startswith(CORE_PREFIXES):
            ungated.append(f"{method} {path}")
    assert not unauth, f"routes without authentication: {unauth}"
    assert not ungated, f"module routes without require_module: {ungated}"


# -- CSRF -------------------------------------------------------------------------------


async def test_login_and_setup_require_csrf_header(app) -> None:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as bare:
        r = await bare.post("/api/setup/owner", json={"username": "owner", "password": PASSWORD})
        assert r.status_code == 403
        r = await bare.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
        assert r.status_code == 403
        assert (await bare.post("/api/auth/logout")).status_code == 403
        r = await bare.post("/api/setup/owner", json={"username": "owner", "password": PASSWORD}, headers={"x-warden-csrf": "1"})
        assert r.status_code == 201


# -- rate limits ------------------------------------------------------------------------


async def test_login_spraying_many_usernames_is_throttled_per_address(owner) -> None:  # type: ignore[no-untyped-def]
    for i in range(20):
        r = await owner.post("/api/auth/login", json={"username": f"nobody{i}", "password": "wrong password!!"})
        assert r.status_code == 401
    # A different username from the same address is now refused too, even with the right password.
    r = await owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 429


async def test_password_confirmation_is_throttled(owner) -> None:  # type: ignore[no-untyped-def]
    for _ in range(5):
        r = await owner.post("/api/auth/password", json={"current_password": "nope nope nope", "new_password": "x" * 20})
        assert r.status_code == 403
    r = await owner.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "another long passphrase 7"})
    assert r.status_code == 429
    r = await owner.post("/api/auth/mfa/disable", json={"password": PASSWORD})
    assert r.status_code == 429


async def test_mfa_verify_rotates_the_session_token(owner) -> None:  # type: ignore[no-untyped-def]
    import pyotp

    secret = (await owner.post("/api/auth/mfa/enroll")).json()["secret"]
    assert (await owner.post("/api/auth/mfa/activate", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    await owner.post("/api/auth/logout")
    await owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    pending = owner.cookies.get("warden_session")
    assert (await owner.post("/api/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    full = owner.cookies.get("warden_session")
    assert full and full != pending
    owner.cookies.set("warden_session", pending)
    assert (await owner.get("/api/auth/me")).status_code == 401


# -- body size --------------------------------------------------------------------------


async def test_oversized_request_bodies_are_refused(owner) -> None:  # type: ignore[no-untyped-def]
    big = "x" * (3 * 1024 * 1024)
    r = await owner.put("/api/auth/preferences", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413

    async def chunks():  # no Content-Length: counted while streaming
        for _ in range(3):
            yield b"x" * (1024 * 1024)

    r = await owner.put("/api/auth/preferences", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


async def test_list_fields_are_bounded(owner) -> None:  # type: ignore[no-untyped-def]
    r = await owner.put("/api/auth/preferences", json={"overview_panels": ["attention"] * 100})
    assert r.status_code == 422


# -- SSRF -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://0.0.0.0:8000/",
        "http://[::ffff:169.254.169.254]/",
        "http://admin:secret@10.0.0.1/",
        "file:///etc/passwd",
        "gopher://10.0.0.1:6379/",
    ],
)
def test_check_url_refuses_dangerous_destinations(url: str) -> None:
    with pytest.raises(OutboundBlocked):
        check_url(url)


@pytest.mark.parametrize(
    "url", ["https://192.168.1.1", "https://10.0.0.5:8443/", "http://127.0.0.1:11434", "https://unifi.lan", "https://[fd00::1]/"]
)
def test_check_url_allows_lan_equipment(url: str) -> None:
    assert check_url(url) == url


def test_policy_overrides() -> None:
    assert check_host("169.254.10.1", OutboundPolicy(allow_link_local=True))
    allowed = OutboundPolicy(allowed=(ipaddress.ip_network("169.254.169.254/32"),))
    assert check_host("169.254.169.254", allowed)
    blocked = OutboundPolicy(blocked=(ipaddress.ip_network("172.17.0.0/16"),))
    with pytest.raises(OutboundBlocked):
        check_host("172.17.0.2", blocked)
    with pytest.raises(OutboundBlocked):
        check_url("unix:///var/run/docker.sock")
    assert check_url("unix:///var/run/docker.sock", allow_unix=True)


async def test_guarded_transport_checks_resolved_addresses(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sent: list[str] = []

    async def fake_resolve(host: str, port: int):  # type: ignore[no-untyped-def]
        return [ipaddress.ip_address("169.254.169.254")] if host == "evil.example" else [ipaddress.ip_address("192.168.1.10")]

    monkeypatch.setattr(outbound, "_resolve", fake_resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=GuardedTransport(httpx.MockTransport(handler))) as c:
        assert (await c.get("http://good.example/")).status_code == 200
        with pytest.raises(httpx.ConnectError, match="outbound policy"):
            await c.get("http://evil.example/")
        with pytest.raises(httpx.ConnectError, match="outbound policy"):
            await c.get("http://169.254.169.254/")
    assert sent == ["http://good.example/"]


async def _enable(owner, *mods: str) -> None:  # type: ignore[no-untyped-def]
    for m in mods:
        r = await owner.post(f"/api/modules/{m}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text


META = "http://169.254.169.254/latest"


async def test_user_supplied_urls_are_validated_everywhere(owner) -> None:  # type: ignore[no-untyped-def]
    await _enable(owner, "notifications", "servers", "unifi-protect", "home-assistant", "ai-providers", "wan", "snmp")
    cases = [
        ("/api/controllers/test", {"base_url": META, "api_key": "k" * 16}),
        ("/api/controllers/test", {"base_url": "https://user:pw@10.0.0.1", "api_key": "k" * 16}),
        ("/api/notifications/channels", {"name": "n", "kind": "webhook", "webhook": {"url": META}}),
        ("/api/notifications/channels", {"name": "n", "kind": "ntfy", "ntfy": {"server": "http://[fe80::1]", "topic": "t"}}),
        (
            "/api/notifications/channels",
            {"name": "n", "kind": "email", "email": {"host": "169.254.169.254", "from_addr": "a@b.co", "to": ["c@d.co"]}},
        ),
        ("/api/servers/connections/test", {"kind": "docker", "base_url": META}),
        ("/api/protect/connections/test", {"base_url": META, "secret": "s"}),
        ("/api/home-assistant/connections/test", {"base_url": "http://metadata.google.internal", "secret": "s"}),
        ("/api/ai/providers/test", {"kind": "ollama", "base_url": META}),
        ("/api/snmp/targets/test", {"profile_id": "x", "host": "169.254.169.254"}),
    ]
    for path, body in cases:
        r = await owner.post(path, json=body)
        assert r.status_code == 422, (path, r.status_code, r.text)
    r = await owner.put("/api/wan/probe", json={"enabled": True, "targets": [{"host": "169.254.169.254", "port": 80}]})
    assert r.status_code == 422, r.text


# -- secrets in audit and logs ----------------------------------------------------------


def test_redact_masks_secret_keys_but_keeps_references() -> None:
    out = redact(
        {
            "api_key": "abc123",
            "password": "hunter2",
            "nested": [{"token": "t0k3n", "token_id": "tid", "credential_fingerprint": "fp"}],
            "base_url": "https://admin:pw@10.0.0.1/x",
            "monthly_token_limit": 1000,
            "name": "ok",
        }
    )
    assert out["api_key"] == REDACTED and out["password"] == REDACTED
    assert out["nested"][0] == {"token": REDACTED, "token_id": "tid", "credential_fingerprint": "fp"}
    assert "pw@" not in out["base_url"]
    assert out["monthly_token_limit"] == 1000 and out["name"] == "ok"
    assert "wdn_abcdefgh123" not in redact_text("used wdn_abcdefgh123 here")
    assert "s3cr3t" not in redact_text("Authorization: Bearer s3cr3t-token-value")


async def test_audit_records_never_store_secrets(app) -> None:  # type: ignore[no-untyped-def]
    state = app.state.warden
    async with state.db.sessionmaker() as db:
        rec = await audit.record(db, Actor.system(), "test.secret", detail={"api_key": "plain", "signing_secret": "s"})
        await db.commit()
        assert rec.detail == {"api_key": REDACTED, "signing_secret": REDACTED}
        ok, _ = await audit.verify_chain(db)
        assert ok


def test_log_filter_masks_secrets(caplog) -> None:  # type: ignore[no-untyped-def]
    logger = logging.getLogger("warden.test")
    logger.addFilter(SecretFilter())
    with caplog.at_level(logging.INFO, logger="warden.test"):
        logger.info("calling %s with X-API-KEY: %s", "https://c", "supersecretkey")
    assert "supersecretkey" not in caplog.text and REDACTED in caplog.text


async def test_authorization_denials_are_audited(app, owner) -> None:  # type: ignore[no-untyped-def]
    r = await owner.post("/api/users", json={"username": "viewer1", "password": PASSWORD, "roles": ["viewer"]})
    assert r.status_code == 201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        await c.post("/api/auth/login", json={"username": "viewer1", "password": PASSWORD})
        assert (await c.get("/api/audit")).status_code == 403
    async with app.state.warden.db.sessionmaker() as db:
        rec = await db.scalar(select(AuditRecord).where(AuditRecord.action == "authz.denied"))
        assert rec is not None and rec.actor_name == "viewer1" and rec.outcome == "denied"
        assert rec.detail["permission"] == "audit:read"


# -- headers ----------------------------------------------------------------------------


async def test_api_security_headers(anon) -> None:  # type: ignore[no-untyped-def]
    r = await anon.get("/api/setup/status")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
