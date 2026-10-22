"""Read-only clients and collectors for Proxmox VE, Unraid, Docker Engine and node_exporter.

Read-only by construction: the HTTP wrapper only issues GET requests, plus
POST for GraphQL documents that are queries (Unraid). Mutations and
subscriptions are refused before anything is sent. There is no generic
request method. Every endpoint used is listed with its published source in
official_api_matrix.json next to this file.
"""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from warden.security.outbound import guarded_transport

DOCKER_API = "v1.47"


class HostError(Exception):
    def __init__(self, message: str, *, kind: str = "error", status: int | None = None):
        super().__init__(message)
        self.kind = kind  # auth | unreachable | tls | not_found | protocol | refused | error
        self.status = status


class ReadOnlyHTTP:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        verify_tls: bool = True,
        ca_bundle_pem: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        verify: ssl.SSLContext | bool = verify_tls
        if verify_tls and ca_bundle_pem:
            verify = ssl.create_default_context(cadata=ca_bundle_pem)
        if transport is None and base_url.startswith("unix://"):
            # Docker socket: httpx talks HTTP over the Unix socket; the host part is ignored.
            transport = httpx.AsyncHTTPTransport(uds=base_url.removeprefix("unix://"))
            base_url = "http://docker"
        elif transport is None:
            # Real hosts and integrations: refuse link-local / metadata destinations at connect time.
            transport = guarded_transport(verify)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers or {}, verify=verify, transport=transport, timeout=timeout
        )

    async def __aenter__(self) -> ReadOnlyHTTP:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.ConnectError as e:
            if "CERTIFICATE" in str(e).upper() or "SSL" in str(e).upper():
                raise HostError(f"TLS verification failed: {e}", kind="tls") from e
            raise HostError(f"Cannot connect: {e}", kind="unreachable") from e
        except httpx.TimeoutException as e:
            raise HostError("The host did not answer in time", kind="unreachable") from e
        except httpx.HTTPError as e:
            raise HostError(str(e), kind="error") from e
        if r.status_code in (401, 403):
            raise HostError("Authentication was refused; check the token and its permissions", kind="auth", status=r.status_code)
        if r.status_code == 404:
            raise HostError(f"{path} is not available on this host", kind="not_found", status=404)
        if r.status_code >= 400:
            raise HostError(f"{path} returned HTTP {r.status_code}", kind="protocol", status=r.status_code)
        return r

    async def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return await self._send("GET", path, params=params)

    async def graphql_query(self, path: str, document: str) -> dict[str, Any]:
        """POST a GraphQL *query*. Anything that is not a query is refused locally."""
        head = re.sub(r"#.*", "", document).strip()
        if not (head.startswith("{") or re.match(r"query\b", head)):
            raise HostError("Only GraphQL queries are allowed; mutations and subscriptions are refused", kind="refused")
        r = await self._send("POST", path, json={"query": document})
        body = r.json()
        if body.get("errors") and not body.get("data"):
            raise HostError("; ".join(e.get("message", "error") for e in body["errors"])[:300], kind="protocol")
        return body.get("data") or {}


# -- normalised snapshot ---------------------------------------------------------------


@dataclass
class HostData:
    external_id: str
    name: str
    kind: str
    status: str = "online"
    hostname: str = ""
    os: str = ""
    version: str = ""
    ip_address: str | None = None
    mac_addresses: list[str] = field(default_factory=list)
    cpu_count: int | None = None
    cpu_pct: float | None = None
    mem_total: int | None = None
    mem_used: int | None = None
    load_1m: float | None = None
    uptime_s: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkloadData:
    host: str  # HostData.external_id
    external_id: str
    kind: str
    name: str
    status: str
    raw_status: str = ""
    vmid: int | None = None
    cpu_pct: float | None = None
    cpu_count: float | None = None
    mem_used: int | None = None
    mem_max: int | None = None
    disk_max: int | None = None
    uptime_s: int | None = None
    ip_addresses: list[str] = field(default_factory=list)
    mac_addresses: list[str] = field(default_factory=list)
    image: str = ""
    ports: list[dict[str, Any]] = field(default_factory=list)
    autostart: bool | None = None
    template: bool = False
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StorageData:
    host: str
    external_id: str
    name: str
    kind: str
    total_bytes: int | None = None
    used_bytes: int | None = None
    status: str = ""
    healthy: bool | None = None
    shared: bool = False
    temperature_c: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    version: str
    hosts: list[HostData] = field(default_factory=list)
    workloads: list[WorkloadData] = field(default_factory=list)
    storage: list[StorageData] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _host_of(url: str) -> str | None:
    m = re.match(r"^[a-z]+://\[?([^\]/:]+)", url)
    return m.group(1) if m else None


