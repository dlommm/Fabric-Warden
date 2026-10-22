"""Targets from the observed inventory that a many-target operation could apply to.

Used to pick targets for a bulk operation or a fixed automation target. The
engine still checks each one live when it is proposed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.models import Device, FirewallPolicy, Network, Port

# Operations offered over many targets (and as automation actions). Renames and zone moves are one-off edits.
MANY_TARGET_OPERATIONS = (
    "port.power_cycle",
    "device.restart",
    "firewall_policy.set_logging",
    "firewall_policy.set_enabled",
    "network.set_internet_access",
    "network.set_isolation",
)


async def candidates(db: AsyncSession, operation: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if operation == "port.power_cycle":
        q = select(Port, Device).join(Device, Port.device_id == Device.id).where(Device.present.is_(True)).order_by(Device.name, Port.idx)
        for port, d in (await db.execute(q)).all():
            if not port.poe or not port.poe.get("enabled", True):
                continue
            out.append(
                {
                    "target_id": d.id,
                    "params": {"port_idx": port.idx},
                    "label": f"{d.name} port {port.idx}",
                    "group": d.name,
                    "detail": f"PoE {str(port.poe.get('state') or 'unknown').lower()} · link {port.state.lower()}",
                    "ready": d.state == "ONLINE",
                    "is_demo": d.is_demo,
                }
            )
    elif operation == "device.restart":
        for d in await db.scalars(select(Device).where(Device.present.is_(True)).order_by(Device.name)):
            features = d.features or []
            out.append(
                {
                    "target_id": d.id,
                    "params": {},
                    "label": d.name,
                    "group": "Gateways"
                    if "gateway" in features
                    else "Access points"
                    if "accessPoint" in features
                    else "Switches and other",
                    "detail": f"{d.model} · {d.state.lower().replace('_', ' ')}",
                    "ready": d.state == "ONLINE",
                    "is_demo": d.is_demo,
                }
            )
    elif operation.startswith("firewall_policy."):
        q = select(FirewallPolicy).where(FirewallPolicy.present.is_(True), FirewallPolicy.origin == "USER_DEFINED")
        for p in await db.scalars(q.order_by(FirewallPolicy.index)):
            out.append(
                {
                    "target_id": p.id,
                    "params": {},
                    "label": p.name,
                    "group": p.action.title(),
                    "detail": f"{'enabled' if p.enabled else 'disabled'} · logging {'on' if p.logging_enabled else 'off'}",
                    "ready": True,
                    "is_demo": p.is_demo,
                }
            )
    elif operation.startswith("network."):
        q = select(Network).where(Network.present.is_(True))
        if operation == "network.set_internet_access":
            q = q.where(Network.management == "GATEWAY")
        for n in await db.scalars(q.order_by(Network.vlan_id)):
            out.append(
                {
                    "target_id": n.id,
                    "params": {},
                    "label": n.name,
                    "group": n.management.title(),
                    "detail": f"VLAN {n.vlan_id}" if n.vlan_id is not None else "untagged",
                    "ready": True,
                    "is_demo": n.is_demo,
                }
            )
    return out
