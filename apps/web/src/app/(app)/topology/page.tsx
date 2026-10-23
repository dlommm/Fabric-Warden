"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronDown, ChevronRight, CircleCheck, CircleDashed, ExternalLink, Maximize, Minimize, Minus, Network, OctagonAlert, Plus, Router, Scan, TriangleAlert, Wifi, Waypoints, X } from "lucide-react";
import Link from "next/link";
import { type PointerEvent as ReactPointerEvent, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { type Hop, RouteTrace } from "@/components/route-trace";
import { Select } from "@/components/ui/field";
import { EmptyState, KeyValue, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel, type Tone, VlanPatch, ZonePatch, zoneLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { bps } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AlertItem, TopoEdge, TopoNode, Topology } from "@/lib/types";

const NODE_W = 196;
const NODE_H = 60;
const COL_W = 216;
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

/** Firing alerts by device: a critical alert draws its device critical, so one fault reads the same on every page. */
const AlertSeverity = createContext<Map<string, Tone>>(new Map());
function useNodeTone(n: TopoNode): Tone {
  const alert = useContext(AlertSeverity).get(n.id);
  const own = HEALTH[n.health].tone;
  return alert === "crit" || (alert === "warn" && own === "ok") ? alert : own;
}

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
  const alerts = useQuery({
    queryKey: ["alerts", "active"],
    queryFn: () => get<{ items: AlertItem[] }>("/api/monitoring/alerts"),
    enabled: moduleEnabled("monitoring"),
    refetchInterval: 30_000,
  });
  const alertTone = useMemo(() => {
    const m = new Map<string, Tone>();
    for (const a of alerts.data?.items ?? []) {
      if (a.state !== "firing" || a.silenced || a.entity_type !== "device") continue;
      const t: Tone = a.severity === "critical" ? "crit" : a.severity === "warning" ? "warn" : "info";
      if (t === "crit" || !m.has(a.entity_id)) m.set(a.entity_id, t);
    }
    return m;
  }, [alerts.data]);

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
        <div className="plate flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-2.5">
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
          <AlertSeverity.Provider value={alertTone}>
            <Diagram t={t} />
          </AlertSeverity.Provider>
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
      <span className="flex overflow-hidden rounded-[var(--radius)] border border-rule-strong">
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
                const n = (i + (e.key === "ArrowRight" ? 1 : options.length - 1)) % options.length;
                onChange(options[n][0]);
                (e.currentTarget.parentElement?.children[n] as HTMLElement | undefined)?.focus();
              }
            }}
            tabIndex={value === v ? 0 : -1}
            className={clsx(
              "h-8 px-3 text-sm font-semibold transition-[background-color,color,box-shadow] duration-[var(--dur)] ease-[var(--ease-out)] not-first:border-l not-first:border-rule",
              value === v ? "bg-accent-soft text-ink shadow-[inset_0_-2px_0_var(--accent)]" : "text-ink-2 hover:text-ink",
            )}
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
        <span aria-hidden className="h-6 w-px bg-line" />
      </div>
      <div className="flex flex-col gap-5 border-t border-line pt-5">
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

const MIN_K = 0.35;
/** How far an uplink-rate label can reach past its node's right edge. */
const LABEL_W = 64;
const MAX_K = 2;

/** Throughput sets the line weight (1.5–3.5px on a log scale): heavier lines carry more measured traffic. */
function edgeWeight(e: TopoEdge): number {
  const total = (e.rx_bps ?? 0) + (e.tx_bps ?? 0);
  if (!total) return 1.5;
  return Math.min(3.5, 1.5 + Math.max(0, Math.log10(total / 1e6 + 1)) * 0.9);
}

/** Seconds for a traffic dot to travel one dash period, from the measured uplink rate; null when none was reported. */
function flowSeconds(e: TopoEdge): number | null {
  const total = (e.rx_bps ?? 0) + (e.tx_bps ?? 0);
  if (!total) return null;
  return Math.min(5.5, Math.max(0.7, 5.5 - Math.log10(total / 1e6 + 1) * 1.7));
}

/**
 * The physical workspace: a tidy tree you can pan (drag, or arrow keys when
 * the canvas is focused), zoom (buttons, + and −, or Ctrl/⌘ + wheel), fit
 * and take full screen. Selecting a device shows its details and lights its
 * route up to the gateway; a parent with children can be folded.
 */
