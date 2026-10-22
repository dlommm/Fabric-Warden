# Agent catalog

**Status: three agents implemented in Phase 7, five agents and "Ask the network" in Phase 8** (module `ai-agents`). Answers are AI-generated and labelled as such; each run keeps its transcript as evidence. All tested only against the deterministic offline demo model and fake model servers; the quality of real models' answers has not been evaluated.

| Agent | Subject | Tools | Max autonomy | Needs | Phase |
|---|---|---|---|---|---|
| **Explain this alert** (`alert-explainer`) | an active alert | `get_alert`, `get_device`, `get_metric_history`, `list_alerts`, `search_logs` | Explain | monitoring (logs optional) | 7 |
| **Why is this client slow?** (`client-slowness`) | a client | `get_client`, `get_device`, `get_metric_history`, `list_alerts`, `search_logs`, `find_client` | Explain | inventory (monitoring, logs optional) | 7 |
| **Firewall review** (`firewall-review`) | a site (optional) | `firewall_overview`, `search_logs`, `propose_change` | Act after approval: only `firewall_policy.set_logging` (turn logging on for a block/reject policy without it) | firewall (change-engine to propose) | 7 (level 3 in 8) |
| **Wi-Fi and RF review** (`wifi-rf`) | a site (optional) | `wifi_overview`, `get_device`, `get_metric_history`, `list_alerts` | Explain | inventory | 8 |
| **Capacity and IP plan** (`capacity-planner`) | a site (optional) | `ipam_overview`, `list_devices`, `get_metric_history`, `get_device` | Explain | ipam | 8 |
| **Compliance and security review** (`security-review`) | — | `compliance_overview`, `firewall_overview`, `list_devices`, `search_logs` | Explain | compliance (firewall, logs optional) | 8 |
| **Summarise this case** (`soc-case-summary`) | a SOC case | `get_soc_case`, `find_client`, `get_client`, `get_device`, `search_logs`, `list_alerts` | Explain | soc | 8 |
| **Explain this change** (`change-impact`) | a change request | `get_change` (with the digital twin's impact preview when `digital-twin` is on), `get_device`, `list_alerts` | Explain | change-engine | 8 |
| **Ask the network** (`ask-network`) | — (a question is required) | every read-only tool above | Explain | inventory | 8 |

Every agent is also offered `suggest_note` (see Agent notes). A tool whose module is disabled returns an error to the model rather than data.

## What the agents say they cannot know

- The client agent: the official UniFi API reports no per-client throughput, signal, retries or switch port.
- The Wi-Fi agent: no neighbouring or rogue networks, noise, channel utilisation, DFS events or per-client signal. Its channel findings (overlapping 2.4 GHz channels, wide 2.4 GHz channels, access points sharing a channel, high retry rates, busy access points) are inferred from configured channels and labelled as such.
- The capacity agent: used addresses come from clients, reservations and gateways seen in the last sync; DHCP leases are not exposed.
- The compliance agent reads the controls as last evaluated (it does not re-run them) and says how old the results are.
- The change agent: the impact preview is computed from the last synced inventory, not a live read. It does not recommend approving or rejecting.

## Defaults

Autonomy Explain, 60,000 tokens, 12 tool calls and 6 model turns per run, provider "Automatic" (the first enabled provider, a real one before the demo one). All adjustable per agent by `ai:manage`.

## Agent notes (memory)

Short background facts given to every run of an agent (or of every agent) in a separate "Operator notes" section of the system prompt, marked as data that never overrides the rules. A person with `ai:manage` writes them directly. An agent can only *suggest* a note (`suggest_note`, at most three per run, 500 characters): it is stored as `pending` and no agent sees it until a person accepts it. Every note change is audited (`ai.note.create/suggest/accept/reject/delete`).

## Scheduled runs

A schedule runs one agent with a fixed subject and question every 1 hour to 30 days, as the person who created it, with that person's roles at run time; it is capped per day (1–24) on top of the agent's per-run limits and the provider's monthly budget. The `ai-agents.schedules` job (60 s) runs due schedules and also closes runs left "running" by a restart. A refused scheduled run (kill switch, agent off, cap, the person can no longer sign in or lost `ai:use`) is recorded on the schedule and audited as `ai.run.refused`.

## Planned

Event-triggered agents (an Automation Studio action that starts an agent), drift explainer, firmware planner, documentation writer, agent-to-agent delegation, retrieval over the wiki. None of these exist yet.
