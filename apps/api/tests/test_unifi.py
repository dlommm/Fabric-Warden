import pathlib

import httpx
import pytest
from sqlalchemy import select, text

from warden.integrations.unifi import capabilities
from warden.integrations.unifi.client import UniFiError, UniFiNetworkClient, WriteAuthorization, WriteNotAuthorized
from warden.integrations.unifi.simulator import SIM_API_KEY, SITE_ID, SimulatorState, create_simulator_app
from warden.models import Credential

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "warden"


def sim_client(state: SimulatorState | None = None, key: str = SIM_API_KEY) -> UniFiNetworkClient:
    return UniFiNetworkClient("http://sim", key, transport=httpx.ASGITransport(app=create_simulator_app(state)))


def test_capability_matrix_versions():
    old = capabilities.evaluate("9.3.45")["capabilities"]
    assert old["network.devices.read"]["status"] == "supported"
    assert old["network.firewall.policies.read"]["status"] == "unsupported"
    new = capabilities.evaluate("10.6.106")["capabilities"]
    assert new["network.firewall.policies.read"]["status"] == "supported"
    assert new["network.switching.read"]["min_version"] == "10.3.58"
    assert capabilities.evaluate("11.0.1")["beyond_known_spec"] is True
    assert capabilities.evaluate(None)["capabilities"]["network.sites.read"]["status"] == "unknown"


def test_not_exposed_facts_are_declared():
    ids = {x["id"] for x in capabilities.evaluate("10.6.106")["not_exposed"]}
    assert {"sfp.optical_diagnostics", "port.fec", "dhcp.leases"} <= ids


async def test_client_paginates():
    async with sim_client() as api:
        clients = await api.clients(SITE_ID)
    assert len(clients) == 26  # > one default page of 25 in the simulator


async def test_client_bad_key_is_auth_error():
    async with sim_client(key="wrong-key-123") as api:
        with pytest.raises(UniFiError) as e:
            await api.info()
    assert e.value.kind == "auth"


async def test_simulator_hides_endpoints_newer_than_version():
    async with sim_client(SimulatorState(version="9.3.45")) as api:
        with pytest.raises(UniFiError) as e:
            await api.networks(SITE_ID)
    assert e.value.kind == "not_found"


async def test_writes_refused_without_change_engine():
    async with sim_client() as api:
        with pytest.raises(WriteNotAuthorized):
            await api._request("POST", f"/v1/sites/{SITE_ID}/devices/x/actions", json={"action": "RESTART"})
    with pytest.raises(WriteNotAuthorized):
        WriteAuthorization("chg-1", object())


def test_write_gate_issuer_not_imported_outside_change_engine():
    allowed = {
        pathlib.Path("integrations/unifi/client.py"),  # checks the sentinel when a WriteAuthorization is built
        pathlib.Path("integrations/unifi/_write_gate.py"),
        pathlib.Path("changes/engine.py"),  # the change engine, the only issuer
    }
    offenders = [p.relative_to(SRC) for p in SRC.rglob("*.py") if "_write_gate" in p.read_text() and p.relative_to(SRC) not in allowed]
    assert offenders == [], "only the change engine may mint WriteAuthorization"


async def test_controller_test_endpoint_explains_failures(owner, monkeypatch):
    r = await owner.post("/api/controllers/test", json={"base_url": "https://192.0.2.1", "api_key": "abcdefgh123"})
    body = r.json()
    assert body["ok"] is False
    assert body["error_kind"] in ("unreachable", "tls")
    assert body["hint"]


async def test_sync_populates_inventory_with_provenance(demo, owner):
    devices = (await owner.get("/api/inventory/devices")).json()["items"]
    gw = next(d for d in devices if "gateway" in d["features"])
    assert gw["provenance"]["is_demo"] is True
    assert gw["provenance"]["source"].startswith("simulator@")
    assert gw["cpu_pct"] is not None
    detail = (await owner.get(f"/api/inventory/devices/{gw['id']}")).json()
    assert any(p["connector"] == "SFP28" for p in detail["ports"])
    assert detail["uplink"] is None and len(detail["downstream"]) == 1
    assert detail["history"][0]["type"] == "inventory.device.discovered"


async def test_networks_compute_pools_and_clients(demo, owner):
    nets = {n["name"]: n for n in (await owner.get("/api/inventory/networks")).json()["items"]}
    assert nets["IoT"]["subnet"] == "10.0.40.1/23"
    assert nets["IoT"]["dhcp_pool_size"] == 151
    assert nets["Cameras"]["active_clients"] >= 4
    assert nets["Guest"]["zone"]["name"] == "Hotspot"


