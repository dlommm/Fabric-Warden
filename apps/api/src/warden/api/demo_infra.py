"""Demo infrastructure: a labelled rack layout for the simulated home lab.

Only available while a demo controller exists. Every row it creates has
is_demo=True and can be removed in one call. Parts are seeded only for
modules that are enabled (cabling, power, assets, docs).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.api.deps import DB, Allowed, State, require_module
from warden.models import (
    Asset,
    Cable,
    CableEnd,
    Controller,
    Device,
    DocPage,
    DocVersion,
    Location,
    PowerFeed,
    PowerLink,
    Rack,
    RackItem,
    Site,
)
from warden.security.rbac import P

router = APIRouter(prefix="/api/dcim/demo", tags=["dcim"], dependencies=[Depends(require_module("dcim"))])

Editor = Allowed(P.DCIM_EDIT)


async def _demo_devices(db: AsyncSession) -> dict[str, Device]:
    return {d.name: d for d in await db.scalars(select(Device).where(Device.is_demo.is_(True), Device.present.is_(True)))}


NETWORK_OVERVIEW = """# Network overview

The lab hangs off [[device:Gateway]] with a 25G DAC to [[device:Core Aggregation]], which fans out
to the rack PoE switch and the office over single-mode fiber.

## Where things are

- Everything core lives in [[rack:Rack A]] in the utility closet.
- Server subnet: [[prefix:10.0.30.0/24|servers]] (adopt it in IPAM if this shows as unresolved).
- The office switch is fed by [[cable:C-0004]] through the fiber panel.

## Related

- [[page:power-outage-runbook]]
- [[page:office-fiber-run]]
"""

RUNBOOK_V1 = """# Power outage runbook

1. Check [[rack:Rack A]] UPS load on the Power page.
2. Shut down [[client:unraid-tower]] first; it has the longest shutdown.
"""

RUNBOOK_V2 = """# Power outage runbook

The UPS carries the whole rack. At current load the runtime estimate is short, so act early.

1. Check the UPS load and runtime estimate on the Power page.
2. Shut down [[client:unraid-tower]] first; it has the longest shutdown.
3. Shut down [[client:proxmox-01]] and [[client:proxmox-02]] (migrate nothing; both lose power).
4. Leave [[device:Gateway]], [[device:Core Aggregation]] and [[device:Rack PoE Switch]] up so cameras keep recording.
5. When power returns, confirm every device is back on the Topology page.

Both Proxmox hosts have dual supplies, but both supplies trace back to the same circuit:
see the shared-feed finding on the Power page.
"""

FIBER_RUN = """# Office fiber run

[[device:Office Switch]] uplinks over a single-mode run:

| Segment | Cable |
|---|---|
| Aggregation → fiber panel (rear) | [[cable:C-0003]] |
| Fiber panel (front) → office | [[cable:C-0004]] |

