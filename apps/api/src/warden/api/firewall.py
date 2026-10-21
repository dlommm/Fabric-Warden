"""Firewall & Policy Manager: ordered policies with analysis, the zone matrix and findings. Read-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from warden import firewall_analysis as fa
from warden.api.deps import DB, Allowed, require_module
from warden.security.rbac import P

router = APIRouter(prefix="/api/firewall", tags=["firewall"], dependencies=[Depends(require_module("firewall"))])

Reader = Allowed(P.INVENTORY_READ)


@router.get("")
async def overview(db: DB, principal: Reader) -> dict:
    analyses = await fa.analyse(db)
    sites = []
    for sa in analyses:
        names = {v.p.id: v.p.name for v in sa.policies + sa.unordered + sa.disabled}
        sites.append(
            {
                "site": {"id": sa.site.id, "name": sa.site.name, "is_demo": sa.site.is_demo},
                "zones": [
                    {"id": z.id, "name": z.name, "networks": len(z.network_external_ids or []), "origin": z.origin} for z in sa.zones
                ],
                "policies": [fa.policy_out(v, names) for v in sa.policies],
                "system_policies": [fa.policy_out(v, names) for v in sa.unordered],
                "disabled": [fa.policy_out(v, names) for v in sa.disabled],
                "matrix": fa.matrix(sa),
            }
        )
    return {
        "sites": sites,
        "findings": await fa.findings(db, analyses),
        "notes": [
            "Order is the policy index the controller reports; system-defined policies have no index, so their position is unknown.",
            "Coverage is only claimed when certain: same zones and either no traffic filter or identical filters. "
            "Other overlaps are marked as not determined.",
        ],
    }