async def test_sync_detects_state_changes_and_missing(app, demo, owner):
    sim = app.state.warden.simulator
    sim.devices[6]["state"] = "ONLINE"  # AP Garage recovers
    removed = sim.clients.pop(0)
    r = await owner.post(f"/api/controllers/{demo['id']}/sync")
    assert r.json()["status"] == "succeeded"
    events = (await owner.get("/api/events")).json()["events"]
    types = [e["type"] for e in events]
    assert "inventory.device.state_changed" in types
    assert any(e["type"] == "inventory.client.disconnected" and e["payload"]["name"] == removed["name"] for e in events)
    # Re-sync without changes is idempotent: no new discovery events.
    before = len(events)
    await owner.post(f"/api/controllers/{demo['id']}/sync")
    after = (await owner.get("/api/events?type=inventory.")).json()["events"]
    assert len([e for e in after if e["type"] == "inventory.device.discovered"]) == 8
    assert len(after) <= before


async def test_capabilities_recorded_and_verified(demo, owner):
    c = (await owner.get(f"/api/controllers/{demo['id']}")).json()
    caps = c["capabilities"]["capabilities"]
    assert caps["network.devices.read"]["verified"] is True
    assert caps["network.firewall.policies.read"]["verified"] is True
    assert caps["network.devices.restart"]["write"] is True
    assert caps["network.devices.restart"]["verified"] is False
    assert c["write_mode"] == "read_only"
    assert c["credential_fingerprint"]


async def test_older_controller_degrades_gracefully(app, owner):
    app.state.warden.simulator.version = "9.3.45"
    await owner.post("/api/modules/presets/minimal/apply")
    ctrl = (await owner.post("/api/controllers/demo")).json()
    assert (await owner.post(f"/api/controllers/{ctrl['id']}/sync")).json()["status"] == "succeeded"
    assert (await owner.get("/api/inventory/devices")).json()["total"] == 8
    assert (await owner.get("/api/inventory/networks")).json()["total"] == 0
    c = (await owner.get(f"/api/controllers/{ctrl['id']}")).json()
    assert c["capabilities"]["capabilities"]["network.networks.read"]["status"] == "unsupported"


async def test_unreachable_controller_marks_status(app, demo, owner):
    async def boom(*a, **k):
        raise UniFiError("cannot connect", kind="unreachable")

    from warden.integrations.unifi import client as client_mod

    orig = client_mod.UniFiNetworkClient.info
    client_mod.UniFiNetworkClient.info = boom
    try:
        await owner.post(f"/api/controllers/{demo['id']}/sync")
    finally:
        client_mod.UniFiNetworkClient.info = orig
    c = (await owner.get(f"/api/controllers/{demo['id']}")).json()
    assert c["status"] == "unreachable"
    # Previously observed inventory is retained, not wiped.
    assert (await owner.get("/api/inventory/devices")).json()["total"] == 8


async def test_api_keys_encrypted_at_rest(app, demo):
    async with app.state.warden.db.sessionmaker() as db:
        cred = await db.scalar(select(Credential).where(Credential.kind == "unifi-api-key"))
        assert SIM_API_KEY not in cred.ciphertext
        dump = (await db.execute(text("SELECT ciphertext FROM credentials"))).scalars().all()
        assert all(SIM_API_KEY not in c for c in dump)


async def test_user_metadata_survives_resync(demo, owner):
    dev = (await owner.get("/api/inventory/devices")).json()["items"][0]
    await owner.patch(f"/api/inventory/devices/{dev['id']}", json={"labels": ["rack-a"], "notes": "top of rack"})
    await owner.post(f"/api/controllers/{demo['id']}/sync")
    d = (await owner.get(f"/api/inventory/devices/{dev['id']}")).json()
    assert d["labels"] == ["rack-a"] and d["notes"] == "top of rack"


async def test_search(demo, owner):
    res = (await owner.get("/api/inventory/search?q=proxmox")).json()["results"]
    assert {r["title"] for r in res} == {"proxmox-01", "proxmox-02"}


async def test_device_list_resolves_uplinks(demo, owner):
    items = {d["name"]: d for d in (await owner.get("/api/inventory/devices")).json()["items"]}
    assert items["Gateway"]["uplink_id"] is None
    assert items["Core Aggregation"]["uplink_id"] == items["Gateway"]["id"]
    assert items["AP Garage"]["uplink_id"] == items["Office Switch"]["id"]


async def test_search_results_carry_demo_label(demo, owner):
    res = (await owner.get("/api/inventory/search?q=gateway")).json()["results"]
    assert res and all(r["is_demo"] is True for r in res)
