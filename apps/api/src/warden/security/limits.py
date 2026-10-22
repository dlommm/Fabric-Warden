"""Request-level limits: body size and a small in-process failure throttle."""

from __future__ import annotations

import json
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.exceptions import HTTPException

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class _TooLarge(HTTPException):
    # An HTTPException so FastAPI's body parsing re-raises it (as 413) instead of turning it into a 400.
    def __init__(self, limit: int) -> None:
        super().__init__(413, f"request body is larger than {limit} bytes")


class BodySizeLimit:
    """Pure ASGI middleware: refuse request bodies over a limit with 413.

    Checks ``Content-Length`` up front and also counts streamed bytes, so a
    chunked upload without a length cannot slip past. Paths listed in
    ``overrides`` (prefix match) get their own limit, e.g. backup restore.
    """

    def __init__(self, app: ASGIApp, *, limit: int, overrides: dict[str, int] | None = None) -> None:
        self.app = app
        self.limit = limit
        self.overrides = overrides or {}

    def _limit_for(self, path: str) -> int:
        for prefix, lim in self.overrides.items():
            if path.startswith(prefix):
                return lim
        return self.limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope.get("path", ""))
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        await _reject(send, limit)
                        return
                except ValueError:
                    await _reject(send, limit, status=400, detail="invalid Content-Length")
                    return
        seen = 0
        started = False

        async def counted() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _TooLarge(limit)
            return msg

        async def tracking_send(msg: Message) -> None:
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counted, tracking_send)
        except _TooLarge:
            if not started:
                await _reject(send, limit)


async def _reject(send: Send, limit: int, *, status: int = 413, detail: str | None = None) -> None:
    body = json.dumps({"detail": detail or f"request body is larger than {limit} bytes"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class Throttle:
    """Sliding-window failure counter keyed by string, bounded in memory.

    In-process only (per API replica); good enough for a single-instance
    self-hosted deployment, documented as such.
    """

    def __init__(self, max_failures: int, window_seconds: float, *, max_keys: int = 10_000) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self.max_keys = max_keys
        self._fails: OrderedDict[str, deque[float]] = OrderedDict()

    def _prune(self, key: str) -> deque[float]:
        q = self._fails.get(key)
        if q is None:
            q = deque()
            self._fails[key] = q
            while len(self._fails) > self.max_keys:
                self._fails.popitem(last=False)
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str, max_failures: int | None = None) -> bool:
        if key not in self._fails:
            return False
        return len(self._prune(key)) >= (max_failures or self.max_failures)

    def fail(self, key: str) -> None:
        self._prune(key).append(time.monotonic())
        self._fails.move_to_end(key)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)

    def clear(self) -> None:
        self._fails.clear()


API_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


class SecurityHeaders:
    """Pure ASGI middleware adding security headers to every HTTP response.

    The JSON API never renders a page, so its CSP forbids everything (except
    the interactive docs under /api/docs). API responses are not cacheable.
    Pure ASGI (not BaseHTTPMiddleware) so errors raised while reading the body,
    such as BodySizeLimit's 413, reach FastAPI unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = list(msg.get("headers", []))
                present = {k.lower() for k, _ in headers}
                extra = list(API_HEADERS)
                if not path.startswith("/api/docs"):
                    extra.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                if path.startswith("/api/"):
                    extra.append((b"cache-control", b"no-store"))
                headers.extend((k, v) for k, v in extra if k not in present)
                msg["headers"] = headers
            await send(msg)

        await self.app(scope, receive, with_headers)
