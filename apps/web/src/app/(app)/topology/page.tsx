"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { CircleCheck, CircleDashed, Network, OctagonAlert, Router, TriangleAlert, Wifi, Waypoints } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel, type Tone, VlanPatch, ZonePatch, zoneLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { bps } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { TopoEdge, TopoNode, Topology } from "@/lib/types";

const NODE_W = 176;
const NODE_H = 60;
const COL_W = 196;
const ROW_H = 120;
const PAD = 16;

const HEALTH: Record<TopoNode["health"], { tone: Tone; word: string; Icon: typeof CircleCheck }> = {
  ok: { tone: "ok", word: "Online", Icon: CircleCheck },
  alerting: { tone: "warn", word: "Alerting", Icon: TriangleAlert },
  degraded: { tone: "warn", word: "Degraded", Icon: TriangleAlert },
  down: { tone: "crit", word: "Down", Icon: OctagonAlert },
  disabled: { tone: "muted", word: "Disabled", Icon: CircleDashed },
};

const ROLE_ICON: Record<string, typeof Router> = { gateway: Router, switch: Network, ap: Wifi };

/** Tidy tree: leaves take consecutive columns in depth-first order; a parent sits centred over its children. */
function layout(nodes: TopoNode[], edges: TopoEdge[]) {
  const kids = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const e of edges) {
    kids.set(e.source, [...(kids.get(e.source) ?? []), e.target]);
    hasParent.add(e.target);
  }
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const sortKids = (ids: string[]) => [...ids].sort((a, b) => (byId.get(a)?.name ?? "").localeCompare(byId.get(b)?.name ?? ""));
  const pos = new Map<string, { x: number; y: number }>();
  let col = 0;
  const place = (id: string, depth: number, seen: Set<string>): number => {
    seen.add(id);
    const children = sortKids(kids.get(id) ?? []).filter((c) => !seen.has(c));
    let x: number;
    if (children.length === 0) x = col++ * COL_W;
    else {
      const xs = children.map((c) => place(c, depth + 1, seen));
      x = (xs[0] + xs[xs.length - 1]) / 2;
    }
    pos.set(id, { x: x + PAD, y: depth * ROW_H + PAD });
    return x;
  };
  const seen = new Set<string>();
  for (const n of [...nodes].sort((a, b) => a.depth - b.depth || a.name.localeCompare(b.name))) {
    if (!hasParent.has(n.id) && !seen.has(n.id)) place(n.id, 0, seen);
  }
  const maxDepth = Math.max(0, ...[...pos.values()].map((p) => (p.y - PAD) / ROW_H));
  return { pos, width: Math.max(1, col) * COL_W + PAD * 2 - (COL_W - NODE_W), height: (maxDepth + 1) * ROW_H - (ROW_H - NODE_H) + PAD * 2 };
}