def _is_ip(v: str | None) -> bool:
    return bool(v) and bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", v or ""))


def _mac(v: str | None) -> str | None:
    if not v:
        return None
    v = v.lower().replace("-", ":")
    return v if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", v) and v != "00:00:00:00:00:00" else None


# -- Proxmox VE --------------------------------------------------------------------------


async def collect_proxmox(api: ReadOnlyHTTP) -> Snapshot:
    data = lambda r: r.json().get("data")  # noqa: E731 - every PVE response wraps its payload in "data"
    version = data(await api.get("/api2/json/version")) or {}
    snap = Snapshot(version=str(version.get("version", "")))
    cluster = data(await api.get("/api2/json/cluster/status")) or []
    node_ips = {c["name"]: c.get("ip") for c in cluster if c.get("type") == "node"}
    cluster_name = next((c.get("name") for c in cluster if c.get("type") == "cluster"), None)
    resources = data(await api.get("/api2/json/cluster/resources")) or []
    for r in resources:
        if r.get("type") != "node":
            continue
        name = r["node"]
        online = r.get("status") == "online"
        h = HostData(
            external_id=f"node/{name}",
            name=name,
            kind="proxmox-node",
            status="online" if online else "offline",
            hostname=name,
            ip_address=node_ips.get(name),
            cpu_count=r.get("maxcpu"),
            cpu_pct=round(100 * r["cpu"], 1) if online and r.get("cpu") is not None else None,
            mem_total=r.get("maxmem"),
            mem_used=r.get("mem") if online else None,
            uptime_s=r.get("uptime") if online else None,
            extra={"cluster": cluster_name},
            raw=r,
        )
        if online:
            try:
                st = data(await api.get(f"/api2/json/nodes/{name}/status")) or {}
                h.version = str(st.get("pveversion", ""))
                h.os = f"Proxmox VE · kernel {(st.get('current-kernel') or {}).get('release', '')}".strip(" ·")
                load = st.get("loadavg") or []
                h.load_1m = float(load[0]) if load else None
            except HostError as e:
                snap.notes.append(f"{name}: node status unavailable ({e.kind})")
        snap.hosts.append(h)
    for r in resources:
        if r.get("type") not in ("qemu", "lxc"):
            continue
        node, vmid, kind = r["node"], r["vmid"], "vm" if r["type"] == "qemu" else "lxc"
        running = r.get("status") == "running"
        w = WorkloadData(
            host=f"node/{node}",
            external_id=f"{r['type']}/{vmid}",
            kind=kind,
            name=r.get("name") or f"{kind}-{vmid}",
            status="running" if running else "stopped" if r.get("status") == "stopped" else "other",
            raw_status=r.get("status", ""),
            vmid=vmid,
            cpu_pct=round(100 * r["cpu"], 1) if running and r.get("cpu") is not None else None,
            cpu_count=r.get("maxcpu"),
            mem_used=r.get("mem") if running else None,
            mem_max=r.get("maxmem"),
            disk_max=r.get("maxdisk"),
            uptime_s=r.get("uptime") if running else None,
            template=bool(r.get("template")),
            tags=[t for t in re.split(r"[;, ]", r.get("tags") or "") if t],
            raw=r,
        )
        try:
            cfg = data(await api.get(f"/api2/json/nodes/{node}/{r['type']}/{vmid}/config")) or {}
            w.autostart = bool(int(cfg.get("onboot", 0)))
            for k, v in cfg.items():
                if re.fullmatch(r"net\d+", k) and isinstance(v, str):
                    m = re.search(r"(?:virtio|e1000|vmxnet3|rtl8139|hwaddr)=([0-9A-Fa-f:]{17})", v)
                    if m and (mac := _mac(m.group(1))):
                        w.mac_addresses.append(mac)
        except HostError:
            pass
        if running and not w.template:
            try:
                if kind == "vm":
                    res = data(await api.get(f"/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")) or {}
                    for iface in res.get("result", []):
                        if iface.get("name") == "lo":
                            continue
                        for a in iface.get("ip-addresses", []):
                            if a.get("ip-address-type") == "ipv4" and not a["ip-address"].startswith("127."):
                                w.ip_addresses.append(a["ip-address"])
                else:
                    for iface in data(await api.get(f"/api2/json/nodes/{node}/lxc/{vmid}/interfaces")) or []:
                        if iface.get("name") != "lo" and iface.get("inet"):
                            w.ip_addresses.append(iface["inet"].split("/")[0])
            except HostError:
                # No guest agent or no permission (VM.GuestAgent.Audit): addresses stay unknown.
                pass
        snap.workloads.append(w)
    for r in resources:
        if r.get("type") != "storage":
            continue
        snap.storage.append(
            StorageData(
                host=f"node/{r['node']}",
                external_id=f"storage/{r['node']}/{r['storage']}",
                name=r["storage"],
                kind="datastore",
                total_bytes=r.get("maxdisk"),
                used_bytes=r.get("disk"),
                status=r.get("status", ""),
                healthy=r.get("status") == "available",
                shared=bool(r.get("shared")),
                extra={"plugin": r.get("plugintype"), "content": r.get("content")},
            )
        )
    return snap


