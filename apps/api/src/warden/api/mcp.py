"""MCP server: read-only, permission-scoped tools over Fabric Warden data.

Transport: the MCP "Streamable HTTP" transport (spec 2025-06-18), one JSON-RPC
message per POST, answered with a single application/json body; no SSE stream
and no session id (the server keeps no per-client state). GET answers 405 as
the spec allows for servers that offer no server-initiated stream.

Authentication: a Fabric Warden API token (`Authorization: Bearer wdn_…`). The
token needs `mcp:use`, and every tool needs its own read permission and its
module enabled, so a narrowed token sees only the tools it may call. There are
no write tools; changes would only ever be proposals through the change engine
(planned, not built). Every tool call is audited.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import __version__, audit
from warden import firewall_analysis as fa
from warden.api import alerts as alerts_api
from warden.api import inventory as inventory_api
from warden.api.deps import DB, Principal, State, authenticate, require_module
from warden.app_state import AppState
from warden.models import Client, Device
from warden.security.rbac import P

router = APIRouter(prefix="/api/mcp", tags=["mcp"], dependencies=[Depends(require_module("mcp"))])

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
LATEST = PROTOCOL_VERSIONS[0]
MAX_BODY = 256 * 1024
MAX_ITEMS = 50

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603


class ToolError(Exception):
    """Reported to the client as a tool result with isError (not a protocol error)."""


@dataclass
class Ctx:
    db: AsyncSession
    state: AppState
    principal: Principal


@dataclass
class Tool:
    name: str
    title: str
    description: str
    permission: P
    module: str
    input_schema: dict[str, Any]
    run: Callable[[Ctx, dict[str, Any]], Awaitable[Any]]

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


def _str(args: dict[str, Any], key: str, *, required: bool = True, max_len: int = 128) -> str:
    v = args.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
        raise ValueError(f"'{key}' must be a non-empty string of at most {max_len} characters")
    return v.strip()


def _cap(items: list[Any]) -> dict[str, Any]:
    return {"items": items[:MAX_ITEMS], "total": len(items), "truncated": len(items) > MAX_ITEMS}


# -- tools ----------------------------------------------------------------------------


async def _search(ctx: Ctx, args: dict[str, Any]) -> Any:
    q = _str(args, "query")
    res = await inventory_api.search(db=ctx.db, state=ctx.state, principal=ctx.principal, q=q)
    return {"query": q, **_cap(res["results"])}


async def _resolve_device(db: AsyncSession, ref: str) -> Device:
    term = ref.lower()
    d = await db.get(Device, ref)
    if d is None:
        d = await db.scalar(
            select(Device)
            .where(Device.present.is_(True))
            .where(or_(func.lower(Device.name) == term, func.lower(Device.mac_address) == term, Device.ip_address == ref))
        )
    if d is None:
        raise ToolError(f"No device matches '{ref}' (try an id, exact name, MAC or IP; or search_inventory)")
    return d


async def _device(ctx: Ctx, args: dict[str, Any]) -> Any:
    d = await _resolve_device(ctx.db, _str(args, "device"))
    return await inventory_api.get_device(d.id, ctx.db, ctx.principal)


async def _client(ctx: Ctx, args: dict[str, Any]) -> Any:
    ref = _str(args, "client")
    term = ref.lower()
    c = await ctx.db.get(Client, ref)
    if c is None:
        c = await ctx.db.scalar(
            select(Client)
            .where(Client.present.is_(True))
            .where(
                or_(
                    Client.ip_address == ref,
                    func.lower(Client.mac_address) == term,
                    func.lower(Client.name) == term,
                    func.lower(Client.alias) == term,
                )
            )
        )
    if c is None:
        raise ToolError(f"No present client matches '{ref}' (try an IP, MAC, name or alias; or search_inventory)")
    uplink = None
    if c.uplink_device_external_id:
        u = await ctx.db.scalar(select(Device).where(Device.site_id == c.site_id, Device.external_id == c.uplink_device_external_id))
        uplink = {"id": u.id, "name": u.name, "model": u.model, "state": u.state} if u else None
    out: dict[str, Any] = {
        "client": {
            "id": c.id,
            "name": c.name,
            "alias": c.alias,
            "owner": c.owner,
            "type": c.type,
            "ip_address": c.ip_address,
            "mac_address": c.mac_address,
            "connected_at": c.connected_at,
            "labels": c.labels,
            "notes": c.notes,
        },
        "uplink_device": uplink,
        "provenance": inventory_api.provenance(c),
    }
    enabled = await ctx.state.modules.enabled_ids(ctx.db)
    if "servers" in enabled:
        from warden.api import servers as servers_api

        out["runs_on"] = await servers_api.runs_on(c.id, ctx.db, ctx.principal)
    if "unifi-protect" in enabled and c.mac_address:
        from warden.api import protect as protect_api
        from warden.models import ProtectDevice

        rows = list(
            await ctx.db.scalars(
                select(ProtectDevice).where(ProtectDevice.present.is_(True), ProtectDevice.mac_address == c.mac_address.lower())
            )
        )
        out["protect"] = [
            {k: v for k, v in i.items() if k in ("id", "kind", "name", "type", "state", "link", "port", "port_note")}
            for i in await protect_api.correlate(ctx.db, rows)
        ]
    return out


async def _alerts(ctx: Ctx, args: dict[str, Any]) -> Any:
    state = args.get("state", "active")
    if state not in ("active", "resolved", "all"):
        raise ValueError("'state' must be one of active, resolved, all")
    res = await alerts_api.list_alerts(ctx.db, ctx.principal, state=state, entity_id=None, limit=MAX_ITEMS)
    return {"counts": res["counts"], **_cap(res["items"])}


async def _firewall(ctx: Ctx, args: dict[str, Any]) -> Any:
    items = await fa.findings(ctx.db)
    sev = args.get("severity")
    if sev is not None:
        if sev not in ("critical", "warning", "info"):
            raise ValueError("'severity' must be one of critical, warning, info")
        items = [f for f in items if f.get("severity") == sev]
    return _cap(items)


async def _cameras(ctx: Ctx, args: dict[str, Any]) -> Any:
    from warden.api import protect as protect_api

    res = await protect_api.list_cameras(ctx.db, ctx.principal)
    return {"counts": res["counts"], "notes": res["notes"], **_cap(res["items"])}


async def _ha(ctx: Ctx, args: dict[str, Any]) -> Any:
    from warden.api import home_assistant as ha_api

    cat = args.get("category")
    if cat is not None and cat not in ("ups", "temperature", "tracker", "connectivity"):
        raise ValueError("'category' must be one of ups, temperature, tracker, connectivity")
    return _cap(await ha_api.entities_out(ctx.db, cat))


def _schema(props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


TOOLS: list[Tool] = [
    Tool(
        "search_inventory", "Search inventory",
        "Search devices, clients (names, aliases, owners, labels, IP and MAC), networks, IP prefixes, reservations, racks, "
        "cables, assets and documentation pages. Exact matches come first.",
        P.INVENTORY_READ, "inventory",
        _schema({"query": {"type": "string", "description": "Name, IP, MAC, label or text", "maxLength": 128}}, ["query"]),
        _search,
    ),
    Tool(
        "get_device", "Get device",
        "A UniFi device with ports (PoE), radios, uplink, downstream devices, connected clients, metrics and history.",
        P.INVENTORY_READ, "inventory",
        _schema({"device": {"type": "string", "description": "Device id, exact name, MAC or IP", "maxLength": 128}}, ["device"]),
        _device,
    ),
    Tool(
        "get_client", "Get client",
        "A network client with identity, uplink device, and where enabled the server/VM it is and the Protect camera it is.",
        P.INVENTORY_READ, "inventory",
        _schema({"client": {"type": "string", "description": "Client id, IP, MAC, name or alias", "maxLength": 128}}, ["client"]),
        _client,
    ),
    Tool(
        "list_alerts", "List alerts",
        "Monitoring alerts with severity, value, threshold and acknowledgement. Default: active (pending and firing).",
        P.INVENTORY_READ, "monitoring",
        _schema({"state": {"type": "string", "enum": ["active", "resolved", "all"], "default": "active"}}),
        _alerts,
    ),
    Tool(
        "firewall_findings", "Firewall findings",
        "Findings from the firewall policy analysis: shadowed and redundant policies, hygiene issues, zone exposure.",
        P.INVENTORY_READ, "firewall",
        _schema({"severity": {"type": "string", "enum": ["critical", "warning", "info"]}}),
        _firewall,
    ),
    Tool(
        "list_cameras", "List cameras",
        "UniFi Protect cameras with connection state, matched network client, uplink switch and documented port/PoE.",
        P.INVENTORY_READ, "unifi-protect",
        _schema(),
        _cameras,
    ),
    Tool(
        "home_assistant_entities", "Home Assistant entities",
        "Selected Home Assistant entities: UPS and temperature sensors, connectivity sensors, router trackers matched to clients.",
        P.INVENTORY_READ, "home-assistant",
        _schema({"category": {"type": "string", "enum": ["ups", "temperature", "tracker", "connectivity"]}}),
        _ha,
    ),
]  # fmt: skip
BY_NAME = {t.name: t for t in TOOLS}


async def available_tools(ctx: Ctx) -> list[Tool]:
    enabled = await ctx.state.modules.enabled_ids(ctx.db)
    return [t for t in TOOLS if t.module in enabled and ctx.principal.can(t.permission)]


# -- JSON-RPC -------------------------------------------------------------------------


def _err(id_: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}, status_code=status)


def _ok(id_: Any, result: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result}, headers=headers)


def _origin_ok(request: Request) -> bool:
    """DNS-rebinding guard required by the transport spec: a browser Origin must be this host."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).netloc.lower() == (request.headers.get("host") or "").lower()


