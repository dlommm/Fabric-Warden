# Agent autonomy

**Status: levels 0–2 implemented in Phase 7; levels 3–4 planned.** The highest level any agent can reach in this release is **Propose**.

| Level | Name | What the agent may do | Status |
|---|---|---|---|
| 0 | Off | Nothing; the agent cannot run. | ✅ |
| 1 | Explain | Call its read-only tools and answer. It has no tool that changes anything. Default for every agent. | ✅ |
| 2 | Propose | Also call `propose_change`, which creates a change proposal through the change engine, for the operations the agent lists. People review, approve and apply it in Change Approval; the agent cannot. | ✅ |
| 3 | Act after approval | Apply a change a person approved for it. | ⬜ planned, cannot be selected |
| 4 | Autonomous within guardrails | Apply pre-authorised low-risk changes on its own. | ⬜ planned, cannot be selected |

Each agent has a ceiling (`max_autonomy`): only the firewall review agent can be set to Propose. Setting a level above the ceiling is refused; levels 3 and 4 are not accepted by the API at all.

## Controls

- **Global kill switch.** Anyone with `ai:use` can engage it ("Stop all agents"); only `ai:manage` can release it. While engaged no run starts, and a running agent stops before its next model call (status "stopped"). Audited as `ai.kill_switch`.
- **Per-agent switch.** "Switch off" (or autonomy Off) stops that agent the same way.
- **Budgets.** Per-run tokens, tool calls and turns; per-provider monthly tokens and cost.
- **Permissions.** Running needs `ai:use` (operator and up); configuring agents and providers needs `ai:manage` (admin, owner). Tools check the running person's own permissions, so an agent never sees or proposes more than that person could.
- **Approval rules still apply.** A proposal is attributed to the person who ran the agent; with "Require a second person" on, that person cannot approve it.

Every run, refusal, denied tool call and configuration change is in the audit trail. See [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md).