# -- Unraid ------------------------------------------------------------------------------

UNRAID_QUERY = """query FabricWarden {
  info {
    os { hostname distro release kernel uptime }
    cpu { brand cores threads }
    versions { core { unraid api } }
  }
  metrics { cpu { percentTotal } memory { total used percentTotal } }
  networkInterfaces { name macAddress ipAddress virtual }
  array {
    state
    capacity { kilobytes { free used total } }
    parities { idx name device size status temp rotational }
    disks { idx name device size status temp rotational }
    caches { idx name device size status temp rotational }
  }
  docker { containers { id names image state status autoStart ports { privatePort publicPort type } } }
  vms { domains { id name state uuid } }
  upsDevices { id name model status battery { chargeLevel estimatedRuntime health } power { loadPercentage } }
}"""

_UNRAID_CONTAINER = {"RUNNING": "running", "EXITED": "stopped", "PAUSED": "paused"}
_UNRAID_VM = {"RUNNING": "running", "SHUTOFF": "stopped", "SHUTDOWN": "stopped", "PAUSED": "paused", "PMSUSPENDED": "paused"}


def _uptime_from(value: str | None) -> int | None:
    """Unraid reports uptime as the boot timestamp (a string)."""
    if not value:
        return None
    try:
        boot = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0, int((datetime.now(UTC) - boot).total_seconds()))
    except ValueError:
        return int(value) if value.isdigit() else None


async def collect_unraid(api: ReadOnlyHTTP, base_url: str) -> Snapshot:
    d = await api.graphql_query("/graphql", UNRAID_QUERY)
    info = d.get("info") or {}
    os_ = info.get("os") or {}
    core = (info.get("versions") or {}).get("core") or {}
    snap = Snapshot(version=str(core.get("unraid") or ""))
    name = os_.get("hostname") or _host_of(base_url) or "unraid"
    hid = f"server/{name}"
    ifaces = [i for i in d.get("networkInterfaces") or [] if not i.get("virtual")]
    mem = (d.get("metrics") or {}).get("memory") or {}
    array = d.get("array") or {}
    ups = d.get("upsDevices") or []
    ip = next((i.get("ipAddress") for i in ifaces if i.get("ipAddress")), None) or (
        _host_of(base_url) if _is_ip(_host_of(base_url)) else None
    )
    snap.hosts.append(
        HostData(
            external_id=hid,
            name=name,
            kind="unraid",
            hostname=name,
            os=f"Unraid {core.get('unraid') or ''}".strip(),
            version=str(core.get("unraid") or ""),
            ip_address=ip,
            mac_addresses=[m for i in ifaces if (m := _mac(i.get("macAddress")))],
            cpu_count=(info.get("cpu") or {}).get("threads"),
            cpu_pct=((d.get("metrics") or {}).get("cpu") or {}).get("percentTotal"),
            mem_total=int(mem["total"]) if mem.get("total") is not None else None,
            mem_used=int(mem["used"]) if mem.get("used") is not None else None,
            uptime_s=_uptime_from(os_.get("uptime")),
            extra={
                "array_state": array.get("state"),
                "api_version": core.get("api"),
                "ups": [
                    {
                        "name": u.get("name"),
                        "model": u.get("model"),
                        "status": u.get("status"),
                        "charge_pct": (u.get("battery") or {}).get("chargeLevel"),
                        "runtime_s": (u.get("battery") or {}).get("estimatedRuntime"),
                        "battery_health": (u.get("battery") or {}).get("health"),
                        "load_pct": (u.get("power") or {}).get("loadPercentage"),
                    }
                    for u in ups
                ],
            },
            raw={"info": info},
        )
    )
    cap = (array.get("capacity") or {}).get("kilobytes") or {}
    if cap.get("total"):
        snap.storage.append(
            StorageData(
                host=hid,
                external_id="array",
                name="Array",
                kind="array",
                total_bytes=int(cap["total"]) * 1024,
                used_bytes=int(cap.get("used") or 0) * 1024,
                status=str(array.get("state") or ""),
                healthy=array.get("state") == "STARTED",
            )
        )
    for role, disks in (("parity", array.get("parities")), ("data", array.get("disks")), ("cache", array.get("caches"))):
        for disk in disks or []:
            if disk.get("status") == "DISK_NP":
                continue  # empty slot
            snap.storage.append(
                StorageData(
                    host=hid,
                    external_id=f"disk/{disk.get('idx')}",
                    name=disk.get("name") or f"slot {disk.get('idx')}",
                    kind="disk",
                    total_bytes=int(disk["size"]) * 1024 if disk.get("size") else None,
                    status=str(disk.get("status") or ""),
                    healthy=disk.get("status") == "DISK_OK",
                    temperature_c=disk.get("temp"),
                    extra={"role": role, "device": disk.get("device"), "rotational": disk.get("rotational")},
                )
            )
    for c in (d.get("docker") or {}).get("containers") or []:
        cname = (c.get("names") or [c.get("id")])[0].lstrip("/")
        snap.workloads.append(
            WorkloadData(
                host=hid,
                external_id=f"container/{cname}",
                kind="container",
                name=cname,
                status=_UNRAID_CONTAINER.get(c.get("state", ""), "other"),
                raw_status=c.get("status") or c.get("state", ""),
                image=c.get("image") or "",
                ports=[
                    {"private": p.get("privatePort"), "public": p.get("publicPort"), "type": p.get("type")} for p in c.get("ports") or []
                ],
                autostart=c.get("autoStart"),
                raw=c,
            )
        )
    for v in (d.get("vms") or {}).get("domains") or []:
        snap.workloads.append(
            WorkloadData(
                host=hid,
                external_id=f"vm/{v.get('uuid') or v.get('id')}",
                kind="vm",
                name=v.get("name") or "vm",
                status=_UNRAID_VM.get(v.get("state", ""), "other"),
                raw_status=v.get("state", ""),
                raw=v,
            )
        )
    return snap


