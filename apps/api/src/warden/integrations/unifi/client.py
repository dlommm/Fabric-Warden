"""Client for the official UniFi Network Integration API.

Read-only by construction: any non-GET request needs a WriteAuthorization,
which only the change engine may issue. Until the change engine ships
(roadmap phase 6), nothing in the platform can construct one.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_PATH = "/proxy/network/integration"
PAGE_LIMIT = 200


class UniFiError(Exception):
    def __init__(self, message: str, *, status: int | None = None, kind: str = "error"):
        super().__init__(message)
        self.status = status
        self.kind = kind  # auth | not_found | unreachable | tls | protocol | error


class WriteNotAuthorized(UniFiError):
    pass


@dataclass(frozen=True)
class WriteAuthorization:
    change_id: str
    _issuer: object

    def __post_init__(self) -> None:
        from warden.integrations.unifi import _write_gate

        if self._issuer is not _write_gate.ISSUER:
            raise WriteNotAuthorized("write authorizations can only be issued by the change engine")


class UniFiNetworkClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        verify_tls: bool = True,
        ca_bundle_pem: str | None = None,
        base_path: str = DEFAULT_BASE_PATH,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        verify: ssl.SSLContext | bool = verify_tls
        if verify_tls and ca_bundle_pem:
            verify = ssl.create_default_context(cadata=ca_bundle_pem)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + base_path,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            verify=verify,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> UniFiNetworkClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        authorization: WriteAuthorization | None = None,
    ) -> Any:
        if method != "GET" and not isinstance(authorization, WriteAuthorization):
            raise WriteNotAuthorized(f"{method} {path} refused: controller writes require the change engine")
        try:
            resp = await self._http.request(method, path, params=params, json=json)
        except httpx.ConnectError as exc:
            kind = "tls" if "SSL" in str(exc) or "certificate" in str(exc).lower() else "unreachable"
            raise UniFiError(f"cannot connect to controller: {exc}", kind=kind) from exc
        except httpx.TimeoutException as exc:
            raise UniFiError("controller request timed out", kind="unreachable") from exc
        if resp.status_code in (401, 403):
            raise UniFiError("controller rejected the API key", status=resp.status_code, kind="auth")
        if resp.status_code == 404:
            raise UniFiError(f"{path} not found on controller", status=404, kind="not_found")
        if resp.status_code >= 400:
            raise UniFiError(f"controller returned HTTP {resp.status_code}", status=resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise UniFiError("controller returned a non-JSON response", kind="protocol") from exc

    async def get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params=params or None)

    async def paginate(self, path: str) -> AsyncIterator[dict[str, Any]]:
        offset = 0
        while True:
            page = await self.get(path, offset=offset, limit=PAGE_LIMIT)
            data = page.get("data", [])
            for item in data:
                yield item
            offset += len(data)
            total = page.get("totalCount", offset)
            if not data or offset >= total:
                return

    async def collect(self, path: str) -> list[dict[str, Any]]:
        return [item async for item in self.paginate(path)]

    # Typed read helpers -------------------------------------------------

    async def info(self) -> dict[str, Any]:
        return await self.get("/v1/info")

    async def sites(self) -> list[dict[str, Any]]:
        return await self.collect("/v1/sites")

    async def devices(self, site_id: str) -> list[dict[str, Any]]:
        return await self.collect(f"/v1/sites/{site_id}/devices")

    async def device(self, site_id: str, device_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sites/{site_id}/devices/{device_id}")

    async def device_stats(self, site_id: str, device_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sites/{site_id}/devices/{device_id}/statistics/latest")

    async def clients(self, site_id: str) -> list[dict[str, Any]]:
        return await self.collect(f"/v1/sites/{site_id}/clients")

    async def networks(self, site_id: str) -> list[dict[str, Any]]:
        return await self.collect(f"/v1/sites/{site_id}/networks")

    async def network(self, site_id: str, network_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sites/{site_id}/networks/{network_id}")

    async def wans(self, site_id: str) -> list[dict[str, Any]]:
        return await self.collect(f"/v1/sites/{site_id}/wans")

    async def firewall_zones(self, site_id: str) -> list[dict[str, Any]]:
        return await self.collect(f"/v1/sites/{site_id}/firewall/zones")

    async def firewall_policies(self, site_id: str) -> list[dict[str, Any]]:
        return await self.collect(f"/v1/sites/{site_id}/firewall/policies")
