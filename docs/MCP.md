# MCP gateway

Provided by the `mcp` module. Every tool is read-only.

## Endpoint

`POST /api/mcp`, the Model Context Protocol **Streamable HTTP** transport ([spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports), retrieved 2026-10-21). Implemented directly from the spec rather than with the SDK, because the endpoint is stateless and has to share Fabric Warden's request authentication:

- One JSON-RPC message per POST; requests get a single `application/json` response (no SSE stream). Notifications and responses get `202 Accepted`. Batches are refused (removed in 2025-06-18).
- No `Mcp-Session-Id`: the server keeps no per-client state.
- `GET` and `DELETE` answer `405` (no server-initiated stream).
- Protocol versions `2025-06-18` and `2025-03-26`; an unsupported `MCP-Protocol-Version` header gets `400`.
- A browser `Origin` that is not this host gets `403` (the spec's DNS-rebinding guard).
- Methods: `initialize`, `ping`, `tools/list`, `tools/call`. Capabilities: `tools` only.

The web app proxies `/api/*`, so the endpoint is `https://<fabric-warden>/api/mcp` on the same origin as the UI.

## Authentication and permissions

An **API token** (`Authorization: Bearer wdn_…`), created under **Account › API tokens**. Session cookies are not accepted here.

- The token needs **`mcp:use`** (operators, auditors, admins and owners hold it; the "Read-only" token option includes it).
- Each tool also needs its own read permission (all current tools: `inventory:read`) and its module enabled. `tools/list` only lists what the token may call; calling anything else answers "Unknown tool", so a narrowed token cannot probe for tools.
- Every `tools/call` is written to the audit trail (`mcp.tool.call`, the tool name and the arguments, truncated).
- Responses include provenance; rows with `is_demo: true` come from the simulator.

## Tools

All read-only (`readOnlyHint: true`, `destructiveHint: false`). Results are JSON in `structuredContent` and as text; lists are capped at 50 items with `total` and `truncated`.

| Tool | Arguments | Needs module |
|---|---|---|
| `search_inventory` | `query` | inventory |
| `get_device` | `device`: id, exact name, MAC or IP | inventory |
| `get_client` | `client`: id, IP, MAC, name or alias; adds the server/VM (Servers) and the camera (Protect) it is | inventory |
| `list_alerts` | `state`: active / resolved / all | monitoring |
| `firewall_findings` | `severity` (optional) | firewall |
| `list_cameras` | — | unifi-protect |
| `home_assistant_entities` | `category` (optional) | home-assistant |

A lookup that finds nothing is a tool result with `isError: true` (so the model can try again); bad arguments are JSON-RPC error `-32602`.

## Connecting a client

Claude Code:

```sh
claude mcp add --transport http fabric-warden https://fabric-warden.lan/api/mcp \
  --header "Authorization: Bearer wdn_…"
```

Clients that take a JSON configuration:

```json
{ "mcpServers": { "fabric-warden": { "type": "http", "url": "https://fabric-warden.lan/api/mcp",
  "headers": { "Authorization": "Bearer wdn_…" } } } }
```

The Account page shows both with this installation's URL, and lists which tools are available.