There is also a copper link from the rack PoE switch to the office switch ([[cable:C-0007]]) that the
controller does not report as an uplink. Either it is a standby link or it should be removed.
"""


@router.post("", status_code=status.HTTP_201_CREATED)
async def seed(db: DB, state: State, principal: Editor) -> dict:
    if not await db.scalar(select(Controller).where(Controller.is_demo.is_(True))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect the demo controller first; the demo layout links to its devices.")
    if await db.scalar(select(Rack).where(Rack.is_demo.is_(True))):
        raise HTTPException(status.HTTP_409_CONFLICT, "The demo layout already exists.")
    dev = await _demo_devices(db)
    if "Gateway" not in dev:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sync the demo controller first; its devices are not in the inventory yet.")
    today = date.today()
    site = await db.scalar(select(Site).where(Site.is_demo.is_(True)))
    created: dict[str, int] = {}

    def add(row: Any) -> Any:
        db.add(row)
        created[row.__tablename__] = created.get(row.__tablename__, 0) + 1
        return row

    lab = add(Location(name="Demo Home Lab", kind="site", site_id=site.id if site else None, is_demo=True))
    await db.flush()
    closet = add(
        Location(name="Utility closet", kind="room", parent_id=lab.id, description="Ground floor, behind the laundry.", is_demo=True)
    )
    await db.flush()
    rack = add(
        Rack(name="Rack A", location_id=closet.id, u_height=25, depth_mm=600, description="25U open frame, 600 mm deep.", is_demo=True)
    )
    await db.flush()

    def item(name: str, kind: str, pos: int | None, h: int = 1, **kw: Any) -> RackItem:
        return add(RackItem(rack_id=rack.id, name=name, kind=kind, position=pos, height_u=h, is_demo=True, **kw))

    pp = item("Patch panel A", "patch_panel", 25, port_count=24, connector="RJ45", full_depth=False)
    gw = item("Gateway", "device", 24, device_id=dev["Gateway"].id, power_draw_w=50)
    item("Cable manager", "cable_manager", 23, full_depth=False)
    agg = item("Core Aggregation", "device", 22, device_id=dev["Core Aggregation"].id, power_draw_w=30)
    poe = item("Rack PoE Switch", "device", 21, device_id=dev["Rack PoE Switch"].id, power_draw_w=60, notes="Draw excludes PoE load.")
    fp = item("Fiber panel", "patch_panel", 19, port_count=12, connector="LC", full_depth=False)
    unraid = item("unraid-tower", "server", 15, 4, power_draw_w=180, manufacturer="Generic", model="4U chassis")
    px1 = item("proxmox-01", "server", 12, 2, power_draw_w=140)
    px2 = item("proxmox-02", "server", 10, 2, power_draw_w=140)
    px3 = item("proxmox-03", "server", 8, 2, power_draw_w=160, status="planned", notes="Ordered; arrives next month.")
    item("Modem shelf", "shelf", 6)
    ups = item("UPS 1500", "ups", 1, 2, power_capacity_w=900, outlet_count=8, runtime_full_load_min=6, power_draw_w=15)
    pdu = item("PDU A", "pdu", None, face="rear", full_depth=False, power_capacity_w=3680, outlet_count=12)
    await db.flush()

    if await state.modules.is_enabled(db, "cabling"):

        def cable(label: str, type: str, a: dict[str, Any], b: dict[str, Any], **kw: Any) -> None:
            c = add(Cable(label=label, type=type, is_demo=True, **kw))
            created["dcim_cable_ends"] = created.get("dcim_cable_ends", 0) + 2
            pending.append((c, a, b))

        def port(name: str, idx: int) -> dict[str, Any]:
            return {"kind": "device_port", "device_id": dev[name].id, "port_idx": idx}

        def panel(p: RackItem, idx: int, side: str) -> dict[str, Any]:
            return {"kind": "panel_port", "item_id": p.id, "port_idx": idx, "side": side}

        pending: list[tuple[Cable, dict[str, Any], dict[str, Any]]] = []
        cable("C-0001", "dac", port("Gateway", 11), port("Core Aggregation", 25), length_m=0.5, color="black")
        cable("C-0002", "dac", port("Core Aggregation", 1), port("Rack PoE Switch", 17), length_m=0.5, color="black")
        cable("C-0003", "os2", port("Core Aggregation", 2), panel(fp, 1, "rear"), length_m=1, color="yellow")
        cable(
            "C-0004",
            "os2",
            panel(fp, 1, "front"),
            port("Office Switch", 25),
            length_m=35,
            color="yellow",
            description="In-wall run to the office.",
        )
        cable("C-0005", "cat6a", port("Rack PoE Switch", 1), panel(pp, 2, "rear"), length_m=1, color="blue")
        cable("C-0006", "cat6a", panel(pp, 2, "front"), port("AP Living Room", 1), length_m=18, color="blue")
        cable("C-0007", "cat6a", port("Rack PoE Switch", 11), panel(pp, 1, "rear"), length_m=1, color="grey")
        cable("C-0008", "cat6a", panel(pp, 1, "front"), port("Office Switch", 3), length_m=30, color="grey", description="Standby link?")
        cable("C-0009", "dac", port("Core Aggregation", 3), {"kind": "item", "item_id": unraid.id}, length_m=1, color="black")
        cable("C-0010", "dac", port("Core Aggregation", 7), {"kind": "item", "item_id": px3.id}, length_m=1, status="planned")
        await db.flush()
        for c, a, b in pending:
            for end, spec in (("a", a), ("b", b)):
                db.add(CableEnd(cable_id=c.id, end=end, **spec))

    if await state.modules.is_enabled(db, "power"):
        feed = add(PowerFeed(name="Circuit 12 (closet)", location_id=closet.id, voltage_v=230, amperage_a=16, is_demo=True))
        await db.flush()
        links = [
            dict(source_feed_id=feed.id, target_item_id=ups.id),
            dict(source_item_id=ups.id, source_outlet=1, target_item_id=pdu.id),
            dict(source_item_id=ups.id, source_outlet=2, target_item_id=gw.id),
            dict(source_item_id=ups.id, source_outlet=3, target_item_id=agg.id),
            dict(source_item_id=ups.id, source_outlet=4, target_item_id=poe.id),
            dict(source_item_id=ups.id, source_outlet=5, target_item_id=unraid.id),
            dict(source_item_id=ups.id, source_outlet=6, target_item_id=px1.id, target_input="A"),
            dict(source_item_id=ups.id, source_outlet=7, target_item_id=px2.id, target_input="A"),
            dict(source_item_id=pdu.id, source_outlet=1, target_item_id=px1.id, target_input="B"),
            dict(source_item_id=pdu.id, source_outlet=2, target_item_id=px2.id, target_input="B"),
            dict(source_item_id=pdu.id, source_outlet=3, target_item_id=px3.id, target_input="A"),
        ]
        for spec in links:
            add(PowerLink(is_demo=True, **spec))

    if await state.modules.is_enabled(db, "asset-lifecycle"):
        d = timedelta
        assets = [
            dict(
                name="UPS 1500",
                kind="hardware",
                rack_item_id=ups.id,
                vendor="Generic",
                model="1500 VA line-interactive",
                serial="DEMO-UPS-0001",
                purchase_date=today - d(days=3 * 365),
                purchase_cost=Decimal("389.00"),
                currency="USD",
                warranty_until=today - d(days=30),
                end_of_life=today + d(days=400),
            ),
            dict(
                name="Core Aggregation",
                kind="hardware",
                device_id=dev["Core Aggregation"].id,
                rack_item_id=agg.id,
                asset_tag="DEMO-0002",
                purchase_date=today - d(days=400),
                purchase_cost=Decimal("899.00"),
                currency="USD",
                warranty_until=today + d(days=330),
            ),
            dict(
                name="UPS replacement battery",
                kind="spare",
                status="spare",
                quantity=1,
                min_quantity=2,
                vendor="Generic",
                model="12 V 9 Ah",
                purchase_cost=Decimal("39.00"),
                currency="USD",
                location_id=closet.id,
            ),
            dict(
                name="10G SR optics",
                kind="spare",
                status="spare",
                quantity=4,
                min_quantity=2,
                model="SFP+ SR 850 nm",
                purchase_cost=Decimal("22.00"),
                currency="USD",
                location_id=closet.id,
            ),
            dict(
                name="Extended warranty: Gateway",
                kind="subscription",
                device_id=dev["Gateway"].id,
                expires_on=today + d(days=60),
                purchase_cost=Decimal("79.00"),
                currency="USD",
            ),
            dict(
                name="Hypervisor support subscription",
                kind="licence",
                seats=3,
                expires_on=today + d(days=200),
                auto_renew=True,
                purchase_cost=Decimal("330.00"),
                currency="USD",
                notes="Key is in the credential vault.",
            ),
            dict(
                name="proxmox-03",
                kind="hardware",
                status="ordered",
                rack_item_id=px3.id,
                order_ref="DEMO-PO-1042",
                supplier="Demo Supplier",
                purchase_cost=Decimal("1240.00"),
                currency="USD",
            ),
        ]
        for spec in assets:
            add(Asset(is_demo=True, **spec))

    if await state.modules.is_enabled(db, "docs"):
        who = principal.user.username
        overview = add(
            DocPage(
                slug="network-overview",
                title="Network overview",
                body=NETWORK_OVERVIEW,
                tags=["architecture"],
                updated_by=who,
                is_demo=True,
            )
        )
        runbook = add(
            DocPage(
                slug="power-outage-runbook",
                title="Power outage runbook",
                body=RUNBOOK_V2,
                tags=["runbook", "power"],
                version=2,
                updated_by=who,
                is_demo=True,
            )
        )
        await db.flush()
        fiber = add(
            DocPage(
                slug="office-fiber-run",
                title="Office fiber run",
                parent_id=overview.id,
                body=FIBER_RUN,
                tags=["cabling"],
                updated_by=who,
                is_demo=True,
            )
        )
        await db.flush()
        db.add_all(
            [
                DocVersion(page_id=overview.id, version=1, title=overview.title, body=overview.body, message="Created", author=who),
                DocVersion(page_id=runbook.id, version=1, title=runbook.title, body=RUNBOOK_V1, message="Created", author=who),
                DocVersion(
                    page_id=runbook.id,
                    version=2,
                    title=runbook.title,
                    body=RUNBOOK_V2,
                    message="Add shutdown order and shared-feed note",
                    author=who,
                ),
                DocVersion(page_id=fiber.id, version=1, title=fiber.title, body=fiber.body, message="Created", author=who),
            ]
        )

    await db.flush()
    await audit.record(db, principal.actor, "dcim.demo.seed", target_type="rack", target_id=rack.id, detail=created)
    return {"created": created, "rack_id": rack.id}


@router.delete("", status_code=status.HTTP_200_OK)
async def remove(db: DB, principal: Editor) -> dict:
    """Delete every demo infrastructure row; user-entered rows are untouched."""
    removed: dict[str, int] = {}
    # Child rows first: bulk deletes do not rely on the database cascading.
    await db.execute(delete(DocVersion).where(DocVersion.page_id.in_(select(DocPage.id).where(DocPage.is_demo.is_(True)))))
    await db.execute(delete(CableEnd).where(CableEnd.cable_id.in_(select(Cable.id).where(Cable.is_demo.is_(True)))))
    for model in (DocPage, Asset, PowerLink, PowerFeed, Cable, RackItem, Rack, Location):
        if model is Location:
            # Children before parents.
            while ids := [
                x.id
                for x in await db.scalars(select(Location).where(Location.is_demo.is_(True)))
                if not await db.scalar(select(Location.id).where(Location.parent_id == x.id).limit(1))
            ]:
                res = await db.execute(delete(Location).where(Location.id.in_(ids)))
                removed[Location.__tablename__] = removed.get(Location.__tablename__, 0) + (res.rowcount or 0)
            continue
        res = await db.execute(delete(model).where(model.is_demo.is_(True)))
        removed[model.__tablename__] = res.rowcount or 0
    await audit.record(db, principal.actor, "dcim.demo.remove", detail=removed)
    return {"removed": removed}