async def _principal(request: Request, db: AsyncSession) -> Principal:
    if not request.headers.get("authorization", "").lower().startswith("bearer "):
        raise HTTPException(401, "An API token is required (Authorization: Bearer wdn_…)", headers={"WWW-Authenticate": "Bearer"})
    p = await authenticate(request, db)
    if p is None:
        raise HTTPException(401, "The token is invalid, expired or revoked", headers={"WWW-Authenticate": "Bearer"})
    if not p.can(P.MCP_USE):
        raise HTTPException(403, "missing permission mcp:use")
    return p


@router.post("")
async def mcp_post(request: Request, db: DB, state: State) -> Response:
    if not _origin_ok(request):
        return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    principal = await _principal(request, db)
    pv = request.headers.get("mcp-protocol-version")
    if pv and pv not in PROTOCOL_VERSIONS:
        return _err(None, INVALID_REQUEST, f"Unsupported MCP-Protocol-Version {pv}; supported: {', '.join(PROTOCOL_VERSIONS)}", 400)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _err(None, INVALID_REQUEST, "Message too large", 413)
    try:
        msg = json.loads(raw)
    except ValueError:
        return _err(None, PARSE_ERROR, "Parse error", 400)
    if isinstance(msg, list):
        return _err(None, INVALID_REQUEST, "JSON-RPC batches are not supported (protocol 2025-06-18)", 400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, INVALID_REQUEST, "Invalid Request", 400)
    method = msg.get("method")
    if method is None:
        # A JSON-RPC response from the client; this server never sends requests, so just accept it.
        return Response(status_code=202)
    if "id" not in msg:
        return Response(status_code=202)  # notification (e.g. notifications/initialized)
    id_ = msg["id"]
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _err(id_, INVALID_PARAMS, "params must be an object")
    ctx = Ctx(db, state, principal)

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else LATEST
        return _ok(
            id_,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fabric-warden", "title": "Fabric Warden", "version": __version__},
                "instructions": (
                    "Read-only tools over a self-hosted UniFi operations platform. Every result carries provenance; "
                    "rows with is_demo=true come from the built-in simulator and are not live. "
                    "There are no tools that change anything."
                ),
            },
        )
    if method == "ping":
        return _ok(id_, {})
    if method == "tools/list":
        return _ok(id_, {"tools": [t.listing() for t in await available_tools(ctx)]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = BY_NAME.get(name) if isinstance(name, str) else None
        if tool is None or tool not in await available_tools(ctx):
            # Unknown, not permitted for this token, or its module is disabled: indistinguishable on purpose.
            return _err(id_, INVALID_PARAMS, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _err(id_, INVALID_PARAMS, "arguments must be an object")
        try:
            data = await tool.run(ctx, args)
            is_error = False
        except ValueError as e:
            return _err(id_, INVALID_PARAMS, str(e))
        except (ToolError, HTTPException) as e:
            data, is_error = {"error": e.detail if isinstance(e, HTTPException) else str(e)}, True
        await audit.record(
            db, principal.actor, "mcp.tool.call", target_type="mcp_tool", target_id=tool.name,
            detail={"arguments": {k: str(v)[:128] for k, v in args.items()}, "error": is_error},
        )  # fmt: skip
        text = json.dumps(data, default=str)
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
        if not is_error:
            result["structuredContent"] = json.loads(text)
        return _ok(id_, result)
    return _err(id_, METHOD_NOT_FOUND, f"Method not found: {method}")


@router.get("")
async def mcp_get() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("")
async def mcp_delete() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})
