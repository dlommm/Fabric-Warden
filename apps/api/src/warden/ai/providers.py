"""Model provider adapters: Anthropic Messages API, OpenAI-compatible chat completions, Ollama.

Every request goes through an httpx client built here with an injectable
transport, so tests and the demo run against in-process fakes and never reach
a real endpoint. Each adapter keeps the conversation in its provider's own
message format (an assistant turn is appended exactly as the provider returned
it) and exposes the same three operations: list models, one chat turn, and
tool results to append.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_URL = "https://api.anthropic.com"
# Server-side refusal fallback ("fallbacks": "default"), Claude API only, for the models that accept it.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

# Published first-party prices, USD per million tokens (input, output). Claude Haiku 5.5 is priced
# higher for prompts over 100K tokens; agent runs here stay far below that.
BUILTIN_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

KINDS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic Claude (Messages API)",
        "default_base_url": ANTHROPIC_URL,
        "needs_key": True,
        "local": False,
        "suggested_models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    },
    "openai_compatible": {
        "label": "OpenAI-compatible (any server or service)",
        "default_base_url": "http://localhost:1234/v1",
        "needs_key": False,
        "local": True,
        "suggested_models": [],
    },
    "ollama": {
        "label": "Ollama",
        "default_base_url": "http://localhost:11434",
        "needs_key": False,
        "local": True,
        "suggested_models": [],
    },
}

# Hosted services that speak the OpenAI chat-completions API, with their published base URLs.
# Each is saved as an ordinary openai_compatible provider; models are listed from the service itself.
PRESETS: list[dict[str, Any]] = [
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "keys_url": "https://platform.openai.com/api-keys"},
    {
        "id": "gemini",
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "keys_url": "https://aistudio.google.com/apikey",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "keys_url": "https://openrouter.ai/settings/keys",
    },
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "keys_url": "https://console.groq.com/keys"},
    {"id": "mistral", "label": "Mistral AI", "base_url": "https://api.mistral.ai/v1", "keys_url": "https://console.mistral.ai/api-keys"},
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "keys_url": "https://platform.deepseek.com/api_keys",
    },
    {"id": "xai", "label": "xAI Grok", "base_url": "https://api.x.ai/v1", "keys_url": "https://console.x.ai"},
    {
        "id": "together",
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "keys_url": "https://api.together.ai/settings/api-keys",
    },
    {
        "id": "fireworks",
        "label": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "keys_url": "https://fireworks.ai/account/api-keys",
    },
    {"id": "cerebras", "label": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "keys_url": "https://cloud.cerebras.ai"},
]
PRESETS = [{**p, "kind": "openai_compatible", "needs_key": True} for p in PRESETS]
LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")


def is_local_url(url: str) -> bool:
    """True when the host is on this machine or the LAN: loopback, private or link-local addresses, or a LAN-only name."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES)
    return ip.is_private or ip.is_loopback or ip.is_link_local


HINTS = {
    "auth": "The provider rejected the key. Paste a valid API key; Anthropic keys are created in the Claude Console.",
    "unreachable": "The provider did not answer. Check the URL and port (Ollama 11434, LM Studio 1234) and that it listens here.",
    "tls": "The certificate could not be verified. Use the provider's trusted certificate, or turn verification off on a trusted network.",
    "not_found": "The server answered but the endpoint is missing. OpenAI-compatible servers need /v1/models and /v1/chat/completions.",
    "rate_limited": "The provider is rate-limiting requests. Wait and try again.",
    "declined": "The model declined to answer this request.",
    "protocol": "The provider answered with an error. See the detail.",
    "error": "The request failed. See the detail.",
}


class ProviderError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    invalid: str | None = None  # set when the model sent arguments that are not a JSON object


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    stop_reason: str
    message: dict[str, Any]  # the assistant message, in the provider's format, to append to the conversation
    model: str = ""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Adapter:
    base_url: str
    api_key: str | None = None
    transport: httpx.AsyncBaseTransport | None = None
    verify_tls: bool = True
    options: dict[str, Any] = field(default_factory=dict)
    timeout: float = 120.0

    def _client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=headers, verify=self.verify_tls, transport=self.transport, timeout=self.timeout)

    async def _request(self, method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._client(headers) as http:
                r = await http.request(method, url, json=body)
        except httpx.TimeoutException as e:
            raise ProviderError("unreachable", f"timed out: {e}") from e
        except httpx.ConnectError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e):
                raise ProviderError("tls", str(e)) from e
            raise ProviderError("unreachable", str(e) or "connection failed") from e
        except httpx.HTTPError as e:
            raise ProviderError("error", str(e)) from e
        if r.status_code in (401, 403):
            raise ProviderError("auth", _detail(r))
        if r.status_code == 404:
            raise ProviderError("not_found", _detail(r))
        if r.status_code == 429:
            raise ProviderError("rate_limited", _detail(r))
        if r.status_code >= 400:
            raise ProviderError("protocol", f"HTTP {r.status_code}: {_detail(r)}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError("protocol", "the provider did not answer with JSON") from e
        if not isinstance(data, dict):
            raise ProviderError("protocol", "unexpected response shape")
        return data

    def initial(self, user_text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": user_text}]

    async def list_models(self) -> list[dict[str, Any]]:  # pragma: no cover - overridden
        raise NotImplementedError

    async def chat(self, model: str, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec], max_tokens: int) -> Turn:
        raise NotImplementedError  # pragma: no cover - overridden

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        raise NotImplementedError  # pragma: no cover - overridden


def _detail(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return r.text[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or data)[:300]


def _args(raw: Any) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            v = json.loads(raw or "{}")
        except ValueError:
            return {}, "arguments were not valid JSON"
        return (v, None) if isinstance(v, dict) else ({}, "arguments were not a JSON object")
    return {}, "arguments were missing"


class AnthropicAdapter(Adapter):
    """Claude via the official Messages API (POST /v1/messages, GET /v1/models)."""

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key or "", "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def _root(self) -> str:
        return self.base_url.rstrip("/").removesuffix("/v1")

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", f"{self._root()}/v1/models?limit=100", self._headers())
        return [
            {"id": m["id"], "name": m.get("display_name") or m["id"]} for m in data.get("data", []) if isinstance(m, dict) and "id" in m
        ]

    def _fallback(self, model: str) -> bool:
        official = urlsplit(self._root()).hostname == urlsplit(ANTHROPIC_URL).hostname
        return official and model in FALLBACK_MODELS and self.options.get("refusal_fallback", True)

    async def chat(self, model: str, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec], max_tokens: int) -> Turn:
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        headers = self._headers()
        if self._fallback(model):
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = FALLBACK_BETA
        data = await self._request("POST", f"{self._root()}/v1/messages", headers, body)
        stop = str(data.get("stop_reason") or "")
        if stop == "refusal":
            raise ProviderError("declined", "The model declined this request (stop_reason: refusal).")
        content = data.get("content") or []
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
        calls = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                args, bad = _args(b.get("input"))
                calls.append(ToolCall(str(b.get("id")), str(b.get("name")), args, bad))
        u = data.get("usage") or {}
        tokens_in = (
            int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        )
        return Turn(
            text=text,
            tool_calls=calls,
            input_tokens=tokens_in,
            output_tokens=int(u.get("output_tokens") or 0),
            stop_reason=stop,
            message={"role": "assistant", "content": content},  # passed back unchanged, thinking blocks included
            model=str(data.get("model") or model),
        )

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        # All results of one turn go back in a single user message.
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": c.id, "content": out, **({"is_error": True} if err else {})}
                    for c, out, err in results
                ],
            }
        ]


