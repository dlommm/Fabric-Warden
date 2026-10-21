"""Phase 5a: firewall analysis, configuration history and compliance."""

from datetime import timedelta

import httpx
import pytest

from tests.conftest import PASSWORD
from warden.db import utcnow


@pytest.fixture
async def sec(demo, owner):
    for mid in ("firewall", "config-history", "compliance"):
        r = await owner.post(f"/api/modules/{mid}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    assert (await owner.post(f"/api/controllers/{demo['id']}/sync")).json()["status"] == "succeeded"
    return demo


async def test_firewall_analysis(sec, owner):
    data = (await owner.get("/api/firewall")).json()
    site = data["sites"][0]
    pol = {p["name"]: p for p in site["policies"]}
    dns = pol["Allow IoT to Internal DNS"]
    assert dns["relation"] == "shadowed" and dns["covered_by"] == "Block IoT to Internal" and dns["filter_summary"] == "to port 53"
    web = pol["Allow Internal to IoT web"]
    assert web["relation"] == "redundant" and web["covered_by"] == "Allow Internal to IoT" and web["filter_summary"] == "to ports 80, 443"
    assert [p["name"] for p in site["system_policies"]] == ["Allow established/related"]
    assert any(p["name"] == "Legacy allow IoT to Servers" for p in site["disabled"])
    kinds = {f["kind"] for f in data["findings"]}
    assert {"shadowed", "redundant", "disabled", "block_without_logging"} <= kinds
    cell = next(c for c in site["matrix"] if c["source"].lower() == "iot" and c["destination"].lower() == "internal")
    assert cell["action"] == "BLOCK" and cell["policy"]["name"] == "Block IoT to Internal"


async def test_config_history_snapshots_diff_and_drift(sec, owner, app):
    snaps = (await owner.get("/api/config/snapshots")).json()["items"]
    assert len(snaps) == 1 and snaps[0]["is_baseline"] and snaps[0]["summary"]["policies"] == 9
    # An unchanged sync stores nothing.
    await owner.post(f"/api/controllers/{sec['id']}/sync")
    assert len((await owner.get("/api/config/snapshots")).json()["items"]) == 1

    sim = app.state.warden.simulator
    sim.policies[1]["name"] = "Block IoT to LAN"
    sim.policies[1]["loggingEnabled"] = False
    sim.devices[3]["firmwareVersion"] = "7.2.200"
    await owner.post(f"/api/controllers/{sec['id']}/sync")
    snaps = (await owner.get("/api/config/snapshots")).json()["items"]
    assert len(snaps) == 2 and snaps[0]["change_total"] == 2
    detail = (await owner.get(f"/api/config/snapshots/{snaps[0]['id']}")).json()
    changed = detail["diff"]["policies"]["changed"][0]
    assert {f["field"] for f in changed["fields"]} == {"name", "logging"}
    assert detail["diff"]["devices"]["changed"][0]["fields"][0] == {"field": "firmware", "from": "7.1.26", "to": "7.2.200"}

    drift = (await owner.get("/api/config/drift")).json()["items"][0]
    assert drift["counts"] == {"policies": {"added": 0, "removed": 0, "changed": 1}, "devices": {"added": 0, "removed": 0, "changed": 1}}
    events = (await owner.get("/api/events", params={"type": "config.changed"})).json()
    assert any(e["type"] == "config.changed" for e in events.get("items", events.get("events", [])))

    # Pinning the latest snapshot as the baseline clears the drift.
    await owner.post(f"/api/config/snapshots/{snaps[0]['id']}/baseline", json={"note": "reviewed"})
    assert (await owner.get("/api/config/drift")).json()["items"][0]["counts"] == {}
    d = (await owner.get("/api/config/diff", params={"from": snaps[1]["id"], "to": snaps[0]["id"]})).json()
    assert d["counts"]["policies"]["changed"] == 1


async def test_compliance_controls_evidence_and_waivers(sec, owner, app):
    data = (await owner.get("/api/compliance")).json()
    st = {c["id"]: c for c in data["items"]}
    assert st["access.mfa_privileged"]["status"] == "fail"  # the owner has no MFA
    assert st["segmentation.iot_isolated"]["status"] == "pass"
    assert st["segmentation.guest_isolated"]["status"] in ("pass", "fail", "unknown")
    assert st["firewall.hygiene"]["status"] == "fail" and any("never matches" in e["fact"] for e in st["firewall.hygiene"]["evidence"])
    assert st["platform.audit_intact"]["status"] == "pass"
    assert st["platform.controller_tls"]["status"] == "unknown"  # only the demo controller
    assert st["config.no_drift"]["status"] == "pass"
    assert st["servers.known_to_network"]["status"] == "unknown"
    assert st["devices.online"]["status"] == "fail"  # the simulator's interrupted AP

    until = (utcnow() + timedelta(days=30)).isoformat()
    r = await owner.post("/api/compliance/access.mfa_privileged/waive", json={"reason": "Lab only, single user", "until": until})
    assert r.status_code == 200, r.text
    st = {c["id"]: c for c in (await owner.get("/api/compliance")).json()["items"]}
    assert st["access.mfa_privileged"]["status"] == "waived" and st["access.mfa_privileged"]["waiver"]["reason"] == "Lab only, single user"
    past = (utcnow() - timedelta(days=1)).isoformat()
    assert (await owner.post("/api/compliance/devices.online/waive", json={"reason": "whatever", "until": past})).status_code == 422
    csv = (await owner.get("/api/compliance/evidence.csv")).text
    assert csv.startswith("control,title,status") and "Lab only, single user" in csv
    assert (await owner.delete("/api/compliance/access.mfa_privileged/waive")).status_code == 204
    actions = {r["action"] for r in (await owner.get("/api/audit")).json()["records"]}
    assert {"compliance.waive", "compliance.unwaive"} <= actions

    await owner.post("/api/users", json={"username": "op5", "password": PASSWORD, "roles": ["operator"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        await c.post("/api/auth/login", json={"username": "op5", "password": PASSWORD})
        assert (await c.get("/api/compliance")).status_code == 200
        assert (await c.post("/api/compliance/devices.online/waive", json={"reason": "operator try", "until": until})).status_code == 403
