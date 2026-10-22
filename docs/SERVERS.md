# Servers & virtualization

Provided by the `servers` module.

## What it does

Fabric Warden reads four kinds of host, **read-only**, and joins them to the network view:

| Connection | Reads | Auth |
|---|---|---|
| **Proxmox VE** (8.x / 9.x) | cluster, nodes (CPU, memory, load, version), VMs and LXC containers (status, resources, start-at-boot, MACs, guest IPs), storage | API token `USER@REALM!TOKENID` + secret; role **PVEAuditor** is enough (add `VM.GuestAgent.Audit` for VM IPs) |
| **Unraid** (7.2+, or earlier with Unraid Connect) | OS, CPU and memory use, array state and capacity, parity/data/cache disks with temperature, Docker containers (autostart), VMs, UPS | API key (`x-api-key`) with read access |
| **Docker Engine** (API 1.47+) | engine, containers with image, state, ports, addresses and restart policy | none; put a **read-only socket proxy** in front of the socket |
| **node_exporter** | uname, memory, load, uptime, CPU utilisation (from two scrapes), filesystems, MAC addresses | none |

Every endpoint used, its required permission and its published source is in [`official_api_matrix.json`](../apps/api/src/warden/integrations/hosts/official_api_matrix.json) and summarised in [API_CAPABILITIES.md](API_CAPABILITIES.md#servers-and-virtualization).

## Read-only by construction

The host HTTP wrapper only has `get()` and `graphql_query()`. The latter refuses any document that is not a query (mutations and subscriptions are rejected before anything is sent). No code path starts, stops or changes a VM, container or array. A test records every request the Proxmox collector makes against the simulator and asserts they are all `GET`.

## Matching servers to the network

After each sync, every host and every VM/LXC is matched to a client the UniFi controller reports: by **IP address**, then **MAC address**, then **hostname** (hosts) or **name** (VMs/LXCs). Docker containers are matched only by their own IP or MAC (macvlan), never by name, because they share the host's address. The match and its reason are stored and shown, and `GET /api/servers/clients/{client_id}` answers "what is this client?" (for example: *home-assistant* is VM 104 on proxmox-02).

## Data

`host_connections` (kind, URL, Proxmox token id, credential in the vault, TLS settings, status), `hosts`, `workloads` (VMs, LXCs, containers), `host_storage` (datastores, Unraid array and disks, filesystems). Observed rows carry the usual provenance (`source` such as `proxmox-ve-api@9.0.10` or `simulator@…`, `observed_at`, `present`, `is_demo`). Hosts take user-owned labels and notes.

Metrics `host.cpu_pct`, `host.mem_pct` and `host.load_1m` are recorded on every sync while Analytics, Monitoring or WAN is enabled, so hosts can go on dashboards and alert rules.

## Findings (`GET /api/servers/findings`)

| Kind | Severity | When |
|---|---|---|
| `connection` | critical / warning | the API is unreachable, or answered with errors for part of the data |
| `host_offline` | critical | a Proxmox node reports offline |
| `array` | critical | the Unraid array is not started |
| `disk` | critical | an array disk is not `DISK_OK` (disabled, missing, invalid…) |
| `disk_hot` | warning | a disk at 50 °C or more |
| `ups` | warning | the UPS is not on mains, or its battery needs replacing |
| `autostart_stopped` | warning | a VM/LXC set to start at boot, or a container set to restart automatically, is not running (on an online host) |
| `storage_full` | warning ≥ 90 %, info ≥ 80 % | a datastore, array or filesystem; shared datastores report once |
| `not_on_network` | info | a host, or a running VM/LXC with an address, that no network client matches (isolated bridge, or not yet seen) |

## Scope

Read-only inventory and health. Fabric Warden takes no power or lifecycle actions on hosts, VMs or containers, and does not collect per-container CPU and memory.

## Demo

**Load demo servers** (`POST /api/servers/demo`, needs the demo controller setting) creates four labelled connections served by in-process simulators that line up with the UniFi simulator's clients: a two-node Proxmox cluster (with a home-assistant VM that matches the network client, a stopped start-at-boot VM and a 91 % full datastore), unraid-tower (a hot disk, a stopped autostart container, a UPS), docker-host (a stopped container with a restart policy) and nas-backup via node_exporter (an 88 % full backup filesystem). `DELETE /api/servers/demo` removes them.
