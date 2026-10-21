"""Phase 2: client history and identity, IPAM, asset records, search expansion."""

import pytest

from warden.security.rbac import ROLES, P

SERVERS = "10.0.30.0/24"
UNRAID_MAC = "02:c1:00:00:00:01"  # simulator client 1, 10.0.30.101


@pytest.fixture
async def phase2(demo, owner):
    for mid in ("client-intel", "ipam"):
        r = await owner.post(f"/api/modules/{mid}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    r = await owner.post(f"/api/controllers/{demo['id']}/sync")
    assert r.json()["status"] == "succeeded"
    return demo


async def _client(owner, name):
    items = (await owner.get("/api/inventory/clients", params={"q": name})).json()["items"]
    return next(c for c in items if c["name"] == name)


async def _sync(owner, ctrl):
    assert (await owner.post(f"/api/controllers/{ctrl['id']}/sync")).json()["status"] == "succeeded"


async def test_client_history_records_moves(phase2, owner, app):
    c = await _client(owner, "unraid-tower")
    view = (await owner.get(f"/api/client-intel/clients/{c['id']}")).json()
    assert view["history_summary"]["sightings"] == 1
    assert "gateway" in view["path"][-1]["features"]  # attachment path climbs to the gateway
    assert view["network"]["name"] == "Servers" and view["fingerprint"] is None

    # Unchanged sync extends the open sighting rather than adding one.
    await _sync(owner, phase2)
    assert (await owner.get(f"/api/client-intel/clients/{c['id']}")).json()["history_summary"]["sightings"] == 1

    sim = app.state.warden.simulator
    item = next(x for x in sim.clients if x["name"] == "unraid-tower")
    item["ipAddress"] = "10.0.30.20"
    await _sync(owner, phase2)
    view = (await owner.get(f"/api/client-intel/clients/{c['id']}")).json()
    summary = view["history_summary"]
    assert (summary["sightings"], summary["distinct_addresses"]) == (2, 2)
    assert view["sightings"][0]["ip_address"] == "10.0.30.20"
    moved = [e for e in view["events"] if e["type"] == "inventory.client.moved"]
    assert moved and moved[0]["payload"]["from_ip"] == "10.0.30.101"


async def test_history_not_recorded_while_module_disabled(demo, owner):
    await owner.post("/api/modules/client-intel/enable", json={"with_dependencies": True})
    await owner.post("/api/modules/client-intel/disable", json={})
    await _sync(owner, demo)
    await owner.post("/api/modules/client-intel/enable", json={})
    c = await _client(owner, "printer")
    assert (await owner.get(f"/api/client-intel/clients/{c['id']}")).json()["history_summary"]["sightings"] == 0


async def test_client_identity_is_user_owned_and_searchable(phase2, owner):
    c = await _client(owner, "printer")
    r = await owner.patch(f"/api/inventory/clients/{c['id']}", json={"alias": "Office laser", "owner": "Facilities", "labels": ["office"]})
    assert r.status_code == 200 and r.json()["alias"] == "Office laser"
    await _sync(owner, phase2)  # discovery must not overwrite identity
    assert (await _client(owner, "printer"))["alias"] == "Office laser"
    hits = (await owner.get("/api/inventory/search", params={"q": "facilities"})).json()["results"]
    assert any(h["kind"] == "client" and h["title"] == "Office laser" and h["href"].endswith(c["id"]) for h in hits)
    recs = (await owner.get("/api/audit")).json()["records"]
    assert any(x["action"] == "inventory.client.identity" for x in recs)


async def test_device_asset_record(phase2, owner):
    d = (await owner.get("/api/inventory/devices")).json()["items"][0]
    asset = {"asset_tag": "FW-0042", "serial_number": "SN123", "purchased_on": "2025-03-01", "warranty_until": "2028-03-01"}
    r = await owner.patch(f"/api/inventory/devices/{d['id']}", json={"asset": asset})
    assert r.status_code == 200 and r.json()["asset_tag"] == "FW-0042"
    detail = (await owner.get(f"/api/inventory/devices/{d['id']}")).json()
    assert detail["asset"]["warranty_until"] == "2028-03-01"
    hits = (await owner.get("/api/inventory/search", params={"q": "fw-0042"})).json()["results"]
    assert [h["id"] for h in hits if h["kind"] == "device"] == [d["id"]]
    bad = await owner.patch(f"/api/inventory/devices/{d['id']}", json={"asset": {"purchased_on": "yesterday"}})
    assert bad.status_code == 422


async def test_adopt_documents_observed_networks(phase2, owner):
    listing = (await owner.get("/api/ipam/prefixes")).json()
    assert listing["total"] == 0 and len(listing["undocumented"]) == 7
    kinds = {f["kind"] for f in (await owner.get("/api/ipam/findings")).json()["items"]}
    assert "network_undocumented" in kinds

    created = (await owner.post("/api/ipam/prefixes/adopt", json={})).json()["created"]
    assert len(created) == 7
    listing = (await owner.get("/api/ipam/prefixes")).json()
    assert listing["undocumented"] == []
    servers = next(p for p in listing["items"] if p["cidr"] == SERVERS)
    assert servers["is_demo"] and servers["vlan_id"] == 30 and servers["observed"]["name"] == "Servers"
    assert servers["usable"] == 254 and servers["used"] >= 6  # gateway + server clients
    iot = next(p for p in listing["items"] if p["vlan_id"] == 40)
    assert iot["cidr"] == "10.0.40.0/23"  # normalised from the host address

    vlans = (await owner.get("/api/ipam/vlans")).json()["items"]
    assert {v["state"] for v in vlans} == {"documented"} and len(vlans) == 7
    assert (await owner.post("/api/ipam/prefixes/adopt", json={})).json()["created"] == []


async def test_prefix_validation_and_tree(phase2, owner):
    await owner.post("/api/ipam/prefixes/adopt", json={})
    r = await owner.post("/api/ipam/prefixes", json={"cidr": "10.0.0.0/16", "description": "Home", "status": "active"})
    assert r.status_code == 201, r.text
    assert (await owner.post("/api/ipam/prefixes", json={"cidr": "10.0.0.0/16"})).status_code == 409
    assert (await owner.post("/api/ipam/prefixes", json={"cidr": "not-a-net"})).status_code == 422
    items = (await owner.get("/api/ipam/prefixes")).json()["items"]
    top = next(p for p in items if p["cidr"] == "10.0.0.0/16")
    assert top["depth"] == 0 and all(p["depth"] == 1 and p["parent_id"] == top["id"] for p in items if p is not top)
    # A container that holds observed networks is not reported as unobserved.
    findings = (await owner.get("/api/ipam/findings")).json()["items"]
    assert not any(f["kind"] == "prefix_unobserved" for f in findings)

    ghost = (await owner.post("/api/ipam/prefixes", json={"cidr": "172.16.9.0/24"})).json()
    findings = (await owner.get("/api/ipam/findings")).json()["items"]
    assert any(f["kind"] == "prefix_unobserved" and f["refs"]["prefix_id"] == ghost["id"] for f in findings)
    await owner.patch(f"/api/ipam/prefixes/{ghost['id']}", json={"status": "planned"})
    findings = (await owner.get("/api/ipam/findings")).json()["items"]
    assert not any(f["kind"] == "prefix_unobserved" for f in findings)


async def test_reservations_and_conflicts(phase2, owner):
    await owner.post("/api/ipam/prefixes/adopt", json={})
    pid = next(p["id"] for p in (await owner.get("/api/ipam/prefixes")).json()["items"] if p["cidr"] == SERVERS)
    base = f"/api/ipam/prefixes/{pid}/reservations"
    assert (await owner.post(base, json={"address": "10.0.31.5"})).status_code == 422
    assert (await owner.post(base, json={"address": "10.0.30.0"})).status_code == 422
    assert (await owner.post(base, json={"address": "10.0.30.9", "mac_address": "nope"})).status_code == 422

    # Reserve unraid-tower's address for a different MAC: conflict, and inside the DHCP pool.
    r = await owner.post(base, json={"address": "10.0.30.101", "mac_address": "AA-BB-CC-DD-EE-FF", "hostname": "nas"})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["mac_address"] == "aa:bb:cc:dd:ee:ff" and res["state"] == "conflict"
    assert (await owner.post(base, json={"address": "10.0.30.101"})).status_code == 409

    findings = (await owner.get("/api/ipam/findings")).json()
    kinds = {f["kind"] for f in findings["items"] if f["refs"].get("reservation_id") == res["id"]}
    assert kinds == {"reservation_conflict", "reservation_in_pool"}
    assert findings["items"][0]["severity"] == "critical"

    detail = (await owner.get(f"/api/ipam/prefixes/{pid}")).json()
    by_addr = {e["address"]: e for e in detail["address_map"]}
    assert len(detail["address_map"]) == 256
    assert by_addr["10.0.30.0"]["state"] == "network" and by_addr["10.0.30.1"]["state"] == "gateway"
    assert by_addr["10.0.30.101"]["conflict"] and by_addr["10.0.30.101"]["holders"][0]["name"] == "unraid-tower"
    assert by_addr["10.0.30.200"]["state"] == "dhcp" and detail["next_free"] == "10.0.30.2"

    # Fix it: point the reservation at the real MAC, outside the pool.
    r = await owner.patch(f"/api/ipam/reservations/{res['id']}", json={"mac_address": UNRAID_MAC, "address": "10.0.30.10"})
    assert r.json()["state"] == "elsewhere" and r.json()["mac_seen_at"] == "10.0.30.101"
    c = await _client(owner, "unraid-tower")
    view = (await owner.get(f"/api/client-intel/clients/{c['id']}")).json()
    assert view["reservation"]["status"] == "elsewhere"

    hits = (await owner.get("/api/inventory/search", params={"q": "10.0.30.10"})).json()["results"]
    assert {"prefix", "reservation"} <= {h["kind"] for h in hits}

    assert (await owner.delete(f"/api/ipam/reservations/{res['id']}")).status_code == 204
    actions = {x["action"] for x in (await owner.get("/api/audit")).json()["records"]}
    assert {"ipam.prefix.adopt", "ipam.reservation.create", "ipam.reservation.update", "ipam.reservation.delete"} <= actions


async def test_vlan_records(phase2, owner):
    r = await owner.post("/api/ipam/vlans", json={"vlan_id": 99, "name": "Lab"})
    assert r.status_code == 201
    assert (await owner.post("/api/ipam/vlans", json={"vlan_id": 99, "name": "Again"})).status_code == 409
    assert (await owner.post("/api/ipam/vlans", json={"vlan_id": 5000, "name": "Bad"})).status_code == 422
    vlans = {v["vlan_id"]: v for v in (await owner.get("/api/ipam/vlans")).json()["items"]}
    assert vlans[99]["state"] == "unobserved" and vlans[30]["state"] == "undocumented"
    await owner.post("/api/ipam/prefixes", json={"cidr": "10.0.88.0/24", "vlan_id": 88, "status": "planned"})
    vlans = {v["vlan_id"]: v for v in (await owner.get("/api/ipam/vlans")).json()["items"]}
    assert vlans[88]["state"] == "prefix_only"
    await owner.patch(f"/api/ipam/vlans/{r.json()['id']}", json={"status": "planned"})
    vlans = {v["vlan_id"]: v for v in (await owner.get("/api/ipam/vlans")).json()["items"]}
    assert vlans[99]["state"] == "planned"


async def test_ipam_requires_module_and_permission(demo, owner):
    assert (await owner.get("/api/ipam/prefixes")).status_code == 409
    assert (await owner.get("/api/client-intel/clients/x")).status_code == 409
    assert P.IPAM_EDIT in ROLES["operator"] and P.IPAM_EDIT not in ROLES["viewer"] | ROLES["auditor"]
