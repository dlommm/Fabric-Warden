"""Phase 5b: syslog receiver and log search, SOC signals and cases, investigation workbench."""

import asyncio
import socket

import pytest

from warden.syslog import Ingest, parse


def test_parsers():
    p = parse(b"<34>1 2026-10-21T10:00:00Z gw sshd 123 - - Failed password for admin from 198.51.100.23 port 4 ssh2", "10.0.1.1")
    assert (p.fmt, p.severity, p.facility, p.host, p.app, p.category) == ("5424", 2, 4, "gw", "sshd", "auth")
    b = parse("<30>Oct  5 10:00:00 tower emhttpd: spinning down /dev/sdd", "10.0.30.101")
    assert (b.fmt, b.host, b.app, b.message) == ("3164", "tower", "emhttpd", "spinning down /dev/sdd")
    c = parse("<34>1 - gw ids - - - CEF:0|Acme|IDS|1.0|100|Port scan|9|src=1.2.3.4 dst=10.0.0.1 msg=scan seen", "10.0.1.1")
    assert c.fmt == "cef" and c.fields["src"] == "1.2.3.4" and c.fields["msg"] == "scan seen" and c.category == "ids" and c.severity == 1
    sd = parse('<14>1 - h app - - [meta x="1" y="two"] hello', "1.1.1.1")
    assert sd.fields == {"meta": {"x": "1", "y": "two"}} and sd.message == "hello"
    raw = parse(b"\xff\xfe not syslog at all", "1.1.1.1")
    assert raw.fmt == "raw" and raw.severity == 6


@pytest.fixture
async def sec5(demo, owner):
    for mid in ("logs", "soc", "investigations", "firewall", "compliance", "client-intel"):
        r = await owner.post(f"/api/modules/{mid}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    assert (await owner.post(f"/api/controllers/{demo['id']}/sync")).json()["status"] == "succeeded"
    assert (await owner.post("/api/logs/demo")).status_code == 201
    return demo


async def test_udp_receiver_stores_and_matches(demo, owner, app):
    await owner.post("/api/modules/logs/enable", json={"with_dependencies": True})
    ing = Ingest(app.state.warden)
    await ing.start("127.0.0.1", 0)
    host, port = ing.listening.removeprefix("udp://").rsplit(":", 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"<38>Oct 21 10:00:00 gw sshd[1]: Failed password for root from 203.0.113.9 port 1 ssh2", (host, int(port)))
    s.close()
    for _ in range(50):
        if ing.received:
            break
        await asyncio.sleep(0.02)
    assert await ing.flush() == 1
    await ing.stop()
    items = (await owner.get("/api/logs", params={"q": "failed password"})).json()["items"]
    assert items[0]["source_ip"] == "127.0.0.1" and items[0]["category"] == "auth" and items[0]["is_demo"] is False


async def test_log_search_stats_and_demo(sec5, owner):
    stats = (await owner.get("/api/logs/stats")).json()
    assert stats["total"] == 120 and {"firewall", "ids", "auth", "system"} <= set(stats["by_category"])
    ids = (await owner.get("/api/logs", params={"category": "ids"})).json()["items"]
    assert ids and ids[0]["fmt"] == "cef" and ids[0]["device"]["name"] == "Gateway" and ids[0]["is_demo"]
    assert all(i["severity"] <= 3 for i in (await owner.get("/api/logs", params={"max_severity": 3})).json()["items"])
    st = (await owner.get("/api/logs/status")).json()
    assert st["enabled"] is False and st["stored"] >= 120
    assert (await owner.delete("/api/logs/demo")).json()["removed"] == 120


async def test_soc_signals_and_cases(sec5, owner):
    data = (await owner.get("/api/soc")).json()
    kinds = {s["kind"] for s in data["signals"]}
    assert {"ids", "auth", "firewall", "compliance"} <= kinds
    auth = next(s for s in data["signals"] if s["kind"] == "auth")
    assert "198.51.100.23" in auth["title"] and auth["severity"] == "high"
    r = await owner.post(
        "/api/soc/cases",
        json={"title": "Brute force on gateway SSH", "severity": "high", "signal_key": auth["key"], "entities": auth["entities"]},
    )
    case = r.json()
    assert r.status_code == 201 and case["status"] == "open" and case["assignee"] == "owner"
    linked = next(s for s in (await owner.get("/api/soc")).json()["signals"] if s["key"] == auth["key"])
    assert linked["case"]["id"] == case["id"]
    upd = (
        await owner.patch(f"/api/soc/cases/{case['id']}", json={"status": "resolved", "note": "Blocked at WAN; source is a known scanner"})
    ).json()
    assert upd["status"] == "resolved" and upd["resolved_at"] and [t["kind"] for t in upd["timeline"]] == ["status", "status", "note"]


async def test_investigation_pivots_on_ip(sec5, owner):
    inv = (await owner.get("/api/investigate", params={"q": "10.0.30.101"})).json()
    assert inv["kind"] == "ip" and any(e["type"] == "client" and e["name"] == "unraid-tower" for e in inv["entities"])
    kinds = {t["kind"] for t in inv["timeline"]}
    assert "log" in kinds and "sighting" in kinds
    by_name = (await owner.get("/api/investigate", params={"q": "Gateway"})).json()
    assert any(e["type"] == "device" for e in by_name["entities"]) and "10.0.1.1" in by_name["addresses"]
    mac = (await owner.get("/api/investigate", params={"q": "02:c1:00:00:00:01"})).json()
    assert mac["kind"] == "mac" and mac["entities"][0]["name"] == "unraid-tower"
