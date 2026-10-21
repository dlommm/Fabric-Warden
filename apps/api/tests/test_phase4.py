"""Phase 4: racks and DCIM, cabling, power, asset lifecycle, topology, documentation."""

from datetime import date, timedelta

import httpx
import pytest

from tests.conftest import PASSWORD

MODULES = ("dcim", "cabling", "power", "asset-lifecycle", "topology", "docs")


@pytest.fixture
async def infra(demo, owner):
    for mid in MODULES:
        r = await owner.post(f"/api/modules/{mid}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    return demo


async def _devices(owner) -> dict[str, dict]:
    return {d["name"]: d for d in (await owner.get("/api/inventory/devices")).json()["items"]}


async def _rack(owner, name="R1", u=12) -> dict:
    r = await owner.post("/api/dcim/racks", json={"name": name, "u_height": u})
    assert r.status_code == 201, r.text
    return r.json()


async def _item(owner, rack, **kw) -> httpx.Response:
    return await owner.post("/api/dcim/items", json={"rack_id": rack["id"], **kw})


# -- DCIM -----------------------------------------------------------------------------


async def test_rack_occupancy_validation(infra, owner):
    rack = await _rack(owner)
    srv = (await _item(owner, rack, name="srv", kind="server", position=1, height_u=2, power_draw_w=100)).json()
    # Overlapping full-depth item is rejected; a top beyond the rack is rejected.
    r = await _item(owner, rack, name="clash", position=2)
    assert r.status_code == 409 and "U2" in r.json()["detail"]
    r = await _item(owner, rack, name="tall", position=11, height_u=3)
    assert r.status_code == 409 and "U13" in r.json()["detail"]
    # Half-depth front and rear items can share a unit.
    assert (await _item(owner, rack, name="front", position=5, full_depth=False, face="front")).status_code == 201
    assert (await _item(owner, rack, name="rear", position=5, full_depth=False, face="rear")).status_code == 201
    assert (await _item(owner, rack, name="full", position=5)).status_code == 409
    # Moving onto an occupied unit is rejected; moving to a free one works.
    assert (await owner.patch(f"/api/dcim/items/{srv['id']}", json={"position": 4})).status_code == 409
    assert (await owner.patch(f"/api/dcim/items/{srv['id']}", json={"position": 7})).status_code == 200
    # The rack cannot shrink below an occupied unit.
    assert (await owner.patch(f"/api/dcim/racks/{rack['id']}", json={"u_height": 6})).status_code == 409

    view = (await owner.get(f"/api/dcim/racks/{rack['id']}")).json()
    units = {u["unit"]: u for u in view["units"]}
    assert units[12]["unit"] == view["units"][0]["unit"]  # top first
    assert units[7]["front"] == units[8]["rear"] == srv["id"]
    assert units[5]["front"] != units[5]["rear"]
    assert view["occupancy"]["used_u"] == 3 and view["occupancy"]["documented_draw_w"] == 100
    assert view["conflicts"] == []


async def test_patch_panel_needs_ports_and_device_links_are_unique(infra, owner):
    rack = await _rack(owner)
    assert (await _item(owner, rack, name="pp", kind="patch_panel", position=1)).status_code == 422
    gw = (await _devices(owner))["Gateway"]
    assert (await _item(owner, rack, name="gw", position=2, device_id=gw["id"])).status_code == 201
    r = await _item(owner, rack, name="gw again", position=3, device_id=gw["id"])
    assert r.status_code == 409
    placement = (await owner.get(f"/api/dcim/devices/{gw['id']}/placement")).json()["placement"]
    assert placement["rack"] == "R1" and placement["position"] == 2
    kinds = {f["kind"] for f in (await owner.get("/api/dcim/findings")).json()["items"]}
    assert "unracked" in kinds  # switches are not placed anywhere


async def test_location_cycle_rejected(infra, owner):
    a = (await owner.post("/api/dcim/locations", json={"name": "House", "kind": "site"})).json()
    b = (await owner.post("/api/dcim/locations", json={"name": "Closet", "parent_id": a["id"]})).json()
    r = await owner.patch(f"/api/dcim/locations/{a['id']}", json={"parent_id": b["id"]})
    assert r.status_code == 422
    assert (await owner.delete(f"/api/dcim/locations/{a['id']}")).status_code == 409


# -- cabling --------------------------------------------------------------------------


async def _cable(owner, a, b, **kw) -> httpx.Response:
    return await owner.post("/api/cabling/cables", json={"a": a, "b": b, **kw})


def _port(dev, idx):
    return {"kind": "device_port", "device_id": dev["id"], "port_idx": idx}


async def test_cable_trace_through_patch_panel(infra, owner):
    dev = await _devices(owner)
    rack = await _rack(owner)
    pp = (await _item(owner, rack, name="PP", kind="patch_panel", position=1, port_count=24, full_depth=False)).json()
    panel = {"kind": "panel_port", "item_id": pp["id"], "port_idx": 3}
    c1 = await _cable(owner, _port(dev["Rack PoE Switch"], 1), {**panel, "side": "rear"})
    c2 = await _cable(owner, {**panel, "side": "front"}, _port(dev["AP Living Room"], 1), type="cat6a")
    assert c1.status_code == c2.status_code == 201, (c1.text, c2.text)
    assert (c1.json()["label"], c2.json()["label"]) == ("C-0001", "C-0002")

    t = (await owner.get("/api/cabling/trace", params={"device_id": dev["Rack PoE Switch"]["id"], "port_idx": 1})).json()
    assert len(t["hops"]) == 2 and t["complete"]
    assert t["b"]["name"] == "AP Living Room" and t["b"]["port_idx"] == 1
    # Tracing from the middle (a panel port) shows the whole path.
    mid = (await owner.get("/api/cabling/trace", params={"item_id": pp["id"], "port_idx": 3, "side": "front"})).json()
    assert [mid["a"]["name"], mid["b"]["name"]] == ["Rack PoE Switch", "AP Living Room"]

    # A port takes one cable; ports must exist; panel ports are bounded.
    assert (await _cable(owner, _port(dev["Rack PoE Switch"], 1), {"kind": "external", "text": "x"})).status_code == 409
    assert (await _cable(owner, _port(dev["Gateway"], 99), {"kind": "external", "text": "x"})).status_code == 422
    r = await _cable(owner, {**panel, "port_idx": 25, "side": "rear"}, {"kind": "external", "text": "x"})
    assert r.status_code == 422

    ports = (await owner.get(f"/api/cabling/devices/{dev['AP Living Room']['id']}/ports")).json()["items"]
    assert ports[0]["far"]["name"] == "Rack PoE Switch"
    view = (await owner.get(f"/api/cabling/panels/{pp['id']}")).json()
    assert view["used"] == 1 and view["ports"][2]["front"]["label"] == "C-0002"
    labels = (await owner.get("/api/cabling/labels")).json()["items"]
    assert labels[0]["lines"][1].startswith("A Rack PoE Switch port 1")
    csv = (await owner.get("/api/cabling/schedule.csv")).text
    assert csv.splitlines()[0].startswith("label,type") and "C-0002,cat6a" in csv


async def test_cabling_findings_compare_with_observed_uplinks(infra, owner):
    dev = await _devices(owner)
    # Aggregation is observed as the PoE switch's uplink: consistent.
    await _cable(owner, _port(dev["Core Aggregation"], 1), _port(dev["Rack PoE Switch"], 17))
    # PoE switch ↔ office switch is not an observed uplink, and office port 3 is down.
    await _cable(owner, _port(dev["Rack PoE Switch"], 11), _port(dev["Office Switch"], 3))
    items = (await owner.get("/api/cabling/findings")).json()["items"]
    kinds = sorted(f["kind"] for f in items)
    assert kinds == ["cabled_port_down", "path_not_observed"]
    assert "Office Switch" in next(f for f in items if f["kind"] == "path_not_observed")["title"]


# -- power ----------------------------------------------------------------------------


async def test_power_rollup_failover_and_shared_feed(infra, owner):
    rack = await _rack(owner, u=20)
    feed = (await owner.post("/api/power/feeds", json={"name": "Circuit 1", "voltage_v": 230, "amperage_a": 10})).json()
    assert feed["capacity_w"] == 1840  # 230 × 10 × 80 %
    ups = (
        await _item(
            owner, rack, name="UPS", kind="ups", position=1, height_u=2, power_capacity_w=1000, outlet_count=4, runtime_full_load_min=5
        )
    ).json()
    pdu = (await _item(owner, rack, name="PDU", kind="pdu", power_capacity_w=2000, outlet_count=8, face="rear", full_depth=False)).json()
    srv = (await _item(owner, rack, name="srv", kind="server", position=3, height_u=2, power_draw_w=200)).json()
    sw = (await _item(owner, rack, name="sw", position=5, power_draw_w=100)).json()
    plan = (await _item(owner, rack, name="new", kind="server", position=6, power_draw_w=300, status="planned")).json()

    async def link(**kw):
        r = await owner.post("/api/power/links", json=kw)
        assert r.status_code == 201, r.text

    await link(source_feed_id=feed["id"], target_item_id=ups["id"])
    await link(source_item_id=ups["id"], source_outlet=1, target_item_id=pdu["id"])
    await link(source_item_id=ups["id"], source_outlet=2, target_item_id=srv["id"], target_input="A")
    await link(source_item_id=pdu["id"], source_outlet=1, target_item_id=srv["id"], target_input="B")
    await link(source_item_id=ups["id"], source_outlet=3, target_item_id=sw["id"])
    await link(source_item_id=pdu["id"], source_outlet=2, target_item_id=plan["id"])

    # Outlet reuse, a second input A and loops are rejected.
    assert (
        await owner.post(
            "/api/power/links", json={"source_item_id": ups["id"], "source_outlet": 1, "target_item_id": sw["id"], "target_input": "B"}
        )
    ).status_code == 409
    assert (
        await owner.post("/api/power/links", json={"source_item_id": pdu["id"], "target_item_id": srv["id"], "target_input": "A"})
    ).status_code == 409
    assert (
        await owner.post("/api/power/links", json={"source_item_id": pdu["id"], "target_item_id": ups["id"], "target_input": "B"})
    ).status_code == 409

    trees = (await owner.get("/api/power/chains")).json()["trees"]
    root = next(t for t in trees if t["kind"] == "feed")
    up = root["children"][0]
    # Both server inputs reach the UPS, so it carries the full 200 W either way.
    assert up["name"] == "UPS" and up["load_w"] == 300 and up["failover_load_w"] == 300
    assert up["projected_failover_w"] == 600 and up["projected_utilisation_pct"] == 60
    assert up["runtime_estimate_min"] == pytest.approx(5 * 1000 / 300, rel=0.01)
    p = next(c for c in up["children"] if c["name"] == "PDU")
    assert p["load_w"] == 100 and p["failover_load_w"] == 200  # half normally, all on failover

    findings = (await owner.get("/api/power/findings")).json()["items"]
    shared = [f for f in findings if f["kind"] == "shared_feed"]
    assert shared and "Circuit 1" in shared[0]["title"]


async def test_poe_upper_bound(infra, owner):
    items = {x["name"]: x for x in (await owner.get("/api/power/poe")).json()["items"]}
    sw = items["Rack PoE Switch"]
    assert sw["active_ports"] == 6 and sw["upper_bound_w"] == 360.0  # 6 × 802.3bt type 3 (60 W)


# -- assets ---------------------------------------------------------------------------


async def test_asset_lifecycle_calendar_and_findings(infra, owner):
    today = date.today()
    dev = await _devices(owner)
    r = await owner.post(
        "/api/assets",
        json={
            "name": "Spare PSU",
            "kind": "spare",
            "status": "spare",
            "quantity": 1,
            "min_quantity": 2,
            "purchase_cost": "49.50",
            "currency": "USD",
        },
    )
    assert r.status_code == 201, r.text
    lic = (
        await owner.post("/api/assets", json={"name": "Support", "kind": "licence", "expires_on": str(today + timedelta(days=20))})
    ).json()
    assert lic["next_date"]["days"] == 20
    await owner.post("/api/assets", json={"name": "Old UPS", "warranty_until": str(today - timedelta(days=5))})
    assert (await owner.post("/api/assets", json={"name": "x", "device_id": "nope"})).status_code == 404
    # Phase 2 device warranty shows up in the calendar.
    await owner.patch(f"/api/inventory/devices/{dev['Gateway']['id']}", json={"asset": {"warranty_until": str(today + timedelta(days=40))}})

    cal = (await owner.get("/api/assets/calendar", params={"days": 60})).json()["items"]
    assert [(c["name"], c["source"]) for c in cal] == [("Old UPS", "asset"), ("Support", "asset"), ("Gateway", "device")]
    kinds = {(f["kind"], f.get("asset_id") or f.get("device_id")) for f in (await owner.get("/api/assets/findings")).json()["items"]}
    assert ("expiring", lic["id"]) in kinds and ("expiring", dev["Gateway"]["id"]) in kinds
    assert {k for k, _ in kinds} == {"expiring", "expired", "low_stock"}
    summary = (await owner.get("/api/assets/summary")).json()
    assert summary["spend"] == {"USD": "49.50"} and summary["due_within_90_days"] == 1
    assert "Spare PSU" in (await owner.get("/api/assets/export.csv")).text


# -- topology -------------------------------------------------------------------------


async def test_topology_layers_and_vlan_overlay(infra, owner):
    t = (await owner.get("/api/topology")).json()
    devices = [n for n in t["nodes"] if n["type"] == "device"]
    assert len(devices) == 8 and len(t["edges"]) == 7  # every device but the gateway has an uplink
    gw = next(n for n in devices if n["role"] == "gateway")
    assert gw["depth"] == 0
    assert next(n for n in devices if n["name"] == "AP Living Room")["depth"] == 3

    servers = next(v["vlan_id"] for v in t["vlans"] if v["name"] == "Servers")
    o = (await owner.get("/api/topology", params={"vlan": servers})).json()
    carriers = {n["name"] for n in o["nodes"] if n["type"] == "device" and n["vlan_clients"]}
    assert {"Gateway", "Core Aggregation"} <= carriers and "AP Garage" not in carriers

    logical = (await owner.get("/api/topology", params={"layer": "logical"})).json()
    assert {n["type"] for n in logical["nodes"]} == {"device", "network"}
    with_clients = (await owner.get("/api/topology", params={"clients": True})).json()
    assert any(n["type"] == "client" for n in with_clients["nodes"])


# -- documentation --------------------------------------------------------------------


async def test_docs_versions_references_and_conflicts(infra, owner):
    rack = await _rack(owner, name="Rack Z")
    body = "See [[device:Gateway]], [[rack:Rack Z|the rack]] and [[device:Nope]]."
    p = (await owner.post("/api/wiki/pages", json={"title": "Core Network", "body": body, "tags": ["arch"]})).json()
    assert p["slug"] == "core-network" and p["version"] == 1
    # Same title gets a suffixed slug.
    assert (await owner.post("/api/wiki/pages", json={"title": "Core Network"})).json()["slug"] == "core-network-2"

    page = (await owner.get("/api/wiki/pages/core-network")).json()
    refs = page["references"]
    assert [r["resolved"] for r in refs] == [True, True, False]
    assert refs[0]["state"] == "ONLINE" and refs[1]["text"] == "the rack" and refs[1]["href"].endswith(rack["id"])

    ok = await owner.put(
        "/api/wiki/pages/core-network", json={"base_version": 1, "title": "Core Network", "body": body + "\nMore.", "message": "edit"}
    )
    assert ok.json()["version"] == 2
    stale = await owner.put("/api/wiki/pages/core-network", json={"base_version": 1, "title": "Core Network", "body": "lost"})
    assert stale.status_code == 409

    d = (await owner.get("/api/wiki/pages/core-network/diff", params={"from": 1, "to": 2})).json()
    assert d["added"] == 1 and d["removed"] == 0
    rv = await owner.post("/api/wiki/pages/core-network/revert", json={"version": 1, "base_version": 2})
    assert rv.json()["version"] == 3
    hist = (await owner.get("/api/wiki/pages/core-network/versions")).json()["items"]
    assert [h["version"] for h in hist] == [3, 2, 1] and hist[0]["message"] == "Reverted to version 1"

    gw = (await _devices(owner))["Gateway"]
    back = (await owner.get("/api/wiki/backlinks", params={"type": "device", "id": gw["id"]})).json()["items"]
    assert [b["slug"] for b in back] == ["core-network"]
    broken = (await owner.get("/api/wiki/references/check")).json()["items"]
    assert [b["raw"] for b in broken] == ["[[device:Nope]]"]


# -- demo layout, permissions, search -------------------------------------------------


async def test_demo_layout_seed_and_remove(infra, owner):
    r = await owner.post("/api/dcim/demo")
    assert r.status_code == 201, r.text
    created = r.json()["created"]
    assert created["dcim_racks"] == 1 and created["dcim_cables"] == 10 and created["assets"] == 7 and created["doc_pages"] == 3
    assert (await owner.post("/api/dcim/demo")).status_code == 409

    racks = (await owner.get("/api/dcim/racks")).json()["items"]
    assert racks[0]["is_demo"] and racks[0]["occupancy"]["planned_u"] == 2
    view = (await owner.get(f"/api/dcim/racks/{racks[0]['id']}")).json()
    assert view["conflicts"] == [] and all(i["is_demo"] for i in view["items"])
    pf = {f["kind"] for f in (await owner.get("/api/power/findings")).json()["items"]}
    assert "shared_feed" in pf
    cf = {f["kind"] for f in (await owner.get("/api/cabling/findings")).json()["items"]}
    assert "path_not_observed" in cf
    t = (
        await owner.get(
            "/api/cabling/trace",
            params={"device_id": next(d["id"] for d in (await _devices(owner)).values() if d["name"] == "Office Switch"), "port_idx": 25},
        )
    ).json()
    assert [h["cable"]["label"] for h in t["hops"]] == ["C-0004", "C-0003"] and t["b"]["name"] == "Core Aggregation"
    hits = (await owner.get("/api/inventory/search", params={"q": "runbook"})).json()["results"]
    assert any(h["kind"] == "page" for h in hits)
    assert any(h["kind"] == "rack" for h in (await owner.get("/api/inventory/search", params={"q": "rack a"})).json()["results"])

    removed = (await owner.delete("/api/dcim/demo")).json()["removed"]
    assert removed["dcim_racks"] == 1 and removed["dcim_locations"] == 2
    assert (await owner.get("/api/dcim/racks")).json()["items"] == []
    assert (await owner.get("/api/wiki/pages")).json()["items"] == []


async def test_viewer_cannot_edit_and_disabled_module_blocks(app, infra, owner):
    await owner.post("/api/users", json={"username": "viewer1", "password": PASSWORD, "roles": ["viewer"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        await c.post("/api/auth/login", json={"username": "viewer1", "password": PASSWORD})
        assert (await c.get("/api/dcim/racks")).status_code == 200
        assert (await c.post("/api/dcim/racks", json={"name": "x"})).status_code == 403
        assert (await c.post("/api/wiki/pages", json={"title": "x"})).status_code == 403
    await owner.post("/api/modules/cabling/disable", json={})
    assert (await owner.get("/api/cabling/cables")).status_code == 409
    rack = await _rack(owner)
    await owner.post("/api/dcim/racks", json={"name": "audit me"})
    actions = {r["action"] for r in (await owner.get("/api/audit")).json()["records"]}
    assert "dcim.rack.create" in actions and rack["name"] == "R1"
