# IPAM and Client Intelligence

**Status: implemented in Phase 2** (modules `ipam` and `client-intel`, both `available`). Everything below is tested against the simulator only; read-only sync has since been checked against one live console (see [CHANGELOG.md](CHANGELOG.md)).

## Principle: documentation, compared against observation

IPAM records (prefixes, VLAN records, reservations) are **documentation owned by people**. Sync never writes them and nothing in IPAM is pushed to a controller. The value is the comparison: the API reads the observed side (networks, clients, devices) and reports where plan and network disagree.

Controller-side DHCP leases and fixed-IP reservations are **not exposed** by the official Integration API (no such endpoint in any analysed version, see [API_CAPABILITIES.md](API_CAPABILITIES.md)). So:

- "In use" means a client or device the controller currently reports at that address, not a lease.
- A reservation here does not create a fixed IP on the controller. That needs the change engine (Phase 6) and an API that supports it.

## Data

| Table | Owner | Notes |
|---|---|---|
| `ip_prefixes` | user | `cidr` stored in normalised network form, `family`, optional `site_id`, `vlan_id`, `status` (`active`, `planned`, `reserved` shown as "Held", `deprecated`), `description`, `labels`, `is_demo` (set when adopted from a demo network) |
| `ip_reservations` | user | `prefix_id` (cascade), `address` (unique per prefix), `kind` (`static`, `dhcp`, `infrastructure`), optional `mac_address`, `hostname`, `description` |
| `vlan_records` | user | `vlan_id` (1–4094), `name`, `status`, optional `site_id` |
| `client_sightings` | client-intel | one row per continuous stretch of a client at the same (IP, uplink, connection type) |

Clients also gained user-owned identity (`alias`, `owner`, `notes`, alongside `labels`); devices gained an `asset` record (tag, serial, location, owner, supplier, purchase and warranty dates). Discovery never overwrites either.

## Prefix tree and utilisation

Prefixes nest by containment: the parent is the smallest documented prefix that strictly contains it. **Used** counts the distinct addresses inside the prefix held by an observed client or device, the observed gateway, and reserved addresses; network and broadcast addresses are excluded for IPv4 prefixes of /30 and larger. Utilisation is used ÷ usable.

An address map is drawn for prefixes of up to 1,024 addresses. Each address is one of: network, broadcast, gateway, observed (in use), reserved (idle), DHCP pool (free), free. It is flagged as a conflict when holders disagree with a reservation's MAC or when two endpoints report the same address.

## Planned-vs-observed findings (`GET /api/ipam/findings`)

| Kind | Severity | When |
|---|---|---|
| `reservation_conflict` | critical | An observed endpoint holds a reserved address and none of the holders has the reserved MAC |
| `duplicate_address` | critical | Two or more present clients/devices report the same IP |
| `prefix_unobserved` | warning | An `active` prefix matches no observed network and contains none (containers are exempt) |
| `deprecated_in_use` | warning | A `deprecated` prefix is still configured on the controller |
| `vlan_mismatch` | warning | Documented VLAN differs from the observed network's VLAN |
| `reservation_in_pool` | warning | A `static`/`infrastructure` reservation sits inside the observed DHCP range |
| `address_outside_plan` | warning | An observed address is in no documented prefix and no observed network |
| `reservation_elsewhere` | info | The reserved MAC is observed at a different address |
| `network_undocumented` | info | An observed network has no documented prefix |

`POST /api/ipam/prefixes/adopt` documents undocumented observed networks (and their VLANs) in one step.

## VLAN inventory (`GET /api/ipam/vlans`)

Merges VLAN IDs from VLAN records, observed networks and prefixes. States: `documented`, `undocumented` (on the controller only), `unobserved` (active record not on the controller), `prefix_only` (only named by a prefix), `deprecated_in_use`, or the record's own `planned`/`reserved`/`deprecated` status. `name_mismatch` flags a record whose name matches none of the observed networks.

## Client Intelligence

- **Sightings** are recorded only while `client-intel` is enabled. Each sync extends the open sighting when (IP, uplink, type) is unchanged and opens a new one when it changes. History therefore begins when the module is enabled and has the sync interval's resolution.
- Address or uplink changes also publish `inventory.client.moved` (from the inventory module, regardless of client-intel).
- `GET /api/client-intel/clients/{id}`: identity, network/zone, attachment path up to the gateway, sightings, distinct addresses, the matching IPAM reservation (when `ipam` is enabled), events.
- **Fingerprinting is not available**: the official API exposes no OS/device-type data, so `fingerprint` is always `null` and the UI says so.

## Permissions and audit

Reads need `inventory:read`. IPAM writes need `ipam:edit` (owner, admin, operator). Client identity and device asset edits need `inventory:edit`. Every write is recorded in the audit trail (`ipam.prefix.*`, `ipam.reservation.*`, `ipam.vlan.*`, `inventory.client.identity`, `inventory.device.metadata`).

## Not yet

IPv6 address maps (IPv6 prefixes are documented and counted, not mapped), site-scoped IPAM views, CSV import/export, DHCP lease ingestion (needs a collector outside the official API), and pushing reservations to the controller (change engine).
