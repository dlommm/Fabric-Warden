# Agent autonomy

**Status: levels 0–3 implemented (0–2 in Phase 7, 3 in Phase 8); level 4 planned.** The highest level any agent can reach in this release is **Act after approval**, and only the firewall review agent can reach it.

| Level | Name | What the agent may do | Status |
|---|---|---|---|
| 0 | Off | Nothing; the agent cannot run. | ✅ |
| 1 | Explain | Call its read-only tools and answer. It has no tool that changes anything. Default for every agent. | ✅ |
| 2 | Propose | Also call `propose_change`, which creates a change proposal through the change engine, for the operations the agent lists. People review, approve and apply it in Change Approval; the agent cannot. | ✅ |
| 3 | Act after approval | Proposes exactly as at Propose, but the proposal is flagged `apply_on_approval`. When a person approves it, that same approval request also applies it, under the approver's name, through `engine.apply` (re-read, drift check, single write, verification, automatic revert). The approver can choose "Approve only" instead. The agent never approves or applies, and has no tool that could. | ✅ ⏳ (simulator only) |
| 4 | Autonomous within guardrails | Apply pre-authorised low-risk changes on its own. | ⬜ planned; refused by the API (`422`) and never offered in the UI |

Each agent has a ceiling (`max_autonomy`): only the firewall review agent can be set above Explain. Setting a level above the ceiling is refused; `autonomous` is not accepted by the API at all, and a stored value that is not a built level refuses the run.

## How "act after approval" stays inside the change engine

- The agent's only path is still `propose_change` → `warden.changes.engine.propose(..., apply_on_approval=True)`. A batch item can never carry the flag.
- The apply happens in `POST /api/changes/{id}/approve` (module `change-engine`, permission `changes:approve`): after `engine.approve` succeeds, the handler calls `engine.apply` with the approving person as the actor. `{"apply": false}` approves only.
- Every rule of the change engine still holds: the second-person rule (the proposal is attributed to the person who ran the agent, so they cannot approve it when a second person is required), protected-path acknowledgement, the 24-hour window, and the controller's write mode. If the controller is read-only, the approval stands, the apply is refused, and the response carries `apply_error`.
- The audit trail shows `change.approve` and `change.apply` by the person, never by the agent. A test checks that the `warden/ai` package calls no engine function other than `propose` and imports nothing from the UniFi integration or the change routers.

## Controls

- **Global kill switch.** Anyone with `ai:use` can engage it ("Stop all agents"); only `ai:manage` can release it. While engaged no run starts (manual, background or scheduled), and a running agent stops before its next model call (status "stopped"). Audited as `ai.kill_switch`.
- **Per-agent switch.** "Switch off" (or autonomy Off) stops that agent the same way.
- **Per-run stop.** "Stop this run" (the person who started it, or `ai:manage`) stops a running run before its next model call. Audited as `ai.run.cancel`.
- **Budgets.** Per-run tokens, tool calls and turns; per-provider monthly tokens and cost; per-schedule runs per day.
- **Permissions.** Running needs `ai:use` (operator and up); configuring agents, providers, schedules and notes needs `ai:manage` (admin, owner). Tools check the running person's own permissions, so an agent never sees or proposes more than that person could. A schedule runs as the person who created it, with their roles at run time, and can only be created from a signed-in session (not an API token).
- **Approval rules still apply.** A proposal is attributed to the person who ran the agent; with "Require a second person" on, that person cannot approve it.

Every run, refusal, denied tool call, cancellation, schedule and note change is in the audit trail. See [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md).
