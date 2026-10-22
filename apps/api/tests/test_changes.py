"""Phase 6a: the change engine against the simulated controller (never a real one)."""

from datetime import timedelta

import httpx
import pytest

from tests.conftest import PASSWORD
from warden.db import utcnow
from warden.models import ChangeRequest


@pytest.fixture
async def changes(demo, owner, app):
    r = await owner.post("/api/modules/change-engine/enable", json={"with_dependencies": True})
    assert r.status_code == 200, r.text
    return app.state.warden.simulator


async def _policy(owner, name: str) -> dict:
    return next(p for p in (await owner.get("/api/inventory/firewall")).json()["policies"] if p["name"] == name)


async def _device(owner, name: str) -> dict:
    return next(d for d in (await owner.get("/api/inventory/devices")).json()["items"] if d["name"] == name)


async def _allow_changes(owner, controller_id: str) -> None:
    r = await owner.put(f"/api/changes/controllers/{controller_id}/write-mode", json={"write_mode": "changes"})
    assert r.status_code == 200, r.text


def _sim_policy(sim, name: str) -> dict:
    return next(p for p in sim.policies if p["name"] == name)


async def test_logging_change_full_lifecycle_and_revert(changes, owner, demo):
    sim = changes
    name = next(p["name"] for p in sim.policies if not p["loggingEnabled"] and p.get("source", {}) is not None)
    pol = await _policy(owner, name)

    r = await owner.post(
        "/api/changes",
        json={
            "operation": "firewall_policy.set_logging",
            "target_id": pol["id"],
            "params": {"enabled": True},
            "reason": "Feed connection analytics",
        },
    )
    assert r.status_code == 201, r.text
    cr = r.json()
    assert cr["ref"] == "CHG-0001" and cr["status"] == "proposed" and cr["risk"] == "low" and cr["reversible"] is True
    assert cr["diff"] == [{"field": "Logging", "before": "Off", "after": "On"}] and cr["before"]["loggingEnabled"] is False
    assert cr["is_demo"] is True

    # A second proposal for the same target is refused while this one is open.
    dup = await owner.post(
        "/api/changes", json={"operation": "firewall_policy.set_logging", "target_id": pol["id"], "params": {"enabled": True}}
    )
    assert dup.status_code == 409 and "CHG-0001" in dup.json()["detail"]

    assert (await owner.post(f"/api/changes/{cr['id']}/approve", json={})).json()["status"] == "approved"
    # Read-only by default: approval alone never writes.
    refused = await owner.post(f"/api/changes/{cr['id']}/apply")
    assert refused.status_code == 409 and "turned off for this controller" in refused.json()["detail"]
    assert sim.writes == []

    await _allow_changes(owner, demo["id"])
    done = (await owner.post(f"/api/changes/{cr['id']}/apply")).json()
    assert done["status"] == "succeeded", done["evidence"]
    assert [w["method"] for w in sim.writes] == ["PATCH"] and sim.writes[0]["body"] == {"loggingEnabled": True}
    assert _sim_policy(sim, name)["loggingEnabled"] is True
    kinds = [e["kind"] for e in done["evidence"]]
    assert kinds[:2] == ["read", "approve"] and "write" in kinds and kinds[-1] == "succeeded"
    # The inventory was refreshed from the controller.
    assert (await _policy(owner, name))["logging_enabled"] is True

    rv = (await owner.post(f"/api/changes/{cr['id']}/revert", json={"note": "Too noisy"})).json()
    assert (
        rv["status"] == "proposed"
        and rv["inverse_of_id"] == cr["id"]
        and rv["diff"][0] == {"field": "Logging", "before": "On", "after": "Off"}
    )
    await owner.post(f"/api/changes/{rv['id']}/approve", json={})
    assert (await owner.post(f"/api/changes/{rv['id']}/apply")).json()["status"] == "succeeded"
    assert _sim_policy(sim, name)["loggingEnabled"] is False and len(sim.writes) == 2

    audit = (await owner.get("/api/audit", params={"action": "change."})).json()
    assert audit["chain_intact"] is True
    assert {"change.propose", "change.approve", "change.apply", "change.finish"} <= {a["action"] for a in audit["records"]}


