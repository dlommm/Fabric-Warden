"""Phase 5d: SNMP interface counters (IF-MIB), simulator and a local pysnmp agent."""

import json
import socket
from pathlib import Path

import pytest
from sqlalchemy import select

from warden.integrations.snmp.client import Credentials, PysnmpSession, SnmpError, collect, port_number
from warden.integrations.snmp.sync import rates
from warden.models import MetricSeries

SNMP_SRC = Path(__file__).parents[1] / "src/warden/integrations/snmp"


@pytest.fixture
async def snmp(demo, owner, app):
    app.state.warden.snmp_sim.frozen = 1_792_000_000.0
    r = await owner.post("/api/modules/snmp/enable", json={"with_dependencies": True})
    assert r.status_code == 200, r.text
    r = await owner.post("/api/snmp/demo")
    assert r.status_code == 201, r.text
    return {t["name"]: t for t in r.json()["items"]}


async def _poll_all(owner, targets):
    for t in targets.values():
        assert (await owner.post(f"/api/snmp/targets/{t['id']}/poll")).json()["status"] == "ok"


async def test_demo_targets_rates_errors_and_metrics(snmp, owner, app):
    assert set(snmp) == {"Gateway", "Core Aggregation", "Rack PoE Switch", "Office Switch"}  # APs have no port table worth polling
    assert all(t["status"] == "ok" and t["is_demo"] and t["device"] for t in snmp.values())
    cands = {c["name"]: c for c in (await owner.get("/api/snmp/candidates")).json()["items"]}
    assert all(c["targeted"] for c in cands.values()) and "AP Office" not in cands

    # The first poll has nothing to compare against: no rates yet.
    rack = snmp["Rack PoE Switch"]
    first = (await owner.get(f"/api/snmp/devices/{rack['device']['id']}")).json()
    assert first["target"]["id"] == rack["id"] and all(i["rx_bps"] is None for i in first["interfaces"])

    app.state.warden.snmp_sim.offset += 60
    await _poll_all(owner, snmp)
    ifaces = {
        i["port_idx"]: i for i in (await owner.get(f"/api/snmp/devices/{rack['device']['id']}")).json()["interfaces"] if i["port_idx"]
    }
    p9 = ifaces[9]
    assert p9["name"] == "Port 9" and p9["oper_status"] == "up" and p9["speed_mbps"] == 2500
    assert p9["errors_per_min"] == 2.0 and p9["rx_bps"] > 0 and 0 < p9["utilisation_pct"] < 100
    down = next(i for i in ifaces.values() if i["oper_status"] == "down")
    assert down["rx_bps"] == 0 and down["utilisation_pct"] is None
    # Loopback is collected but hidden from the physical view.
    allif = (await owner.get("/api/snmp/interfaces", params={"target_id": rack["id"], "physical": False})).json()["items"]
    assert any(i["name"] == "lo" and i["port_idx"] is None for i in allif)

    problems = (await owner.get("/api/snmp/interfaces", params={"problems": True})).json()["items"]
    assert {(i["device"]["name"], i["name"]) for i in problems} == {("Rack PoE Switch", "Port 9"), ("Core Aggregation", "Port 25")}
    found = {f["kind"]: f for f in (await owner.get("/api/snmp/findings")).json()["items"]}
    assert found["errors"]["title"] == "Rack PoE Switch Port 9 is logging 2 errors/min"
    assert found["discards"]["title"] == "Core Aggregation Port 25 is discarding 3 packets/min"

    async with app.state.warden.db.sessionmaker() as db:
        series = list(await db.scalars(select(MetricSeries).where(MetricSeries.metric.like("port.%"))))
    assert {s.metric for s in series} == {"port.rx_bps", "port.tx_bps", "port.errors", "port.discards"}
    assert all(s.is_demo and s.entity_type == "interface" for s in series)
    assert any(s.label == "Rack PoE Switch · Port 9" and s.metric == "port.errors" and s.last_value == 2.0 for s in series)
    assert "port.errors" in {m["id"] for m in (await owner.get("/api/metrics")).json()["items"]}


async def test_wrong_credentials_degrade_with_hint_and_secrets_never_leak(snmp, owner):
    profile = (await owner.get("/api/snmp/profiles")).json()["items"][0]
    assert profile["credential_fingerprint"] and profile["target_count"] == 4
    assert "passphrase" not in json.dumps(profile)
    r = await owner.patch(
        f"/api/snmp/profiles/{profile['id']}",
        json={"secrets": {"name": "x", "version": "v3", "username": profile["username"], "password": "wrong-password"}},
    )
    assert r.status_code == 200 and r.json()["credential_fingerprint"] != profile["credential_fingerprint"]
    gw = snmp["Gateway"]
    assert (await owner.post(f"/api/snmp/targets/{gw['id']}/poll")).json()["status"] == "degraded"
    t = next(x for x in (await owner.get("/api/snmp/targets")).json()["items"] if x["id"] == gw["id"])
    assert "refused the credentials" in t["status_detail"] and "SHA authentication and AES-128" in t["hint"]
    assert any(f["kind"] == "target_failing" for f in (await owner.get("/api/snmp/findings")).json()["items"])

    # A profile in use cannot be deleted; the denial is audited.
    assert (await owner.delete(f"/api/snmp/profiles/{profile['id']}")).status_code == 409
    audit = (await owner.get("/api/audit", params={"action": "snmp.profile.delete"})).json()["records"]
    assert any(a["action"] == "snmp.profile.delete.denied" for a in audit)


