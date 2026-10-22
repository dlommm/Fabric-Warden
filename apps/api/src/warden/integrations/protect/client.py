"""Read-only UniFi Protect Integration API collector.

Uses only the GET endpoints listed in official_api_matrix.json next to this
file (meta/info, cameras, nvrs). The HTTP wrapper is the hosts integration's
ReadOnlyHTTP, which has no method other than GET (plus GraphQL queries, which
this collector never calls), so no Protect setting can be changed from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from warden.integrations.hosts.clients import HostError, ReadOnlyHTTP

INTEGRATION_PATH = "/proxy/protect/integration"
CAMERA_STATES = ("CONNECTED", "CONNECTING", "DISCONNECTED")


def norm_mac(v: str | None) -> str | None:
    """aa:bb:cc:dd:ee:ff from any of AABBCCDDEEFF, aa-bb-..., aa:bb:... (Protect reports bare hex)."""
    if not v:
        return None
    h = re.sub(r"[^0-9a-f]", "", v.lower())
    if len(h) != 12 or h == "0" * 12:
        return None
    return ":".join(h[i : i + 2] for i in range(0, 12, 2))


def api_base(console_url: str) -> str:
    """The Integration API base for a console URL; accepts either form."""
    url = console_url.rstrip("/")
    return url if url.endswith(INTEGRATION_PATH) else url + INTEGRATION_PATH


def headers(api_key: str | None) -> dict[str, str]:
    return {"X-API-KEY": api_key} if api_key else {}


@dataclass
class ProtectItem:
    external_id: str
    kind: str  # camera | nvr
    name: str
    model_key: str
    type: str
    mac_address: str | None
    state: str
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProtectSnapshot:
    version: str
    items: list[ProtectItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _camera(c: dict[str, Any]) -> ProtectItem:
    flags = c.get("featureFlags") or {}
    smart = c.get("smartDetectSettings") or {}
    state = str(c.get("state") or "")
    return ProtectItem(
        external_id=str(c["id"]),
        kind="camera",
        name=c.get("name") or c.get("type") or str(c["id"]),
        model_key=str(c.get("modelKey") or "camera"),
        type=str(c.get("type") or ""),
        mac_address=norm_mac(c.get("mac")),
        state=state if state in CAMERA_STATES else (state or "UNKNOWN"),
        extra={
            "smart_detect_types": list(flags.get("smartDetectTypes") or []),
            "smart_detect_enabled": list(smart.get("objectTypes") or []),
            "has_mic": flags.get("hasMic"),
            "mic_enabled": c.get("isMicEnabled"),
            "has_speaker": flags.get("hasSpeaker"),
            "has_package_camera": c.get("hasPackageCamera"),
        },
        raw={k: c.get(k) for k in ("id", "modelKey", "state", "name", "type", "mac")},
    )


def _nvr(n: dict[str, Any]) -> ProtectItem:
    arm = n.get("armMode") or {}
    return ProtectItem(
        external_id=str(n["id"]),
        kind="nvr",
        name=n.get("name") or n.get("type") or "NVR",
        model_key=str(n.get("modelKey") or "nvr"),
        type=str(n.get("type") or ""),
        mac_address=norm_mac(n.get("mac")),
        state="",
        extra={"arm_status": arm.get("status"), "breach_event_count": arm.get("breachEventCount")},
        raw={k: n.get(k) for k in ("id", "modelKey", "name", "type", "mac")},
    )


async def collect_protect(api: ReadOnlyHTTP) -> ProtectSnapshot:
    info = (await api.get("/v1/meta/info")).json()
    if not isinstance(info, dict) or "applicationVersion" not in info:
        raise HostError("The answer is not a Protect Integration API (no applicationVersion)", kind="protocol")
    snap = ProtectSnapshot(version=str(info["applicationVersion"]))
    cams = (await api.get("/v1/cameras")).json()
    if not isinstance(cams, list):
        raise HostError("GET /v1/cameras did not return a list", kind="protocol")
    snap.items.extend(_camera(c) for c in cams[:500] if isinstance(c, dict) and c.get("id"))
    try:
        nvr = (await api.get("/v1/nvrs")).json()
    except HostError as e:
        if e.kind == "auth":
            raise
        snap.notes.append(f"NVR details unavailable: {e}")
        nvr = None
    # The published schema is one object; tolerate a list for robustness.
    for n in nvr if isinstance(nvr, list) else [nvr] if isinstance(nvr, dict) else []:
        if n.get("id"):
            snap.items.append(_nvr(n))
    return snap
