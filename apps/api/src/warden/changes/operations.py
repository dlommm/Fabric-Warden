"""The operations the change engine can perform, one class each.

An operation knows how to find its target, read the target's live state from
the controller, state the expected result, judge risk and impact, send the
write (with an authorization only the engine can mint), and decide from a
fresh read whether the write took effect. Only operations whose endpoint
and request body are in the published Network API spec are listed; each
names the capability that must be supported at the controller's version.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.integrations.unifi.client import UniFiNetworkClient, WriteAuthorization
from warden.models import Client, Controller, Device, FirewallPolicy, FirewallZone, Network, Port, Site


class ChangeRefused(Exception):
    """The change cannot be proposed or applied as asked; the message says why."""


@dataclass
class Target:
    controller: Controller
    site: Site
    type: str
    id: str
    external_id: str
    label: str
    is_demo: bool
    device: Device | None = None
    policy: FirewallPolicy | None = None
    port_idx: int | None = None
    # The state the engine re-read and checked for drift just before writing; full-object writes send this.
    current: dict[str, Any] | None = None


@dataclass
class Assessment:
    risk: str  # low | medium | high
    reasons: list[str]
    impact: list[str]
    protected: bool = False
    recovery: str = ""


@dataclass
class Check:
    """The outcome of one verification read. `done` False means keep waiting."""

    done: bool
    ok: bool = False
    observed: dict[str, Any] = field(default_factory=dict)
    note: str = ""


class Operation:
    id: ClassVar[str]
    capability: ClassVar[str]
    target_type: ClassVar[str]
    reversible: ClassVar[bool]
    # How long verification may take; zero means one read straight after the write.
    verify_window: ClassVar[timedelta] = timedelta(0)

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        raise NotImplementedError

    def title(self, t: Target, params: dict[str, Any]) -> str:
        raise NotImplementedError

    async def read(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        raise NotImplementedError

    def plan(self, before: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Expected state and the field-level diff. Raises ChangeRefused when nothing would change."""
        raise NotImplementedError

    def precondition(self, before: dict[str, Any]) -> str | None:
        """Why the change must not go ahead in this state, or None."""
        return None

    def drifted(self, before: dict[str, Any], now: dict[str, Any]) -> list[str]:
        """Fields that changed since the change was proposed (another change got there first)."""
        return [k for k in self.watched if before.get(k) != now.get(k)]

    watched: ClassVar[tuple[str, ...]] = ()

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        raise NotImplementedError

    async def execute(self, api: UniFiNetworkClient, t: Target, params: dict[str, Any], auth: WriteAuthorization) -> Any:
        raise NotImplementedError

    def check(self, before: dict[str, Any], expected: dict[str, Any], now: dict[str, Any], progress: dict[str, Any]) -> Check:
        raise NotImplementedError

    def inverse(self, params: dict[str, Any], before: dict[str, Any]) -> dict[str, Any] | None:
        return None

    async def restore(
        self, api: UniFiNetworkClient, t: Target, before: dict[str, Any], params: dict[str, Any], auth: WriteAuthorization
    ) -> Any:
        """Put the target back as it was before the change. Defaults to executing the inverse."""
        inverse = self.inverse(params, before)
        assert inverse is not None
        return await self.execute(api, t, inverse, auth)


async def _controller_site(db: AsyncSession, site_id: str) -> tuple[Controller, Site]:
    site = await db.get(Site, site_id)
    controller = await db.get(Controller, site.controller_id) if site else None
    if site is None or controller is None:
        raise ChangeRefused("The target's site or controller is gone")
    return controller, site