async def test_profile_validation_and_demo_removal(snmp, owner):
    bad = await owner.post("/api/snmp/profiles", json={"name": "v3", "version": "v3", "username": "u", "password": "short"})
    assert bad.status_code == 422
    assert (await owner.post("/api/snmp/profiles", json={"name": "v2", "version": "v2c"})).status_code == 422
    ok = await owner.post("/api/snmp/profiles", json={"name": "Site v3", "username": "monitor", "password": "long-enough-pass"})
    assert ok.status_code == 201 and ok.json()["auth_protocol"] == "SHA" and ok.json()["priv_protocol"] == "AES"
    # Live profiles never poll demo devices.
    dev = snmp["Gateway"]["device"]["id"]
    r = await owner.post("/api/snmp/targets", json={"profile_id": ok.json()["id"], "device_ids": [dev]})
    assert r.status_code == 409  # already polled by the demo target
    assert (await owner.delete("/api/snmp/demo")).json()["removed"] == 4
    r = await owner.post("/api/snmp/targets", json={"profile_id": ok.json()["id"], "device_ids": [dev]})
    assert r.status_code == 422
    assert [p["name"] for p in (await owner.get("/api/snmp/profiles")).json()["items"]] == ["Site v3"]


def test_rates_handle_wraps_resets_and_first_poll():
    prev = {"in_octets": 1_000, "out_octets": 5_000, "in_errors": 2**32 - 10, "out_errors": 0, "in_discards": 7, "out_discards": 0}
    cur = {"in_octets": 751_000, "out_octets": 4_000, "in_errors": 20, "out_errors": 0, "in_discards": 3, "out_discards": 0}
    r = rates(prev, cur, 60)
    assert r["rx_bps"] == 100_000.0  # 750 kB in 60 s
    assert r["tx_bps"] is None  # a 64-bit counter going backwards is a reset, never a wrap
    assert r["errors_per_min"] == 30.0  # the 32-bit error counter wrapped
    assert r["discards_per_min"] is None  # a small step back on a 32-bit counter is a reset
    assert rates({}, cur, 60)["rx_bps"] is None and rates(prev, cur, 0)["rx_bps"] is None


def test_port_matching_is_by_name_only():
    assert port_number("Port 7", "") == 7
    assert port_number("", "Slot: 0 Port: 12 Gigabit - Level") == 12
    assert port_number("eth3", "eth3") is None and port_number("switch0", "") is None
    # Observed on live UniFi switches: Enterprise Campus / Pro XG unit-1 ports use the ifIndex;
    # USW Enterprise uses slot 0; ifAlias (the operator's label) is never read.
    assert port_number("tw1/0/1", "TwoPointFiveGigabitEthernet1/0/1", 1) == 1
    assert port_number("te1/0/31", "TenGigabitEthernet1/0/31", 47) == 47
    assert port_number("tf1/0/1", "TwentyFiveGigaEthernet1/0/1", 49) == 49
    assert port_number("tf2/0/1", "TwentyFiveGigaEthernet2/0/1", 81) is None  # another stack unit
    assert port_number("0/47", "Slot: 0 Port: 47 2.5G - Level", 47) == 47


def test_collector_is_read_only():
    text = "".join(p.read_text() for p in SNMP_SRC.glob("*.py"))
    assert "set_cmd" not in text and "SetCommand" not in text
    matrix = json.loads((SNMP_SRC / "official_api_matrix.json").read_text())
    from warden.integrations.snmp.client import COLUMNS, SYS_DESCR, SYS_NAME, SYS_UPTIME

    documented = {o["oid"] for o in matrix["objects"]}
    assert {*COLUMNS.values(), SYS_DESCR, SYS_NAME, SYS_UPTIME} <= documented


# -- the real UDP session against a local pysnmp agent (no network devices involved) -----


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def agent():
    from pysnmp.carrier.asyncio.dgram import udp
    from pysnmp.entity import config, engine
    from pysnmp.entity.rfc3413 import cmdrsp, context

    port = _free_udp_port()
    e = engine.SnmpEngine()
    config.add_transport(e, udp.DOMAIN_NAME, udp.UdpTransport().open_server_mode(("127.0.0.1", port)))
    config.add_v3_user(e, "fw", config.USM_AUTH_HMAC96_SHA, "passphrase1", config.USM_PRIV_CFB128_AES, "passphrase1")
    config.add_vacm_user(e, 3, "fw", "authPriv", (1, 3, 6), ())
    ctx = context.SnmpContext(e)
    for responder in (cmdrsp.GetCommandResponder, cmdrsp.NextCommandResponder, cmdrsp.BulkCommandResponder):
        responder(e, ctx)
    e.transport_dispatcher.job_started(1)
    yield port
    e.transport_dispatcher.close_dispatcher()


