"""Phase 9: UniFi Protect, Home Assistant and the MCP gateway (simulators only)."""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from warden.integrations.homeassistant.client import classify
from warden.integrations.protect.client import norm_mac
from warden.models import AuditRecord, Cable, CableEnd, Device

SRC = Path(__file__).parents[1] / "src/warden/integrations"


def _matrix_paths(name: str) -> set[tuple[str, str]]:
    m = json.loads((SRC / name / "official_api_matrix.json").read_text())
    assert m["retrieved"] and m["sources"]
    return {(e["method"], e["path"]) for e in m["endpoints"]}


@pytest.fixture
async def protect(demo, owner):
    r = await owner.post("/api/modules/unifi-protect/enable", json={"with_dependencies": True})
    assert r.status_code == 200, r.text
    r = await owner.post("/api/protect/demo")
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def home_assistant(demo, owner):
    r = await owner.post("/api/modules/home-assistant/enable", json={"with_dependencies": True})
    assert r.status_code == 200, r.text
    r = await owner.post("/api/home-assistant/demo")
    assert r.status_code == 201, r.text
    return r.json()


# -- UniFi Protect ----------------------------------------------------------------------


async def test_protect_demo_cameras_matched_to_network(protect, owner, app):
    assert protect["status"] == "ok" and protect["version"] == "7.3.70" and protect["is_demo"]
    assert protect["credential_fingerprint"] and "demo-protect-api-key" not in json.dumps(protect)

    res = (await owner.get("/api/protect/cameras")).json()
    cams = {c["name"]: c for c in res["items"]}
    assert set(cams) == {"cam-driveway", "cam-backyard", "cam-front-door", "doorbell", "cam-garage"}
    assert res["counts"] == {"total": 5, "connected": 4, "disconnected": 1, "on_network": 4}

    drive = cams["cam-driveway"]
    assert drive["mac_address"] == "02:c1:00:00:00:07" and drive["client"]["match"] == "mac"
    assert drive["uplink"]["name"] == "Rack PoE Switch" and drive["link"] == "wired"
    assert drive["port"] is None and "does not report which switch port" in drive["port_note"]
    assert drive["provenance"]["source"].startswith("simulator@") and drive["is_demo"]

    assert cams["doorbell"]["link"] == "wifi" and cams["doorbell"]["uplink"]["name"] == "AP Patio"
    assert cams["cam-garage"]["client"] is None and cams["cam-garage"]["uplink"] is None

    nvr = (await owner.get("/api/protect/nvrs")).json()["items"]
    assert len(nvr) == 1 and nvr[0]["name"] == "unvr-protect" and nvr[0]["client"]["name"] == "unvr-protect"

    # Only the published GET endpoints were called.
    sim = app.state.warden.protect_sim
    called = {(m, p.removeprefix("/proxy/protect/integration")) for m, p in sim.requests}
    assert called <= _matrix_paths("protect") and all(m == "GET" for m, _ in called)


async def test_protect_documented_cable_pins_port_and_poe(protect, owner, app):
    async with app.state.warden.db.sessionmaker() as db:
        sw = await db.scalar(select(Device).where(Device.name == "Rack PoE Switch"))
        cable = Cable(label="C-CAM-3", type="cat6")
        db.add(cable)
        await db.flush()
        db.add_all(
            [
                CableEnd(cable_id=cable.id, end="a", kind="device_port", device_id=sw.id, port_idx=5),
                CableEnd(cable_id=cable.id, end="b", kind="external", text="cam-front-door"),
            ]
        )
        await db.commit()
    cams = {c["name"]: c for c in (await owner.get("/api/protect/cameras")).json()["items"]}
    port = cams["cam-front-door"]["port"]
    assert port["idx"] == 5 and port["basis"] == "documented" and port["cable"] == "C-CAM-3"
    assert port["poe"]["state"] == "DOWN"

    findings = (await owner.get("/api/protect/findings")).json()["items"]
    off = next(f for f in findings if f["kind"] == "camera_offline")
    assert off["severity"] == "critical" and "PoE is off on the documented port Rack PoE Switch #5" in off["detail"]
    assert any(f["kind"] == "not_on_network" and f["target"]["name"] == "cam-garage" for f in findings)


async def test_protect_bad_key_and_module_gate(protect, owner):
    # Rotate the demo connection's key to a wrong one: the simulator refuses it; nothing leaves the process.
    r = await owner.patch(f"/api/protect/connections/{protect['id']}", json={"secret": "wrong"})
    assert r.status_code == 200 and r.json()["credential_fingerprint"] != protect["credential_fingerprint"]
    res = (await owner.post(f"/api/protect/connections/{protect['id']}/sync")).json()
    assert res["status"] == "degraded" and "Authentication was refused" in res["error"]
    findings = (await owner.get("/api/protect/findings")).json()["items"]
    assert any(f["kind"] == "connection" and f["severity"] == "warning" for f in findings)
    await owner.post("/api/modules/unifi-protect/disable", json={})
    assert (await owner.get("/api/protect/cameras")).status_code == 409


