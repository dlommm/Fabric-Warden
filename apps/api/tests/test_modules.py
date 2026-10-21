import pytest

from warden.modules.catalog import CATALOG, PRESETS
from warden.modules.contracts import Maturity
from warden.modules.service import dependency_closure


def test_catalog_dependencies_resolve_and_are_acyclic():
    for mid, m in CATALOG.items():
        for dep in m.requires + m.optional:
            assert dep in CATALOG, f"{mid} depends on unknown {dep}"
        closure = dependency_closure(mid)
        assert closure[-1] == mid and closure[0] == "core"


def test_presets_reference_known_modules():
    for p in PRESETS.values():
        assert all(m in CATALOG for m in p["modules"])


def test_available_modules_only_depend_on_available():
    for m in CATALOG.values():
        if m.maturity != Maturity.PLANNED:
            assert all(CATALOG[d].maturity != Maturity.PLANNED for d in dependency_closure(m.id))


async def test_enable_requires_dependencies(owner):
    r = await owner.post("/api/modules/inventory/enable", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["missing"] == ["unifi-network"]
    r = await owner.post("/api/modules/inventory/enable", json={"with_dependencies": True})
    assert r.json()["enabled"] == ["unifi-network", "inventory"]


async def test_planned_module_cannot_be_enabled(owner):
    r = await owner.post("/api/modules/dcim/enable", json={"with_dependencies": True})
    assert r.status_code == 409
    assert "not yet implemented" in r.json()["detail"]["message"]


async def test_core_cannot_be_disabled(owner):
    assert (await owner.post("/api/modules/core/disable", json={})).status_code == 409


async def test_disable_blocks_on_dependents_unless_cascade(demo, owner):
    r = await owner.post("/api/modules/unifi-network/disable", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["dependents"] == ["inventory"]
    r = await owner.post("/api/modules/unifi-network/disable", json={"cascade": True})
    assert r.json()["disabled"] == ["inventory", "unifi-network"]


async def test_disabled_module_blocks_api_and_jobs_but_keeps_data(demo, owner):
    await owner.post("/api/modules/inventory/disable", json={})
    assert (await owner.get("/api/inventory/devices")).status_code == 409
    await owner.post("/api/modules/unifi-network/disable", json={})
    run = (await owner.post("/api/jobs/unifi-network.sync/run")).json()
    assert run["status"] == "skipped"
    await owner.post("/api/modules/inventory/enable", json={"with_dependencies": True})
    assert (await owner.get("/api/inventory/devices")).json()["total"] == 8


async def test_full_preset_reports_unavailable(owner):
    r = (await owner.post("/api/modules/presets/full/apply")).json()
    assert set(r["enabled"]) == {"unifi-network", "inventory", "client-intel", "ipam"}
    assert "dcim" in r["unavailable"] and "ai-agents" in r["unavailable"]


async def test_module_listing_shape(owner):
    data = (await owner.get("/api/modules")).json()
    core = next(m for m in data["modules"] if m["id"] == "core")
    assert core["enabled"] and core["always_enabled"]
    assert "core.housekeeping" in core["registered_jobs"]
    assert {p["id"] for p in data["presets"]} >= {"minimal", "full", "soc"}


@pytest.mark.parametrize("action", ["module.enable", "module.preset.apply"])
async def test_module_changes_are_audited(owner, action):
    await owner.post("/api/modules/presets/minimal/apply")
    recs = (await owner.get("/api/audit")).json()["records"]
    assert any(r["action"] == action for r in recs)
