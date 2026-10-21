"""Phase 4b: servers and virtualization (Proxmox VE, Unraid, Docker Engine, node_exporter)."""

import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import PASSWORD
from warden.integrations.hosts.clients import HostError, ReadOnlyHTTP, collect_proxmox
from warden.integrations.hosts.simulators import SIM_PVE_TOKEN, HostSimState, create_proxmox_app

MATRIX = Path(__file__).parents[1] / "src/warden/integrations/hosts/official_api_matrix.json"


@pytest.fixture
async def servers(demo, owner):
    r = await owner.post("/api/modules/servers/enable", json={"with_dependencies": True})
    assert r.status_code == 200, r.text
    r = await owner.post("/api/servers/demo")
    assert r.status_code == 201, r.text
    return {c["kind"]: c for c in r.json()["items"]}


async def _hosts(owner) -> dict[str, dict]:
    return {h["name"]: h for h in (await owner.get("/api/servers/hosts")).json()["items"]}


async def _resync(owner, conns):
    for c in conns.values():
        assert (await owner.post(f"/api/servers/connections/{c['id']}/sync")).json()["status"] in ("ok", "degraded")


async def test_demo_inventory_and_network_matching(servers, owner):
    assert all(c["status"] == "ok" and c["is_demo"] for c in servers.values())
    assert servers["proxmox"]["version"] == "9.0.10" and servers["unraid"]["version"] == "7.2.0"
    assert servers["proxmox"]["credential_fingerprint"] and "secret" not in json.dumps(servers)

    hosts = await _hosts(owner)
    assert set(hosts) == {"proxmox-01", "proxmox-02", "unraid-tower", "docker-host", "nas-backup"}
    # Every demo host is a client the UniFi simulator reports: matched by IP, MAC or hostname.
    assert {n: h["client"]["match"] for n, h in hosts.items()} == {
        "proxmox-01": "ip",
        "proxmox-02": "ip",
        "unraid-tower": "ip",
        "docker-host": "hostname",
        "nas-backup": "mac",
    }
    assert hosts["unraid-tower"]["extra"]["array_state"] == "STARTED" and hosts["unraid-tower"]["extra"]["ups"][0]["status"] == "Online"
    assert hosts["proxmox-02"]["workloads"] == {"total": 2, "running": 2}  # the template is not counted
    assert hosts["proxmox-01"]["load_1m"] is not None and hosts["proxmox-01"]["os"].startswith("Proxmox VE")

    detail = (await owner.get(f"/api/servers/hosts/{hosts['proxmox-02']['id']}")).json()
    ha = next(w for w in detail["workload_items"] if w["name"] == "home-assistant")
    assert ha["kind"] == "vm" and ha["ip_addresses"] == ["10.0.30.111"] and ha["autostart"] is True
    assert ha["client"]["name"] == "home-assistant" and ha["client"]["match"] == "ip"
    assert any(s["name"] == "local-lvm" and s["used_pct"] == 91.0 for s in detail["storage_items"])

    # The client's side: home-assistant is VM 104 on proxmox-02.
    runs = (await owner.get(f"/api/servers/clients/{ha['client']['id']}")).json()
    assert runs["workload"]["vmid"] == 104 and runs["workload"]["host"]["name"] == "proxmox-02"

    containers = (await owner.get("/api/servers/workloads", params={"kind": "container"})).json()["items"]
    assert {c["host"] for c in containers} == {"unraid-tower", "docker-host"}
    assert all(c["client"] is None for c in containers)  # bridge addresses never match LAN clients
    paperless = next(c for c in containers if c["name"] == "paperless")
    assert paperless["autostart"] is True and paperless["status"] == "stopped"
    assert (await owner.get("/api/servers/workloads", params={"q": "104"})).json()["items"][0]["name"] == "home-assistant"


async def test_findings_and_simulated_failures(servers, owner, app):
    titles = lambda items: {f["kind"]: [x["title"] for x in items if x["kind"] == f["kind"]] for f in items}  # noqa: E731
    items = (await owner.get("/api/servers/findings")).json()["items"]
    by = titles(items)
    assert sorted(t.split(" ")[1] for t in by["autostart_stopped"]) == ["immich", "paperless", "win11-lab"]
    assert by["storage_full"] == ["proxmox-02: local-lvm is 91.0% full", "nas-backup: /srv/backup is 88.0% full"]
    assert by["disk_hot"] == ["unraid-tower: disk2 is at 52 °C"]
    assert {t.split(" ")[1] for t in by["not_on_network"]} == {"pihole", "k3s-01", "media"}
    assert items[0]["severity"] == "warning"  # nothing critical in the healthy demo

    sim = app.state.warden.host_sim
    sim.nodes_online["proxmox-01"] = False
    sim.unraid_disks[3]["status"] = "DISK_DSBL"
    sim.ups_status = "On Battery"
    await _resync(owner, servers)
    items = (await owner.get("/api/servers/findings")).json()["items"]
    crit = [f["title"] for f in items if f["severity"] == "critical"]
    assert "proxmox-01 is offline" in crit and "unraid-tower: disk3 is dsbl" in crit
    assert any(f["kind"] == "ups" and "On Battery" in f["title"] for f in items)
    # Workloads on an offline node do not raise autostart findings of their own.
    assert not any("win11-lab" in f["title"] for f in items)


