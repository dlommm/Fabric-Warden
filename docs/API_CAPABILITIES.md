# UniFi API capabilities

Fabric Warden only uses **official, versioned, documented** UniFi APIs. Nothing is inferred from the existence of a feature in the UniFi UI.

## Sources

- UniFi Network **Integration API** — published per application version at developer.ui.com; retrieved 2026-10-20 via the unofficial mirror `opastorello/unifi-api-docs` (OpenAPI JSON per version). Versions analysed: 9.1.120, 9.2.87, 9.3.45, 9.4.19, 9.5.21, 10.0.162, 10.1.84, 10.3.58, 10.4.57, 10.5.67, 10.6.106.
- Machine-readable matrix: `apps/api/src/warden/integrations/unifi/official_api_matrix.json` (generated from those specs; used at runtime by capability detection).
- UniFi Protect Integration API (v7.3.70 spec analysed) — integration planned for Phase 9.

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
5. Write capabilities are listed but **disabled**: the client refuses non-GET calls without a change-engine `WriteAuthorization` (Phase 6).

## Not exposed by the official API (any analysed version)

| Fact | Consequence |
|---|---|
| SFP/SFP28 optical DOM (TX/RX power, temperature) | Needs SNMP/SSH collectors (Fiber module, Phase 3+) — never fabricated |
| FEC mode/state | Same |
| Per-port CRC/symbol/discard and byte/packet counters | Same; port utilisation history needs SNMP or IPFIX |
| DHCP lease table | IPAM shows configured pools and address-matched clients only |
| Controller fixed-IP / DHCP reservations | IPAM reservations are local documentation; they are compared with observed clients, not with controller config |
| Client OS / device-type fingerprint | Client Intelligence reports no fingerprint (`null`) rather than guessing |
| LLDP neighbours | Topology uses controller-reported uplinks only |
| IDS/IPS events | Needs syslog/CEF ingestion (Log Management, Phase 5) |
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

## Servers and virtualization

Phase 4b reads Proxmox VE, Unraid, the Docker Engine and node_exporter. Endpoints were verified on 2026-10-21 against the published sources below; the machine-readable list with required permissions is `apps/api/src/warden/integrations/hosts/official_api_matrix.json`. Everything is read-only (see [SERVERS.md](SERVERS.md)).

| Integration | Source | Endpoints used |
|---|---|---|
| Proxmox VE API | [API wiki](https://pve.proxmox.com/wiki/Proxmox_VE_API), [API viewer schema](https://pve.proxmox.com/pve-docs/api-viewer/) | `GET /version`, `/cluster/status`, `/cluster/resources`, `/nodes/{node}/status`, `/nodes/{node}/{qemu,lxc}/{vmid}/config`, `/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces`, `/nodes/{node}/lxc/{vmid}/interfaces` |
| Unraid API | [docs.unraid.net/API](https://docs.unraid.net/API/), [GraphQL schema](https://github.com/unraid/api/blob/main/api/generated-schema.graphql) | one `query` on `/graphql`: `info`, `metrics`, `networkInterfaces`, `array`, `docker.containers`, `vms.domains`, `upsDevices` |
| Docker Engine API v1.47 | [Engine API reference](https://docs.docker.com/reference/api/engine/version/v1.47/) | `GET /version`, `/info`, `/containers/json?all=true`, `/containers/{id}/json` |
| node_exporter | [Prometheus guide](https://prometheus.io/docs/guides/node-exporter/), [collector source](https://github.com/prometheus/node_exporter/tree/master/collector) | `GET /metrics` |