class OpenAICompatibleAdapter(Adapter):
    """POST /v1/chat/completions and GET /v1/models, as served by LM Studio, vLLM, llama.cpp, OpenAI and the hosted presets."""

    def _v1(self) -> str:
        """The base URL as given when it already names an API version (/v1, /v1beta/openai), else with /v1 added."""
        b = self.base_url.rstrip("/")
        return b if re.search(r"/v\d+[a-z0-9]*(/|$)", urlsplit(b).path) else f"{b}/v1"

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", f"{self._v1()}/models", self._headers())
        return [{"id": m["id"], "name": m["id"]} for m in data.get("data", []) if isinstance(m, dict) and "id" in m]

    async def chat(self, model: str, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec], max_tokens: int) -> Turn:
        body: dict[str, Any] = {"model": model, "messages": [{"role": "system", "content": system}, *messages], "max_tokens": max_tokens}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools
            ]
        data = await self._request("POST", f"{self._v1()}/chat/completions", self._headers(), body)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ProviderError("protocol", "the response had no choices")
        msg = choices[0].get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args, bad = _args(fn.get("arguments"))
            calls.append(ToolCall(str(tc.get("id") or f"call_{i}"), str(fn.get("name")), args, bad))
        u = data.get("usage") or {}
        return Turn(
            text=str(msg.get("content") or "").strip(),
            tool_calls=calls,
            input_tokens=int(u.get("prompt_tokens") or 0),
            output_tokens=int(u.get("completion_tokens") or 0),
            stop_reason=str(choices[0].get("finish_reason") or ""),
            message={
                k: v for k, v in {"role": "assistant", "content": msg.get("content"), "tool_calls": msg.get("tool_calls")}.items() if v
            },
            model=str(data.get("model") or model),
        )

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "tool", "tool_call_id": c.id, "content": out} for c, out, _ in results]


class OllamaAdapter(Adapter):
    """Ollama's native API: GET /api/tags, POST /api/chat (stream off)."""

    def _root(self) -> str:
        return self.base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", f"{self._root()}/api/tags", self._headers())
        return [{"id": m["name"], "name": m["name"]} for m in data.get("models", []) if isinstance(m, dict) and "name" in m]

    async def chat(self, model: str, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec], max_tokens: int) -> Turn:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools
            ]
        data = await self._request("POST", f"{self._root()}/api/chat", self._headers(), body)
        msg = data.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args, bad = _args(fn.get("arguments"))
            calls.append(ToolCall(f"call_{i}", str(fn.get("name")), args, bad))
        return Turn(
            text=str(msg.get("content") or "").strip(),
            tool_calls=calls,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            stop_reason=str(data.get("done_reason") or ""),
            message={
                k: v
                for k, v in {"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls")}.items()
                if v is not None
            },
            model=str(data.get("model") or model),
        )

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "tool", "content": out, "tool_name": c.name} for c, out, _ in results]


ADAPTERS: dict[str, type[Adapter]] = {
    "anthropic": AnthropicAdapter,
    "openai_compatible": OpenAICompatibleAdapter,
    "ollama": OllamaAdapter,
    "demo": OpenAICompatibleAdapter,  # the built-in offline model speaks the OpenAI-compatible protocol
}


def price(kind: str, model: str, default_in: float, default_out: float) -> tuple[float, float]:
    if kind == "anthropic" and model in BUILTIN_PRICES:
        return BUILTIN_PRICES[model]
    return default_in, default_out


def cost_usd(kind: str, model: str, tokens_in: int, tokens_out: int, default_in: float, default_out: float) -> float:
    p_in, p_out = price(kind, model, default_in, default_out)
    return round((tokens_in * p_in + tokens_out * p_out) / 1_000_000, 6)
