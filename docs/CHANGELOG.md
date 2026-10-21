# Changelog

## 0.1.0 — unreleased
- Phase 0 research: UniFi Network/Protect official API matrices.
- Phase 4b: Servers & Virtualization — read-only Proxmox VE, Unraid (GraphQL API), Docker Engine and node_exporter collectors with simulators; hosts, VMs/LXCs and containers matched to network clients; storage, disk, UPS and autostart findings; host metrics. New permission `servers:manage`. Migration `0005`.
- Phase 4a: Rack & DCIM (locations, elevations, occupancy validation), Cabling & Fiber (tracing through patch panels, schedule, labels), Power & Environment (feeds, UPS/PDU chains, failover load, runtime estimate, PoE upper bound), Asset Lifecycle (renewal calendar, expiry and stock findings), Network Topology (uplink tree, logical view, VLAN overlay), Documentation & Wiki (versioned pages with live inventory references); labelled demo layout; search covers racks, cables, assets and pages. New permissions `dcim:edit`, `docs:edit`. Migration `0004`.
- Phase 3: metric history store with rollups, Analytics Studio dashboards, Monitoring & Alerts (rules, maintenance windows), Notification Channels (webhook, ntfy, email), WAN page with opt-in probe; simulator statistics follow a daily cycle. Migration `0003`.
- Phase 2: Client Intelligence (sightings, Client 360, client identity), IPAM (prefixes, reservations, VLAN records, planned-vs-observed findings, address map), device asset records, wider search. Migration `0002`.
- Phase 1 core: auth + MFA + tokens, RBAC, vault, audit chain, modules, scheduler, event bus, UniFi read-only integration with capability detection, simulator, inventory sync, web UI.
