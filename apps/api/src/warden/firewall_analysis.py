"""Zone-based firewall policy analysis over the policies the controller reports.

Policies are evaluated in `index` order; the first match wins. Analysis is
deliberately conservative: an earlier policy is said to *cover* a later one
only when that is certain from the data, meaning same source and destination
zone, a protocol scope at least as wide, and either no traffic filter at all
(match-all) or exactly the same filters. Anything else (two different port
lists, an IP set against a network) is reported as *not determined* rather than
guessed. System-defined policies carry no index in the API, so their place in
the order is unknown and they are left out of ordering analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden.models import FirewallPolicy, FirewallZone, Network, Site

ALLOWISH = {"ALLOW"}
BLOCKISH = {"BLOCK", "REJECT"}
SCOPE_RANK = {"IPV4": 1, "IPV6": 1, "IPV4_AND_IPV6": 2}
ACRONYMS = {"dmz", "vpn", "iot", "wan", "lan"}


def zone_label(name: str) -> str:
    return ("IoT" if name.lower() == "iot" else name.upper()) if name.lower() in ACRONYMS else name


@dataclass
class PolicyView:
    p: FirewallPolicy
    src: str
    dst: str
    src_filter: dict[str, Any] | None
    dst_filter: dict[str, Any] | None
    scope: str | None
    states: frozenset[str]
    analysis: str = "exact"  # exact | not_determined | unordered
    covered_by: str | None = None
    relation: str | None = None  # shadowed | redundant
    overlaps: list[str] = field(default_factory=list)

    @property
    def match_all(self) -> bool:
        return self.src_filter is None and self.dst_filter is None


def _view(p: FirewallPolicy, zones: dict[str, FirewallZone]) -> PolicyView:
    raw = p.raw or {}
    name = lambda ext: zones[ext].name if ext in zones else (ext or "?")  # noqa: E731
    return PolicyView(
        p=p,
        src=name(p.source_zone_external_id),
        dst=name(p.destination_zone_external_id),
        src_filter=(raw.get("source") or {}).get("trafficFilter"),
        dst_filter=(raw.get("destination") or {}).get("trafficFilter"),
        scope=(raw.get("ipProtocolScope") or {}).get("ipVersion"),
        states=frozenset(raw.get("connectionStateFilter") or []),
    )


def covers(a: PolicyView, b: PolicyView) -> bool | None:
    """True if `a` matches everything `b` matches; False if certainly not; None if the data cannot tell."""
    if (a.p.source_zone_external_id, a.p.destination_zone_external_id) != (b.p.source_zone_external_id, b.p.destination_zone_external_id):
        return False
    if a.scope and b.scope and a.scope != b.scope and SCOPE_RANK.get(a.scope, 0) < 2:
        return False
    if a.states and (not b.states or not b.states <= a.states):
        return False
    if a.match_all:
        return True
    if (a.src_filter, a.dst_filter) == (b.src_filter, b.dst_filter):
        return True
    return None


@dataclass
class SiteAnalysis:
    site: Site
    zones: list[FirewallZone]
    policies: list[PolicyView]
    unordered: list[PolicyView]
    disabled: list[PolicyView]


async def analyse(db: AsyncSession) -> list[SiteAnalysis]:
    out = []
    for site in await db.scalars(select(Site).where(Site.present.is_(True))):
        zones = {
            z.external_id: z
            for z in await db.scalars(select(FirewallZone).where(FirewallZone.site_id == site.id, FirewallZone.present.is_(True)))
        }
        all_p = [
            _view(p, zones)
            for p in await db.scalars(select(FirewallPolicy).where(FirewallPolicy.site_id == site.id, FirewallPolicy.present.is_(True)))
        ]
        disabled = [v for v in all_p if not v.p.enabled]
        unordered = [v for v in all_p if v.p.enabled and v.p.index is None]
        for v in unordered:
            v.analysis = "unordered"
        ordered = sorted((v for v in all_p if v.p.enabled and v.p.index is not None), key=lambda v: v.p.index)
        for i, b in enumerate(ordered):
            for a in ordered[:i]:
                c = covers(a, b)
                if c:
                    b.covered_by = a.p.id
                    b.relation = "redundant" if a.p.action == b.p.action else "shadowed"
                    break
                if c is None:
                    b.overlaps.append(a.p.id)
            if b.covered_by is None and b.overlaps:
                b.analysis = "not_determined"
        out.append(
            SiteAnalysis(
                site=site, zones=sorted(zones.values(), key=lambda z: z.name), policies=ordered, unordered=unordered, disabled=disabled
            )
        )
    return out


def matrix(sa: SiteAnalysis) -> list[dict[str, Any]]:
    """For each zone pair: the first enabled match-all policy (what unfiltered traffic gets).

    Also counts the filtered policies that sit in front of it.
    """
    rows = []
    for src in sa.zones:
        for dst in sa.zones:
            pair = [
                v
                for v in sa.policies
                if v.p.source_zone_external_id == src.external_id and v.p.destination_zone_external_id == dst.external_id
            ]
            first_all = next((v for v in pair if v.match_all), None)
            filtered = [v for v in pair if not v.match_all and (first_all is None or v.p.index < first_all.p.index)]
            system = [
                v
                for v in sa.unordered
                if v.p.source_zone_external_id == src.external_id and v.p.destination_zone_external_id == dst.external_id
            ]
            if not pair and not system:
                continue
            rows.append(
                {
                    "source": src.name,
                    "destination": dst.name,
                    "action": first_all.p.action if first_all else None,
                    "policy": {"id": first_all.p.id, "name": first_all.p.name} if first_all else None,
                    "filtered_before": len(filtered),
                    "system_policies": len(system),
                }
            )
    return rows


async def findings(db: AsyncSession, analyses: list[SiteAnalysis] | None = None) -> list[dict[str, Any]]:
    analyses = analyses if analyses is not None else await analyse(db)
    out: list[dict[str, Any]] = []
    for sa in analyses:
        by_id = {v.p.id: v for v in sa.policies}
        for v in sa.policies:
            if v.relation == "shadowed":
                a = by_id[v.covered_by]  # type: ignore[index]
                out.append(
                    {
                        "key": f"shadowed:{v.p.id}",
                        "kind": "shadowed",
                        "severity": "warning",
                        "title": f"“{v.p.name}” never matches: “{a.p.name}” comes first and {a.p.action.lower()}s the same traffic",
                        "detail": f"{v.src} → {v.dst}. Move it above “{a.p.name}” or remove it.",
                        "policy_id": v.p.id,
                        "subject": v.p.name,
                    }
                )
            elif v.relation == "redundant":
                a = by_id[v.covered_by]  # type: ignore[index]
                out.append(
                    {
                        "key": f"redundant:{v.p.id}",
                        "kind": "redundant",
                        "severity": "info",
                        "title": f"“{v.p.name}” is redundant: “{a.p.name}” already {a.p.action.lower()}s the same traffic",
                        "detail": f"{v.src} → {v.dst}.",
                        "policy_id": v.p.id,
                        "subject": v.p.name,
                    }
                )
            if v.p.action in BLOCKISH and not v.p.logging_enabled and v.p.origin == "USER_DEFINED":
                out.append(
                    {
                        "key": f"nolog:{v.p.id}",
                        "kind": "block_without_logging",
                        "severity": "info",
                        "title": f"“{v.p.name}” blocks without logging",
                        "detail": "Blocked traffic leaves no trace to investigate.",
                        "policy_id": v.p.id,
                        "subject": v.p.name,
                    }
                )
            if v.p.action in ALLOWISH and v.match_all and v.src.lower() == "external" and v.dst.lower() not in ("external",):
                out.append(
                    {
                        "key": f"inbound:{v.p.id}",
                        "kind": "permissive_inbound",
                        "severity": "warning",
                        "title": f"“{v.p.name}” allows all traffic from External to {v.dst}",
                        "detail": "No port, address or application filter.",
                        "policy_id": v.p.id,
                        "subject": v.p.name,
                    }
                )
        for v in sa.disabled:
            if v.p.origin == "USER_DEFINED":
                out.append(
                    {
                        "key": f"disabled:{v.p.id}",
                        "kind": "disabled",
                        "severity": "info",
                        "title": f"“{v.p.name}” is disabled",
                        "detail": "Remove it if it is no longer needed.",
                        "policy_id": v.p.id,
                        "subject": v.p.name,
                    }
                )
        referenced = {v.p.source_zone_external_id for v in sa.policies + sa.unordered} | {
            v.p.destination_zone_external_id for v in sa.policies + sa.unordered
        }
        has_net = {
            z
            for (z,) in (
                await db.execute(select(Network.zone_external_id).where(Network.site_id == sa.site.id, Network.present.is_(True)))
            ).all()
        }
        for z in sa.zones:
            if z.external_id not in referenced and z.external_id not in has_net and not z.network_external_ids:
                out.append(
                    {
                        "key": f"zone_unused:{z.id}",
                        "kind": "zone_unused",
                        "severity": "info",
                        "title": f"Zone {zone_label(z.name)} has no networks and no policies",
                        "detail": "",
                    }
                )
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["kind"], f["title"]))


def policy_out(v: PolicyView, by_name: dict[str, str]) -> dict[str, Any]:
    p = v.p
    return {
        "id": p.id,
        "name": p.name,
        "index": p.index,
        "enabled": p.enabled,
        "action": p.action,
        "source_zone": v.src,
        "destination_zone": v.dst,
        "match_all": v.match_all,
        "source_filter": v.src_filter,
        "destination_filter": v.dst_filter,
        "filter_summary": summarise(v.src_filter, v.dst_filter),
        "ip_scope": v.scope,
        "logging": p.logging_enabled,
        "origin": p.origin,
        "analysis": v.analysis,
        "relation": v.relation,
        "covered_by": by_name.get(v.covered_by or ""),
        "overlaps": [by_name.get(x, x) for x in v.overlaps],
        "is_demo": p.is_demo,
    }


def summarise(src: dict[str, Any] | None, dst: dict[str, Any] | None) -> str:
    """A one-line reading of the traffic filters, e.g. 'to ports 80, 443'."""
    parts = []
    for side, f in (("from", src), ("to", dst)):
        if not f:
            continue
        t = f.get("type", "")
        if t == "PORT":
            items = (f.get("portFilter") or {}).get("items") or []
            ports = [str(i.get("value") or f"{i.get('start')}–{i.get('stop')}") for i in items]
            neg = (f.get("portFilter") or {}).get("matchOpposite")
            parts.append(f"{side} {'all ports except' if neg else 'port' + ('s' if len(ports) > 1 else '')} {', '.join(ports)}")
        else:
            parts.append(f"{side} a {t.lower().replace('_', ' ')} filter")
    return "; ".join(parts) or "all traffic"
