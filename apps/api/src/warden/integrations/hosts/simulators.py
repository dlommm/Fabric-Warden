"""Deterministic simulators for the demo home lab's servers.

They answer the same paths and shapes as the real APIs (see
official_api_matrix.json) for the hosts the UniFi simulator reports as
clients: proxmox-01/02 (10.0.30.102/103), unraid-tower (10.0.30.101),
docker-host (10.0.30.112) and nas-backup (10.0.30.106, node_exporter). The
home-assistant client (10.0.30.111) is a VM on proxmox-02, so the demo shows
a network client resolved to the VM behind it.

The Unraid simulator returns the fields Fabric Warden's query selects; it is
not a GraphQL engine. Every simulator is served in-process and its data is
always labelled as demo.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

SIM_PVE_TOKEN = "PVEAPIToken=demo@pve!fabricwarden=00000000-0000-4000-8000-000000000000"  # noqa: S105 - simulator-only demo token
SIM_UNRAID_KEY = "demo-unraid-api-key"
GiB = 1024**3
TiB = 1024**4


def _wave(n: float, period: float = 900.0, amp: float = 1.0) -> float:
    return amp * math.sin(time.time() / period + n)


@dataclass
class HostSimState:
    """Mutable so tests can stop a VM or fail a disk and watch the findings change."""

    vms: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "vmid": 101,
                "type": "lxc",
                "node": "proxmox-01",
                "name": "pihole",
                "status": "running",
                "maxcpu": 1,
                "maxmem": 1 * GiB,
                "maxdisk": 8 * GiB,
                "onboot": 1,
                "ip": "10.0.30.53",
                "mac": "bc:24:11:00:01:01",
                "tags": "dns",
            },
            {
                "vmid": 102,
                "type": "qemu",
                "node": "proxmox-01",
                "name": "k3s-01",
                "status": "running",
                "maxcpu": 4,
                "maxmem": 8 * GiB,
                "maxdisk": 64 * GiB,
                "onboot": 1,
                "ip": "10.0.30.121",
                "mac": "bc:24:11:00:01:02",
                "tags": "k8s",
            },
            {
                "vmid": 103,
                "type": "qemu",
                "node": "proxmox-01",
                "name": "win11-lab",
                "status": "stopped",
                "maxcpu": 4,
                "maxmem": 16 * GiB,
                "maxdisk": 128 * GiB,
                "onboot": 1,
                "ip": None,
                "mac": "bc:24:11:00:01:03",
                "tags": "",
            },
            {
                "vmid": 104,
                "type": "qemu",
                "node": "proxmox-02",
                "name": "home-assistant",
                "status": "running",
                "maxcpu": 2,
                "maxmem": 4 * GiB,
                "maxdisk": 32 * GiB,
                "onboot": 1,
                "ip": "10.0.30.111",
                "mac": "02:c1:00:00:00:0b",
                "tags": "iot",
            },
            {
                "vmid": 105,
                "type": "lxc",
                "node": "proxmox-02",
                "name": "media",
                "status": "running",
                "maxcpu": 2,
                "maxmem": 2 * GiB,
                "maxdisk": 16 * GiB,
                "onboot": 1,
                "ip": "10.0.30.122",
                "mac": "bc:24:11:00:02:05",
                "tags": "",
            },
            {
                "vmid": 9000,
                "type": "qemu",
                "node": "proxmox-02",
                "name": "debian-12-template",
                "status": "stopped",
                "maxcpu": 2,
                "maxmem": 2 * GiB,
                "maxdisk": 16 * GiB,
                "onboot": 0,
                "ip": None,
                "mac": "bc:24:11:00:90:00",
                "tags": "",
                "template": 1,
            },
        ]
    )
    nodes_online: dict[str, bool] = field(default_factory=lambda: {"proxmox-01": True, "proxmox-02": True})
    unraid_disks: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "idx": 0,
                "name": "parity",
                "device": "sdb",
                "size": 12 * 1024**3,
                "status": "DISK_OK",
                "temp": 34,
                "rotational": True,
                "role": "parity",
            },
            {
                "idx": 1,
                "name": "disk1",
                "device": "sdc",
                "size": 12 * 1024**3,
                "status": "DISK_OK",
                "temp": 36,
                "rotational": True,
                "role": "data",
            },
            {
                "idx": 2,
                "name": "disk2",
                "device": "sdd",
                "size": 8 * 1024**3,
                "status": "DISK_OK",
                "temp": 52,
                "rotational": True,
                "role": "data",
            },
            {
                "idx": 3,
                "name": "disk3",
                "device": "sde",
                "size": 8 * 1024**3,
                "status": "DISK_OK",
                "temp": 38,
                "rotational": True,
                "role": "data",
            },
            {
                "idx": 30,
                "name": "cache",
                "device": "nvme0n1",
                "size": 1 * 1024**3,
                "status": "DISK_OK",
                "temp": 41,
                "rotational": False,
                "role": "cache",
            },
        ]
    )
    unraid_containers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "plex", "image": "plexinc/pms-docker:latest", "state": "RUNNING", "autoStart": True, "ports": [(32400, 32400, "TCP")]},
            {
                "name": "sonarr",
                "image": "lscr.io/linuxserver/sonarr",
                "state": "RUNNING",
                "autoStart": True,
                "ports": [(8989, 8989, "TCP")],
            },
            {
                "name": "radarr",
                "image": "lscr.io/linuxserver/radarr",
                "state": "RUNNING",
                "autoStart": True,
                "ports": [(7878, 7878, "TCP")],
            },
            {
                "name": "immich",
                "image": "ghcr.io/immich-app/immich-server",
                "state": "EXITED",
                "autoStart": True,
                "ports": [(2283, 2283, "TCP")],
            },
            {
                "name": "syncthing",
                "image": "lscr.io/linuxserver/syncthing",
                "state": "EXITED",
                "autoStart": False,
                "ports": [(8384, 8384, "TCP")],
            },
        ]
    )
    ups_status: str = "Online"
    docker_containers: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "traefik", "image": "traefik:v3", "state": "running", "restart": "unless-stopped", "ports": [(80, 80), (443, 443)]},
            {"name": "grafana", "image": "grafana/grafana:11", "state": "running", "restart": "unless-stopped", "ports": [(3000, None)]},
            {"name": "prometheus", "image": "prom/prometheus", "state": "running", "restart": "unless-stopped", "ports": [(9090, None)]},
            {"name": "uptime-kuma", "image": "louislam/uptime-kuma:1", "state": "running", "restart": "always", "ports": [(3001, 3001)]},
            {
                "name": "paperless",
                "image": "ghcr.io/paperless-ngx/paperless-ngx",
                "state": "exited",
                "restart": "unless-stopped",
                "ports": [(8000, 8010)],
            },
            {"name": "watchtower", "image": "containrrr/watchtower", "state": "exited", "restart": "no", "ports": []},
        ]
    )
    backup_fill: float = 0.88  # nas-backup /srv/backup usage
    boot_time: float = field(default_factory=lambda: time.time() - 2_592_000)
    # Each scrape advances the CPU counters by 15 virtual seconds, so utilisation is computable even between quick scrapes.
    scrapes: int = 0


# -- Proxmox VE --------------------------------------------------------------------------


def create_proxmox_app(sim: HostSimState) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    ips = {"proxmox-01": "10.0.30.102", "proxmox-02": "10.0.30.103"}

    def auth(authorization: str | None) -> None:
        if authorization != SIM_PVE_TOKEN:
            raise HTTPException(401, "authentication failure")

    def vm(vmid: int) -> dict[str, Any]:
        for v in sim.vms:
            if v["vmid"] == vmid:
                return v
        raise HTTPException(404)

    @app.get("/api2/json/version")
    async def version(authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        return {"data": {"version": "9.0.10", "release": "9.0", "repoid": "simulated"}}

    @app.get("/api2/json/cluster/status")
    async def cluster_status(authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        nodes = [
            {
                "type": "node",
                "id": f"node/{n}",
                "name": n,
                "ip": ips[n],
                "online": int(on),
                "local": int(n == "proxmox-01"),
                "nodeid": i + 1,
            }
            for i, (n, on) in enumerate(sim.nodes_online.items())
        ]
        return {"data": [{"type": "cluster", "id": "cluster", "name": "pve-lab", "nodes": 2, "quorate": 1, "version": 4}, *nodes]}

    @app.get("/api2/json/cluster/resources")
    async def resources(authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        out: list[dict[str, Any]] = []
        for i, (n, on) in enumerate(sim.nodes_online.items()):
            out.append(
                {
                    "type": "node",
                    "id": f"node/{n}",
                    "node": n,
                    "status": "online" if on else "offline",
                    "cpu": round(0.18 + 0.08 * i + 0.05 * _wave(i), 4) if on else 0,
                    "maxcpu": 16,
                    "mem": int((0.52 + 0.1 * i + 0.03 * _wave(i + 1)) * 64 * GiB) if on else 0,
                    "maxmem": 64 * GiB,
                    "uptime": 3_400_000 + i * 86_400 if on else 0,
                    "disk": 18 * GiB,
                    "maxdisk": 96 * GiB,
                }
            )
        for v in sim.vms:
            running = v["status"] == "running" and sim.nodes_online.get(v["node"], False)
            out.append(
                {
                    "type": v["type"],
                    "id": f"{v['type']}/{v['vmid']}",
                    "vmid": v["vmid"],
                    "name": v["name"],
                    "node": v["node"],
                    "status": "running" if running else "stopped",
                    "maxcpu": v["maxcpu"],
                    "maxmem": v["maxmem"],
                    "maxdisk": v["maxdisk"],
                    "cpu": round(0.05 + 0.03 * _wave(v["vmid"]) ** 2, 4) if running else 0,
                    "mem": int(v["maxmem"] * (0.45 + 0.1 * _wave(v["vmid"], 600))) if running else 0,
                    "uptime": 900_000 + v["vmid"] * 1000 if running else 0,
                    "template": v.get("template", 0),
                    "tags": v["tags"],
                }
            )
        storage = [
            ("proxmox-01", "local", 0.31, 0, "dir"),
            ("proxmox-01", "local-lvm", 0.62, 0, "lvmthin"),
            ("proxmox-02", "local", 0.28, 0, "dir"),
            ("proxmox-02", "local-lvm", 0.91, 0, "lvmthin"),
            ("proxmox-01", "nas-nfs", 0.55, 1, "nfs"),
            ("proxmox-02", "nas-nfs", 0.55, 1, "nfs"),
        ]
        for node, name, fill, shared, plugin in storage:
            size = 4 * TiB if name == "nas-nfs" else 900 * GiB if name == "local-lvm" else 96 * GiB
            out.append(
                {
                    "type": "storage",
                    "id": f"storage/{node}/{name}",
                    "storage": name,
                    "node": node,
                    "status": "available" if sim.nodes_online[node] else "unknown",
                    "disk": int(size * fill),
                    "maxdisk": size,
                    "shared": shared,
                    "plugintype": plugin,
                    "content": "images,rootdir",
                }
            )
        return {"data": out}

    @app.get("/api2/json/nodes/{node}/status")
    async def node_status(node: str, authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        if not sim.nodes_online.get(node):
            raise HTTPException(595, "no route to host")
        return {
            "data": {
                "pveversion": "pve-manager/9.0.10/simulated",
                "current-kernel": {"release": "6.14.8-2-pve"},
                "loadavg": [f"{1.2 + _wave(3):.2f}", "1.10", "1.05"],
            }
        }

    @app.get("/api2/json/nodes/{node}/{kind}/{vmid}/config")
    async def config(node: str, kind: str, vmid: int, authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        v = vm(vmid)
        net = f"virtio={v['mac'].upper()},bridge=vmbr0" if kind == "qemu" else f"name=eth0,bridge=vmbr0,hwaddr={v['mac'].upper()},ip=dhcp"
        return {"data": {"name": v["name"], "onboot": v["onboot"], "net0": net, "cores": v["maxcpu"]}}

    @app.get("/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
    async def agent(node: str, vmid: int, authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        v = vm(vmid)
        if v["status"] != "running" or not v["ip"]:
            raise HTTPException(500, "QEMU guest agent is not running")
        return {
            "data": {
                "result": [
                    {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4", "prefix": 8}]},
                    {
                        "name": "eth0",
                        "hardware-address": v["mac"],
                        "ip-addresses": [{"ip-address": v["ip"], "ip-address-type": "ipv4", "prefix": 24}],
                    },
                ]
            }
        }

    @app.get("/api2/json/nodes/{node}/lxc/{vmid}/interfaces")
    async def lxc_ifaces(node: str, vmid: int, authorization: str | None = Header(None)) -> dict:
        auth(authorization)
        v = vm(vmid)
        return {
            "data": [
                {"name": "lo", "hwaddr": "00:00:00:00:00:00", "inet": "127.0.0.1/8"},
                {"name": "eth0", "hwaddr": v["mac"], "inet": f"{v['ip']}/24"},
            ]
        }

    return app


# -- Unraid ------------------------------------------------------------------------------


def create_unraid_app(sim: HostSimState) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.post("/graphql")
    async def graphql(request: Request, x_api_key: str | None = Header(None)) -> dict:
        if x_api_key != SIM_UNRAID_KEY:
            raise HTTPException(401, "Invalid API key")
        body = await request.json()
        if not str(body.get("query", "")).lstrip().startswith(("query", "{")):
            return {"errors": [{"message": "Simulator only answers queries"}]}
        disks = [{k: d[k] for k in ("idx", "name", "device", "size", "status", "temp", "rotational")} for d in sim.unraid_disks]
        total = sum(d["size"] for d in sim.unraid_disks if d["role"] == "data")
        used = int(total * 0.71)
        return {
            "data": {
                "info": {
                    "os": {
                        "hostname": "unraid-tower",
                        "distro": "Unraid OS",
                        "release": "7.2.0",
                        "kernel": "6.12.24-Unraid",
                        "uptime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1_814_400)),
                    },
                    "cpu": {"brand": "Intel Core i5-13500", "cores": 14, "threads": 20},
                    "versions": {"core": {"unraid": "7.2.0", "api": "4.25.0"}},
                },
                "metrics": {
                    "cpu": {"percentTotal": round(22 + 6 * _wave(5), 1)},
                    "memory": {"total": 64 * GiB, "used": int((0.58 + 0.04 * _wave(6)) * 64 * GiB), "percentTotal": 58.0},
                },
                "networkInterfaces": [
                    {"name": "eth0", "macAddress": "02:c1:00:00:00:01", "ipAddress": "10.0.30.101", "virtual": False},
                    {"name": "docker0", "macAddress": "02:42:ac:11:00:01", "ipAddress": "172.17.0.1", "virtual": True},
                ],
                "array": {
                    "state": "STARTED",
                    "capacity": {"kilobytes": {"free": str(total - used), "used": str(used), "total": str(total)}},
                    "parities": [d for d, s in zip(disks, sim.unraid_disks, strict=True) if s["role"] == "parity"],
                    "disks": [d for d, s in zip(disks, sim.unraid_disks, strict=True) if s["role"] == "data"],
                    "caches": [d for d, s in zip(disks, sim.unraid_disks, strict=True) if s["role"] == "cache"],
                },
                "docker": {
                    "containers": [
                        {
                            "id": f"ct-{c['name']}",
                            "names": [f"/{c['name']}"],
                            "image": c["image"],
                            "state": c["state"],
                            "status": "Up 3 days" if c["state"] == "RUNNING" else "Exited (0) 2 hours ago",
                            "autoStart": c["autoStart"],
                            "ports": [{"privatePort": p, "publicPort": q, "type": t} for p, q, t in c["ports"]],
                        }
                        for c in sim.unraid_containers
                    ]
                },
                "vms": {
                    "domains": [
                        {"id": "vm-macos", "name": "macos-sonoma", "state": "SHUTOFF", "uuid": "7f6c2d4e-0000-4000-8000-0000000000a1"}
                    ]
                },
                "upsDevices": [
                    {
                        "id": "ups1",
                        "name": "ups",
                        "model": "Back-UPS 1500",
                        "status": sim.ups_status,
                        "battery": {
                            "chargeLevel": 100 if sim.ups_status == "Online" else 64,
                            "estimatedRuntime": 1500 if sim.ups_status == "Online" else 540,
                            "health": "Good",
                        },
                        "power": {"loadPercentage": 31},
                    }
                ],
            }
        }

    return app


# -- Docker Engine -----------------------------------------------------------------------


def create_docker_app(sim: HostSimState) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    prefix = "/v1.47"

    def find(cid: str) -> dict[str, Any]:
        for c in sim.docker_containers:
            if f"sim{c['name']}" == cid:
                return c
        raise HTTPException(404, "No such container")

    @app.get(f"{prefix}/version")
    async def version() -> dict:
        return {"Version": "28.3.3", "ApiVersion": "1.47", "Os": "linux", "Arch": "amd64", "KernelVersion": "6.8.0-60-generic"}

    @app.get(f"{prefix}/info")
    async def info() -> dict:
        running = sum(1 for c in sim.docker_containers if c["state"] == "running")
        return {
            "ID": "SIMDOCKER",
            "Name": "docker-host",
            "OperatingSystem": "Ubuntu 24.04 LTS",
            "KernelVersion": "6.8.0-60-generic",
            "NCPU": 8,
            "MemTotal": 32 * GiB,
            "Containers": len(sim.docker_containers),
            "ContainersRunning": running,
            "ServerVersion": "28.3.3",
        }

    @app.get(f"{prefix}/containers/json")
    async def containers(all: str = "false") -> list:
        out = []
        for i, c in enumerate(sim.docker_containers):
            if all != "true" and c["state"] != "running":
                continue
            out.append(
                {
                    "Id": f"sim{c['name']}",
                    "Names": [f"/{c['name']}"],
                    "Image": c["image"],
                    "State": c["state"],
                    "Status": "Up 6 days" if c["state"] == "running" else "Exited (1) 40 minutes ago",
                    "Ports": [
                        {"PrivatePort": p, **({"PublicPort": q, "IP": "0.0.0.0"} if q else {}), "Type": "tcp"} for p, q in c["ports"]
                    ],
                    "Labels": {"com.docker.compose.project": "homelab"},
                    "NetworkSettings": {
                        "Networks": {
                            "homelab_default": {
                                "IPAddress": f"172.18.0.{i + 2}" if c["state"] == "running" else "",
                                "MacAddress": f"02:42:ac:12:00:{i + 2:02x}" if c["state"] == "running" else "",
                            }
                        }
                    },
                }
            )
        return out

    @app.get(prefix + "/containers/{cid}/json")
    async def inspect(cid: str) -> dict:
        c = find(cid)
        return {"Id": cid, "Name": f"/{c['name']}", "HostConfig": {"RestartPolicy": {"Name": c["restart"], "MaximumRetryCount": 0}}}

    return app


# -- node_exporter -----------------------------------------------------------------------


def create_node_exporter_app(sim: HostSimState) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        now = time.time()
        boot = sim.boot_time
        sim.scrapes += 1
        elapsed = now - boot + 15 * sim.scrapes
        lines = [
            "# HELP node_exporter_build_info A metric with a constant '1' value labeled by version.",
            "# TYPE node_exporter_build_info gauge",
            'node_exporter_build_info{version="1.10.2"} 1',
            'node_uname_info{domainname="(none)",machine="x86_64",nodename="nas-backup",'
            'release="6.1.0-38-amd64",sysname="Linux",version="#1 SMP Debian"} 1',
            f"node_memory_MemTotal_bytes {16 * GiB}",
            f"node_memory_MemAvailable_bytes {int((0.38 + 0.03 * _wave(7)) * 16 * GiB)}",
            f"node_load1 {0.42 + 0.1 * _wave(8):.2f}",
            f"node_boot_time_seconds {boot:.0f}",
            f"node_time_seconds {now:.3f}",
            'node_network_info{address="02:c1:00:00:00:06",broadcast="ff:ff:ff:ff:ff:ff",device="eth0",duplex="full",operstate="up"} 1',
            'node_network_info{address="00:00:00:00:00:00",broadcast="00:00:00:00:00:00",device="lo",duplex="",operstate="unknown"} 1',
        ]
        busy = 0.12 + 0.04 * _wave(9)
        for cpu in range(4):
            lines.append(f'node_cpu_seconds_total{{cpu="{cpu}",mode="idle"}} {elapsed * (1 - busy):.2f}')
            lines.append(f'node_cpu_seconds_total{{cpu="{cpu}",mode="user"}} {elapsed * busy * 0.7:.2f}')
            lines.append(f'node_cpu_seconds_total{{cpu="{cpu}",mode="system"}} {elapsed * busy * 0.3:.2f}')
        for mount, dev, fs, size, fill in (
            ("/", "/dev/sda2", "ext4", 50 * GiB, 0.61),
            ("/srv/backup", "/dev/md0", "xfs", 8 * TiB, sim.backup_fill),
            ("/run", "tmpfs", "tmpfs", GiB, 0.01),
        ):
            lbl = f'device="{dev}",fstype="{fs}",mountpoint="{mount}"'
            lines.append(f"node_filesystem_size_bytes{{{lbl}}} {size}")
            lines.append(f"node_filesystem_avail_bytes{{{lbl}}} {int(size * (1 - fill))}")
        return "\n".join(lines) + "\n"

    return app
