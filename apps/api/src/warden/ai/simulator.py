"""In-process fake model servers for the demo and for tests.

These are not language models. They speak the wire protocols of the Anthropic
Messages API, OpenAI-compatible chat completions and Ollama, and answer
deterministically: on the first turn they call every offered tool whose
required arguments are in the run's subject; once tool results are back they
may propose turning logging on for a block policy that has none (when the
propose tool is offered), and then answer with the tools' own summaries.
Tests can queue exact responses in ``script``.

They are served through ``httpx.ASGITransport``; nothing listens on a socket.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DEMO_BASE_URL = "http://ai-simulator.local/v1"
DEMO_MODEL = "fabric-warden-demo"
SUBJECT_RE = re.compile(r"^Subject \(JSON\): (\{.*\})$", re.MULTILINE)
PROPOSE_TOOL = "propose_change"


@dataclass
class AiSimState:
    requests: list[dict[str, Any]] = field(default_factory=list)
    # Queued responses, consumed first: {"text": "..."} or {"tool_calls": [{"name": ..., "arguments": {...}}]}.
    script: list[dict[str, Any]] = field(default_factory=list)
    api_key: str | None = None  # when set, requests must carry this key
    models: list[str] = field(default_factory=lambda: [DEMO_MODEL])
    tokens: tuple[int, int] = (420, 90)  # (input, output) reported per response
    refuse: bool = False  # Anthropic fake answers stop_reason "refusal"


@dataclass
class _Conversation:
    user_text: str = ""
    called: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)


def _decide(state: AiSimState, conv: _Conversation, tools: list[dict[str, Any]]) -> dict[str, Any]:
    if state.script:
        return state.script.pop(0)
    names = {t["name"]: t for t in tools}
    if not conv.results:
        m = SUBJECT_RE.search(conv.user_text)
        subject = json.loads(m.group(1)) if m else {}
        calls = []
        for t in tools:
            if t["name"] == PROPOSE_TOOL:
                continue
            req = list((t.get("schema") or {}).get("required") or [])
            if all(k in subject for k in req):
                calls.append({"name": t["name"], "arguments": {k: subject[k] for k in req}})
        if calls:
            return {"tool_calls": calls[:4]}
    elif PROPOSE_TOOL in names and PROPOSE_TOOL not in conv.called:
        for text in conv.results:
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for f in data.get("findings", []) if isinstance(data, dict) else []:
                if f.get("kind") == "block_without_logging" and f.get("policy_id"):
                    return {
                        "tool_calls": [
                            {
                                "name": PROPOSE_TOOL,
                                "arguments": {
                                    "operation": "firewall_policy.set_logging",
                                    "target_id": f["policy_id"],
                                    "params": {"enabled": True},
                                    "reason": f"{f.get('subject', 'This policy')} blocks traffic without logging.",
                                },
                            }
                        ]
                    }
    lines = []
    for text in conv.results:
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        summary = data.get("summary") if isinstance(data, dict) else None
        lines.append(f"- {summary or (data.get('error') if isinstance(data, dict) else None) or text[:160]}")
    body = "\n".join(lines) if lines else "- No tool returned evidence for this subject."
    return {"text": f"Offline demo model: deterministic, not a language model. What the tools returned:\n{body}"}


def _key_ok(state: AiSimState, presented: str | None) -> bool:
    return state.api_key is None or presented == state.api_key


def _record(state: AiSimState, request: Request, body: Any) -> None:
    state.requests.append({"path": request.url.path, "body": body, "beta": request.headers.get("anthropic-beta")})


def create_openai_app(state: AiSimState) -> FastAPI:
    app = FastAPI()

    def auth(request: Request) -> bool:
        return _key_ok(state, (request.headers.get("authorization") or "").removeprefix("Bearer ") or None)

    @app.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        if not auth(request):
            return JSONResponse({"error": {"message": "Incorrect API key provided"}}, status_code=401)
        return JSONResponse({"object": "list", "data": [{"id": m, "object": "model", "owned_by": "simulator"} for m in state.models]})

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        if not auth(request):
            return JSONResponse({"error": {"message": "Incorrect API key provided"}}, status_code=401)
        body = await request.json()
        _record(state, request, body)
        conv = _Conversation()
        for m in body.get("messages", []):
            if m.get("role") == "user" and not conv.user_text:
                conv.user_text = str(m.get("content") or "")
            elif m.get("role") == "assistant":
                conv.called += [tc["function"]["name"] for tc in m.get("tool_calls") or []]
            elif m.get("role") == "tool":
                conv.results.append(str(m.get("content") or ""))
        tools = [{"name": t["function"]["name"], "schema": t["function"].get("parameters")} for t in body.get("tools") or []]
        d = _decide(state, conv, tools)
        n = len(state.requests)
        msg: dict[str, Any] = {"role": "assistant", "content": d.get("text")}
        if d.get("tool_calls"):
            msg["tool_calls"] = [
                {"id": f"call_{n}_{i}", "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                for i, c in enumerate(d["tool_calls"])
            ]
        return JSONResponse(
            {
                "id": f"chatcmpl-{n}",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if d.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": state.tokens[0], "completion_tokens": state.tokens[1], "total_tokens": sum(state.tokens)},
            }
        )

    return app


def create_anthropic_app(state: AiSimState) -> FastAPI:
    app = FastAPI()

    def error(status: int, kind: str, message: str) -> JSONResponse:
        return JSONResponse({"type": "error", "error": {"type": kind, "message": message}}, status_code=status)

    def check(request: Request) -> JSONResponse | None:
        if not _key_ok(state, request.headers.get("x-api-key")):
            return error(401, "authentication_error", "invalid x-api-key")
        if not request.headers.get("anthropic-version"):
            return error(400, "invalid_request_error", "anthropic-version header is required")
        return None

    @app.get("/v1/models")
    async def models(request: Request) -> JSONResponse:
        if bad := check(request):
            return bad
        data = [
            {"type": "model", "id": m, "display_name": m.replace("-", " ").title(), "created_at": "2026-09-01T00:00:00Z"}
            for m in state.models
        ]
        return JSONResponse({"data": data, "has_more": False, "first_id": data[0]["id"] if data else None, "last_id": None})

    @app.post("/v1/messages")
    async def messages(request: Request) -> JSONResponse:
        if bad := check(request):
            return bad
        body = await request.json()
        _record(state, request, body)
        usage = {"input_tokens": state.tokens[0], "output_tokens": state.tokens[1]}
        n = len(state.requests)
        if state.refuse:
            return JSONResponse(
                {
                    "id": f"msg_{n}",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [],
                    "stop_reason": "refusal",
                    "stop_details": {"type": "refusal", "category": None},
                    "usage": usage,
                }
            )
        conv = _Conversation()
        for m in body.get("messages", []):
            content = m.get("content")
            if m.get("role") == "user" and isinstance(content, str) and not conv.user_text:
                conv.user_text = content
            elif isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        conv.called.append(b["name"])
                    elif b.get("type") == "tool_result":
                        conv.results.append(str(b.get("content") or ""))
        tools = [{"name": t["name"], "schema": t.get("input_schema")} for t in body.get("tools") or []]
        d = _decide(state, conv, tools)
        blocks: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": f"sig-{n}"}]
        if d.get("text"):
            blocks.append({"type": "text", "text": d["text"]})
        for i, c in enumerate(d.get("tool_calls") or []):
            blocks.append({"type": "tool_use", "id": f"toolu_{n}_{i}", "name": c["name"], "input": c["arguments"]})
        return JSONResponse(
            {
                "id": f"msg_{n}",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": blocks,
                "stop_reason": "tool_use" if d.get("tool_calls") else "end_turn",
                "stop_details": None,
                "usage": usage,
            }
        )

    return app


def create_ollama_app(state: AiSimState) -> FastAPI:
    app = FastAPI()

    @app.get("/api/tags")
    async def tags() -> JSONResponse:
        return JSONResponse({"models": [{"name": m, "model": m, "size": 0} for m in state.models]})

    @app.post("/api/chat")
    async def chat(request: Request) -> JSONResponse:
        body = await request.json()
        _record(state, request, body)
        conv = _Conversation()
        for m in body.get("messages", []):
            if m.get("role") == "user" and not conv.user_text:
                conv.user_text = str(m.get("content") or "")
            elif m.get("role") == "assistant":
                conv.called += [tc["function"]["name"] for tc in m.get("tool_calls") or []]
            elif m.get("role") == "tool":
                conv.results.append(str(m.get("content") or ""))
        tools = [{"name": t["function"]["name"], "schema": t["function"].get("parameters")} for t in body.get("tools") or []]
        d = _decide(state, conv, tools)
        msg: dict[str, Any] = {"role": "assistant", "content": d.get("text") or ""}
        if d.get("tool_calls"):
            msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in d["tool_calls"]]
        return JSONResponse(
            {
                "model": body.get("model"),
                "message": msg,
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": state.tokens[0],
                "eval_count": state.tokens[1],
            }
        )

    return app
