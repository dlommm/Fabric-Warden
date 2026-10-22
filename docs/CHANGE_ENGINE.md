# Change Approval & Recovery engine

**Status: Phases 6a and 6b implemented** (module `change-engine`). Four operations, tested only against the simulated controller; no write has been sent to a live controller yet. Bulk operations, automation, the digital twin and the independent watchdog are not built (see [ROADMAP.md](ROADMAP.md)).

## Invariants

- The UniFi client raises `WriteNotAuthorized` for any non-GET call without a `WriteAuthorization`.
- A `WriteAuthorization` can only be built with the `_write_gate.ISSUER` sentinel. `tests/test_unifi.py::test_write_gate_issuer_not_imported_outside_change_engine` allows exactly three files to reference it: the client (which checks it), the gate itself, and `warden/changes/engine.py`.
- Every controller starts **read-only** (`write_mode = read_only`). Only an owner (`system:manage`) can allow changes on a controller, and can make it read-only again at any time. Proposing and approving work either way; applying needs the controller to allow changes.
- Only operations whose endpoint and request body are in the published Network API spec are implemented, and each is refused unless its capability is supported at the controller's detected version.

## Lifecycle

| Step | What happens | Who |
|---|---|---|
| Propose | Resolve the target; check the capability; read the target's **live** state from the controller; refuse if the state makes the change pointless or unsafe (already set, device not online, port without PoE); compute the expected state, a field-level diff, risk, protected-path status, impact and recovery. Only one open change per target. | `changes:propose` (operator and up) |
| Approve / reject | Record the decision and note. Changes on a protected path need an explicit acknowledgement. Self-approval is allowed and recorded as such unless the owner turns on **Require a second person**, which then refuses (and audits) a proposer approving their own change. | `changes:approve` (admin, owner) |
| Apply | Within 24 hours of the proposal. The controller must allow changes. The target is **read again**; if any watched field differs from the proposal, the change ends as **conflict** and nothing is sent. Otherwise exactly one write is sent with a `WriteAuthorization` naming the change. | `changes:approve` |
| Verify | Re-read the controller until the expected state is seen or the operation's verification window closes (immediately for logging, 10 minutes for a restart, 3 minutes for a PoE cycle). The `changes.verify` job advances waiting changes every 30 seconds. | engine |
| Finish | **succeeded**, **failed**, or **reverted**. A reversible change whose check fails is undone straight away under the same approval and the revert is verified too. Every finish triggers an inventory sync so the rest of the product shows the new state. | engine |
| Revert | For a succeeded reversible change, proposes the inverse as a new change that goes through approval like any other. | `changes:propose` |

Everything read, sent and observed is kept on the change as evidence; every step is in the hash-chained audit trail (`change.propose`, `change.approve`, `change.approve.denied`, `change.reject`, `change.cancel`, `change.apply`, `change.finish`, `controller.write_mode`, `change.settings`) and published as events (`change.proposed`, `change.decided`, `change.applied`, `change.finished`).

## Operations

| Operation | Endpoint (since) | Body | Reversible | Verification | Risk |
|---|---|---|---|---|---|
| `firewall_policy.set_logging` | `PATCH /v1/sites/{siteId}/firewall/policies/{id}` (10.1.84) | `{"loggingEnabled": bool}`; the published PATCH schema has no other field | Yes: the opposite value | `loggingEnabled` reads back as expected | Low; notes log volume for allow policies |
| `firewall_policy.set_enabled` | `PUT /v1/sites/{siteId}/firewall/policies/{id}` (10.1.84) | The object just read, minus `id`, `index` and `metadata` (absent from the published PUT schema), with only `enabled` changed | Yes: the original object is put back whole | `enabled` reads back as expected **and every other top-level field is exactly as before** | Medium; **high** to disable a block/reject policy; **high and protected** when either zone is Gateway |
| `device.restart` | `POST /v1/sites/{siteId}/devices/{id}/actions` (9.1.120) | `{"action": "RESTART"}` | No | The device was seen not online, or its uptime went backwards, and is online again | Medium; **high and protected** for the gateway or any device other devices uplink through |
| `port.power_cycle` | `POST /v1/sites/{siteId}/devices/{id}/interfaces/ports/{idx}/actions` (9.1.120) | `{"action": "POWER_CYCLE"}` | No (self-recovering) | PoE state reads back as before | Medium; says plainly that the API does not report which device is on the port |

Bodies were checked against the Network API 10.6.106 OpenAPI document (`opastorello/unifi-api-docs` mirror of developer.ui.com).

## Read-modify-write (full-replacement PUT)

The published PUT endpoints replace the whole object, and the spec marks no field read-only or write-only. For `firewall_policy.set_enabled` the engine:

1. sends exactly the object it re-read and drift-checked just before writing, minus the fields the PUT schema does not list, with one field changed;
2. re-reads the object and requires every other field to be unchanged;
3. otherwise puts the original object back (the `restore` hook) and verifies that too.

System-defined policies are never changed: the spec does not say whether UniFi accepts edits to them.

## Not built yet

- Wi-Fi broadcast edits: the spec shares one schema for the passphrase in GET and PUT but does not say whether a controller returns it in clear; if it is masked, a read-modify-write would overwrite the passphrase with the mask. Not attempted until that is verified on a live controller.
- Network and zone edits (same read-modify-write path; each needs its own risk model and tests).
- Bulk operations, Automation Studio, the network digital twin.
- An independently deployed watchdog for high-risk workflows, and protected-path detection for the ports hosting Fabric Warden itself (the official API does not map clients to switch ports).