async def test_protect_sync_marks_removed_camera(protect, owner, app):
    sim = app.state.warden.protect_sim
    sim.cameras = [c for c in sim.cameras if c["name"] != "cam-garage"]
    r = await owner.post(f"/api/protect/connections/{protect['id']}/sync")
    assert r.json()["cameras"] == 4
    names = {c["name"] for c in (await owner.get("/api/protect/cameras")).json()["items"]}
    assert "cam-garage" not in names
    assert (await owner.delete("/api/protect/demo")).json() == {"removed": 1}
    assert (await owner.get("/api/protect/cameras")).json()["items"] == []


def test_mac_normalisation():
    assert norm_mac("02C100000007") == "02:c1:00:00:00:07"
    assert norm_mac("02-C1-00-00-00-07") == "02:c1:00:00:00:07"
    assert norm_mac("000000000000") is None and norm_mac("xyz") is None


# -- Home Assistant -----------------------------------------------------------------------


async def test_home_assistant_selected_entities_and_matching(home_assistant, owner, app):
    assert home_assistant["status"] == "ok" and home_assistant["version"] == "2026.10.1"
    res = (await owner.get("/api/home-assistant/entities")).json()
    assert res["counts"] == {"temperature": 3, "ups": 4, "connectivity": 2, "tracker": 3}
    ents = {e["entity_id"]: e for e in res["items"]}
    assert "device_tracker.alex_phone_gps" not in ents  # GPS trackers are skipped
    assert "sensor.groups_count" not in ents and "sensor.office_humidity" not in ents
    assert all("latitude" not in e["attributes"] for e in ents.values())
    assert ents["sensor.rack_ups_status"]["category_basis"] == "name"
    assert ents["sensor.rack_temperature"]["value"] == 31.5 and ents["sensor.rack_temperature"]["unit"] == "°C"
    assert ents["device_tracker.phone_a"]["client"]["match"] == "ip"
    assert ents["device_tracker.phone_b"]["client"]["match"] == "mac"
    assert ents["device_tracker.old_tablet"]["client"] is None

    f = (await owner.get("/api/home-assistant/findings")).json()
    kinds = {(x["kind"], x["target"].get("entity_id")) for x in f["items"]}
    assert ("connectivity_down", "binary_sensor.nas_backup_ping") in kinds
    assert ("unavailable", "sensor.garage_temperature") in kinds

    called = set(app.state.warden.ha_sim.requests)
    assert called <= _matrix_paths("homeassistant") and all(m == "GET" for m, _ in called)


async def test_home_assistant_ups_on_battery(home_assistant, owner, app):
    sim = app.state.warden.ha_sim
    for s in sim.states:
        if s["entity_id"] == "sensor.rack_ups_status":
            s["state"] = "On Battery"
        if s["entity_id"] == "sensor.rack_ups_battery_charge":
            s["state"] = "41"
    await owner.post(f"/api/home-assistant/connections/{home_assistant['id']}/sync")
    f = {x["kind"]: x for x in (await owner.get("/api/home-assistant/findings")).json()["items"]}
    assert f["ups_on_battery"]["severity"] == "critical"
    # Each entity is judged on its own: the status sensor says on battery, the charge sensor says 41 %.
    assert f["ups_battery_low"]["target"]["entity_id"] == "sensor.rack_ups_battery_charge"


def test_home_assistant_classification():
    assert classify("sensor.ups_load", {}) == ("ups", "name")
    assert classify("sensor.backups_size", {}) is None
    assert classify("sensor.t", {"device_class": "temperature"}) == ("temperature", "device_class")
    assert classify("device_tracker.p", {"source_type": "gps", "mac": "x"}) is None
    assert classify("device_tracker.p", {"source_type": "router"}) == ("tracker", "domain")


# -- MCP ------------------------------------------------------------------------------------


@pytest.fixture
async def mcp(demo, owner, app):
    for m in ("mcp", "monitoring", "firewall", "unifi-protect"):
        r = await owner.post(f"/api/modules/{m}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    assert (await owner.post("/api/protect/demo")).status_code == 201

    async def token(perms: list[str]) -> httpx.AsyncClient:
        raw = (await owner.post("/api/auth/tokens", json={"name": f"mcp {perms}", "permissions": perms})).json()["token"]
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"authorization": f"Bearer {raw}"})

    return token