# -- Docker Engine -----------------------------------------------------------------------

_RESTARTS = {"always", "unless-stopped"}


async def collect_docker(api: ReadOnlyHTTP, base_url: str) -> Snapshot:
    ver = (await api.get(f"/{DOCKER_API}/version")).json()
    info = (await api.get(f"/{DOCKER_API}/info")).json()
    snap = Snapshot(version=str(ver.get("Version", "")))
    name = info.get("Name") or _host_of(base_url) or "docker"
    hid = f"engine/{info.get('ID') or name}"
    host_ip = _host_of(base_url)
    snap.hosts.append(
        HostData(
            external_id=hid,
            name=name,
            kind="docker",
            hostname=name,
            os=f"{info.get('OperatingSystem', '')} · kernel {info.get('KernelVersion', '')}".strip(" ·"),
            version=f"Docker {ver.get('Version', '')} (API {ver.get('ApiVersion', '')})",
            ip_address=host_ip if _is_ip(host_ip) else None,
            cpu_count=info.get("NCPU"),
            mem_total=info.get("MemTotal"),
            extra={"containers": info.get("Containers"), "running": info.get("ContainersRunning")},
            raw={k: info.get(k) for k in ("Name", "ID", "OperatingSystem", "KernelVersion", "NCPU", "MemTotal", "ServerVersion")},
        )
    )
    for c in (await api.get(f"/{DOCKER_API}/containers/json", params={"all": "true"})).json()[:200]:
        cname = (c.get("Names") or [c["Id"][:12]])[0].lstrip("/")
        nets = ((c.get("NetworkSettings") or {}).get("Networks") or {}).values()
        restart = None
        try:
            detail = (await api.get(f"/{DOCKER_API}/containers/{c['Id']}/json")).json()
            restart = ((detail.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
        except HostError:
            pass
        state = c.get("State", "")
        snap.workloads.append(
            WorkloadData(
                host=hid,
                external_id=f"container/{cname}",
                kind="container",
                name=cname,
                status="running"
                if state == "running"
                else "stopped"
                if state in ("exited", "created", "dead")
                else "paused"
                if state == "paused"
                else "other",
                raw_status=c.get("Status") or state,
                image=c.get("Image", ""),
                ports=[
                    {"private": p.get("PrivatePort"), "public": p.get("PublicPort"), "type": p.get("Type")} for p in c.get("Ports") or []
                ],
                # Bridge addresses are private to the host; only macvlan/ipvlan addresses can match a LAN client.
                ip_addresses=[n["IPAddress"] for n in nets if n.get("IPAddress")],
                mac_addresses=[m for n in nets if (m := _mac(n.get("MacAddress")))],
                autostart=(restart in _RESTARTS) if restart is not None else None,
                raw={"Id": c["Id"], "Labels": c.get("Labels"), "RestartPolicy": restart},
            )
        )
    return snap


# -- node_exporter -----------------------------------------------------------------------

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{([^}]*)\})?\s+([^\s]+)")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
SKIP_FS = {"tmpfs", "devtmpfs", "overlay", "squashfs", "ramfs", "nsfs", "proc", "sysfs", "autofs", "fuse.lxcfs"}


