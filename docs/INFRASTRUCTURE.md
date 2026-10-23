# Infrastructure: racks, cabling, power, assets, topology and docs

Provided by the `dcim`, `cabling`, `power`, `asset-lifecycle`, `topology` and `docs` modules. Servers and virtualization are covered in [SERVERS.md](SERVERS.md).

The master specification describes these modules only in outline, so the detailed behaviour below is this project's design, not a transcription of the spec.

## Principle: documentation, compared against observation where possible

Like IPAM, every row here is **documentation owned by people**. Discovery never writes it and nothing is pushed to a controller. Where a documented object links to something the controller reports (a rack item linked to a device, a cable end on a device port), the APIs compare the two and report disagreement as findings.

The official UniFi Integration API does not expose physical location, LLDP neighbours, uplink port numbers, PoE wattage or UPS/PDU telemetry ([API_CAPABILITIES.md](API_CAPABILITIES.md)). Therefore:

- Racks, cables and power chains cannot be discovered; they are entered by hand.
- A documented cable path is only checked against which device each device reports as its uplink.
- The official API names a device's uplink device but not the port on either side, so a cable suggested from an uplink is planned with both ports left to confirm.
- Power figures are documented typical draw, not measurements.

## Permissions

| Permission | Grants | Roles |
|---|---|---|
| `dcim:edit` | racks, locations, rack items, cables, power feeds and links, assets | owner, admin, operator |
| `docs:edit` | wiki pages (create, save, revert, delete) | owner, admin, operator |

Reading needs `inventory:read`. Every write is audited (`dcim.*`, `cabling.*`, `power.*`, `assets.*`, `docs.page.*`).

## Racks & DCIM (`dcim`)

**Data.** `dcim_locations` (nested freely: site → building → floor → room → row → cage; optional link to an observed controller site), `dcim_racks` (height 1–60 U, numbering ascending or descending, width, depth, status), `dcim_rack_items` (kind, lowest occupied unit, height, front or rear face, full or half depth, status `installed`/`planned`/`removed`, optional link to one observed device, model/serial, patch panel port count and connector, power draw, UPS/PDU capacity, outlets and runtime).

**Occupancy rules.** An item occupies units `position … position + height − 1`. A full-depth item blocks both faces; a half-depth item blocks only its face, so a half-depth patch panel at the front and a PDU shelf at the rear can share a unit. Creates and moves that overlap another item, or extend above the top unit, are rejected with `409` and a message naming the blocking item. A rack cannot be shortened below its highest occupied unit. An item without a position is "not placed" (zero-U PDUs, spares).

**Elevation.** `GET /api/dcim/racks/{id}` returns one row per unit (top first) with the item id on each face, the items with linked device state, unplaced items and any conflicts. The UI draws front and rear between ink rails. Plates are plain for documented hardware, yellow when the linked device needs attention, ringed red when it is missing, offline or overlapping, and dashed when planned. Items move by drag and drop, by **Alt + ↑/↓** on a focused item, or with the Up / Down / To rear buttons in the item panel. Dragging is never the only way. On phones the elevation shows one face at a time.

**Findings** (`GET /api/dcim/findings`): `overlap`, `out_of_bounds` (warning), `racked_missing` (a racked device the controller no longer reports, warning), `unracked` (a present gateway or switch not placed in any rack, info).

## Cabling & Fiber (`cabling`)

**Data.** `dcim_cables` (unique label, type such as `cat6a`, `om4`, `os2`, `dac`, `aoc`; colour, length, status `planned`/`installed`/`decommissioned`) and `dcim_cable_ends` (two per cable). An end is a **device port** (observed device + port index), a **patch panel port** (panel + port + front/rear side), a **rack item** without modelled ports (a server NIC), or **external** free text (wall jack, ISP handoff).

**Rules.** A device port or panel port side holds one live cable. Device ports must exist on the observed device; panel ports must be within the panel's port count. An empty label takes the next free `C-NNNN`.

**Tracing.** Patch panel ports pass straight through: front port N connects to rear port N. `GET /api/cabling/trace` starts at a device port, a panel port or an item and follows cables and pass-throughs until it reaches something that is not a panel. Tracing from a panel port walks both directions. The UI shows the path as a route: endpoints bold, cables as labelled segments.

**Outputs.** Cable schedule as CSV (`/api/cabling/schedule.csv`); label text with one label per cable naming both ends (`/api/cabling/labels`, printable from the Cabling page); a patch panel port map (`/api/cabling/panels/{id}`) drawn in the rack item panel; documented cables per port on the device detail page.

**Findings** (`GET /api/cabling/findings`): `path_not_observed` (warning) when a device-to-device path connects two devices and neither reports the other as its uplink (it may be a standby or aggregated link, or stale documentation); `cabled_port_down` (info) when an installed cable lands on a port that reports link down.

**Suggested cables** (`GET /api/cabling/suggestions`): every observed uplink (a device and the device it reports as its uplink, both present) that no planned or installed cable path connects. `POST /api/cabling/suggestions/accept` with `keys` documents the chosen suggestions as **planned** cables, each with the next free `C-NNNN` label and both device-port ends marked *port to confirm*; each is audited as `cabling.cable.create` with the suggestion it came from. A cable with an unconfirmed port is not traced or checked against port state; the Cabling page asks for both ports when you open it, and the cable then traces like any other.

**Drift events.** After each sync, when the cabling module is on, the findings above plus every undocumented uplink are compared with the ones already open. A new one publishes `cabling.finding.opened` and one that has cleared publishes `cabling.finding.resolved` (payload: `key`, `kind`, `title`, and `cable_id` or `device_id`/`uplink_id`). Which findings are open is read back from those events, so nothing else is stored. Both are Automation Studio triggers, so a rule can notify when documented cabling stops matching the network. Nothing here writes to a controller.

