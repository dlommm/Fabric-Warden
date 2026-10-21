from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from warden.config import Settings
from warden.main import create_app

PASSWORD = "correct horse battery staple 9"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/warden.db",
        data_dir=tmp_path,
        scheduler_enabled=False,
        cookie_secure=False,
    )


@pytest.fixture
async def app(settings: Settings):  # type: ignore[no-untyped-def]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def anon(app) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        yield c


@pytest.fixture
async def owner(anon: httpx.AsyncClient) -> httpx.AsyncClient:
    r = await anon.post("/api/setup/owner", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 201, r.text
    return anon


@pytest.fixture
async def demo(owner: httpx.AsyncClient) -> dict:
    """Owner session with the minimal preset and a synced demo controller."""
    r = await owner.post("/api/modules/presets/minimal/apply")
    assert r.status_code == 200, r.text
    r = await owner.post("/api/controllers/demo")
    assert r.status_code == 201, r.text
    ctrl = r.json()
    r = await owner.post(f"/api/controllers/{ctrl['id']}/sync")
    assert r.json()["status"] == "succeeded", r.text
    return ctrl