def _session(port: int, user: str = "fw", password: str = "passphrase1") -> PysnmpSession:  # noqa: S107 - local test agent
    return PysnmpSession("127.0.0.1", port, Credentials("v3", user, "SHA", password, "AES", password), timeout=1, retries=0)


async def test_pysnmp_session_v3_get_walk_and_errors(agent):
    s = _session(agent)
    try:
        got = await s.get(["1.3.6.1.2.1.1.1.0"])
        assert got["1.3.6.1.2.1.1.1.0"].startswith(b"PySNMP engine")
        walked = await s.walk("1.3.6.1.2.1.1")
        assert "1.3.6.1.2.1.1.3.0" in walked and all(k.startswith("1.3.6.1.2.1.1.") for k in walked)
        # The test agent has no IF-MIB: the collector says so instead of reporting zero ports.
        with pytest.raises(SnmpError) as e:
            await collect(s)
        assert e.value.kind == "protocol"
    finally:
        s.close()
    for bad in (_session(agent, password="wrong-pass"), _session(agent, user="nobody")):
        with pytest.raises(SnmpError) as e:
            await bad.get(["1.3.6.1.2.1.1.1.0"])
        bad.close()
        assert e.value.kind == "auth"


def test_virtual_interfaces_on_linux_agents_are_not_ports():
    from warden.integrations.snmp.client import is_physical

    # Names and types as a UniFi gateway on UniFi OS 5.1 reports them (all ifType 6 unless noted).
    assert all(is_physical(6, n) for n in ("eth2", "eth8", "eth13", "Port 7"))
    virtual = ["eth13.100", "br0", "br100", "switch0", "switch0.1", "ifb0", "gretap0", "erspan0", "enP2p1s0v3", "wgsrv1"]
    # And as U6/U7 access points report their Wi-Fi side.
    virtual += ["wifi0ap3", "wifi2ap16", "mld-wifi0", "ra0", "rai4", "wds0", "apcli0", "apclii0", "ra4.90"]
    assert not any(is_physical(6, n) for n in virtual)
    assert all(is_physical(6, n) for n in ("tw1/0/1", "tf1/0/32", "0/47"))
    assert not is_physical(6, "gi2/0/5", "notPresent")  # an absent stack member's port
    assert not is_physical(24, "lo") and not is_physical(131, "gre1")


async def test_same_agent_at_two_addresses_is_reported(snmp, owner, app):
    from warden.models import SnmpTarget

    gw = snmp["Gateway"]
    async with app.state.warden.db.sessionmaker() as db:
        db.add(
            SnmpTarget(
                profile_id=gw["profile"]["id"], name="Gateway (management IP)", host="10.0.9.1", device_id=gw["device"]["id"], is_demo=True
            )
        )
        await db.commit()
    second = next(t for t in (await owner.get("/api/snmp/targets")).json()["items"] if t["host"] == "10.0.9.1")
    assert (await owner.post(f"/api/snmp/targets/{second['id']}/poll")).json()["status"] == "ok"
    dup = [f for f in (await owner.get("/api/snmp/findings")).json()["items"] if f["kind"] == "duplicate_agent"]
    assert len(dup) == 1 and "are the same agent" in dup[0]["title"] and dup[0]["severity"] == "info"


async def test_target_added_by_host_links_to_device_once_synced(snmp, owner, app):
    """A live target added before its controller was synced attaches to the device at its address when polled."""
    from warden.integrations.snmp.simulator import SIM_PASSWORD, SimulatedAgent
    from warden.models import Device

    profile = await owner.post("/api/snmp/profiles", json={"name": "Live", "version": "v2c", "community": SIM_PASSWORD})
    assert profile.status_code == 201, profile.text
    r = await owner.post("/api/snmp/targets", json={"profile_id": profile.json()["id"], "host": "10.99.0.5"})
    assert r.status_code == 201, r.text
    target = r.json()["items"][0]
    assert target["device"] is None

    state = app.state.warden
    state.snmp_session = lambda host, port, creds: SimulatedAgent(state.snmp_sim, {"name": "sw", "ports": []}, creds)
    async with state.db.sessionmaker() as db:
        device = await db.get(Device, snmp["Gateway"]["device"]["id"])
        device.is_demo, device.ip_address = False, "10.99.0.5"  # stands in for a device the live sync just added
        await db.commit()
    assert (await owner.post(f"/api/snmp/targets/{target['id']}/poll")).json()["status"] == "ok"
    t = next(x for x in (await owner.get("/api/snmp/targets")).json()["items"] if x["id"] == target["id"])
    assert t["device"]["id"] == snmp["Gateway"]["device"]["id"]