function Diagram({ t }: { t: Topology }) {
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [dragging, setDragging] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const port = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);

  const kids = useMemo(() => {
    const m = new Map<string, string[]>();
    t.edges.forEach((e) => m.set(e.source, [...(m.get(e.source) ?? []), e.target]));
    return m;
  }, [t]);
  const parentOf = useMemo(() => new Map(t.edges.map((e) => [e.target, e])), [t]);
  const hidden = useMemo(() => {
    const out = new Set<string>();
    const hide = (id: string) => (kids.get(id) ?? []).forEach((c) => {
      out.add(c);
      hide(c);
    });
    folded.forEach(hide);
    return out;
  }, [folded, kids]);
  const nodes = useMemo(() => t.nodes.filter((n) => !hidden.has(n.id)), [t, hidden]);
  const edges = useMemo(() => t.edges.filter((e) => !hidden.has(e.target) && !hidden.has(e.source)), [t, hidden]);
  const { pos, width, height } = useMemo(() => layout(nodes, edges), [nodes, edges]);
  const descendants = useCallback((id: string): number => (kids.get(id) ?? []).reduce((a, c) => a + 1 + descendants(c), 0), [kids]);

  // The selected node's route up to the root, as edge ids, for the lit path.
  const alertTone = useContext(AlertSeverity);
  const route = useMemo(() => {
    const ids = new Set<string>();
    let cur = selected;
    while (cur && parentOf.has(cur)) {
      const e = parentOf.get(cur)!;
      ids.add(e.id);
      cur = e.source;
    }
    return ids;
  }, [selected, parentOf]);

  const fit = useCallback(() => {
    const el = port.current;
    if (!el) return;
    // On narrow canvases keep plates readable and let the operator pan, rather than shrinking to fit.
    // Fitting never shrinks plates below readable text; past that the canvas pans.
    const floor = el.clientWidth < 640 ? 0.7 : 0.95;
    // Uplink-rate labels hang to the right of the last column; count them in the bounds.
    const span = width + LABEL_W;
    const k = Math.max(floor, Math.min(1, (el.clientWidth - 32) / span, (el.clientHeight - 32) / height));
    if (span * k > el.clientWidth) {
      // Wider than the canvas even at the floor: start at the top of the tree, centred on its root.
      const root = nodes.find((n) => n.depth === 0) ?? nodes[0];
      const rp = root ? pos.get(root.id) : undefined;
      setView({ k, x: el.clientWidth / 2 - ((rp?.x ?? 0) + NODE_W / 2) * k, y: 16 });
      return;
    }
    setView({ k, x: (el.clientWidth - span * k) / 2, y: Math.max(16, (el.clientHeight - height * k) / 2) });
  }, [width, height, nodes, pos]);
  useEffect(() => {
    fit();
  }, [fit, full]);
  useEffect(() => {
    // Refit when the canvas itself changes size: window resizes, full screen, the details pane opening.
    const el = port.current;
    const ro = el && typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => fit()) : null;
    if (el) ro?.observe(el);
    const onFs = () => setFull(document.fullscreenElement === wrap.current);
    document.addEventListener("fullscreenchange", onFs);
    return () => {
      ro?.disconnect();
      document.removeEventListener("fullscreenchange", onFs);
    };
  }, [fit]);

  const zoom = (f: number, cx?: number, cy?: number) =>
    setView((v) => {
      const el = port.current;
      const px = cx ?? (el ? el.clientWidth / 2 : 0);
      const py = cy ?? (el ? el.clientHeight / 2 : 0);
      const k = Math.max(MIN_K, Math.min(MAX_K, v.k * f));
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });
  useEffect(() => {
    const el = port.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else wrap.current?.requestFullscreen?.();
  };
  const onDown = (e: ReactPointerEvent) => {
    if ((e.target as HTMLElement).closest("button,a")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
    setDragging(true);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
  };
  const onUp = () => {
    if (drag.current && !drag.current.moved) setSelected(null);
    drag.current = null;
    setDragging(false);
  };

  if (t.layer === "logical") return <LogicalDiagram t={t} />;
  const overlay = t.vlan != null;
  const byId = new Map(t.nodes.map((n) => [n.id, n]));
  const sel = selected ? byId.get(selected) : undefined;
  const tool = "grid size-8 place-items-center rounded-[3px] text-ink-2 transition-colors duration-[var(--dur-fast)] hover:bg-accent-soft hover:text-ink";
  return (
    <div ref={wrap} className={clsx("plate overflow-hidden", full && "flex h-full flex-col rounded-none bg-ground")}>
      <div className={clsx("grid", sel ? "lg:grid-cols-[minmax(0,1fr)_20rem]" : "", full && "min-h-0 flex-1")}>
        <div className="relative min-w-0">
          <div
            ref={port}
            tabIndex={0}
            role="group"
            aria-label="Topology canvas. Arrow keys pan, plus and minus zoom, 0 fits the tree."
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={() => {
              drag.current = null;
              setDragging(false);
            }}
            onKeyDown={(e) => {
              const step = 48;
              const map: Record<string, () => void> = {
                ArrowLeft: () => setView((v) => ({ ...v, x: v.x + step })),
                ArrowRight: () => setView((v) => ({ ...v, x: v.x - step })),
                ArrowUp: () => setView((v) => ({ ...v, y: v.y + step })),
                ArrowDown: () => setView((v) => ({ ...v, y: v.y - step })),
                "+": () => zoom(1.2),
                "=": () => zoom(1.2),
                "-": () => zoom(1 / 1.2),
                "0": fit,
                Escape: () => setSelected(null),
              };
              if (e.target === e.currentTarget && map[e.key]) {
                e.preventDefault();
                map[e.key]();
              }
            }}
            className={clsx(
              "relative cursor-grab touch-none overflow-hidden bg-ground bg-[linear-gradient(var(--grid-minor)_1px,transparent_1px),linear-gradient(90deg,var(--grid-minor)_1px,transparent_1px),linear-gradient(var(--grid-major)_1px,transparent_1px),linear-gradient(90deg,var(--grid-major)_1px,transparent_1px)] bg-[length:24px_24px,24px_24px,120px_120px,120px_120px] outline-none select-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] focus-visible:ring-inset active:cursor-grabbing",
              full ? "h-full min-h-[60vh]" : "h-[min(68vh,640px)] min-h-[420px]",
            )}
          >
            <div
              className="absolute top-0 left-0 origin-top-left transition-transform duration-[var(--dur)] ease-[var(--ease-out)] will-change-transform"
              style={{ width, height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, transitionDuration: dragging ? "0ms" : undefined }}
            >
              <svg width={width} height={height} className="absolute inset-0 overflow-visible" aria-hidden>
                {edges.map((e) => {
                  const a = pos.get(e.source);
                  const b = pos.get(e.target);
                  const child = byId.get(e.target);
                  if (!a || !b || !child) return null;
                  const x1 = a.x + NODE_W / 2;
                  const y1 = a.y + NODE_H;
                  const x2 = b.x + NODE_W / 2;
                  const y2 = b.y;
                  const mid = y1 + (y2 - y1) / 2;
                  const d = `M${x1},${y1} V${mid} H${x2} V${y2}`;
                  const inVlan = overlay && e.in_vlan;
                  const lit = route.has(e.id);
                  const own = HEALTH[child.health].tone;
                  const at = alertTone.get(child.id);
                  const tone = at === "crit" || (at === "warn" && own === "ok") ? at : own;
                  const stroke = lit || inVlan ? "var(--accent)" : tone === "crit" ? "var(--crit)" : tone === "warn" ? "var(--warn-line)" : overlay ? "var(--construct)" : "var(--line)";
                  const flow = tone !== "crit" ? flowSeconds(e) : null;
                  return (
                    <g key={e.id}>
                      {lit && <path d={d} fill="none" stroke="var(--accent)" strokeWidth={10} strokeOpacity={0.16} strokeLinejoin="round" />}
                      <path
                        d={d}
                        fill="none"
                        strokeLinejoin="round"
                        stroke={stroke}
                        strokeWidth={lit || inVlan ? 3.5 : edgeWeight(e)}
                        // Receding never uses opacity: links that don't carry the overlaid VLAN go dashed instead.
                        strokeDasharray={(tone === "crit" || tone === "warn") && !lit ? "5 4" : overlay && !inVlan && !lit ? "3 5" : undefined}
                        className="transition-[stroke,stroke-width] duration-[var(--dur)]"
                      />
                      {flow && (!overlay || inVlan) && <path d={d} className="traffic" style={{ animationDuration: `${flow}s` }} />}
                    </g>
                  );
                })}
              </svg>
              {edges.map((e) => {
                const b = pos.get(e.target);
                const n = byId.get(e.target);
                if (!b || e.tx_bps == null || !n) return null;
                return (
                  <span
                    key={`r-${e.id}`}
                    className="pointer-events-none absolute rounded-[2px] bg-ground/85 px-1 font-mono text-[10.5px] whitespace-nowrap text-ink-3 tabular"
                    style={{ left: b.x + NODE_W / 2 + 8, top: b.y - 22 }}
                    title={`${n.name} uplink: ${bps(e.rx_bps)} down, ${bps(e.tx_bps)} up`}
                  >
                    ↓{bps(e.rx_bps)} ↑{bps(e.tx_bps)}
                  </span>
                );
              })}
              {nodes.map((n) => {
                const p = pos.get(n.id);
                if (!p) return null;
                const below = kids.has(n.id) ? descendants(n.id) : 0;
                return (
                  <NodePlate
                    key={n.id}
                    n={n}
                    x={p.x}
                    y={p.y}
                    overlay={overlay}
                    selected={selected === n.id}
                    onSelect={() => setSelected(selected === n.id ? null : n.id)}
                    fold={below ? { folded: folded.has(n.id), count: below, toggle: () => setFolded((f) => { const nx = new Set(f); if (nx.has(n.id)) nx.delete(n.id); else nx.add(n.id); return nx; }) } : undefined}
                  />
                );
              })}
            </div>
          </div>
          <div className="float absolute top-3 right-3 flex flex-col gap-0.5 p-1" role="toolbar" aria-label="Canvas">
            <button type="button" className={tool} onClick={() => zoom(1.2)} aria-label="Zoom in" title="Zoom in (+)"><Plus className="size-4" /></button>
            <button type="button" className={tool} onClick={() => zoom(1 / 1.2)} aria-label="Zoom out" title="Zoom out (−)"><Minus className="size-4" /></button>
            <button type="button" className={tool} onClick={fit} aria-label="Fit to view" title="Fit to view (0)"><Scan className="size-4" /></button>
            <span className="mx-1 my-0.5 h-px bg-rule" aria-hidden />
            <button type="button" className={tool} onClick={toggleFull} aria-label={full ? "Leave full screen" : "Full screen"} title={full ? "Leave full screen" : "Full screen"}>
              {full ? <Minimize className="size-4" /> : <Maximize className="size-4" />}
            </button>
          </div>
          <span className="pointer-events-none absolute bottom-3 left-3 rounded-[2px] bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-3 tabular shadow-[0_0_0_1px_var(--rule)]" aria-hidden>
            {Math.round(view.k * 100)}%
          </span>
        </div>
        {sel && <NodeDetails n={sel} t={t} onClose={() => setSelected(null)} />}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-rule px-4 py-3 text-sm text-ink-2">
        <span className="inline-flex items-center gap-2"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--line)" strokeWidth="1.5" /></svg>Uplink</span>
        <span className="inline-flex items-center gap-2"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--line)" strokeWidth="3.5" /></svg>Heavier, more measured traffic</span>
        <span className="inline-flex items-center gap-2"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--warn-line)" strokeWidth="1.5" strokeDasharray="5 4" /></svg>To a degraded or alerting device</span>
        <span className="inline-flex items-center gap-2"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--crit)" strokeWidth="1.5" strokeDasharray="5 4" /></svg>To a device that is down</span>
        <span className="inline-flex items-center gap-2"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--trace)" strokeWidth="2.5" strokeDasharray="1.5 6" strokeLinecap="round" /></svg>Traffic, faster where busier</span>
        {overlay && <span className="inline-flex items-center gap-2"><svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="var(--accent)" strokeWidth="3.5" /></svg>Carries the overlaid VLAN</span>}
        <span className="ml-auto hidden text-ink-3 md:inline">Drag to pan · Ctrl + wheel to zoom · select a device for details</span>
      </div>
    </div>
  );
}