async def test_drift_since_proposal_is_a_conflict_and_nothing_is_sent(changes, owner, demo):
    sim = changes
    await _allow_changes(owner, demo["id"])
    name = next(p["name"] for p in sim.policies if not p["loggingEnabled"])
    cr = (
        await owner.post(
            "/api/changes",
            json={"operation": "firewall_policy.set_logging", "target_id": (await _policy(owner, name))["id"], "params": {"enabled": True}},
        )
    ).json()
    await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    _sim_policy(sim, name)["loggingEnabled"] = True  # someone changed it in UniFi meanwhile
    out = (await owner.post(f"/api/changes/{cr['id']}/apply")).json()
    assert out["status"] == "conflict" and "nothing was sent" in out["evidence"][-1]["text"]
    assert sim.writes == []


async def test_write_that_does_not_take_effect_is_reverted(changes, owner, demo):
    sim = changes
    await _allow_changes(owner, demo["id"])
    sim.faults.add("patch_ignored")
    name = next(p["name"] for p in sim.policies if not p["loggingEnabled"])
    cr = (
        await owner.post(
            "/api/changes",
            json={"operation": "firewall_policy.set_logging", "target_id": (await _policy(owner, name))["id"], "params": {"enabled": True}},
        )
    ).json()
    await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    out = (await owner.post(f"/api/changes/{cr['id']}/apply")).json()
    assert out["status"] == "reverted"
    assert [w["body"] for w in sim.writes] == [{"loggingEnabled": True}, {"loggingEnabled": False}]
    assert any("Reverting under the same approval" in e["text"] for e in out["evidence"])


async def test_controller_error_fails_the_change(changes, owner, demo):
    sim = changes
    await _allow_changes(owner, demo["id"])
    sim.faults.add("writes_fail")
    name = next(p["name"] for p in sim.policies if not p["loggingEnabled"])
    cr = (
        await owner.post(
            "/api/changes",
            json={"operation": "firewall_policy.set_logging", "target_id": (await _policy(owner, name))["id"], "params": {"enabled": True}},
        )
    ).json()
    await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    out = (await owner.post(f"/api/changes/{cr['id']}/apply")).json()
    assert out["status"] == "failed" and "refused the write" in out["evidence"][-1]["text"]


async def test_restart_needs_acknowledgement_on_protected_paths_and_is_verified(changes, owner, demo, app):
    sim = changes
    await _allow_changes(owner, demo["id"])
    gw = await _device(owner, "Gateway")
    r = await owner.post("/api/changes", json={"operation": "device.restart", "target_id": gw["id"]})
    gw_cr = r.json()
    assert gw_cr["risk"] == "high" and gw_cr["protected"] is True and gw_cr["reversible"] is False
    assert any("gateway" in x.lower() for x in gw_cr["risk_reasons"])
    blocked = await owner.post(f"/api/changes/{gw_cr['id']}/approve", json={})
    assert blocked.status_code == 409 and "protected path" in blocked.json()["detail"]
    assert (await owner.post(f"/api/changes/{gw_cr['id']}/cancel", json={"note": "not today"})).json()["status"] == "cancelled"

    ap = await _device(owner, "AP Office")
    cr = (await owner.post("/api/changes", json={"operation": "device.restart", "target_id": ap["id"], "reason": "Stuck radio"})).json()
    assert cr["risk"] == "medium" and cr["protected"] is False
    await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    sim.clock = 1_792_000_000.0
    out = (await owner.post(f"/api/changes/{cr['id']}/apply")).json()
    assert out["status"] == "verifying" and sim.writes[-1]["body"] == {"action": "RESTART"}
    sim.clock += 120  # past the simulated restart time
    assert (await owner.post("/api/jobs/changes.verify/run")).json()["status"] == "succeeded"
    done = (await owner.get(f"/api/changes/{cr['id']}")).json()
    assert done["status"] == "succeeded" and done["evidence"][-2]["text"] == "Restarted and back online"


