# Rollback and recovery

Recovery is per operation; there is no universal rollback. See [CHANGE_ENGINE.md](CHANGE_ENGINE.md) for the lifecycle.

- **Reversible operations** (firewall policy logging) declare a tested inverse. If the check after a write fails, the engine sends the inverse straight away under the same approval, re-reads the controller and finishes the change as *reverted* (or *failed* if the revert cannot be confirmed). A succeeded reversible change can be reverted later by proposing its inverse, which is reviewed and approved like any other change.
- **Self-recovering operations** (device restart, PoE power-cycle) cannot be undone. They are verified by watching the device or port come back; if it does not within the window, the change is marked *failed* with no automatic action, and its recovery note says what to check.
- **Conflicts**: if the target changed between proposal and apply, nothing is sent and the change ends as *conflict*.
- **Stopping all writes**: an owner makes a controller read-only on the Changes page; disabling the module also stops all writes. Approved changes then cannot be applied.