async def test_proxmox_collector_only_reads():
    seen: list[str] = []
    sim_app = create_proxmox_app(HostSimState())

    class Recorder(httpx.ASGITransport):
        async def handle_async_request(self, request):
            seen.append(request.method)
            return await super().handle_async_request(request)

    async with ReadOnlyHTTP("https://pve", headers={"Authorization": SIM_PVE_TOKEN}, transport=Recorder(app=sim_app)) as api:
        snap = await collect_proxmox(api)
        assert len(snap.hosts) == 2 and len(snap.workloads) == 6
        assert not hasattr(api, "post") and not hasattr(api, "put") and not hasattr(api, "delete")
        with pytest.raises(HostError) as e:
            await api.graphql_query("/graphql", "mutation { arrayStop }")
        assert e.value.kind == "refused"
    assert set(seen) == {"GET"}


async def test_bad_token_and_unreachable_host(servers, owner, app):
    sim = create_proxmox_app(HostSimState())
    async with ReadOnlyHTTP(
        "https://pve", headers={"Authorization": "PVEAPIToken=x@pve!y=wrong"}, transport=httpx.ASGITransport(app=sim)
    ) as api:
        with pytest.raises(HostError) as e:
            await collect_proxmox(api)
    assert e.value.kind == "auth"
    r = await owner.post("/api/servers/connections/test", json={"kind": "node_exporter", "base_url": "http://127.0.0.1:9"})
    body = r.json()
    assert body["ok"] is False and body["error_kind"] == "unreachable" and "9100" in body["hint"]
    r = await owner.post(
        "/api/servers/connections",
        json={"name": "x", "kind": "proxmox", "base_url": "https://127.0.0.1:9", "token_id": "nobang", "secret": "s"},
    )
    assert r.status_code == 422  # token id must be USER@REALM!TOKENID


async def test_node_exporter_cpu_needs_two_scrapes_and_metrics_recorded(servers, owner):
    await owner.post("/api/modules/analytics-studio/enable", json={"with_dependencies": True})
    hosts = await _hosts(owner)
    assert hosts["nas-backup"]["cpu_pct"] is None  # first scrape has no previous counters
    await _resync(owner, servers)
    hosts = await _hosts(owner)
    assert hosts["nas-backup"]["cpu_pct"] is not None and hosts["nas-backup"]["mem_pct"] > 0
    catalog = (await owner.get("/api/metrics")).json()["items"]
    cpu = next(m for m in catalog if m["id"] == "host.cpu_pct")
    assert {s["label"] for s in cpu["series"]} >= {"proxmox-01", "unraid-tower", "nas-backup"}


async def test_permissions_and_demo_removal(servers, owner, app):
    await owner.post("/api/users", json={"username": "op1", "password": PASSWORD, "roles": ["operator"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as c:
        await c.post("/api/auth/login", json={"username": "op1", "password": PASSWORD})
        assert (await c.get("/api/servers/hosts")).status_code == 200
        assert (await c.post("/api/servers/connections/test", json={"kind": "docker", "base_url": "http://x:2375"})).status_code == 403
        host = (await c.get("/api/servers/hosts")).json()["items"][0]
        assert (await c.patch(f"/api/servers/hosts/{host['id']}", json={"labels": ["rack-a"]})).status_code == 200
    assert (await owner.post("/api/servers/demo")).status_code == 409
    assert (await owner.delete("/api/servers/demo")).json()["removed"] == 4
    assert (await owner.get("/api/servers/hosts")).json()["items"] == []
    actions = {r["action"] for r in (await owner.get("/api/audit")).json()["records"]}
    assert {"servers.demo.create", "servers.demo.remove", "servers.host.update"} <= actions


def test_matrix_lists_every_endpoint_the_collectors_call():
    matrix = json.loads(MATRIX.read_text())["integrations"]
    src = (Path(__file__).parents[1] / "src/warden/integrations/hosts/clients.py").read_text()
    pve = {e["path"] for e in matrix["proxmox"]["endpoints"]}
    for path in (
        "/version",
        "/cluster/status",
        "/cluster/resources",
        "/nodes/{node}/status",
        "/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
        "/nodes/{node}/lxc/{vmid}/interfaces",
    ):
        assert path in pve
    assert "/config" in src and {"/nodes/{node}/qemu/{vmid}/config", "/nodes/{node}/lxc/{vmid}/config"} <= pve
    docker = {e["path"].split("?")[0] for e in matrix["docker"]["endpoints"]}
    assert docker == {"/version", "/info", "/containers/json", "/containers/{id}/json"}
    assert all(e["method"] == "GET" for k in ("proxmox", "docker", "node_exporter") for e in matrix[k]["endpoints"])
    assert matrix["unraid"]["endpoints"][0]["operation"] == "query"