def parse_exposition(text: str) -> list[tuple[str, dict[str, str], float]]:
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        try:
            value = float(m.group(4))
        except ValueError:
            continue
        out.append((m.group(1), dict(_LABEL.findall(m.group(3) or "")), value))
    return out


async def collect_node_exporter(api: ReadOnlyHTTP, base_url: str, previous: dict[str, Any] | None = None) -> Snapshot:
    """CPU utilisation needs two scrapes: the idle/total counters of the last one are carried in `previous`."""
    samples = parse_exposition((await api.get("/metrics")).text)
    first = lambda name: next(((lbl, v) for n, lbl, v in samples if n == name), (None, None))  # noqa: E731
    uname, _ = first("node_uname_info")
    uname = uname or {}
    name = uname.get("nodename") or _host_of(base_url) or "linux"
    hid = f"node/{name}"
    _, mem_total = first("node_memory_MemTotal_bytes")
    _, mem_avail = first("node_memory_MemAvailable_bytes")
    _, load1 = first("node_load1")
    _, boot = first("node_boot_time_seconds")
    _, now = first("node_time_seconds")
    cpu_idle = sum(v for n, lbl, v in samples if n == "node_cpu_seconds_total" and lbl.get("mode") == "idle")
    cpu_total = sum(v for n, lbl, v in samples if n == "node_cpu_seconds_total")
    cpus = len({lbl.get("cpu") for n, lbl, v in samples if n == "node_cpu_seconds_total"})
    cpu_pct = None
    if previous and cpu_total > previous.get("cpu_total", 0):
        busy = (cpu_total - previous["cpu_total"]) - (cpu_idle - previous.get("cpu_idle", 0))
        cpu_pct = round(max(0.0, min(100.0, 100 * busy / (cpu_total - previous["cpu_total"]))), 1)
    build_lbl = next((lbl for n, lbl, v in samples if n == "node_exporter_build_info"), {})
    host_ip = _host_of(base_url)
    snap = Snapshot(version=build_lbl.get("version", ""))
    snap.hosts.append(
        HostData(
            external_id=hid,
            name=name,
            kind="linux",
            hostname=name,
            os=f"{uname.get('sysname', 'Linux')} {uname.get('release', '')}".strip(),
            version=uname.get("release", ""),
            ip_address=host_ip if _is_ip(host_ip) else None,
            mac_addresses=sorted(
                {m for n, lbl, v in samples if n == "node_network_info" and lbl.get("device") != "lo" and (m := _mac(lbl.get("address")))}
            ),
            cpu_count=cpus or None,
            cpu_pct=cpu_pct,
            mem_total=int(mem_total) if mem_total else None,
            mem_used=int(mem_total - mem_avail) if mem_total and mem_avail is not None else None,
            load_1m=load1,
            uptime_s=int(now - boot) if now and boot else None,
            extra={"cpu_idle": cpu_idle, "cpu_total": cpu_total, "machine": uname.get("machine")},
        )
    )
    sizes = {(lbl.get("mountpoint"), lbl.get("device")): (lbl, v) for n, lbl, v in samples if n == "node_filesystem_size_bytes"}
    avail = {(lbl.get("mountpoint"), lbl.get("device")): v for n, lbl, v in samples if n == "node_filesystem_avail_bytes"}
    for key, (lbl, size) in sizes.items():
        if lbl.get("fstype") in SKIP_FS or not size:
            continue
        free = avail.get(key)
        snap.storage.append(
            StorageData(
                host=hid,
                external_id=f"fs/{lbl.get('mountpoint')}",
                name=lbl.get("mountpoint") or "/",
                kind="filesystem",
                total_bytes=int(size),
                used_bytes=int(size - free) if free is not None else None,
                status=lbl.get("fstype", ""),
                extra={"device": lbl.get("device")},
            )
        )
    return snap