export default function TopologyPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("topology");
  const [layer, setLayer] = useState<"physical" | "logical">("physical");
  const [vlan, setVlan] = useState<number | null>(null);
  const [view, setView] = useState<"diagram" | "table">("diagram");
  const q = useQuery({
    queryKey: ["topology", layer, vlan],
    queryFn: () => get<Topology>(`/api/topology?layer=${layer}${vlan ? `&vlan=${vlan}` : ""}`),
    enabled: on,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Topology" name="Network Topology" canManage={can("modules:manage")}>
        See the uplink tree and your networks with live health and a VLAN overlay.
      </ModuleOff>
    );
  }
  const t = q.data;
  const demo = t?.nodes.some((n) => n.is_demo);
  const vlanName = t?.vlans.find((v) => v.vlan_id === vlan)?.name;
  const carriers = t?.nodes.filter((n) => n.type === "device" && (n.vlan_clients ?? 0) > 0).length ?? 0;

  return (
    <>
      <PageHeader
        title="Topology"
        description="How your devices connect, from the uplink each one reports, with live health. Overlay a VLAN to see which devices carry it."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Simulated network</span> : undefined}
      />
      {q.error && <Notice tone="crit" className="mb-4">{(q.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-[var(--radius)] border border-rule bg-surface px-4 py-2.5">
          <Segmented label="Layer" value={layer} onChange={(v) => setLayer(v as typeof layer)} options={[["physical", "Physical"], ["logical", "Logical"]]} />
          <label className="flex items-center gap-2 text-sm font-semibold text-ink-2">
            VLAN overlay
            <Select className="w-52" value={vlan ?? ""} onChange={(e) => setVlan(e.target.value ? Number(e.target.value) : null)}>
              <option value="">None</option>
              {t?.vlans.map((v) => <option key={v.vlan_id} value={v.vlan_id}>{v.vlan_id} · {v.name}</option>)}
            </Select>
          </label>
          <Segmented label="View" value={view} onChange={(v) => setView(v as typeof view)} options={[["diagram", "Diagram"], ["table", "Table"]]} className="ml-auto" />
        </div>
        {vlan != null && t && layer === "physical" && (
          <p className="flex flex-wrap items-center gap-2 text-sm text-ink-2" role="status">
            <VlanPatch id={vlan} />
            <span><b className="text-ink">{vlanName}</b> clients attach through {carriers} device{carriers === 1 ? "" : "s"}, drawn on the heavy route line. Inferred from where clients attach.</span>
          </p>
        )}
        {!t ? (
          <Skeleton className="h-96" />
        ) : t.nodes.length === 0 ? (
          <Panel>
            <EmptyState icon={<Waypoints />} title="No devices to draw">Connect a controller and run a sync; the tree is built from the uplinks devices report.</EmptyState>
          </Panel>
        ) : view === "diagram" ? (
          <Diagram t={t} />
        ) : (
          <TopologyTable t={t} />
        )}
        {t && (
          <ul className="flex flex-col gap-1 text-sm text-ink-3">
            {t.notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
      </div>
    </>
  );
}

function Segmented({ label, value, onChange, options, className }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][]; className?: string }) {
  return (
    <div role="radiogroup" aria-label={label} className={clsx("flex items-center gap-2", className)}>
      <span className="text-sm font-semibold text-ink-2">{label}</span>
      <span className="flex rounded-[var(--radius)] border border-rule-strong p-0.5">
        {options.map(([v, l]) => (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={value === v}
            onClick={() => onChange(v)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                const i = options.findIndex((o) => o[0] === value);
                onChange(options[(i + (e.key === "ArrowRight" ? 1 : options.length - 1)) % options.length][0]);
              }
            }}
            tabIndex={value === v ? 0 : -1}
            className={clsx("h-8 rounded-[3px] px-3 text-sm font-semibold transition-colors duration-150", value === v ? "bg-accent text-accent-ink" : "text-ink-2 hover:bg-surface-2")}
          >
            {l}
          </button>
        ))}
      </span>
    </div>
  );
}