async def test_restart_that_never_comes_back_fails_without_recovery(changes, owner, demo, app):
    sim = changes
    await _allow_changes(owner, demo["id"])
    sim.faults.add("restart_never_returns")
    ap = await _device(owner, "AP Living Room")
    cr = (await owner.post("/api/changes", json={"operation": "device.restart", "target_id": ap["id"]})).json()
    await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    assert (await owner.post(f"/api/changes/{cr['id']}/apply")).json()["status"] == "verifying"
    async with app.state.warden.db.sessionmaker() as db:
        row = await db.get(ChangeRequest, cr["id"])
        row.verify_deadline = utcnow() - timedelta(seconds=1)
        await db.commit()
    await owner.post("/api/jobs/changes.verify/run")
    out = (await owner.get(f"/api/changes/{cr['id']}")).json()
    assert out["status"] == "failed" and "No automatic recovery" in out["evidence"][-1]["text"]


async def test_poe_power_cycle(changes, owner, demo):
    sim = changes
    await _allow_changes(owner, demo["id"])
    sw = await _device(owner, "Rack PoE Switch")
    detail = (await owner.get(f"/api/inventory/devices/{sw['id']}")).json()
    poe_port = next(p["idx"] for p in detail["ports"] if p["poe"] and p["poe"].get("state") == "UP")
    no_poe = next(p["idx"] for p in detail["ports"] if not p["poe"])
    bad = await owner.post("/api/changes", json={"operation": "port.power_cycle", "target_id": sw["id"], "params": {"port_idx": no_poe}})
    assert bad.status_code == 409 and "does not supply PoE" in bad.json()["detail"]
    cr = (
        await owner.post("/api/changes", json={"operation": "port.power_cycle", "target_id": sw["id"], "params": {"port_idx": poe_port}})
    ).json()
    assert cr["title"] == f"Power-cycle PoE on Rack PoE Switch port {poe_port}"
    assert any("cannot name it" in i for i in cr["impact"])
    await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    out = (await owner.post(f"/api/changes/{cr['id']}/apply")).json()
    assert out["status"] == "succeeded" and sim.writes[-1]["path"].endswith(f"/interfaces/ports/{poe_port}/actions")


async def test_second_person_rule_and_role_limits(changes, owner, demo, app):
    sim = changes
    assert (await owner.put("/api/changes/settings/current", json={"require_second_person": True})).status_code == 200
    name = next(p["name"] for p in sim.policies if not p["loggingEnabled"])
    cr = (
        await owner.post(
            "/api/changes",
            json={"operation": "firewall_policy.set_logging", "target_id": (await _policy(owner, name))["id"], "params": {"enabled": True}},
        )
    ).json()
    denied = await owner.post(f"/api/changes/{cr['id']}/approve", json={})
    assert denied.status_code == 403
    audit = (await owner.get("/api/audit", params={"action": "change.approve.denied"})).json()["records"]
    assert audit and audit[0]["outcome"] == "denied"

    await owner.post("/api/users", json={"username": "op6", "password": PASSWORD, "roles": ["operator"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as op:
        await op.post("/api/auth/login", json={"username": "op6", "password": PASSWORD})
        # Operators propose but cannot approve, apply or switch a controller to changes.
        assert (await op.post(f"/api/changes/{cr['id']}/approve", json={})).status_code == 403
        assert (await op.post(f"/api/changes/{cr['id']}/apply")).status_code == 403
        assert (await op.put(f"/api/changes/controllers/{demo['id']}/write-mode", json={"write_mode": "changes"})).status_code == 403
        assert (await op.post(f"/api/changes/{cr['id']}/cancel", json={})).status_code == 403  # not their proposal


async def test_operations_unsupported_by_the_controller_version_are_refused(changes, owner, demo):
    sim = changes
    sim.version = "10.0.162"  # before PATCH firewall policy (10.1.84)
    assert (await owner.post(f"/api/controllers/{demo['id']}/sync")).json()["status"] == "succeeded"
    pols = (await owner.get("/api/inventory/firewall")).json()["policies"]
    assert pols  # synced earlier; the controller simply no longer offers the PATCH
    r = await owner.post(
        "/api/changes", json={"operation": "firewall_policy.set_logging", "target_id": pols[0]["id"], "params": {"enabled": True}}
    )
    assert r.status_code == 409 and "needs 10.1.84" in r.json()["detail"]
