# Change Approval & Recovery engine

**Status: Phases 6a–6d implemented** (modules `change-engine`, `bulk-ops`, `automation`, `digital-twin`). Nine operations, tested only against the simulated controller; no write has been sent to a live controller yet. Digital-twin snapshots and the independent watchdog are not built (see [ROADMAP.md](ROADMAP.md)).

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
| `network.rename` | `PUT /v1/sites/{siteId}/networks/{id}` (10.0.162) | Network as read minus `id`, `default`, `metadata`, with `name` changed | Yes (original restored) | Field reads back; every other field unchanged | Low |
| `network.set_internet_access` | same | … with `internetAccessEnabled` changed (gateway-managed networks) | Yes | same | Medium; **high and protected** for the default network or the network the controller's address is in |
| `network.set_isolation` | same | … with `isolationEnabled` changed | Yes | same | Medium to enable, high to disable; protected as above |
| `network.move_zone` | same | … with `zoneId` changed; never into External, Gateway or VPN | Yes | same | **High**; protected as above |
| `zone.rename` | `PUT /v1/sites/{siteId}/firewall/zones/{id}` (10.0.162) | `{name, networkIds}` as read with `name` changed; custom zones only | Yes | same | Low |
| `device.restart` | `POST /v1/sites/{siteId}/devices/{id}/actions` (9.1.120) | `{"action": "RESTART"}` | No | The device was seen not online, or its uptime went backwards, and is online again | Medium; **high and protected** for the gateway or any device other devices uplink through |
| `port.power_cycle` | `POST /v1/sites/{siteId}/devices/{id}/interfaces/ports/{idx}/actions` (9.1.120) | `{"action": "POWER_CYCLE"}` | No (self-recovering) | PoE state reads back as before | Medium; says plainly that the API does not report which device is on the port |

Bodies were checked against the Network API 10.6.106 OpenAPI document (`opastorello/unifi-api-docs` mirror of developer.ui.com).

## Read-modify-write (full-replacement PUT)

The published PUT endpoints replace the whole object, and the spec marks no field read-only or write-only. For `firewall_policy.set_enabled` the engine:

1. sends exactly the object it re-read and drift-checked just before writing, minus the fields the PUT schema does not list, with one field changed;
2. re-reads the object and requires every other field to be unchanged;
3. otherwise puts the original object back (the `restore` hook) and verifies that too.

System-defined policies are never changed: the spec does not say whether UniFi accepts edits to them.

## Bulk operations

Module `bulk-ops`, code `warden/changes/batches.py`, API `/api/batches`. A batch adds **no write path**: it never mints a `WriteAuthorization` and never calls the client.

- **Operations:** PoE power-cycle, device restart, firewall policy logging, firewall policy enable/disable, network internet access, network isolation. Renames and zone moves stay single changes. At most 50 targets.
- **Propose:** every target goes through `engine.propose` (live read, plan, risk, protected path) and becomes an ordinary change request with `batch_id` and its position. Targets the engine refuses are listed on the batch as *skipped* with the reason; at least one must remain. Batch risk is the highest item risk; it is protected if any item is.
- **One open change per target** still holds, with one refinement: PoE cycles of *different ports* on the same switch may be open together.
- **Approve once:** one decision approves every item through `engine.approve` (second-person rule and protected-path acknowledgement apply). Items cannot be approved, applied or cancelled on their own (`409`); they are decided with their batch. A succeeded item can still be reverted on its own.
- **Apply:** refused while any item's controller is read-only. Items are applied in order through `engine.apply` (fresh re-read, drift check, one write, verification). The next item starts only after the current one has **succeeded** (verified) and the optional pause has passed; the `bulk-ops.advance` job (30 s) continues batches waiting on verification or a pause.
- **Stop on first failure:** an item that fails, conflicts, is reverted or cannot be applied stops the batch; remaining items are cancelled and never sent. An approver can also stop a running batch; the item in progress finishes and is verified.
- **Evidence:** each item keeps its own evidence; the batch keeps a log. Audit: `change.batch.propose`, `change.batch.approve`, `change.batch.reject`, `change.batch.cancel`, `change.batch.apply`, `change.batch.finish`, plus every item's `change.*` records. Events: `change.batch.proposed`, `change.batch.decided`, `change.batch.finished`.

## Automation Studio

Module `automation`, code `warden/automation.py`, API `/api/automation`.

- **Rule** = trigger (one event type from the event log, or every N ≥ 15 minutes) + conditions on the event (`severity`, `entity_type`, `entity_id`, `site_id`, `source`, `payload.<key>`; is, is not, one of, contains, at least, at most, present) + one action.
- **Actions:** *propose* one of the bulk operations above, for the event's object (a device, policy or network the event names) or a fixed target; or *notify* through the Notifications module's channels. **A rule never approves or applies.** Its proposals are made as `Automation: <rule name>`, so approving them is never self-approval.
- **Evaluation:** the `automation.evaluate` job (30 s) reads new events from the persisted event log by sequence number. Enabling a rule starts from that moment; old events are never replayed. Change events cannot trigger proposals (a finished change can only notify). A per-rule, per-target quiet period (default 60 minutes) and the engine's one-open-change rule stop repeats.
- **Dry run** (saved or unsaved rule): replays up to 50 matching events from the last 7 days (or one scheduled run) and says what would happen — would propose (with the title), would be refused (and why), would notify, skipped, or no match. It reads only the inventory; nothing is stored, proposed or sent.
- **History and audit:** every firing is a run (proposed, notified, refused, skipped, error). Audit: `automation.rule.create/update/enable/disable/delete` and `automation.fire`.

## Impact preview (Network Digital Twin)

Module `digital-twin`, code `warden/twin.py`, API `/api/twin/changes/{id}/impact` and `/api/twin/batches/{id}/impact`. Read-only, stores nothing. It computes from the **last synced inventory** (not a live read):

- restart: the device, every device behind it on the uplink tree, and clients attached through them; for the gateway, every network on the site;
- PoE cycle: the switch, and the devices and clients behind it marked only *possibly* affected (the official API does not report which device is on a port);
- firewall policy: the zones it joins, networks in those zones and their clients, other policies on the same zone pair; logging changes are marked as no traffic change;
- network and zone edits: the network's clients (matched by subnet), its zone and the policies that govern it.

**Not built:** versioned graph snapshots, history of the graph, and what-if simulation of traffic.

## Not built yet

- Wi-Fi broadcast edits: the spec shares one schema for the passphrase in GET and PUT but does not say whether a controller returns it in clear; if it is masked, a read-modify-write would overwrite the passphrase with the mask. Not attempted until that is verified on a live controller.
- VLAN, subnet, DHCP and IPv6 edits: nested configuration whose round-trip semantics need a live controller to confirm.
- Versioned digital-twin snapshots and what-if simulation; multi-step automation workflows.
- An independently deployed watchdog for high-risk workflows, and protected-path detection for the ports hosting Fabric Warden itself (the official API does not map clients to switch ports).