/** The logical layer: the gateway routes every network, so networks hang off one bus and wrap. */
function LogicalDiagram({ t }: { t: Topology }) {
  const gateway = t.nodes.find((n) => n.type === "device");
  const nets = t.nodes.filter((n) => n.type === "network").sort((a, b) => (a.vlan_id ?? 0) - (b.vlan_id ?? 0));
  // Zones are road classes: networks group under their firewall zone's patch.
  const zones = [...nets.reduce((m, n) => m.set(n.zone ?? null, [...(m.get(n.zone ?? null) ?? []), n]), new Map<string | null, TopoNode[]>())].sort(
    ([a], [b]) => (a === null ? 1 : b === null ? -1 : a.localeCompare(b)),
  );
  const overlay = t.vlan != null;
  return (
    <Panel>
      <div className="flex flex-col items-center">
        {gateway && <NodePlate n={gateway} overlay={overlay} />}
        <span aria-hidden className="h-6 w-0.5 bg-rule-strong" />
      </div>
      <div className="flex flex-col gap-5 border-t-2 border-rule-strong pt-5">
        {zones.map(([zone, members]) => (
          <section key={zone ?? "none"} aria-label={zone ? `${zoneLabel(zone)} zone` : "No zone"} className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm text-ink-2">
              {zone ? <ZonePatch name={zone} /> : <Chip dashed>No zone</Chip>}
              <span>{members.length} network{members.length > 1 ? "s" : ""}</span>
            </h3>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-4">
              {members.map((n) => (
                <li key={n.id} className="flex justify-center">
                  <NodePlate n={n} overlay={overlay} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Panel>
  );
}

function Diagram({ t }: { t: Topology }) {
  const { pos, width, height } = useMemo(() => layout(t.nodes, t.edges), [t]);
  if (t.layer === "logical") return <LogicalDiagram t={t} />;
  const overlay = t.vlan != null;
  const byId = new Map(t.nodes.map((n) => [n.id, n]));
  return (
    <Panel bodyClassName="p-0">
      <div className="overflow-x-auto">
        <div className="relative mx-auto" style={{ width, height }}>
          <svg width={width} height={height} className="absolute inset-0" aria-hidden>
            {t.edges.map((e) => {
              const a = pos.get(e.source);
              const b = pos.get(e.target);
              if (!a || !b) return null;
              const x1 = a.x + NODE_W / 2;
              const y1 = a.y + NODE_H;
              const x2 = b.x + NODE_W / 2;
              const y2 = b.y;
              const mid = y1 + (y2 - y1) / 2;
              const on = overlay && e.in_vlan;
              return (
                <path
                  key={e.id}
                  d={`M${x1},${y1} V${mid} H${x2} V${y2}`}
                  fill="none"
                  strokeLinejoin="round"
                  className={on ? "stroke-[var(--accent)]" : "stroke-[var(--rule-strong)]"}
                  strokeWidth={on ? 6 : 2}
                  opacity={overlay && !on ? 0.5 : 1}
                />
              );
            })}
          </svg>
          {t.edges.map((e) => {
            const b = pos.get(e.target);
            const n = byId.get(e.target);
            if (!b || e.tx_bps == null || !n) return null;
            return (
              <span
                key={`r-${e.id}`}
                className="absolute rounded-[2px] bg-surface px-1 font-mono text-[11px] whitespace-nowrap text-ink-2 tabular"
                style={{ left: b.x + NODE_W / 2 + 8, top: b.y - 22 }}
                title={`${n.name} uplink: ${bps(e.rx_bps)} down, ${bps(e.tx_bps)} up`}
              >
                ↓{bps(e.rx_bps)} ↑{bps(e.tx_bps)}
              </span>
            );
          })}
          {t.nodes.map((n) => {
            const p = pos.get(n.id);
            if (!p) return null;
            return <NodePlate key={n.id} n={n} x={p.x} y={p.y} overlay={overlay} />;
          })}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-rule px-4 py-3 text-sm text-ink-2">
        <span className="inline-flex items-center gap-2"><span className="inline-block size-4 rounded-[3px] bg-ink" />Gateway</span>
        <span className="inline-flex items-center gap-2"><span className="sign inline-block size-4 rounded-[3px] bg-warn text-warn-ink" />Degraded or alerting</span>
        <span className="inline-flex items-center gap-2"><span className="inline-block size-4 rounded-[3px] bg-surface ring-[3px] ring-crit ring-inset" />Down</span>
        {overlay && <span className="inline-flex items-center gap-2"><span className="inline-block h-1.5 w-6 bg-accent" />Carries the overlaid VLAN</span>}
      </div>
    </Panel>
  );
}

function NodePlate({ n, x, y, overlay }: { n: TopoNode; x?: number; y?: number; overlay: boolean }) {
  const h = HEALTH[n.health];
  const gateway = n.type === "device" && n.role === "gateway";
  const attention = h.tone === "warn";
  const down = h.tone === "crit";
  const Icon = n.type === "network" ? Network : ROLE_ICON[n.role] ?? Router;
  const carries = n.type === "device" ? (n.vlan_clients ?? 0) > 0 : !!n.highlight;
  // Receding never uses opacity: sign plates stay full strength, plain plates go dashed and quieter.
  const dim = overlay && !carries && !gateway && !attention && !down;
  const href = n.type === "device" ? `/inventory/devices/${n.id}` : n.type === "network" ? "/inventory/networks" : `/inventory/clients/${n.id}`;
  const sub = n.type === "network" ? n.cidr ?? (n.enabled ? "No subnet" : "Disabled") : n.ip_address ?? n.model;
  return (
    <Link
      href={href}
      aria-label={`${n.name}, ${n.type === "network" ? `network${n.vlan_id ? ` VLAN ${n.vlan_id}` : ""}` : n.role}, ${h.word}${n.clients ? `, ${n.clients} clients` : ""}${n.placement ? `, ${n.placement}` : ""}${overlay && carries ? ", carries the overlaid VLAN" : ""}`}
      className={clsx(
        "flex flex-col justify-center gap-0.5 rounded-[3px] px-2.5 transition-opacity duration-150",
        x == null ? "relative w-full max-w-[13rem]" : "absolute",
        gateway && "sign bg-ink text-surface",
        !gateway && attention && "sign bg-warn text-warn-ink",
        !gateway && !attention && !dim && "border border-rule-strong bg-surface text-ink hover:bg-surface-2",
        dim && "border border-dashed border-rule-strong bg-surface-2 text-ink-2",
        down && "ring-[3px] ring-crit ring-inset",
        overlay && carries && !gateway && !attention && "outline-[2.5px] -outline-offset-[5px] outline-accent",
      )}
      style={x == null ? { height: NODE_H } : { left: x, top: y, width: NODE_W, height: NODE_H }}
    >
      <span className="flex items-center gap-1.5">
        <Icon className={clsx("size-4 shrink-0", gateway ? "text-surface" : attention ? "text-warn-ink" : "text-ink-3")} aria-hidden />
        <span className="truncate text-sm font-bold">{n.name}</span>
        {n.type === "network" && n.vlan_id ? <span className="ml-auto"><VlanPatch id={n.vlan_id} /></span> : null}
      </span>
      <span className={clsx("flex items-center gap-2 text-xs", gateway ? "text-surface" : attention ? "text-warn-ink" : "text-ink-3")}>
        <span className="truncate font-mono">{sub}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1 font-semibold">
          {(h.tone !== "ok" || (!gateway && !attention)) && (
            <h.Icon
              className={clsx("size-3.5", attention ? "text-warn-ink" : gateway ? "text-surface" : down ? "text-crit" : h.tone === "ok" ? "text-ok" : "text-ink-3")}
              aria-hidden
              strokeWidth={2.25}
            />
          )}
          {h.tone !== "ok" ? h.word : n.clients ? `${n.clients} cl.` : ""}
        </span>
      </span>
    </Link>
  );
}

function TopologyTable({ t }: { t: Topology }) {
  const parent = new Map(t.edges.map((e) => [e.target, e]));
  const byId = new Map(t.nodes.map((n) => [n.id, n]));
  const columns: Column<TopoNode>[] = [
    {
      key: "name",
      header: "Name",
      sort: (n) => n.name.toLowerCase(),
      cell: (n) => (
        <span className="flex items-center gap-2" style={{ paddingLeft: `${n.depth * 1.25}rem` }}>
          {n.depth > 0 && <span aria-hidden className="-mt-2 h-3 w-2.5 shrink-0 border-b-2 border-l-2 border-rule-strong" />}
          <Link href={n.type === "device" ? `/inventory/devices/${n.id}` : "/inventory/networks"} className="font-bold hover:underline">{n.name}</Link>
          {n.vlan_id ? <VlanPatch id={n.vlan_id} /> : null}
        </span>
      ),
    },
    { key: "role", header: "Role", sort: (n) => n.role, cell: (n) => <span className="capitalize">{n.type === "network" ? `${n.role.toLowerCase()} network` : n.role === "ap" ? "Access point" : n.role}</span> },
    { key: "up", header: "Upstream", cell: (n) => <span className="text-ink-2">{byId.get(parent.get(n.id)?.source ?? "")?.name ?? "—"}</span> },
    {
      key: "rate",
      header: "Uplink rate",
      cell: (n) => {
        const e = parent.get(n.id);
        return e?.tx_bps != null ? <span className="font-mono text-xs tabular">↓{bps(e.rx_bps)} ↑{bps(e.tx_bps)}</span> : <span className="text-ink-3">—</span>;
      },
    },
    { key: "health", header: "Health", sort: (n) => n.health, cell: (n) => <StateLabel tone={HEALTH[n.health].tone}>{HEALTH[n.health].word}</StateLabel> },
    { key: "clients", header: "Clients", align: "right", sort: (n) => n.clients ?? 0, cell: (n) => n.clients ?? "—" },
    ...(t.vlan != null && t.layer === "physical"
      ? [{ key: "vlan", header: `VLAN ${t.vlan} clients`, align: "right" as const, sort: (n: TopoNode) => n.vlan_clients ?? 0, cell: (n: TopoNode) => n.vlan_clients || "—" }]
      : []),
    { key: "place", header: "Placement", cell: (n) => n.placement ?? <span className="text-ink-3">—</span> },
  ];
  // Depth-first order so the indentation reads as the tree.
  const kids = new Map<string, string[]>();
  t.edges.forEach((e) => kids.set(e.source, [...(kids.get(e.source) ?? []), e.target]));
  const order: TopoNode[] = [];
  const visit = (id: string) => {
    const n = byId.get(id);
    if (!n || order.includes(n)) return;
    order.push(n);
    [...(kids.get(id) ?? [])].sort((a, b) => (byId.get(a)?.name ?? "").localeCompare(byId.get(b)?.name ?? "")).forEach(visit);
  };
  t.nodes.filter((n) => !parent.has(n.id)).forEach((n) => visit(n.id));
  return (
    <Panel bodyClassName="p-0">
      <DataTable caption={`${t.layer} topology`} rows={order} columns={columns} />
    </Panel>
  );
}