## Power & Environment (`power`)

**Data.** `dcim_power_feeds` (circuit voltage, breaker amperage and continuous-load limit, 80 % by default) and `dcim_power_links` (source feed or UPS/PDU item and outlet → target rack item and input A/B/…). Loops, a reused outlet and a reused target input are rejected.

**Load model.** For a load with *n* live inputs, *k* of which reach a given source (directly or through UPS/PDU items):

- **normal** share = draw × k ÷ n;
- **failover** share = draw if k = n, otherwise draw × k ÷ (n − 1), meaning one supply was lost somewhere else and the remaining supplies share the draw.

Every source shows load now, load on failover, projected load with planned items, headroom against usable capacity (feed: V × A × limit; UPS/PDU: documented capacity) and, for a UPS with a documented full-load runtime, a **linear runtime estimate** (runtime at full load × capacity ÷ load). Real batteries usually last longer at partial load, so the estimate is conservative and labelled as one.

**PoE.** `GET /api/power/poe` lists, per switch, the ports delivering PoE and an **upper bound**: the maximum PSE power for each active port's negotiated standard (802.3af 15.4 W, at 30 W, bt type 3 60 W, type 4 90 W). The API reports no wattage, so no actual figure is shown.

**Findings** (`GET /api/power/findings`): `over_capacity` (critical now, warning on failover, info with planned items), `near_capacity` (≥ 80 %), `low_runtime` (estimate under 10 min), `shared_feed` (a load with two or more inputs that all trace back to one feed: redundancy that does not survive losing that circuit), `no_capacity`, `no_draw`.

## Asset Lifecycle (`asset-lifecycle`)

**Data.** `assets`: kind (`hardware`, `spare`, `licence`, `subscription`, `consumable`), status (`ordered`, `in_service`, `spare`, `in_repair`, `retired`, `disposed`), tag, vendor, model, serial, quantity and minimum stock, supplier, order reference, unit cost and currency, warranty / support / end-of-sale / end-of-life dates, licence seats, expiry and auto-renew, optional links to a device, a rack item and a location, labels and notes. **Licence keys are not stored here**; keep them in the credential vault and reference them.

**Views.** The list shows each asset's soonest upcoming date. The renewal calendar (`GET /api/assets/calendar`) lists every lifecycle date in the window, overdue first, and includes warranty dates from **device asset records** (marked "device record"; they are still edited on the device). Summary: active count, dates due within 90 days, purchase value per currency. CSV export.

**Findings** (`GET /api/assets/findings`): `expired` (a date passed; info instead of warning when the asset renews automatically), `expiring` (within 90 days and not auto-renewing), `low_stock` (quantity below the minimum), and device warranty expiry.

## Network Topology (`topology`)

`GET /api/topology?layer=physical|logical&vlan=&clients=` builds the graph from observed data on each request.

- **Physical:** devices joined by each device's reported uplink, with depth from the gateway, health (online, degraded, down, alerting when Monitoring has a firing device alert), attached client counts, rack placement when DCIM is on, and the uplink's current transmit/receive rate.
- **Logical:** the gateway and the networks it routes, with VLAN, subnet and client count.
- **VLAN overlay:** marks the devices on the attachment path of that VLAN's clients and draws those uplinks as the heavy route line. This is **inferred from client attachment**, not from switch port VLAN configuration, which the official API does not expose.

The UI offers the diagram and a table view of the same graph (the table is the accessible primary view). The official API does not identify uplink ports, so edges carry no port numbers.

## Documentation & Wiki (`docs`)

**Data.** `doc_pages` (unique slug, title, optional parent page, Markdown body, tags, head version, last editor) and `doc_versions` (every save, with author and message). API prefix is `/api/wiki` (`/api/docs` is the OpenAPI UI).

**Live references.** Write `[[type:key]]` or `[[type:key|text]]` with type `device`, `client`, `network`, `vlan`, `prefix`, `rack`, `cable`, `asset` or `page`. Keys match by id or by name (device MAC, client alias/IP, prefix CIDR, cable label, asset tag, page slug). References resolve on every read, so a page shows the object's current state, and a renamed or removed object shows as **unresolved** instead of pointing at stale text. `GET /api/wiki/references/check` lists every unresolved reference; `GET /api/wiki/backlinks?type=&id=` lists pages that mention an object (shown on device detail).

**Versions.** Saves carry the version they were based on; a save based on an older version is rejected with `409` naming who saved in between, so concurrent edits never silently overwrite each other. History lists versions, shows a line diff between any two, and restores an old version **as a new version**; history is never rewritten.

**Rendering.** The web app renders Markdown itself into React elements (headings, lists, tables, code, quotes, links, emphasis); page text is never injected as HTML, and only `http(s)`, relative, `#` and `mailto:` links are rendered as links.

## Demo layout

With the demo controller connected and synced, **Load demo layout** (`POST /api/dcim/demo`) creates a labelled layout for the simulated home lab: a site and utility closet, a 25 U rack with the gateway, aggregation and PoE switches linked to their simulated devices, patch and fiber panels, servers (one planned), a UPS and a rear PDU; and, for enabled modules, ten cables (including an undocumented standby link and a fiber run through the panel), a feed and power chain (with dual-PSU servers whose supplies share one circuit), seven assets (an expired warranty, a low spare) and three wiki pages with references and history. Every row has `is_demo = true`; **Remove demo layout** (`DELETE /api/dcim/demo`) deletes only demo rows.
