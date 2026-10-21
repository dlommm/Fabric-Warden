# Firewall analysis, configuration history and compliance

**Status: Phase 5a backend implemented** (modules `firewall`, `config-history`, `compliance`, all `available`; web screens in progress). Simulator-validated only.

## Firewall & Policy Manager (`GET /api/firewall`)

Reads the zone-based firewall policies and zones the controller reports (`GET /v1/sites/{siteId}/firewall/policies`, 10.1.84+; zones 10.0.162+). Policies are taken in their `index` order (first match wins). System-defined policies have no index in the API, so their position is unknown and they are listed separately.

**Coverage is only claimed when certain.** An earlier policy covers a later one when both have the same source and destination zones, the earlier protocol scope is at least as wide, its connection-state filter (if any) includes the later one's, and it either has **no traffic filter** (match-all) or **exactly the same** filters. A covered policy with a different action is **shadowed** (it never matches); with the same action it is **redundant**. Any other overlap (two different port lists, an IP set against a network) is marked **not determined**: the API's traffic filters (IP, network, port, MAC, region, application, domain, VPN) are not compared semantically.

Also reported: the **zone matrix** (per zone pair, the first match-all policy and how many filtered policies sit before it), blocking policies without logging, allow-all from External, disabled user policies, and unused zones. Read-only: nothing is reordered or changed.

## Configuration History (`/api/config`)

After every successful controller sync, each site's **configuration** (networks with VLAN, zone, gateway and DHCP range; zones and their networks; policies with order, action, filters and logging; WANs; device model and firmware) is canonicalised and hashed. A snapshot is stored only when the hash differs from the previous one, and `config.changed` is published with per-section counts. State (online/offline, clients, statistics) is never part of a snapshot.

Diffs are semantic: objects are keyed by controller id, so a rename is a changed `name`, not a removal and an addition. The first snapshot is the **baseline** until another is pinned (`POST /snapshots/{id}/baseline`, audited); `GET /drift` compares each site's latest snapshot with its baseline. Manual capture: `POST /capture?site_id=`.

The official API keeps no change log, so **a diff says what changed between two syncs, not who changed it**. Snapshots document observed configuration; they are not controller backups and cannot be restored.

## Policy & Compliance (`/api/compliance`)

Built-in controls are evaluated on request against the platform's own data. Each returns **pass**, **fail** or **unknown** (never a guess), with the evidence it looked at:

| Control | Fails when |
|---|---|
| Owners and admins use MFA | an active owner/admin has no TOTP |
| Audit trail is intact | the hash chain does not verify |
| Controller connections verify TLS | a real controller skips verification (unknown with only the demo controller) |
| Guest network cannot reach internal | the first match-all Hotspot/Guest → Internal policy does not block |
| IoT cannot reach internal | the first match-all IoT → Internal policy does not block |
| No shadowed or permissive inbound policies | firewall analysis finds either |
| Blocking policies log | a user blocking policy has logging off |
| Devices run current firmware | the controller offers an update |
| All devices are online | a present device is not online |
| Configuration matches the baseline | the latest snapshot differs from the baseline (unknown without Configuration History) |
| Every server is a known network client | a Servers host matches no client (unknown without Servers) |

A failing control can be **waived** with a reason and an expiry (`compliance:manage`, owner/admin); waivers are audited and lapse automatically. `GET /api/compliance/evidence.csv` exports every control with its evidence. Custom baselines are not implemented yet.