/** Details for the selected device, with its route from the internet as a vertical Route Sign. */
function NodeDetails({ n, t, onClose }: { n: TopoNode; t: Topology; onClose: () => void }) {
  const byId = new Map(t.nodes.map((x) => [x.id, x]));
  const parent = new Map(t.edges.map((e) => [e.target, e]));
  const chain: TopoNode[] = [];
  let cur: TopoNode | undefined = n;
  while (cur) {
    chain.unshift(cur);
    const e = parent.get(cur.id);
    cur = e ? byId.get(e.source) : undefined;
  }
  const hops: Hop[] = [
    ...(chain[0]?.role === "gateway" ? [{ key: "wan", label: "Internet", sub: "via WAN", tone: "muted" as Tone }] : []),
    ...chain.map((c) => ({ key: c.id, label: c.name, sub: c.model ?? c.role, tone: HEALTH[c.health].tone, current: c.id === n.id })),
  ];
  const up = parent.get(n.id);
  const h = HEALTH[n.health];
  return (
    <aside aria-label={`${n.name} details`} className="anim-rise flex flex-col gap-4 border-t border-rule bg-surface-3 p-4 shadow-[-24px_0_48px_-28px_rgb(0_0_0/0.6)] lg:border-t-0 lg:border-l">
      <div className="flex items-start justify-between gap-2">
        <span aria-hidden className="mt-0.5 grid size-[22px] shrink-0 place-items-center rounded-full border-[1.5px] border-accent font-mono text-[11px] font-semibold text-accent">A</span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-md font-bold">{n.name}</h3>
          <p className="text-sm text-ink-3 capitalize">{n.role === "ap" ? "Access point" : n.role}{n.model ? ` · ${n.model}` : ""}</p>
        </div>
        <button type="button" onClick={onClose} className="grid size-7 place-items-center rounded-[3px] border border-rule-strong text-ink-2 hover:border-accent hover:text-ink" aria-label="Close details">
          <X className="size-4" />
        </button>
      </div>
      <StateLabel tone={h.tone}>{h.word}{n.alerts ? ` · ${n.alerts} alert${n.alerts > 1 ? "s" : ""}` : ""}</StateLabel>
      <KeyValue
        className="grid-cols-[minmax(6rem,auto)_1fr] gap-y-1.5"
        items={[
          ["Address", <span key="a" className="font-mono text-xs">{n.ip_address ?? "—"}</span>],
          ["Clients", <span key="c" className="tabular">{n.clients ?? 0}</span>],
          ["Upstream", byId.get(up?.source ?? "")?.name ?? "—"],
          ["Uplink", up?.tx_bps != null ? <span key="u" className="font-mono text-xs tabular">↓{bps(up.rx_bps)} ↑{bps(up.tx_bps)}</span> : "—"],
          ["Placement", n.placement ?? "—"],
        ]}
      />
      <RouteTrace vertical hops={hops} label={`Route from the internet to ${n.name}`} />
      {n.type === "device" && (
        <Link href={`/inventory/devices/${n.id}`} className="inline-flex items-center gap-1.5 self-start text-sm font-semibold text-accent hover:underline">
          Open device page <ExternalLink className="size-3.5" aria-hidden />
        </Link>
      )}
    </aside>
  );
}

