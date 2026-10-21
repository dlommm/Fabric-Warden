# Change Approval & Recovery engine

**Status: not implemented (Phase 6).** This document records the constraints the implementation must satisfy; it describes no working behaviour.

## Invariants already enforced

- The UniFi client raises `WriteNotAuthorized` for any non-GET call without a `WriteAuthorization`.
- `WriteAuthorization` can only be constructed with the `_write_gate.ISSUER` sentinel; `tests/test_unifi.py::test_write_gate_issuer_not_imported_outside_change_engine` fails if any module other than the engine references it.
- Controllers have `write_mode = read_only`; the UI states writes are disabled.

## Required lifecycle (from the product spec)

Capture state → verify credentials and capability → check freshness → detect concurrent change (optimistic concurrency on `configurationId`/object versions) → validate → identify affected infrastructure and protected management paths → exact before/after diff → expected benefit and risk → recovery plan → verify recovery observability → approvals → bounded execution → health verification → commit or supported recovery → record evidence.

## Constraints

- Only operations the official API supports at the controller's version (see API_CAPABILITIES.md). Today that is: device restart, PoE power-cycle, adopt/unadopt, guest authorize, CRUD on networks, Wi-Fi broadcasts, firewall zones/policies, ACL rules, DNS policies, traffic matching lists, vouchers.
- No universal rollback promise. Each operation declares whether a tested inverse exists; without one, automatic execution is disallowed.
- Protected paths (gateway management, management VLAN, controller uplinks, DNS/default route, WAN, core interconnects, ports hosting Fabric Warden/watchdog, recovery VPN, camera infrastructure) require explicit approval at any autonomy level.
- An independently deployed watchdog is required for high-risk workflows.