async def _user_policy(db: AsyncSession, target_id: str, target_type: str) -> Target:
    p = await db.get(FirewallPolicy, target_id)
    if p is None or not p.present:
        raise ChangeRefused("That firewall policy is not in the current inventory")
    if p.origin == "DERIVED":
        # UniFi derives these (the "(Return)" half of a rule) from a user rule and keeps them in step with it.
        raise ChangeRefused("UniFi derives this policy from another rule and keeps it in step; change that rule instead")
    if p.origin != "USER_DEFINED":
        # The published API does not say whether system policies accept edits, so Fabric Warden never tries.
        raise ChangeRefused("System-defined policies are managed by UniFi; Fabric Warden only changes user-defined ones")
    controller, site = await _controller_site(db, p.site_id)
    return Target(controller, site, target_type, p.id, p.external_id, p.name, p.is_demo, policy=p)


class SetPolicyLogging(Operation):
    id = "firewall_policy.set_logging"
    capability = "network.firewall.policies.logging"
    target_type = "firewall_policy"
    reversible = True
    watched = ("loggingEnabled",)

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        if not isinstance(params.get("enabled"), bool):
            raise ChangeRefused("Say whether logging should be on or off")
        return await _user_policy(db, target_id, self.target_type)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Turn {'on' if params['enabled'] else 'off'} logging for “{t.label}”"

    async def read(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        p = await api.firewall_policy(t.site.external_id, t.external_id)
        return {
            "loggingEnabled": p.get("loggingEnabled"),
            "enabled": p.get("enabled"),
            "name": p.get("name"),
            "action": (p.get("action") or {}).get("type"),
        }

    def plan(self, before: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if before.get("loggingEnabled") == params["enabled"]:
            raise ChangeRefused(f"Logging is already {'on' if params['enabled'] else 'off'} for this policy")
        return {"loggingEnabled": params["enabled"]}, [
            {"field": "Logging", "before": "On" if before.get("loggingEnabled") else "Off", "after": "On" if params["enabled"] else "Off"}
        ]

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        reasons = ["Changes only whether the gateway writes a log line when this policy matches; traffic is handled exactly as before."]
        impact = []
        if params["enabled"] and before.get("action") == "ALLOW":
            impact.append("Allowed connections are logged one line each, so a busy allow policy can add many log lines.")
        if params["enabled"]:
            impact.append("Matches start appearing under Logs and Connections once the gateway sends syslog to Fabric Warden.")
        else:
            impact.append("Connections analytics and SOC detections stop seeing this policy's matches.")
        return Assessment(
            "low", reasons, impact, recovery="Reversible: logging can be switched back, and a failed check reverts it automatically."
        )

    async def execute(self, api: UniFiNetworkClient, t: Target, params: dict[str, Any], auth: WriteAuthorization) -> Any:
        return await api.patch_firewall_policy_logging(t.site.external_id, t.external_id, params["enabled"], authorization=auth)

    def check(self, before: dict[str, Any], expected: dict[str, Any], now: dict[str, Any], progress: dict[str, Any]) -> Check:
        ok = now.get("loggingEnabled") == expected["loggingEnabled"]
        return Check(
            True,
            ok,
            {"loggingEnabled": now.get("loggingEnabled")},
            "" if ok else "The controller accepted the request but still reports the old value",
        )

    def inverse(self, params: dict[str, Any], before: dict[str, Any]) -> dict[str, Any] | None:
        return {"enabled": bool(before.get("loggingEnabled"))}


# Fields the controller manages: present in GET, absent from the published PUT schema (Network API 10.6.106).
POLICY_SERVER_FIELDS = ("id", "index", "metadata")
BLOCKISH = ("BLOCK", "REJECT")


def _put_body(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if k not in POLICY_SERVER_FIELDS}


def _others_changed(before: dict[str, Any], now: dict[str, Any], edited: str) -> list[str]:
    """Top-level fields, other than the edited one and server-managed ones, that no longer match."""
    keys = (set(before) | set(now)) - {edited, *POLICY_SERVER_FIELDS}
    return sorted(k for k in keys if before.get(k) != now.get(k))


class SetPolicyEnabled(Operation):
    """Enable or disable a policy with the published full-replacement PUT.

    Read-modify-write: the body is the object just read (minus server-managed fields) with
    only `enabled` changed. Verification re-reads the whole object: `enabled` must match and
    every other field must be exactly as before; otherwise the original object is put back.
    """

    id = "firewall_policy.set_enabled"
    capability = "network.firewall.policies.update"
    target_type = "firewall_policy"
    reversible = True

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        if not isinstance(params.get("enabled"), bool):
            raise ChangeRefused("Say whether the policy should be enabled or disabled")
        return await _user_policy(db, target_id, self.target_type)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"{'Enable' if params['enabled'] else 'Disable'} firewall policy “{t.label}”"

    async def read(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        return await api.firewall_policy(t.site.external_id, t.external_id)

    def drifted(self, before: dict[str, Any], now: dict[str, Any]) -> list[str]:
        keys = (set(before) | set(now)) - {"metadata"}
        return sorted(k for k in keys if before.get(k) != now.get(k))

    def plan(self, before: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if before.get("enabled") == params["enabled"]:
            raise ChangeRefused(f"The policy is already {'enabled' if params['enabled'] else 'disabled'}")
        return {"enabled": params["enabled"]}, [
            {
                "field": "Policy",
                "before": "Enabled" if before.get("enabled") else "Disabled",
                "after": "Enabled" if params["enabled"] else "Disabled",
            },
            {
                "field": "Everything else",
                "before": "As read from the controller",
                "after": "Unchanged (checked after the write)",
                "unchanged": True,
            },
        ]

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        action = (before.get("action") or {}).get("type")
        zones = {
            z.external_id: z.name
            for z in await db.scalars(select(FirewallZone).where(FirewallZone.site_id == t.site.id, FirewallZone.present.is_(True)))
        }
        src = zones.get((before.get("source") or {}).get("zoneId"), "unknown zone")
        dst = zones.get((before.get("destination") or {}).get("zoneId"), "unknown zone")
        pair = f"{src} → {dst}"
        enabling = params["enabled"]
        reasons: list[str] = []
        impact: list[str] = []
        risk, protected = "medium", False
        if not enabling and action in BLOCKISH:
            risk = "high"
            reasons.append(
                f"This policy blocks {pair} traffic. Disabled, that traffic is decided by later policies "
                "or the zone default, which may allow it."
            )
        elif enabling and action in BLOCKISH:
            reasons.append(f"Once enabled it blocks matching {pair} traffic, which may cut off something that works today.")
        elif not enabling:
            reasons.append(f"Traffic it allows ({pair}) is then decided by later policies or the zone default, which may block it.")
        else:
            reasons.append(f"Once enabled it allows matching {pair} traffic and may shadow policies after it.")
        if "Gateway" in (src, dst):
            risk, protected = "high", True
            reasons.append("It involves the Gateway zone: a mistake can cut off management access to the gateway and controller.")
        impact.append("Takes effect on the gateway within seconds of the controller provisioning it.")
        impact.append(
            "Sent as a full replacement of the policy: Fabric Warden sends back exactly what it read with only "
            "“enabled” changed, then checks that nothing else moved."
        )
        return Assessment(
            risk,
            reasons,
            impact,
            protected,
            (
                "Reversible: if the check finds anything else changed, the original policy is put back automatically; "
                "afterwards it can be reverted by a new change."
            ),
        )

    async def execute(self, api: UniFiNetworkClient, t: Target, params: dict[str, Any], auth: WriteAuthorization) -> Any:
        current = t.current
        if current is None:  # restore paths and direct calls read fresh
            current = await self.read(api, t)
        body = {**_put_body(current), "enabled": params["enabled"]}
        return await api.put_firewall_policy(t.site.external_id, t.external_id, body, authorization=auth)

    async def restore(
        self, api: UniFiNetworkClient, t: Target, before: dict[str, Any], params: dict[str, Any], auth: WriteAuthorization
    ) -> Any:
        return await api.put_firewall_policy(t.site.external_id, t.external_id, _put_body(before), authorization=auth)

    def check(self, before: dict[str, Any], expected: dict[str, Any], now: dict[str, Any], progress: dict[str, Any]) -> Check:
        moved = _others_changed(before, now, "enabled")
        observed = {"enabled": now.get("enabled"), "other_fields_changed": moved}
        if now.get("enabled") != expected["enabled"]:
            return Check(True, False, observed, "The controller accepted the request but still reports the old value")
        if moved:
            return Check(True, False, observed, f"The controller also changed {', '.join(moved)}")
        return Check(True, True, observed, "Enabled state as expected; every other field unchanged")

    def inverse(self, params: dict[str, Any], before: dict[str, Any]) -> dict[str, Any] | None:
        return {"enabled": bool(before.get("enabled"))}


async def _downstream(db: AsyncSession, d: Device) -> tuple[list[str], int]:
    """Devices whose uplink is this device, and clients attached to it."""
    devices = list(
        await db.scalars(
            select(Device.name).where(
                Device.site_id == d.site_id, Device.uplink_device_external_id == d.external_id, Device.present.is_(True)
            )
        )
    )
    clients = await db.scalar(
        select(func.count())
        .select_from(Client)
        .where(Client.site_id == d.site_id, Client.uplink_device_external_id == d.external_id, Client.present.is_(True))
    )
    return devices, clients or 0


class RestartDevice(Operation):
    id = "device.restart"
    capability = "network.devices.restart"
    target_type = "device"
    reversible = False
    verify_window = timedelta(minutes=10)
    watched = ("state",)

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        d = await db.get(Device, target_id)
        if d is None or not d.present:
            raise ChangeRefused("That device is not in the current inventory")
        controller, site = await _controller_site(db, d.site_id)
        return Target(controller, site, self.target_type, d.id, d.external_id, d.name, d.is_demo, device=d)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Restart {t.label}"

    async def read(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        d = await api.device(t.site.external_id, t.external_id)
        out = {"state": d.get("state"), "firmwareVersion": d.get("firmwareVersion")}
        if d.get("state") == "ONLINE":
            try:
                out["uptimeSec"] = (await api.device_stats(t.site.external_id, t.external_id)).get("uptimeSec")
            except Exception:  # statistics are optional evidence, not a precondition
                out["uptimeSec"] = None
        return out

    def plan(self, before: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return {"state": "ONLINE"}, [{"field": "State", "before": "Online", "after": "Restarts, then Online"}]

    def precondition(self, before: dict[str, Any]) -> str | None:
        if before.get("state") != "ONLINE":
            return f"The controller reports the device as {str(before.get('state')).lower().replace('_', ' ')}, not online"
        return None

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        d = t.device
        assert d is not None
        devices, clients = await _downstream(db, d)
        impact = [f"{d.name} is unavailable for one to three minutes while it restarts."]
        reasons: list[str] = []
        risk, protected = "medium", False
        if "gateway" in (d.features or []):
            risk, protected = "high", True
            reasons.append("This is the gateway: routing between networks, internet access and DNS stop during the restart.")
        if devices:
            risk, protected = "high", True
            n = len(devices)
            reasons.append(
                f"{n} device{'s' if n > 1 else ''} {'reach' if n > 1 else 'reaches'} the network through it: {', '.join(devices[:6])}."
            )
            impact.append(f"Everything behind {'those devices' if n > 1 else devices[0]} loses connectivity until it is back.")
        if clients:
            impact.append(f"{clients} client{'s' if clients > 1 else ''} attached to it disconnect{'s' if clients == 1 else ''}.")
        if not reasons:
            reasons.append("Only this device and what is attached to it are affected.")
        return Assessment(
            risk,
            reasons,
            impact,
            protected,
            (
                "Not reversible: a restart cannot be undone. If the device is not back online within 10 minutes "
                "the change is marked failed; check its power and uplink."
            ),
        )

    async def execute(self, api: UniFiNetworkClient, t: Target, params: dict[str, Any], auth: WriteAuthorization) -> Any:
        return await api.restart_device(t.site.external_id, t.external_id, authorization=auth)

    def check(self, before: dict[str, Any], expected: dict[str, Any], now: dict[str, Any], progress: dict[str, Any]) -> Check:
        # Proof of a restart: the device was seen not online, or its uptime went backwards; then it must be online again.
        if now.get("state") != "ONLINE":
            progress["saw_down"] = True
        rebooted = progress.get("saw_down") or (
            isinstance(now.get("uptimeSec"), int) and isinstance(before.get("uptimeSec"), int) and now["uptimeSec"] < before["uptimeSec"]
        )
        observed = {"state": now.get("state"), "uptimeSec": now.get("uptimeSec")}
        if rebooted and now.get("state") == "ONLINE":
            return Check(True, True, observed, "Restarted and back online")
        return Check(False, observed=observed, note="Restarting" if rebooted else "Waiting for the restart")


class PowerCyclePort(Operation):
    id = "port.power_cycle"
    capability = "network.ports.power_cycle"
    target_type = "port"
    reversible = False
    verify_window = timedelta(minutes=3)
    watched = ("poe",)

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        idx = params.get("port_idx")
        if not isinstance(idx, int):
            raise ChangeRefused("Say which port to power-cycle")
        d = await db.get(Device, target_id)
        if d is None or not d.present:
            raise ChangeRefused("That device is not in the current inventory")
        port = await db.scalar(select(Port).where(Port.device_id == d.id, Port.idx == idx))
        if port is None or not port.poe:
            raise ChangeRefused(f"Port {idx} on {d.name} does not supply PoE")
        controller, site = await _controller_site(db, d.site_id)
        return Target(controller, site, self.target_type, d.id, d.external_id, f"{d.name} port {idx}", d.is_demo, device=d, port_idx=idx)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Power-cycle PoE on {t.label}"

    async def read(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        d = await api.device(t.site.external_id, t.external_id)
        port = next((p for p in (d.get("interfaces") or {}).get("ports", []) if p.get("idx") == t.port_idx), None) or {}
        poe = port.get("poe") or {}
        return {"device_state": d.get("state"), "port_state": port.get("state"), "poe": poe.get("state"), "poe_enabled": poe.get("enabled")}

    def plan(self, before: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return {"poe": before.get("poe")}, [
            {
                "field": "PoE power",
                "before": str(before.get("poe") or "unknown").title(),
                "after": "Off for a few seconds, then " + str(before.get("poe") or "unknown").title(),
            }
        ]

    def precondition(self, before: dict[str, Any]) -> str | None:
        if before.get("device_state") != "ONLINE":
            return "The switch is not online"
        if not before.get("poe_enabled"):
            return "PoE is turned off on this port"
        return None

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        impact = [
            f"Whatever is powered by port {t.port_idx} loses power for a few seconds and reboots.",
            "The official API does not report which device is on a port, so Fabric Warden cannot name it.",
        ]
        reasons = ["Self-recovering: power comes back on its own."]
        risk, protected = "medium", False
        if t.device and "gateway" in (t.device.features or []):
            reasons.append("The port is on the gateway.")
        if before.get("poe") != "UP":
            reasons.append("The port is not delivering power right now, so the cycle may have no visible effect.")
        return Assessment(
            risk,
            reasons,
            impact,
            protected,
            "Not reversible, and not needed: PoE power returns by itself. If it does not within 3 minutes the change is marked failed.",
        )

    async def execute(self, api: UniFiNetworkClient, t: Target, params: dict[str, Any], auth: WriteAuthorization) -> Any:
        assert t.port_idx is not None
        return await api.power_cycle_port(t.site.external_id, t.external_id, t.port_idx, authorization=auth)

    def check(self, before: dict[str, Any], expected: dict[str, Any], now: dict[str, Any], progress: dict[str, Any]) -> Check:
        observed = {"poe": now.get("poe"), "port_state": now.get("port_state")}
        if now.get("poe") == expected.get("poe"):
            return Check(True, True, observed, "PoE is back to its previous state")
        return Check(False, observed=observed, note="Waiting for PoE to come back")


# -- 6c: generic checked read-modify-write for one field -----------------------------------


class FieldEdit(Operation):
    """Change one top-level field of an object updated by a full-replacement PUT.

    Sends the object exactly as just re-read (minus `server_fields`, which the published PUT
    schema does not list) with one field changed; verification requires that field to read
    back as expected and every other field to be unchanged, else the original is put back.
    """

    reversible = True
    server_fields: ClassVar[tuple[str, ...]] = ("id", "metadata")
    field: ClassVar[str]
    label: ClassVar[str]

    def new_value(self, params: dict[str, Any]) -> Any:
        return params["value"]

    def show(self, v: Any) -> str:
        return "On" if v is True else "Off" if v is False else str(v)

    async def fetch(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        raise NotImplementedError

    async def put(self, api: UniFiNetworkClient, t: Target, body: dict[str, Any], auth: WriteAuthorization) -> Any:
        raise NotImplementedError

    def body(self, obj: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in obj.items() if k not in self.server_fields}

    async def read(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        return await self.fetch(api, t)

    def drifted(self, before: dict[str, Any], now: dict[str, Any]) -> list[str]:
        return sorted(k for k in (set(before) | set(now)) - {"metadata"} if before.get(k) != now.get(k))

    def plan(self, before: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        new = self.new_value(params)
        if before.get(self.field) == new:
            raise ChangeRefused(f"{self.label} is already {self.show(new)}")
        return {self.field: new}, [
            {"field": self.label, "before": self.show(before.get(self.field)), "after": self.show(new)},
            {
                "field": "Everything else",
                "before": "As read from the controller",
                "after": "Unchanged (checked after the write)",
                "unchanged": True,
            },
        ]

    async def execute(self, api: UniFiNetworkClient, t: Target, params: dict[str, Any], auth: WriteAuthorization) -> Any:
        current = t.current if t.current is not None else await self.fetch(api, t)
        return await self.put(api, t, {**self.body(current), self.field: self.new_value(params)}, auth)

    async def restore(
        self, api: UniFiNetworkClient, t: Target, before: dict[str, Any], params: dict[str, Any], auth: WriteAuthorization
    ) -> Any:
        return await self.put(api, t, self.body(before), auth)

    def check(self, before: dict[str, Any], expected: dict[str, Any], now: dict[str, Any], progress: dict[str, Any]) -> Check:
        keys = (set(before) | set(now)) - {self.field, "metadata", *self.server_fields}
        moved = sorted(k for k in keys if before.get(k) != now.get(k))
        observed = {self.field: now.get(self.field), "other_fields_changed": moved}
        if now.get(self.field) != expected[self.field]:
            return Check(True, False, observed, "The controller accepted the request but still reports the old value")
        if moved:
            return Check(True, False, observed, f"The controller also changed {', '.join(moved)}")
        return Check(True, True, observed, f"{self.label} as expected; every other field unchanged")

    def inverse(self, params: dict[str, Any], before: dict[str, Any]) -> dict[str, Any] | None:
        return {**params, "value": before.get(self.field)}


def _text(params: dict[str, Any], what: str) -> str:
    v = params.get("value")
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > 64:
        raise ChangeRefused(f"Give the {what} a name of 1 to 64 characters")
    return v.strip()


def _flag(params: dict[str, Any]) -> bool:
    if not isinstance(params.get("value"), bool):
        raise ChangeRefused("Say whether it should be on or off")
    return params["value"]


class NetworkEdit(FieldEdit):
    target_type = "network"
    capability = "network.networks.update"
    server_fields = ("id", "default", "metadata")
    gateway_only: ClassVar[bool] = False

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        n = await db.get(Network, target_id)
        if n is None or not n.present:
            raise ChangeRefused("That network is not in the current inventory")
        if self.gateway_only and n.management != "GATEWAY":
            raise ChangeRefused("Only gateway-managed networks have this setting")
        controller, site = await _controller_site(db, n.site_id)
        self.new_value(params)  # validate early
        return Target(controller, site, self.target_type, n.id, n.external_id, n.name, n.is_demo)

    async def fetch(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        return await api.network(t.site.external_id, t.external_id)

    async def put(self, api: UniFiNetworkClient, t: Target, body: dict[str, Any], auth: WriteAuthorization) -> Any:
        return await api.put_network(t.site.external_id, t.external_id, body, authorization=auth)

    async def carries_controller(self, db: AsyncSession, t: Target, before: dict[str, Any]) -> bool:
        """The controller's own address is in this network's subnet (only knowable for an IP address URL)."""
        host = (t.controller.base_url.split("//", 1)[-1].split("/", 1)[0]).rsplit(":", 1)[0].strip("[]")
        cfg = before.get("ipv4Configuration") or {}
        try:
            net = ipaddress.ip_network(f"{cfg.get('hostIpAddress')}/{cfg.get('prefixLength')}", strict=False)
            return ipaddress.ip_address(host) in net
        except ValueError:
            return False

    async def common(self, db: AsyncSession, t: Target, before: dict[str, Any]) -> tuple[list[str], bool]:
        reasons: list[str] = []
        protected = False
        if before.get("default"):
            reasons.append("This is the site's default network.")
            protected = True
        if await self.carries_controller(db, t, before):
            reasons.append("The controller is reached through this network; a mistake can cut Fabric Warden off from it.")
            protected = True
        return reasons, protected


class RenameNetwork(NetworkEdit):
    id = "network.rename"
    field = "name"
    label = "Name"

    def new_value(self, params: dict[str, Any]) -> Any:
        return _text(params, "network")

    def show(self, v: Any) -> str:
        return f"“{v}”"

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Rename network “{t.label}” to “{self.new_value(params)}”"

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        return Assessment(
            "low",
            ["Only the label changes; addressing, VLAN and policies are untouched."],
            ["Fabric Warden pages, UniFi and anything that matches on the network's name see the new name."],
            recovery="Reversible: rename it back with a new change; a failed check restores the original automatically.",
        )


class SetInternetAccess(NetworkEdit):
    id = "network.set_internet_access"
    field = "internetAccessEnabled"
    label = "Internet access"
    gateway_only = True

    def new_value(self, params: dict[str, Any]) -> Any:
        return _flag(params)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"{'Allow' if self.new_value(params) else 'Block'} internet access for “{t.label}”"

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        reasons, protected = await self.common(db, t, before)
        on = self.new_value(params)
        risk = "high" if protected else "medium"
        if on:
            reasons.insert(0, "Devices on this network can reach the internet again, subject to firewall policies.")
        else:
            reasons.insert(
                0, "Every device on this network loses internet access; traffic to other local networks follows the firewall as before."
            )
        return Assessment(
            risk,
            reasons,
            ["Devices that phone home for updates, time or cloud control stop working while blocked."] if not on else [],
            protected,
            "Reversible: switch it back with a new change; a failed check restores the original automatically.",
        )


class SetIsolation(NetworkEdit):
    id = "network.set_isolation"
    field = "isolationEnabled"
    label = "Network isolation"
    gateway_only = True

    def new_value(self, params: dict[str, Any]) -> Any:
        return _flag(params)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Turn {'on' if self.new_value(params) else 'off'} isolation for “{t.label}”"

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        reasons, protected = await self.common(db, t, before)
        on = self.new_value(params)
        reasons.insert(
            0,
            "Devices on this network can no longer reach other local networks."
            if on
            else "Devices on this network can reach other local networks again, subject to firewall policies.",
        )
        return Assessment(
            "high" if protected or not on else "medium",
            reasons,
            [],
            protected,
            "Reversible: switch it back with a new change; a failed check restores the original automatically.",
        )


class MoveNetworkZone(NetworkEdit):
    id = "network.move_zone"
    field = "zoneId"
    label = "Firewall zone"
    gateway_only = True
    _zones: dict[str, str]

    def new_value(self, params: dict[str, Any]) -> Any:
        v = params.get("value")
        if not isinstance(v, str) or not v:
            raise ChangeRefused("Pick the zone to move the network to")
        return v

    def show(self, v: Any) -> str:
        return getattr(self, "_zones", {}).get(v, str(v))

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        t = await super().resolve(db, target_id, params)
        zones = list(await db.scalars(select(FirewallZone).where(FirewallZone.site_id == t.site.id, FirewallZone.present.is_(True))))
        self._zones = {z.external_id: z.name for z in zones}
        # The UI sends Fabric Warden's zone id; the controller needs its own.
        by_platform = {z.id: z.external_id for z in zones}
        if params["value"] in by_platform:
            params["value"] = by_platform[params["value"]]
        dest = next((z for z in zones if z.external_id == params["value"]), None)
        if dest is None:
            raise ChangeRefused("That zone is not on this network's site")
        if dest.name.lower() in ("external", "gateway", "vpn"):
            raise ChangeRefused(f"Networks cannot be moved into the {dest.name} zone")
        return t

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Move network “{t.label}” to zone {self.show(params['value'])}"

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        reasons, protected = await self.common(db, t, before)
        src, dst = self.show(before.get("zoneId")), self.show(params["value"])
        reasons.insert(0, f"Traffic to and from this network stops following the {src} zone's policies and follows {dst}'s instead.")
        return Assessment(
            "high",
            reasons,
            ["Check the zone matrix on the Firewall page for what each pair allows before approving."],
            protected,
            "Reversible: move it back with a new change; a failed check restores the original automatically.",
        )


class RenameZone(FieldEdit):
    id = "zone.rename"
    target_type = "zone"
    capability = "network.firewall.zones.update"
    field = "name"
    label = "Name"

    def new_value(self, params: dict[str, Any]) -> Any:
        return _text(params, "zone")

    def show(self, v: Any) -> str:
        return f"“{v}”"

    async def resolve(self, db: AsyncSession, target_id: str, params: dict[str, Any]) -> Target:
        z = await db.get(FirewallZone, target_id)
        if z is None or not z.present:
            raise ChangeRefused("That zone is not in the current inventory")
        if z.origin != "USER_DEFINED":
            raise ChangeRefused("Built-in zones are managed by UniFi; only custom zones can be renamed")
        self.new_value(params)
        controller, site = await _controller_site(db, z.site_id)
        return Target(controller, site, self.target_type, z.id, z.external_id, z.name, z.is_demo)

    def title(self, t: Target, params: dict[str, Any]) -> str:
        return f"Rename zone “{t.label}” to “{self.new_value(params)}”"

    async def fetch(self, api: UniFiNetworkClient, t: Target) -> dict[str, Any]:
        return await api.firewall_zone(t.site.external_id, t.external_id)

    async def put(self, api: UniFiNetworkClient, t: Target, body: dict[str, Any], auth: WriteAuthorization) -> Any:
        return await api.put_firewall_zone(t.site.external_id, t.external_id, body, authorization=auth)

    async def assess(self, db: AsyncSession, t: Target, before: dict[str, Any], params: dict[str, Any]) -> Assessment:
        return Assessment(
            "low",
            ["Only the label changes; member networks and policies are untouched."],
            [],
            recovery="Reversible: rename it back with a new change.",
        )


OPERATIONS: dict[str, Operation] = {
    op.id: op
    for op in (
        SetPolicyLogging(),
        SetPolicyEnabled(),
        RestartDevice(),
        PowerCyclePort(),
        RenameNetwork(),
        SetInternetAccess(),
        SetIsolation(),
        MoveNetworkZone(),
        RenameZone(),
    )
}
