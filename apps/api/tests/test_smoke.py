async def test_live(anon):
    r = await anon.get("/api/health/live")
    assert r.json() == {"status": "ok"}


async def test_demo_sync(demo, owner):
    r = await owner.get("/api/inventory/summary")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["devices"]["total"] == 8
    assert s["demo_only"] is True
