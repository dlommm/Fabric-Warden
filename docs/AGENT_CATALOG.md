# Agent catalog

**Status: three agents implemented in Phase 7** (module `ai-agents`). Answers are AI-generated and labelled as such; each run keeps its transcript as evidence.

| Agent | Subject | Tools | Max autonomy | Needs |
|---|---|---|---|---|
| **Explain this alert** (`alert-explainer`) | an active alert | `get_alert`, `get_device`, `get_metric_history`, `list_alerts`, `search_logs` | Explain | monitoring (logs optional) |
| **Why is this client slow?** (`client-slowness`) | a client | `get_client`, `get_device`, `get_metric_history`, `list_alerts`, `search_logs`, `find_client` | Explain | inventory (monitoring, logs optional) |
| **Firewall review** (`firewall-review`) | a site (optional) | `firewall_overview`, `search_logs`, `propose_change` | Propose: only `firewall_policy.set_logging` (turn logging on for a block/reject policy without it) | firewall (change-engine to propose) |

A tool whose module is disabled returns an error to the model rather than data. The client agent says plainly that the official UniFi API reports no per-client throughput, signal, retries or switch port, so its answer can only rest on the uplink device, radios, moves and alerts.

## Defaults

Autonomy Explain, 60,000 tokens, 12 tool calls and 6 model turns per run, provider "Automatic" (the first enabled provider, a real one before the demo one). All adjustable per agent by `ai:manage`.

## Planned

Change review assistant, drift explainer, capacity and firmware planner, SOC triage, documentation writer; agents that run on a schedule or on an event. None of these exist yet.