async def _rpc(client: httpx.AsyncClient, method: str, params: dict | None = None, id_: int = 1) -> dict:
    r = await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": id_, "method": method, **({"params": params} if params is not None else {})},
        headers={"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_mcp_lifecycle_and_tools(mcp, app):
    async with await mcp([]) as bot:  # empty list = the owner's permissions
        init = await _rpc(
            bot, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
        )
        assert init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["capabilities"] == {"tools": {"listChanged": False}}
        r = await bot.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code == 202 and r.content == b""
        assert (await _rpc(bot, "initialize", {"protocolVersion": "1999-01-01"}))["result"]["protocolVersion"] == "2025-06-18"

        tools = {t["name"]: t for t in (await _rpc(bot, "tools/list"))["result"]["tools"]}
        assert set(tools) == {"search_inventory", "get_device", "get_client", "list_alerts", "firewall_findings", "list_cameras"}
        assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools.values())

        res = (await _rpc(bot, "tools/call", {"name": "search_inventory", "arguments": {"query": "Gateway"}}))["result"]
        assert res["isError"] is False and res["structuredContent"]["total"] >= 1
        assert json.loads(res["content"][0]["text"]) == res["structuredContent"]

        dev = (await _rpc(bot, "tools/call", {"name": "get_device", "arguments": {"device": "rack poe switch"}}))["result"]
        assert dev["structuredContent"]["name"] == "Rack PoE Switch"

        cli = (await _rpc(bot, "tools/call", {"name": "get_client", "arguments": {"client": "02:c1:00:00:00:07"}}))["result"]
        assert cli["structuredContent"]["client"]["name"] == "cam-driveway"
        assert cli["structuredContent"]["protect"][0]["name"] == "cam-driveway"

        miss = (await _rpc(bot, "tools/call", {"name": "get_device", "arguments": {"device": "nope"}}))["result"]
        assert miss["isError"] is True and "No device matches" in miss["content"][0]["text"]

        bad = await _rpc(bot, "tools/call", {"name": "get_device", "arguments": {}})
        assert bad["error"]["code"] == -32602
        assert (await _rpc(bot, "tools/call", {"name": "delete_everything", "arguments": {}}))["error"]["code"] == -32602
        assert (await _rpc(bot, "resources/list"))["error"]["code"] == -32601
        assert (await _rpc(bot, "ping"))["result"] == {}

        cams = (await _rpc(bot, "tools/call", {"name": "list_cameras", "arguments": {}}))["result"]["structuredContent"]
        assert cams["counts"]["total"] == 5
        assert (await _rpc(bot, "tools/call", {"name": "list_alerts", "arguments": {}}))["result"]["isError"] is False
        assert (await _rpc(bot, "tools/call", {"name": "firewall_findings", "arguments": {}}))["result"]["isError"] is False

    async with app.state.warden.db.sessionmaker() as db:
        calls = list(await db.scalars(select(AuditRecord).where(AuditRecord.action == "mcp.tool.call")))
    assert len(calls) >= 7 and all(c.actor_type == "token" for c in calls)


async def test_mcp_permission_narrowing(mcp):
    async with await mcp(["inventory:read"]) as no_mcp:
        r = await no_mcp.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 403
    async with await mcp(["mcp:use"]) as only_mcp:
        assert (await _rpc(only_mcp, "tools/list"))["result"]["tools"] == []
        call = await _rpc(only_mcp, "tools/call", {"name": "search_inventory", "arguments": {"query": "x"}})
        assert call["error"]["code"] == -32602
    async with await mcp(["mcp:use", "inventory:read"]) as both:
        assert len((await _rpc(both, "tools/list"))["result"]["tools"]) == 6


async def test_mcp_transport_rules(mcp, owner):
    # Cookie sessions are not accepted: MCP clients authenticate with an API token.
    assert (await owner.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})).status_code == 401
    async with await mcp([]) as bot:
        assert (await bot.get("/api/mcp")).status_code == 405
        r = await bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"origin": "http://evil.example"})
        assert r.status_code == 403
        r = await bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"origin": "http://test"})
        assert r.status_code == 200
        assert (await bot.post("/api/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])).status_code == 400
        assert (await bot.post("/api/mcp", content=b"{nope", headers={"content-type": "application/json"})).status_code == 400
        r = await bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"mcp-protocol-version": "2000-01-01"})
        assert r.status_code == 400
        await owner.post("/api/modules/mcp/disable", json={})
        assert (await bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})).status_code == 409
    async with httpx.AsyncClient(transport=owner._transport, base_url="http://test") as anon:
        r = await anon.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"authorization": "Bearer wdn_bad"})
        assert r.status_code in (401, 409)
