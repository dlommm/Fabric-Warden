# UniFi API capabilities

Fabric Warden only uses **official, versioned, documented** UniFi APIs. Nothing is inferred from the existence of a feature in the UniFi UI.

## Sources

- UniFi Network **Integration API** — published per application version at developer.ui.com; retrieved 2026-10-20 via the unofficial mirror `opastorello/unifi-api-docs` (OpenAPI JSON per version). Versions analysed: 9.1.120, 9.2.87, 9.3.45, 9.4.19, 9.5.21, 10.0.162, 10.1.84, 10.3.58, 10.4.57, 10.5.67, 10.6.106.
- Machine-readable matrix: `apps/api/src/warden/integrations/unifi/official_api_matrix.json` (generated from those specs; used at runtime by capability detection).
- UniFi Protect Integration API — specs 5.3.38 to 7.3.70 from the same mirror; used read-only by the Cameras module (see [UniFi Protect](#unifi-protect)).

**Live validation against a real console is pending** — all development used the simulator.

## Access

| | |
|---|---|
| Base path (local console) | `https://<console>/proxy/network/integration` |
| Remote (cloud connector) | `https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration` (not used; core works offline) |
| Auth | `X-API-KEY` header. Key created on the console under Settings → Control Plane → Integrations; shown once. |
| Pagination | `offset`, `limit` (max 200 in Fabric Warden), envelope `{offset, limit, count, totalCount, data}` |
| TLS | Consoles ship self-signed certificates; Fabric Warden supports pinning a CA PEM or (explicitly) disabling verification. |

API keys are per console: a key from a gateway does not authenticate against a separate UNVR running Protect (third-party report; to verify).

## How capability detection works

1. `GET /v1/info` → `applicationVersion`.
2. Each Fabric Warden capability (e.g. `network.firewall.policies.read`) maps to one or more endpoints; it is **supported** when the version is ≥ the newest `introduced` version of those endpoints.
3. Versions newer than the newest known spec are flagged `beyond_known_spec`; endpoints are probed, not assumed.
4. During sync, a read that succeeds marks the capability **verified**; a 404 downgrades it to unsupported.
5. Write capabilities are listed but **disabled**: the client refuses non-GET calls without a change-engine `WriteAuthorization`.

## Not exposed by the official API (any analysed version)

| Fact | Consequence |
|---|---|
| SFP/SFP28 optical DOM (TX/RX power, temperature) | Not read: Ubiquiti publishes no MIB for it, so SNMP does not cover it either; never fabricated |
| FEC mode/state | Same |
| Per-port CRC/symbol/discard and byte/packet counters | Read over SNMP instead (SNMP Port Counters, below) |
| DHCP lease table | IPAM shows configured pools and address-matched clients only |
| Controller fixed-IP / DHCP reservations | IPAM reservations are local documentation; they are compared with observed clients, not with controller config |
| Client OS / device-type fingerprint | Client Intelligence reports no fingerprint (`null`) rather than guessing |
| LLDP neighbours | Topology uses controller-reported uplinks only |
| IDS/IPS events | Needs syslog/CEF ingestion (Log Management) |
| Historical statistics | Only `statistics/latest`; Fabric Warden samples it on every sync (see ANALYTICS.md), so history starts when a metrics module is enabled |
| WAN state, addresses, throughput, failover | `GET /v1/sites/{siteId}/wans` (10.0.162+) lists id and name only; WAN latency/loss come from Warden's own probe |
| Device actions beyond `RESTART`; port actions beyond `POWER_CYCLE` | Port enable/disable, profiles, VLAN changes not available via this API |

## Endpoint availability by version

| Introduced | Method | Path | Summary |
|---|---|---|---|
| 9.1.120 | `GET` | `/v1/info` | Get Application Info |
| 9.1.120 | `GET` | `/v1/sites` | List Local Sites |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/clients` | List Connected Clients |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/clients/{clientId}` | Get Connected Client Details |
| 9.1.120 | `POST` | `/v1/sites/{siteId}/clients/{clientId}/actions` | Execute Client Action |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/devices` | List Devices |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/devices/{deviceId}` | Get Device Details |
| 9.1.120 | `POST` | `/v1/sites/{siteId}/devices/{deviceId}/actions` | Execute Device Action |
| 9.1.120 | `POST` | `/v1/sites/{siteId}/devices/{deviceId}/interfaces/ports/{portIdx}/actions` | Execute Port Action |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | Get Latest Device Statistics |
| 9.1.120 | `DELETE` | `/v1/sites/{siteId}/hotspot/vouchers` | Delete Vouchers |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/hotspot/vouchers` | List Vouchers |
| 9.1.120 | `POST` | `/v1/sites/{siteId}/hotspot/vouchers` | Generate Vouchers |
| 9.1.120 | `DELETE` | `/v1/sites/{siteId}/hotspot/vouchers/{voucherId}` | Delete Voucher |
| 9.1.120 | `GET` | `/v1/sites/{siteId}/hotspot/vouchers/{voucherId}` | Get Voucher Details |
| 10.0.162 | `GET` | `/v1/countries` | List Countries |
| 10.0.162 | `GET` | `/v1/dpi/applications` | List DPI Applications |
| 10.0.162 | `GET` | `/v1/dpi/categories` | List DPI Categories |
| 10.0.162 | `GET` | `/v1/pending-devices` | List Devices Pending Adoption |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/acl-rules` | List ACL Rules |
| 10.0.162 | `POST` | `/v1/sites/{siteId}/acl-rules` | Create ACL Rule |
| 10.0.162 | `DELETE` | `/v1/sites/{siteId}/acl-rules/{aclRuleId}` | Delete ACL Rule |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/acl-rules/{aclRuleId}` | Get ACL Rule |
| 10.0.162 | `PUT` | `/v1/sites/{siteId}/acl-rules/{aclRuleId}` | Update ACL Rule |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/device-tags` | List Device Tags |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/firewall/zones` | List Firewall Zones |
| 10.0.162 | `POST` | `/v1/sites/{siteId}/firewall/zones` | Create Custom Firewall Zone |
| 10.0.162 | `DELETE` | `/v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | Delete Custom Firewall Zone |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | Get Firewall Zone |
| 10.0.162 | `PUT` | `/v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | Update Firewall Zone |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/networks` | List Networks |
| 10.0.162 | `POST` | `/v1/sites/{siteId}/networks` | Create Network |
| 10.0.162 | `DELETE` | `/v1/sites/{siteId}/networks/{networkId}` | Delete Network |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/networks/{networkId}` | Get Network Details |
| 10.0.162 | `PUT` | `/v1/sites/{siteId}/networks/{networkId}` | Update Network |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/networks/{networkId}/references` | Get Network References |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/radius/profiles` | List Radius Profiles |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/traffic-matching-lists` | List Traffic Matching Lists |
| 10.0.162 | `POST` | `/v1/sites/{siteId}/traffic-matching-lists` | Create Traffic Matching List |
| 10.0.162 | `DELETE` | `/v1/sites/{siteId}/traffic-matching-lists/{trafficMatchingListId}` | Delete Traffic Matching List |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/traffic-matching-lists/{trafficMatchingListId}` | Get Traffic Matching List |
| 10.0.162 | `PUT` | `/v1/sites/{siteId}/traffic-matching-lists/{trafficMatchingListId}` | Update Traffic Matching List |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/vpn/servers` | List VPN Servers |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/vpn/site-to-site-tunnels` | List Site-To-Site VPN Tunnels |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/wans` | List WAN Interfaces |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/wifi/broadcasts` | List Wifi Broadcasts |
| 10.0.162 | `POST` | `/v1/sites/{siteId}/wifi/broadcasts` | Create Wifi Broadcast |
| 10.0.162 | `DELETE` | `/v1/sites/{siteId}/wifi/broadcasts/{wifiBroadcastId}` | Delete Wifi Broadcast |
| 10.0.162 | `GET` | `/v1/sites/{siteId}/wifi/broadcasts/{wifiBroadcastId}` | Get Wifi Broadcast Details |
| 10.0.162 | `PUT` | `/v1/sites/{siteId}/wifi/broadcasts/{wifiBroadcastId}` | Update Wifi Broadcast |
| 10.1.84 | `GET` | `/v1/sites/{siteId}/acl-rules/ordering` | Get User-Defined ACL Rule Ordering |
| 10.1.84 | `PUT` | `/v1/sites/{siteId}/acl-rules/ordering` | Reorder User-Defined ACL Rules |
| 10.1.84 | `POST` | `/v1/sites/{siteId}/devices` | Adopt Devices |
| 10.1.84 | `DELETE` | `/v1/sites/{siteId}/devices/{deviceId}` | Remove (Unadopt) Device |
| 10.1.84 | `GET` | `/v1/sites/{siteId}/dns/policies` | List DNS Policies |
| 10.1.84 | `POST` | `/v1/sites/{siteId}/dns/policies` | Create DNS Policy |
| 10.1.84 | `DELETE` | `/v1/sites/{siteId}/dns/policies/{dnsPolicyId}` | Delete DNS Policy |
| 10.1.84 | `GET` | `/v1/sites/{siteId}/dns/policies/{dnsPolicyId}` | Get DNS Policy |
| 10.1.84 | `PUT` | `/v1/sites/{siteId}/dns/policies/{dnsPolicyId}` | Update DNS Policy |
| 10.1.84 | `GET` | `/v1/sites/{siteId}/firewall/policies` | List Firewall Policies |
| 10.1.84 | `POST` | `/v1/sites/{siteId}/firewall/policies` | Create Firewall Policy |
| 10.1.84 | `GET` | `/v1/sites/{siteId}/firewall/policies/ordering` | Get User-Defined Firewall Policy Ordering |
| 10.1.84 | `PUT` | `/v1/sites/{siteId}/firewall/policies/ordering` | Reorder User-Defined Firewall Policies |
| 10.1.84 | `DELETE` | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Delete Firewall Policy |
| 10.1.84 | `GET` | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Get Firewall Policy |
| 10.1.84 | `PATCH` | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Patch Firewall Policy |
| 10.1.84 | `PUT` | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Update Firewall Policy |
| 10.3.58 | `GET` | `/v1/sites/{siteId}/switching/lags` | List LAGs |
| 10.3.58 | `GET` | `/v1/sites/{siteId}/switching/lags/{lagId}` | Get LAG Details |
| 10.3.58 | `GET` | `/v1/sites/{siteId}/switching/mc-lag-domains` | List MC-LAG Domains |
| 10.3.58 | `GET` | `/v1/sites/{siteId}/switching/mc-lag-domains/{mcLagDomainId}` | Get MC-LAG Domain |
| 10.3.58 | `GET` | `/v1/sites/{siteId}/switching/switch-stacks` | List Switch Stacks |
| 10.3.58 | `GET` | `/v1/sites/{siteId}/switching/switch-stacks/{switchStackId}` | Get Switch Stack |

## Writes used by the change engine

Only these writes are sent, only by the change engine, and only to a controller an owner has allowed changes on (see [CHANGE_ENGINE.md](CHANGE_ENGINE.md)):

| Capability | Endpoint | Since | Body (Network API 10.6.106 schema) |
|---|---|---|---|
| `network.firewall.policies.logging` | `PATCH /v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | 10.1.84 | `{"loggingEnabled": boolean}`, the only property in the published PATCH schema |
| `network.firewall.policies.update` | `PUT /v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | 10.1.84 | The policy as read, minus `id`, `index`, `metadata`, with `enabled` changed |
| `network.networks.update` | `PUT /v1/sites/{siteId}/networks/{networkId}` | 10.0.162 | The network as read, minus `id`, `default`, `metadata`, with one of `name`, `internetAccessEnabled`, `isolationEnabled`, `zoneId` changed |
| `network.firewall.zones.update` | `PUT /v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | 10.0.162 | `{name, networkIds}` as read with `name` changed |
| `network.devices.restart` | `POST /v1/sites/{siteId}/devices/{deviceId}/actions` | 9.1.120 | `{"action": "RESTART"}` |
| `network.ports.power_cycle` | `POST /v1/sites/{siteId}/devices/{deviceId}/interfaces/ports/{portIdx}/actions` | 9.1.120 | `{"action": "POWER_CYCLE"}` |

## Observed on a live console

First read-only sync against a real console (UDM Beast, Network 10.6.106, 2026-10-22). Where it differs from what the published schema suggests, Fabric Warden follows the console:

- **Gateway feature missing.** The device list's `features` enum includes `gateway`, but the UDM Beast reports only `["switching"]` (and the details endpoint's `features` object has only `switching`). Fabric Warden also recognises a gateway by model family (UDM, UDR, UDW, UCG, UXG, EFG, UX) so restart risk, the twin, topology and roles stay right.
- **Gateway address is the WAN address.** `ipAddress` for the gateway is its public WAN address, not a LAN address. SNMP targets added by LAN address attach to it by interface MAC instead.
- **Derived firewall policies.** Besides `USER_DEFINED` and `SYSTEM_DEFINED`, policies with `metadata.origin = DERIVED` appear: the "(Return)" halves UniFi keeps in step with a user rule. The change engine refuses to change them and analysis does not flag them.
- **ISP modem as a device.** A UniFi-integrated cable modem ("Cable Internet") is listed as an adopted device with no features and is the root of the uplink tree.

## Servers and virtualization

The Servers module reads Proxmox VE, Unraid, the Docker Engine and node_exporter. Endpoints were verified on 2026-10-21 against the published sources below; the machine-readable list with required permissions is `apps/api/src/warden/integrations/hosts/official_api_matrix.json`. Everything is read-only (see [SERVERS.md](SERVERS.md)).

| Integration | Source | Endpoints used |
|---|---|---|
| Proxmox VE API | [API wiki](https://pve.proxmox.com/wiki/Proxmox_VE_API), [API viewer schema](https://pve.proxmox.com/pve-docs/api-viewer/) | `GET /version`, `/cluster/status`, `/cluster/resources`, `/nodes/{node}/status`, `/nodes/{node}/{qemu,lxc}/{vmid}/config`, `/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces`, `/nodes/{node}/lxc/{vmid}/interfaces` |
| Unraid API | [docs.unraid.net/API](https://docs.unraid.net/API/), [GraphQL schema](https://github.com/unraid/api/blob/main/api/generated-schema.graphql) | one `query` on `/graphql`: `info`, `metrics`, `networkInterfaces`, `array`, `docker.containers`, `vms.domains`, `upsDevices` |
| Docker Engine API v1.47 | [Engine API reference](https://docs.docker.com/reference/api/engine/version/v1.47/) | `GET /version`, `/info`, `/containers/json?all=true`, `/containers/{id}/json` |
| node_exporter | [Prometheus guide](https://prometheus.io/docs/guides/node-exporter/), [collector source](https://github.com/prometheus/node_exporter/tree/master/collector) | `GET /metrics` |

## SNMP port counters

The SNMP module reads standard MIB-II / IF-MIB objects (RFC 3418, RFC 2863) over SNMP v3 or v2c: `sysDescr`, `sysUpTime`, `sysName`, and per interface `ifDescr`, `ifType`, `ifPhysAddress`, `ifAdminStatus`, `ifOperStatus`, `ifIn/OutErrors`, `ifIn/OutDiscards`, `ifName`, `ifHCIn/OutOctets`, `ifHighSpeed`, `ifAlias`. Only GET and GETBULK walks are sent; SET is never used (a test enforces it). The list with OIDs is `apps/api/src/warden/integrations/snmp/official_api_matrix.json`.

What Ubiquiti publishes ([SNMP Monitoring in UniFi Network](https://help.ui.com/hc/en-us/articles/33502980942615-SNMP-Monitoring-in-UniFi-Network); the page refuses automated fetches, so it was read from a search engine's index of it on 2026-10-21):

- SNMP is turned on per site under **Settings › CyberSecure › Traffic Logging**; there is no per-device switch.
- v1/v2c use a community; v3 uses a username and password with SHA authentication and AES-128 privacy, the same password for both.
- USW Flex and USW Ultra switches do not support SNMP. Fabric Warden marks them unavailable when picking devices.

Observed on a live UniFi gateway (UniFi OS 5.1.33, Linux 6.6, 2026-10-21):

- Ports are named `ethN` (no "Port N"), so they are not matched to front-panel numbers.
- VLAN subinterfaces (`eth13.100`), bridges (`br0`), the switch CPU port (`switch0`), IFB queues, tunnels, WireGuard and SR-IOV functions (`enP2p1s0v3`) all report ifType 6 (ethernetCsmacd); Fabric Warden excludes them from ports by name.
- The built-in switch ports `eth2`–`eth9` report ifHighSpeed 50000 whether up or down, so their utilisation is understated; the SFP28 port reported 25000 as expected.
- The agent answers on every gateway address, so the same device can be added twice; a finding says so.

Observed on live UniFi switches and access points (2026-10-21):

| Family (firmware) | Port names | Front-panel number | Notes |
|---|---|---|---|
| Enterprise Campus ECS-48-PoE, Pro XG Aggregation (AC5P 7.5.15) | `tw1/0/1`, `te1/0/31`, `tf1/0/4`, `gi1/0/5` | ifIndex (numbering restarts per speed class) | Lists 432 ports for every possible stack member; absent ones are `notPresent` and are ignored. LAGs are `Po1`… (ifType 161) |
| USW Enterprise 48 PoE (7.5.15) | `0/47` | the number after `0/`; ifDescr "Slot: 0 Port: 47 2.5G - Level" | LAGs are `3/N`; CPU interface `5/1` |
| U6 / U7 access points | `eth0` | not matched | Wi-Fi VAPs (`wifi0ap3`, `ra0`, `rai4`), WDS and client-mode interfaces report ifType 6 and are excluded by name |

ifAlias carries the operator's own port name on these switches ("SFP28 1", a custom label), so it is shown but never used to number ports. Some UniFi devices answer "Unknown USM user": their agent runs but did not take the site's SNMP v3 user.

Not published, so not assumed: which MIBs each model implements, how `ifName`/`ifDescr` map to front-panel ports (Fabric Warden matches "Port N" names and labels the match inferred), and any optics or FEC MIB.

## NetFlow and IPFIX

The Flows module receives what a gateway exports; it never asks the controller for anything. Wire formats come from the published specifications: Cisco's NetFlow v5 record, [RFC 3954](https://www.rfc-editor.org/rfc/rfc3954) (NetFlow v9), [RFC 7011](https://www.rfc-editor.org/rfc/rfc7011) (IPFIX) and the [IANA IPFIX information element registry](https://www.iana.org/assignments/ipfix/ipfix.xhtml).

What Ubiquiti publishes ([Traffic Flows and Traffic Logging in UniFi Network](https://help.ui.com/hc/en-us/articles/32201256219799-Traffic-Flows-and-Traffic-Logging-in-UniFi-Network); the page refuses automated fetches, so it was read from a search engine's index of it on 2026-10-24):

- NetFlow (IPFIX) export is set under **Settings › CyberSecure › Traffic Logging** and sends sampled traffic data to a collector.
- It needs UniFi Network 9.1 or later and UniFi OS 4.2.8 or later, on most gateways; the article excludes UDR, UDR7, UDR 5G Max, Express, Express 7, UDM, UCG-Ultra and UXG-Lite.
- The settings are networks to export, version (v5, v9, v10 and IPFIX), collector address and port, engine ID, timeout and refresh rates, sampling mode and sampling rate. NetFlow v5 does not support IPv6.

Fabric Warden decodes v10 and IPFIX identically (both are IPFIX version 10 on the wire). Not published, so not assumed: which information elements UniFi's templates carry, whether addresses are before or after NAT, and whether the sampling rate is exported in options records. The Flows page lists, per exporter, the fields its templates actually carry and the sampling rate it reported.

## UniFi Protect

The Cameras module reads the **UniFi Protect Integration API** (`https://<console>/proxy/protect/integration`, `X-API-KEY`). Checked on 2026-10-21 against the OpenAPI documents published at developer.ui.com, via the automated mirror `opastorello/unifi-api-docs` (36 Protect versions, 5.3.38 to 7.3.70). The list with fields used is `apps/api/src/warden/integrations/protect/official_api_matrix.json`; details in [PROTECT.md](PROTECT.md).

| Method | Path | In every spec since | Used for |
|---|---|---|---|
| `GET` | `/v1/meta/info` | 5.3.38 | `applicationVersion`; connection test |
| `GET` | `/v1/cameras` | 5.3.38 | id, name, type, MAC, state, smart-detection types |
| `GET` | `/v1/nvrs` | 5.3.38 | id, name, type, MAC, arm mode |

Published in 7.3.70 but not used: every PATCH/POST/DELETE (camera, light, sensor, chime, siren, relay, speaker, bridge settings; PTZ; RTSPS streams; talkback; alarm manager; arm profiles; file upload; POS ingest), snapshots, the device and event WebSocket subscriptions, viewers, live views, users. **Not published at all:** camera IP address, firmware, uptime, recording state; NVR storage and disk health; the switch port a camera uses.

## Home Assistant

The Home Assistant module reads the **Home Assistant REST API** ([developer docs](https://developers.home-assistant.io/docs/api/rest/), retrieved 2026-10-21), `Authorization: Bearer <long-lived access token>`. Details in [HOME_ASSISTANT.md](HOME_ASSISTANT.md); list in `apps/api/src/warden/integrations/homeassistant/official_api_matrix.json`.

| Method | Path | Admin only | Used for |
|---|---|---|---|
| `GET` | `/api/` | no | connection test (`{"message": "API running."}`) |
| `GET` | `/api/config` | no | version, location name, temperature unit |
| `GET` | `/api/states` | no | entity states; only temperature, UPS (by name), connectivity and router-tracker entities are kept |

Tracker attribute names (`ip`, `mac`, `host_name`, `source_type`) are from [`device_tracker/const.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/device_tracker/const.py); device classes from the [sensor](https://developers.home-assistant.io/docs/core/entity/sensor) and [binary sensor](https://developers.home-assistant.io/docs/core/entity/binary-sensor) docs. No POST endpoint (states, events, services, template) is used.

## MCP

The MCP gateway implements the Model Context Protocol Streamable HTTP transport, [specification 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18) (retrieved 2026-10-21), with read-only tools. See [MCP.md](MCP.md).
