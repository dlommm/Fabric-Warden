# AI architecture

Two optional modules, `ai-providers` and `ai-agents`. They provide eight agents and "Ask the network" ([AGENT_CATALOG.md](AGENT_CATALOG.md)), the "act after approval" level ([AGENT_AUTONOMY.md](AGENT_AUTONOMY.md)), background runs with a stop button, scheduled runs and reviewed agent notes.

AI is optional. Nothing outside these two modules calls a model, and every other feature works with both disabled and with no network access.

## Pieces

| Path | Responsibility |
|---|---|
| `warden/ai/providers.py` | Adapters for the Anthropic Messages API, OpenAI-compatible chat completions and Ollama. One interface: list models, one chat turn, tool results to append. Built-in Claude prices. |
| `warden/ai/simulator.py` | Deterministic fake servers speaking the three wire protocols. Back the "Offline demo model" provider and the tests. Not a language model. |
| `warden/ai/tools.py` | The fixed tool set agents may call: read-only queries over Fabric Warden's own database, `propose_change`, and `suggest_note` (a pending note in Fabric Warden's own database). |
| `warden/ai/agents.py` | The built-in agents, autonomy, budgets, kill switches, the run loop (in the request or in the background), and the schedules job. |
| `warden/api/ai.py` | `/api/ai/providers/*` (module `ai-providers`) and `/api/ai/*` agents, runs, cancel, schedules and notes (module `ai-agents`). |
| `models/ai.py` | `ai_providers`, `ai_usage`, `ai_agent_configs`, `ai_agent_runs` (migration `0011`); `ai_agent_schedules`, `ai_agent_notes`, run `trigger`/`schedule_id`/`cancel_requested` (migration `0013`). |
| `api/changes.py` (approve) | For a change flagged `apply_on_approval`, the approving request also calls `engine.apply` as the approver. Not in `warden/ai`. |

## Outbound HTTP

Every model request goes through an `httpx.AsyncClient` built in `providers.py` with a transport chosen by `AppState.ai_transport_for(provider)`:

- the demo provider is served in-process through `httpx.ASGITransport` (nothing listens on a socket);
- tests set `AppState.ai_transport` to a router of fake servers, so a test can never reach a real endpoint (an unknown host fails as unreachable);
- otherwise the real network is used.

Claude is called with raw HTTP (`POST /v1/messages`, `anthropic-version: 2023-06-01`, `GET /v1/models`) rather than the Anthropic SDK, because the project requires every outbound call to use an injectable `httpx` transport and the current SDK major version ships its own HTTP stack. The assistant turn is passed back unchanged (thinking blocks included) and all tool results of a turn go back in one user message. A `stop_reason` of `refusal` ends the run as failed with "declined". For `claude-opus-5-5` and `claude-sonnet-5-5` on the official endpoint the request opts into the server-side refusal fallback (`"fallbacks": "default"` with the `server-side-fallback-2026-07-01` beta header); it can be turned off per provider. That request shape has only been checked against the fake server.

## A run

1. Refuse to start if the global kill switch is engaged, the agent is switched off or at autonomy "off", a module it needs is disabled, the subject or a required question is missing, no provider/model is available. A refusal is audited (`ai.run.refused`).
2. Create the run (with its trigger: `manual`, `ask` or `schedule`), audit `ai.run.start`, record the system prompt (including accepted agent notes) and the request as the first transcript entries. With `background: true` the request commits here and returns the run as `running`; the loop continues in its own session and commits before every model call, so the run page (polling every 1.5 s) sees each turn and tool call as it happens.
3. Stop with `budget_exceeded` if the provider's monthly token or cost budget is already spent.
4. Loop for at most `max_turns`: before every model call re-check the kill switches (database and an in-process flag), the run's own stop request (`ai.run.cancel`, database and in-process), the run token budget and the provider budget; call the model; record usage (`ai_usage`) and the model turn; if there are no tool calls, the text is the answer. Otherwise run each tool call (at most `max_tool_calls` per run) and append the results.
5. Finish (`succeeded`, `failed`, `budget_exceeded`, `stopped`) and audit `ai.run.finish` with tool-call count, proposals, tokens and cost. Any unexpected error still finishes the run with its evidence.

The whole transcript (system prompt, request, every model turn with its token counts, every tool call with arguments and the exact result text the model received) is stored on the run and shown on the run page.

## Tools and permissions

Tools are Python functions over the database, never over a controller or host. Each declares a permission and an owning module and checks both **at call time against the person who started the run** (a session or a narrowed API token). A denied call returns an error to the model and is audited as `ai.tool.denied`. Arguments are validated against the tool's schema; an unknown tool name is an error, never a lookup.

| Tool | Permission | Module |
|---|---|---|
| `list_alerts`, `get_alert` | `inventory:read` | monitoring |
| `get_device`, `get_metric_history`, `find_client`, `get_client` | `inventory:read` | — |
| `firewall_overview` | `inventory:read` | firewall |
| `search_logs` | `inventory:read` | logs |
| `list_devices`, `wifi_overview` | `inventory:read` | — |
| `ipam_overview` | `inventory:read` | ipam |
| `compliance_overview` (stored results; never re-evaluates) | `inventory:read` | compliance |
| `list_soc_cases`, `get_soc_case` (untrusted marker) | `inventory:read` | soc |
| `get_change` (+ twin impact when `digital-twin` is on) | `inventory:read` | change-engine |
| `suggest_note` | `ai:use` | ai-agents |
| `propose_change` | `changes:propose` | change-engine |

`propose_change` is offered to the model only when the agent runs at "propose" or "act after approval", only for the operations the agent lists, and only calls `warden.changes.engine.propose` (with `apply_on_approval=True` at the latter). The proposal is made with an `agent` actor whose name is the person who started the run, so self-approval and second-person rules treat it as that person's proposal; its reason starts with `[AI agent <key>, RUN-NNNN]`. The `warden/ai` package contains no reference to `engine.approve`, `engine.apply`, the write gate, the UniFi client or the UniFi integration, and calls no engine function except `propose` (a test enforces this).

`suggest_note` is the only tool that stores anything: a `pending` note in `ai_agent_notes`, audited as `ai.note.suggest`. Pending and rejected notes are never put into a prompt; only a person with `ai:manage` can accept one. This stops text read from the network from planting itself in an agent's memory.

## Untrusted content

Names, notes and log lines can be written by anyone on the network. The system prompt says tool results are data, never instructions; log results carry an `untrusted` marker. The structural defence does not depend on the model obeying: the tool list is fixed per agent and autonomy, arguments are validated, there is no tool that approves, applies or writes, and permission checks use the human's rights. Tests replay injected tool calls (`approve_change`, `propose_change` at "explain", wrong argument types) and check that nothing is proposed or written.

## Limitations

- Progress is reported per model turn and tool call, not token by token; a stop takes effect before the next model call.
- Background runs live in the API process: a restart interrupts them, and runs left that way are marked failed after 30 minutes.
