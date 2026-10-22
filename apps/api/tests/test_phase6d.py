"""Phase 6d: bulk operations, Automation Studio and the impact preview, against the simulated controller only."""

from datetime import timedelta

import pytest

from warden.db import utcnow
from warden.events import ALERT_FIRING, Event
from warden.models import ChangeRequest


@pytest.fixture
async def sim(demo, owner, app):
    for module in ("bulk-ops", "automation", "digital-twin"):
        r = await owner.post(f"/api/modules/{module}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    return app.state.warden.simulator


async def _device(owner, name: str) -> dict:
    return next(d for d in (await owner.get("/api/inventory/devices")).json()["items"] if d["name"] == name)


async def _allow(owner, controller_id: str) -> None:
    assert (await owner.put(f"/api/changes/controllers/{controller_id}/write-mode", json={"write_mode": "changes"})).status_code == 200


async def _poe_ports(owner, sw: dict) -> tuple[list[int], int]:
    detail = (await owner.get(f"/api/inventory/devices/{sw['id']}")).json()
    poe = [p["idx"] for p in detail["ports"] if p["poe"] and p["poe"].get("state") == "UP"]
    return poe, next(p["idx"] for p in detail["ports"] if not p["poe"])


async def test_bulk_poe_cycle_decided_once_applied_item_by_item(sim, owner, demo):
    sw = await _device(owner, "Rack PoE Switch")
    poe, no_poe = await _poe_ports(owner, sw)
    assert len(poe) >= 2
    cands = (await owner.get("/api/changes/candidates", params={"operation": "port.power_cycle"})).json()["items"]
    assert {c["params"]["port_idx"] for c in cands if c["target_id"] == sw["id"]} >= set(poe[:2])

    targets = [{"target_id": sw["id"], "params": {"port_idx": i}} for i in (*poe[:2], no_poe)]
    r = await owner.post("/api/batches", json={"operation": "port.power_cycle", "targets": targets, "reason": "Cameras hung"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["ref"] == "BAT-0001" and b["status"] == "proposed" and b["risk"] == "medium" and b["protected"] is False
    assert len(b["items"]) == 2 and len(b["skipped"]) == 1 and "does not supply PoE" in b["skipped"][0]["reason"]
    assert all(i["batch_id"] == b["id"] and i["status"] == "proposed" for i in b["items"])
    assert b["title"].startswith("Power-cycle PoE on 2 ports")
    # The impact preview names what may be behind the switch, and says it cannot know which port powers what.
    imp = (await owner.get(f"/api/twin/batches/{b['id']}/impact")).json()
    assert any("does not report which device" in n for n in imp["notes"])
    assert {d["name"]: d["relation"] for d in imp["devices"]}.get("AP Living Room") == "possibly"

    # Items are decided with their batch, never one by one.
    single = await owner.post(f"/api/changes/{b['items'][0]['id']}/approve", json={})
    assert single.status_code == 409 and "BAT-0001" in single.json()["detail"]
    detail = (await owner.get(f"/api/changes/{b['items'][0]['id']}")).json()
    assert detail["batch"]["ref"] == "BAT-0001"

    b = (await owner.post(f"/api/batches/{b['id']}/approve", json={"note": "ok"})).json()
    assert b["status"] == "approved" and b["counts"] == {"approved": 2}
    # Read-only controller: nothing starts and the batch stays approved.
    refused = await owner.post(f"/api/batches/{b['id']}/apply")
    assert refused.status_code == 409 and "turned off" in refused.json()["detail"] and sim.writes == []

    await _allow(owner, demo["id"])
    b = (await owner.post(f"/api/batches/{b['id']}/apply")).json()
    assert b["status"] == "succeeded", b["log"]
    assert b["counts"] == {"succeeded": 2}
    assert [w["path"].rsplit("/", 2)[-2] for w in sim.writes] == [str(i) for i in poe[:2]]
    # Each item has its own evidence: re-read, write, verify.
    for item in b["items"]:
        kinds = [e["kind"] for e in item["evidence"]]
        assert "batch" in kinds and "write" in kinds and kinds[-1] == "succeeded"

    audit = (await owner.get("/api/audit", params={"action": "change.batch"})).json()
    assert {"change.batch.propose", "change.batch.approve", "change.batch.apply", "change.batch.finish"} <= {
        a["action"] for a in audit["records"]
    }


async def test_bulk_stops_at_first_failure_and_cancels_the_rest(sim, owner, demo):
    await _allow(owner, demo["id"])
    quiet = [p for p in (await owner.get("/api/inventory/firewall")).json()["policies"] if not p["logging_enabled"]]
    cands = (await owner.get("/api/changes/candidates", params={"operation": "firewall_policy.set_logging"})).json()["items"]
    ids = {c["target_id"] for c in cands}
    targets = [{"target_id": p["id"]} for p in quiet if p["id"] in ids][:3]
    assert len(targets) >= 2
    b = (
        await owner.post("/api/batches", json={"operation": "firewall_policy.set_logging", "targets": targets, "params": {"enabled": True}})
    ).json()
    assert b["risk"] == "low" and len(b["items"]) == len(targets)
    await owner.post(f"/api/batches/{b['id']}/approve", json={})
    sim.faults.add("writes_fail")
    b = (await owner.post(f"/api/batches/{b['id']}/apply")).json()
    assert b["status"] == "stopped"
    assert b["counts"] == {"failed": 1, "cancelled": len(targets) - 1}
    assert "stops at the first item" in b["log"][-1]["text"]
    assert sim.writes == []


async def test_bulk_restart_waits_for_verification_and_pauses(sim, owner, demo, app):
    await _allow(owner, demo["id"])
    aps = [await _device(owner, n) for n in ("AP Office", "AP Living Room")]
    b = (
        await owner.post(
            "/api/batches", json={"operation": "device.restart", "targets": [{"target_id": a["id"]} for a in aps], "pause_seconds": 60}
        )
    ).json()
    await owner.post(f"/api/batches/{b['id']}/approve", json={})
    sim.clock = 1_792_000_000.0
    b = (await owner.post(f"/api/batches/{b['id']}/apply")).json()
    assert b["status"] == "running" and [i["status"] for i in b["items"]] == ["verifying", "approved"]
    sim.clock += 120
    await owner.post("/api/jobs/changes.verify/run")
    await owner.post("/api/jobs/bulk-ops.advance/run")
    b = (await owner.get(f"/api/batches/{b['id']}")).json()
    # First item verified; the second waits for the pause.
    assert [i["status"] for i in b["items"]] == ["succeeded", "approved"] and b["next_at"] is not None
    async with app.state.warden.db.sessionmaker() as db:
        row = await db.get(ChangeRequest, b["items"][0]["id"])
        row.finished_at = utcnow() - timedelta(seconds=61)
        await db.commit()
    await owner.post("/api/jobs/bulk-ops.advance/run")
    b = (await owner.get(f"/api/batches/{b['id']}")).json()
    assert [i["status"] for i in b["items"]] == ["succeeded", "verifying"]
    sim.clock += 120
    await owner.post("/api/jobs/changes.verify/run")
    await owner.post("/api/jobs/bulk-ops.advance/run")
    b = (await owner.get(f"/api/batches/{b['id']}")).json()
    assert b["status"] == "succeeded" and len(sim.writes) == 2


async def test_bulk_refuses_one_off_operations_and_cancel(sim, owner, demo):
    r = await owner.post("/api/batches", json={"operation": "network.rename", "targets": [{"target_id": "x"}]})
    assert r.status_code == 409 and "not offered in bulk" in r.json()["detail"]
    ap = await _device(owner, "AP Office")
    b = (await owner.post("/api/batches", json={"operation": "device.restart", "targets": [{"target_id": ap["id"]}]})).json()
    b = (await owner.post(f"/api/batches/{b['id']}/cancel", json={"note": "later"})).json()
    assert b["status"] == "cancelled" and b["items"][0]["status"] == "cancelled"


async def _publish(app, **kw) -> None:
    state = app.state.warden
    async with state.db.sessionmaker() as db:
        await state.bus.publish(db, Event(source="monitoring", **kw))
        await db.commit()


RULE = {
    "name": "Restart APs with a firing alert",
    "trigger_kind": "event",
    "event_type": ALERT_FIRING,
    "conditions": [{"field": "severity", "op": "gte", "value": "warning"}, {"field": "entity_type", "op": "eq", "value": "device"}],
    "action": {"kind": "propose", "operation": "device.restart", "target": "event", "reason": "Radio stuck"},
    "cooldown_minutes": 60,
}


async def test_automation_rule_only_proposes_and_records_runs(sim, owner, demo, app):
    ap = await _device(owner, "AP Office")
    await _publish(app, type=ALERT_FIRING, entity_type="device", entity_id=ap["id"], severity="warning", payload={"summary": "CPU high"})
    await _publish(app, type=ALERT_FIRING, entity_type="device", entity_id=ap["id"], severity="info", payload={"summary": "minor"})

    # Dry run of a draft: nothing stored, proposed or sent.
    dry = (await owner.post("/api/automation/dry-run", json=RULE)).json()
    assert dry["evaluated"] == 2 and dry["matched"] == 1
    hit = next(i for i in dry["items"] if i["matched"])
    assert hit["outcome"] == "would_propose" and "Restart AP Office" in hit["detail"]
    assert next(i for i in dry["items"] if not i["matched"])["outcome"] == "no_match"
    assert (await owner.get("/api/changes")).json()["items"] == []

    rule = (await owner.post("/api/automation/rules", json=RULE)).json()
    assert rule["enabled"] is False
    assert (await owner.put(f"/api/automation/rules/{rule['id']}/enabled", json={"enabled": True})).json()["enabled"] is True
    # Enabling never replays old events.
    await owner.post("/api/jobs/automation.evaluate/run")
    assert (await owner.get("/api/automation/runs")).json()["items"] == []

    await _publish(
        app, type=ALERT_FIRING, entity_type="device", entity_id=ap["id"], severity="critical", payload={"summary": "Clients drop"}
    )
    await owner.post("/api/jobs/automation.evaluate/run")
    runs = (await owner.get("/api/automation/runs")).json()["items"]
    assert len(runs) == 1 and runs[0]["outcome"] == "proposed" and runs[0]["change_ref"] == "CHG-0001"
    cr = (await owner.get(f"/api/changes/{runs[0]['change_id']}")).json()
    assert cr["status"] == "proposed" and cr["proposed_by"].startswith("Automation: ") and "Radio stuck" in cr["reason"]
    assert sim.writes == []

    # Again within the cooldown: skipped, nothing new proposed.
    await _publish(app, type=ALERT_FIRING, entity_type="device", entity_id=ap["id"], severity="critical", payload={"summary": "again"})
    await owner.post("/api/jobs/automation.evaluate/run")
    runs = (await owner.get(f"/api/automation/rules/{rule['id']}")).json()["runs"]
    assert [r["outcome"] for r in runs] == ["skipped", "proposed"]
    assert len((await owner.get("/api/changes")).json()["items"]) == 1

    # A person approves it like any other change; a rule's proposal is never self-approved.
    assert (await owner.post(f"/api/changes/{cr['id']}/approve", json={})).json()["status"] == "approved"

    audit = {a["action"] for a in (await owner.get("/api/audit", params={"action": "automation."})).json()["records"]}
    assert {"automation.rule.create", "automation.rule.enable", "automation.fire"} <= audit


async def test_automation_validation_and_notify_without_module(sim, owner, demo, app):
    bad = await owner.post("/api/automation/rules", json={**RULE, "trigger_kind": "schedule", "interval_minutes": 5, "conditions": []})
    assert bad.status_code == 422 and "15 minutes" in bad.json()["detail"]
    loop = await owner.post("/api/automation/rules", json={**RULE, "event_type": "change.proposed"})
    assert loop.status_code == 422
    note = {
        **RULE,
        "name": "Tell someone",
        "conditions": [],
        "action": {"kind": "notify", "channel_ids": [], "severity": "warning", "title": "Alert"},
    }
    rule = (await owner.post("/api/automation/rules", json=note)).json()
    await owner.put(f"/api/automation/rules/{rule['id']}/enabled", json={"enabled": True})
    await owner.post("/api/modules/notifications/disable", json={})
    await _publish(app, type=ALERT_FIRING, entity_type="site", entity_id="s", severity="warning", payload={"summary": "x"})
    await owner.post("/api/jobs/automation.evaluate/run")
    runs = (await owner.get("/api/automation/runs")).json()["items"]
    assert runs[0]["outcome"] == "skipped" and "Notifications module is off" in runs[0]["detail"]


async def test_impact_preview_from_the_inventory_graph(sim, owner, demo):
    sw = await _device(owner, "Rack PoE Switch")
    cr = (await owner.post("/api/changes", json={"operation": "device.restart", "target_id": sw["id"]})).json()
    imp = (await owner.get(f"/api/twin/changes/{cr['id']}/impact")).json()
    names = {d["name"]: d["relation"] for d in imp["devices"]}
    assert names["Rack PoE Switch"] == "target" and names["AP Living Room"] == "downstream"
    assert imp["traffic_change"] is True and "Computed from the inventory" in imp["basis"]["text"]

    gw = await _device(owner, "Gateway")
    gcr = (await owner.post("/api/changes", json={"operation": "device.restart", "target_id": gw["id"]})).json()
    gimp = (await owner.get(f"/api/twin/changes/{gcr['id']}/impact")).json()
    assert len(gimp["devices"]) >= 5 and gimp["networks"] and "gateway" in gimp["summary"][0]

    cands = (await owner.get("/api/changes/candidates", params={"operation": "firewall_policy.set_logging"})).json()["items"]
    pol = next(c for c in cands if "logging off" in c["detail"])
    pcr = (
        await owner.post(
            "/api/changes", json={"operation": "firewall_policy.set_logging", "target_id": pol["target_id"], "params": {"enabled": True}}
        )
    ).json()
    pimp = (await owner.get(f"/api/twin/changes/{pcr['id']}/impact")).json()
    assert pimp["traffic_change"] is False and pimp["policies"][0]["relation"] == "target"