function NodePlate({
  n,
  x,
  y,
  overlay,
  selected,
  onSelect,
  fold,
}: {
  n: TopoNode;
  x?: number;
  y?: number;
  overlay: boolean;
  selected?: boolean;
  onSelect?: () => void;
  fold?: { folded: boolean; count: number; toggle: () => void };
}) {
  const h = HEALTH[n.health];
  const tone = useNodeTone(n);
  const gateway = n.type === "device" && n.role === "gateway";
  const attention = tone === "warn";
  const down = tone === "crit";
  // When a firing alert raised the node above its own state, say so in its words and shape.
  const raised = tone !== h.tone;
  const word = raised ? (down ? "Critical alert" : "Alert firing") : h.word;
  const StateIcon = raised ? (down ? OctagonAlert : TriangleAlert) : h.Icon;
  const Icon = n.type === "network" ? Network : ROLE_ICON[n.role] ?? Router;
  const carries = n.type === "device" ? (n.vlan_clients ?? 0) > 0 : !!n.highlight;
  // Receding never uses opacity: nodes that do not carry the overlay go dashed and quieter.
  const dim = overlay && !carries && !gateway && !attention && !down;
  const href = n.type === "device" ? `/inventory/devices/${n.id}` : n.type === "network" ? "/inventory/networks" : `/inventory/clients/${n.id}`;
  const sub = n.type === "network" ? n.cidr ?? (n.enabled ? "No subnet" : "Disabled") : n.ip_address ?? n.model;
  const label = `${n.name}, ${n.type === "network" ? `network${n.vlan_id ? ` VLAN ${n.vlan_id}` : ""}` : n.role}, ${word}${n.clients ? `, ${n.clients} clients` : ""}${n.placement ? `, ${n.placement}` : ""}${overlay && carries ? ", carries the overlaid VLAN" : ""}`;
  const cls = clsx(
    "flex flex-col justify-center gap-0.5 rounded-[3px] bg-surface px-2.5 text-left text-ink transition-[box-shadow,background-color,border-color] duration-[var(--dur)] ease-[var(--ease-out)] hover:bg-surface-3",
    x == null ? "relative w-full max-w-[13rem]" : "absolute",
    gateway && !down && "border-2 border-line",
    !gateway && attention && "border-[1.5px] border-dashed border-warn-line",
    down && "border-[1.5px] border-dashed border-crit",
    !gateway && !attention && !down && !dim && "border-[1.5px] border-line",
    dim && "border border-dashed border-construct text-ink-2",
    overlay && carries && !attention && !down && "border-accent shadow-[0_0_0_3px_var(--accent-soft)]",
    selected && "z-10 border-solid border-accent bg-surface-3 shadow-[0_0_0_1px_var(--accent),var(--elev-3)]",
  );
  const body = (
    <>
      <span className="flex items-center gap-1.5">
        <Icon className="size-4 shrink-0 text-ink-3" aria-hidden strokeWidth={1.75} />
        <span className="truncate text-sm font-semibold">{n.name}</span>
        {n.type === "network" && n.vlan_id ? <span className="ml-auto"><VlanPatch id={n.vlan_id} /></span> : null}
      </span>
      <span className="flex items-center gap-2 text-xs text-ink-3">
        <span className="shrink-0 font-mono">{sub}</span>
        <span className="ml-auto flex min-w-0 items-center gap-1 font-semibold">
          {(tone !== "ok" || (!gateway && !attention)) && (
            <StateIcon
              className={clsx("size-3.5", attention ? "text-warn-text" : down ? "text-crit" : h.tone === "ok" ? "text-ok" : "text-ink-3")}
              aria-hidden
              strokeWidth={2.25}
            />
          )}
          <span className={clsx("truncate", attention && "text-warn-text", down && "text-crit")}>{tone !== "ok" ? word : n.clients ? `${n.clients} cl.` : ""}</span>
        </span>
      </span>
    </>
  );
  const style = x == null ? { height: NODE_H } : { left: x, top: y, width: NODE_W, height: NODE_H };
  if (!onSelect) {
    return (
      <Link href={href} aria-label={label} className={cls} style={style}>
        {body}
      </Link>
    );
  }
  return (
    <div className="contents">
      <button type="button" aria-label={label} aria-pressed={selected} onClick={onSelect} className={clsx(cls, "cursor-pointer")} style={style}>
        {body}
      </button>
      {fold && x != null && y != null && (
        <button
          type="button"
          onClick={fold.toggle}
          aria-expanded={!fold.folded}
          aria-label={`${fold.folded ? "Show" : "Fold"} ${fold.count} device${fold.count > 1 ? "s" : ""} below ${n.name}`}
          className="absolute z-20 inline-flex h-5 -translate-x-1/2 items-center gap-0.5 rounded-full border border-line bg-surface px-1.5 font-mono text-[11px] font-medium text-ink-2 transition-colors before:absolute before:-inset-3 before:content-[''] hover:border-accent hover:text-ink"
          style={{ left: x + NODE_W / 2, top: y + NODE_H - 10 }}
        >
          {fold.folded ? <ChevronRight className="size-3" aria-hidden /> : <ChevronDown className="size-3" aria-hidden />}
          {fold.folded && <span className="tabular">+{fold.count}</span>}
        </button>
      )}
    </div>
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
          {n.depth > 0 && <span aria-hidden className="-mt-2 h-3 w-2.5 shrink-0 border-b border-l border-line" />}
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
