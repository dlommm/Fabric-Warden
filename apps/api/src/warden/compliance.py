"""Built-in compliance controls, each evaluated against the platform's own data.

A control returns pass, fail or unknown (never a guess) with the evidence it
looked at. Controls that depend on a disabled module return unknown and say
which module would answer them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit, config_history, firewall_analysis
from warden.models import ConfigSnapshot, Controller, Device, FirewallZone, Host, RoleAssignment, Site, User

if TYPE_CHECKING:
    from warden.app_state import AppState

Result = tuple[str, list[dict[str, Any]], str]  # status, evidence, detail


@dataclass(frozen=True)
class Control:
    id: str
    title: str
    category: str
    rationale: str
    check: Callable[[AsyncSession, AppState], Awaitable[Result]]


def _zone(zones: list[FirewallZone], *names: str) -> FirewallZone | None:
    return next((z for z in zones if z.name.lower() in names), None)


async def _mfa(db: AsyncSession, state: AppState) -> Result:
    rows = (
        await db.execute(
            select(User, RoleAssignment.role).join(RoleAssignment, RoleAssignment.user_id == User.id).where(User.is_active.is_(True))
        )
    ).all()
    privileged = {u.id: u for u, role in rows if role in ("owner", "admin")}
    missing = [u for u in privileged.values() if not u.mfa_enabled]
    ev = [{"object": f"user {u.username}", "fact": "MFA on" if u.mfa_enabled else "MFA off"} for u in privileged.values()]
    return (
        "fail" if missing else "pass",
        ev,
        f"{len(missing)} of {len(privileged)} owners and admins have no MFA" if missing else "Every owner and admin uses MFA",
    )


async def _isolation(db: AsyncSession, src_names: tuple[str, ...], dst_names: tuple[str, ...], label: str) -> Result:
    analyses = await firewall_analysis.analyse(db)
    if not analyses:
        return ("unknown", [], "No site has been synced")
    ev: list[dict[str, Any]] = []
    status = "pass"
    for sa in analyses:
        src, dst = _zone(sa.zones, *src_names), _zone(sa.zones, *dst_names)
        if not src or not dst:
            ev.append({"object": f"site {sa.site.name}", "fact": f"no {label} zone pair"})
            status = "unknown" if status == "pass" else status
            continue
        first = next(
            (
                v
                for v in sa.policies
                if v.p.source_zone_external_id == src.external_id and v.p.destination_zone_external_id == dst.external_id and v.match_all
            ),
            None,
        )
        if first and first.p.action in firewall_analysis.BLOCKISH:
            ev.append(
                {"object": f"policy {first.p.name}", "fact": f"{src.name} → {dst.name} {first.p.action.lower()} (first match-all policy)"}
            )
        else:
            ev.append(
                {
                    "object": f"site {sa.site.name}",
                    "fact": f"{src.name} → {dst.name}: "
                    + (f"first match-all policy allows ({first.p.name})" if first else "no blocking policy"),
                }
            )
            status = "fail"
    return (
        status,
        ev,
        {
            "pass": f"{label} traffic is blocked by policy",
            "fail": f"{label} traffic is not blocked",
            "unknown": "The zones this control needs are not present",
        }[status],
    )


async def _guest(db: AsyncSession, state: AppState) -> Result:
    return await _isolation(db, ("hotspot", "guest"), ("internal",), "Guest to internal")


async def _iot(db: AsyncSession, state: AppState) -> Result:
    return await _isolation(db, ("iot",), ("internal",), "IoT to internal")


async def _fw_hygiene(db: AsyncSession, state: AppState) -> Result:
    found = [f for f in await firewall_analysis.findings(db) if f["kind"] in ("shadowed", "permissive_inbound")]
    return (
        "fail" if found else "pass",
        [{"object": "policy", "fact": f["title"]} for f in found],
        f"{len(found)} shadowed or overly permissive polic{'y' if len(found) == 1 else 'ies'}"
        if found
        else "No shadowed or permissive inbound policies",
    )


async def _fw_logging(db: AsyncSession, state: AppState) -> Result:
    found = [f for f in await firewall_analysis.findings(db) if f["kind"] == "block_without_logging"]
    return (
        "fail" if found else "pass",
        [{"object": "policy", "fact": f["title"]} for f in found],
        f"{len(found)} blocking policies do not log" if found else "Every blocking policy logs",
    )


async def _firmware(db: AsyncSession, state: AppState) -> Result:
    devices = list(await db.scalars(select(Device).where(Device.present.is_(True))))
    if not devices:
        return ("unknown", [], "No devices synced")
    old = [d for d in devices if d.firmware_updatable]
    return (
        "fail" if old else "pass",
        [{"object": f"device {d.name}", "fact": f"firmware {d.firmware_version}, update available"} for d in old],
        f"{len(old)} of {len(devices)} devices have a firmware update"
        if old
        else "All devices run the latest firmware the controller offers",
    )


async def _online(db: AsyncSession, state: AppState) -> Result:
    devices = list(await db.scalars(select(Device).where(Device.present.is_(True))))
    if not devices:
        return ("unknown", [], "No devices synced")
    off = [d for d in devices if d.state != "ONLINE"]
    return (
        "fail" if off else "pass",
        [{"object": f"device {d.name}", "fact": d.state.lower().replace("_", " ")} for d in off],
        f"{len(off)} devices are not online" if off else "Every adopted device is online",
    )


async def _tls(db: AsyncSession, state: AppState) -> Result:
    real = list(await db.scalars(select(Controller).where(Controller.is_demo.is_(False))))
    if not real:
        return ("unknown", [], "Only the demo controller is connected")
    bad = [c for c in real if not c.verify_tls]
    return (
        "fail" if bad else "pass",
        [{"object": f"controller {c.name}", "fact": "TLS verified" if c.verify_tls else "TLS verification off"} for c in real],
        f"{len(bad)} controllers skip TLS verification" if bad else "Every controller connection verifies TLS",
    )


async def _drift(db: AsyncSession, state: AppState) -> Result:
    if not await state.modules.is_enabled(db, "config-history"):
        return ("unknown", [], "Enable Configuration History to check drift")
    ev, drifted = [], 0
    for site in await db.scalars(select(Site).where(Site.present.is_(True))):
        base = await db.scalar(
            select(ConfigSnapshot)
            .where(ConfigSnapshot.site_id == site.id, ConfigSnapshot.is_baseline.is_(True))
            .order_by(ConfigSnapshot.taken_at.desc())
            .limit(1)
        )
        last = await db.scalar(
            select(ConfigSnapshot).where(ConfigSnapshot.site_id == site.id).order_by(ConfigSnapshot.taken_at.desc()).limit(1)
        )
        if not base or not last:
            continue
        changes = config_history.counts(config_history.diff(base.content, last.content))
        n = sum(sum(v.values()) for v in changes.values())
        drifted += bool(n)
        ev.append(
            {
                "object": f"site {site.name}",
                "fact": f"{n} change{'s' if n != 1 else ''} since the baseline of {base.taken_at:%Y-%m-%d %H:%M}",
            }
        )
    if not ev:
        return ("unknown", [], "No snapshots yet")
    return (
        "fail" if drifted else "pass",
        ev,
        f"{drifted} sites drifted from their baseline" if drifted else "Configuration matches the baseline",
    )


async def _servers(db: AsyncSession, state: AppState) -> Result:
    if not await state.modules.is_enabled(db, "servers"):
        return ("unknown", [], "Enable Servers & Virtualization to check this")
    hosts = list(await db.scalars(select(Host).where(Host.present.is_(True))))
    if not hosts:
        return ("unknown", [], "No servers connected")
    unmatched = [h for h in hosts if h.client_id is None]
    return (
        "fail" if unmatched else "pass",
        [
            {"object": f"host {h.name}", "fact": "not seen by the network" if not h.client_id else f"matched by {h.match_reason}"}
            for h in hosts
        ],
        f"{len(unmatched)} servers are unknown to the network" if unmatched else "Every server is a known network client",
    )


async def _audit(db: AsyncSession, state: AppState) -> Result:
    ok, bad = await audit.verify_chain(db)
    return (
        "pass" if ok else "fail",
        [{"object": "audit trail", "fact": "hash chain intact" if ok else f"chain breaks at record {bad}"}],
        "The audit trail is tamper-evident and intact" if ok else "The audit trail hash chain is broken",
    )


CONTROLS: list[Control] = [
    Control(
        "access.mfa_privileged",
        "Owners and admins use MFA",
        "Access",
        "A stolen password alone should not grant control of the network.",
        _mfa,
    ),
    Control(
        "platform.audit_intact",
        "Audit trail is intact",
        "Access",
        "The record of who changed what must not be editable after the fact.",
        _audit,
    ),
    Control(
        "platform.controller_tls",
        "Controller connections verify TLS",
        "Access",
        "An unverified connection can be intercepted and its API key captured.",
        _tls,
    ),
    Control(
        "segmentation.guest_isolated", "Guest network cannot reach internal", "Segmentation", "Visitors' devices are untrusted.", _guest
    ),
    Control(
        "segmentation.iot_isolated",
        "IoT cannot reach internal",
        "Segmentation",
        "IoT devices are rarely patched and should not reach workstations or servers.",
        _iot,
    ),
    Control(
        "firewall.hygiene",
        "No shadowed or permissive inbound policies",
        "Firewall",
        "A policy that never matches hides intent; an allow-all from External exposes the network.",
        _fw_hygiene,
    ),
    Control(
        "firewall.logging",
        "Blocking policies log",
        "Firewall",
        "Without logs, blocked attacks and misconfigurations leave no evidence.",
        _fw_logging,
    ),
    Control("devices.firmware_current", "Devices run current firmware", "Devices", "Firmware updates carry security fixes.", _firmware),
    Control(
        "devices.online", "All devices are online", "Devices", "An offline device is either broken or no longer under management.", _online
    ),
    Control(
        "config.no_drift", "Configuration matches the baseline", "Change", "Unreviewed change is the most common cause of outages.", _drift
    ),
    Control(
        "servers.known_to_network",
        "Every server is a known network client",
        "Inventory",
        "A server the network does not see may be on an undocumented path.",
        _servers,
    ),
]
BY_ID = {c.id: c for c in CONTROLS}
