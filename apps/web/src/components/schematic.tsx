"use client";

// The live schematic: the estate drawn as an engineering drawing, left to
// right from the internet. Links ink themselves in once per session; traffic
// moves along each link at a speed set from the uplink rate the controller
// last reported, and does not move where none was reported. Selecting a
// device opens its detail callout beside it.
import { clsx } from "clsx";
import { X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { bps } from "@/lib/format";
import type { TopoEdge, TopoNode, Topology } from "@/lib/types";
import { buttonClass } from "./ui/button";
import type { Tone } from "./ui/signs";

const W = 148;
const H = 40;
/** The narrowest column pitch; columns spread wider to fill the sheet. */
const COL = 196;
const MAX_COL = 300;
const ROW = 60;
const PAD = 14;
/** Past this many leaves, access points under one switch fold into one summary node. */
const MAX_LEAVES = 14;

export interface Callout {
  /** The device the callout points at. */
  nodeId: string;
  n: number;
  tone: Tone;
}

interface Placed {
  id: string;
  name: string;
  sub: string;
  x: number;
  y: number;
  depth: number;
  node?: TopoNode;
  folded?: number;
}

interface Link_ {
  id: string;
  from: Placed;
  to: Placed;
  edge?: TopoEdge;
  tone: Tone;
}

const HEALTH_TONE: Record<TopoNode["health"], Tone> = { ok: "ok", alerting: "warn", degraded: "warn", down: "crit", disabled: "muted" };
const RANK: Record<Tone, number> = { ok: 0, muted: 0, info: 1, warn: 2, crit: 3 };
const worse = (a: Tone, b?: Tone): Tone => (b && RANK[b] > RANK[a] ? b : a);

const HEALTH_WORD: Record<TopoNode["health"], string> = { ok: "Online", alerting: "Alerting", degraded: "Degraded", down: "Down", disabled: "Disabled" };

/** Seconds for a traffic dot to travel one dash period: faster where more traffic was measured. */
function flowSeconds(e?: TopoEdge): number | null {
  const total = (e?.rx_bps ?? 0) + (e?.tx_bps ?? 0);
  if (!total) return null;
  const mbps = total / 1e6;
  return Math.min(5.5, Math.max(0.7, 5.5 - Math.log10(mbps + 1) * 1.7));
}

function useInkOnce() {
  // Drawing in is an introduction: once per browser session, then the estate is simply there.
  const [ink, setInk] = useState(false);
  useEffect(() => {
    try {
      if (!sessionStorage.getItem("fw.schematic.inked")) {
        setInk(true);
        sessionStorage.setItem("fw.schematic.inked", "1");
      }
    } catch {}
  }, []);
  return ink;
}

function layout(t: Topology, wans: number, COL: number) {
  const devices = t.nodes.filter((n) => n.type === "device");
  const edges = t.edges.filter((e) => e.kind === "uplink");
  const kids = new Map<string, string[]>();
  const parentEdge = new Map<string, TopoEdge>();
  for (const e of edges) {
    kids.set(e.source, [...(kids.get(e.source) ?? []), e.target]);
    parentEdge.set(e.target, e);
  }
  const byId = new Map(devices.map((n) => [n.id, n]));
  const isLeaf = (id: string) => !(kids.get(id) ?? []).length;
  const leafCount = devices.filter((d) => isLeaf(d.id)).length;
  const fold = leafCount > MAX_LEAVES;
  const placed = new Map<string, Placed>();
  const links: Link_[] = [];
  let row = 0;
  const sub = (n: TopoNode) => n.model ?? n.role;
  const place = (id: string, depth: number): number => {
    const n = byId.get(id)!;
    let children = (kids.get(id) ?? []).filter((c) => byId.has(c)).sort((a, b) => byId.get(a)!.name.localeCompare(byId.get(b)!.name));
    let summary: Placed | undefined;
    if (fold) {
      const aps = children.filter((c) => isLeaf(c) && byId.get(c)!.role === "ap" && byId.get(c)!.health === "ok");
      if (aps.length > 2) {
        children = children.filter((c) => !aps.includes(c));
        summary = { id: `${id}:aps`, name: `${aps.length} access points`, sub: "all online", x: 0, y: 0, depth: depth + 1, folded: aps.length };
      }
    }
    const ys = children.map((c) => place(c, depth + 1));
    if (summary) {
      summary.x = PAD + (depth + 2) * COL;
      summary.y = PAD + row++ * ROW;
      placed.set(summary.id, summary);
      ys.push(summary.y);
    }
    const y = ys.length ? (ys[0] + ys[ys.length - 1]) / 2 : PAD + row++ * ROW;
    const p: Placed = { id, name: n.name, sub: sub(n), x: PAD + (depth + 1) * COL, y, depth: depth + 1, node: n };
    placed.set(id, p);
    for (const c of children) {
      const cp = placed.get(c)!;
      links.push({ id: `${id}>${c}`, from: p, to: cp, edge: parentEdge.get(c), tone: HEALTH_TONE[byId.get(c)!.health] });
    }
    if (summary) links.push({ id: `${id}>aps`, from: p, to: summary, tone: "ok" });
    return y;
  };
  const roots = devices.filter((d) => !parentEdge.has(d.id) || !byId.has(parentEdge.get(d.id)!.source)).sort((a, b) => a.depth - b.depth || a.name.localeCompare(b.name));
  const rootYs = roots.map((r) => place(r.id, 0));
  const inet: Placed = { id: "internet", name: "Internet", sub: `${wans} WAN${wans === 1 ? "" : "s"}`, x: PAD, y: rootYs.length ? (rootYs[0] + rootYs[rootYs.length - 1]) / 2 : PAD, depth: 0 };
  placed.set(inet.id, inet);
  for (const r of roots) if (r.role === "gateway") links.unshift({ id: `inet>${r.id}`, from: inet, to: placed.get(r.id)!, tone: "ok" });
  const all = [...placed.values()];
  const width = Math.max(...all.map((p) => p.x)) + W + PAD;
  const height = Math.max(...all.map((p) => p.y)) + H + PAD;
  return { placed: all, links, width, height, parentEdge };
}

export function Schematic({
  t,
  wans,
  callouts = [],
  lit,
  label,
}: {
  t: Topology;
  wans: number;
  callouts?: Callout[];
  /** A node to light, from hovering its callout elsewhere. */
  lit?: string | null;
  label: string;
}) {
  const ink = useInkOnce();
  const scroller = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setRoom(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // Columns spread to fill the sheet, so the drawing uses the width it is given.
  const base = useMemo(() => layout(t, wans, COL), [t, wans]);
  const cols = Math.max(1, ...base.placed.map((p) => Math.round((p.x - PAD) / COL)));
  const pitch = room ? Math.min(MAX_COL, Math.max(COL, (room - 2 * PAD - W) / cols)) : COL;
  const { placed, links, width, height, parentEdge } = useMemo(() => (pitch === COL ? base : layout(t, wans, pitch)), [base, pitch, t, wans]);
  // Where the drawing scrolls sideways, the callout can't float beside its node; it docks under the drawing instead.
  const narrow = room > 0 && room < width;
  const [sel, setSel] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const selected = placed.find((p) => p.id === sel && p.node);
  useEffect(() => {
    if (selected) closeRef.current?.focus();
  }, [selected]);
  const byNode = new Map(callouts.map((c) => [c.nodeId, c]));
  const path = (l: Link_) => {
    const x1 = l.from.x + W;
    const y1 = l.from.y + H / 2;
    const x2 = l.to.x;
    const y2 = l.to.y + H / 2;
    const mx = x1 + (x2 - x1) / 2;
    return y1 === y2 ? `M${x1},${y1} H${x2}` : `M${x1},${y1} H${mx} V${y2} H${x2}`;
  };
  // A node with children opens its callout below it, aligned to its right edge so it falls under the upstream
  // path rather than over its children;
  // a leaf opens to the right where there is room, else to the left.
  const CW = 300;
  const hasKids = selected ? links.some((l) => l.from.id === selected.id) : false;
  const fitsRight = selected ? selected.x + W + 24 + CW <= Math.max(width, room) : true;
  const side: "below" | "right" | "left" = hasKids ? "below" : fitsRight || !selected || selected.x - 24 - CW < 0 ? "right" : "left";
  const up = selected ? parentEdge.get(selected.id) : undefined;
  const selTone: Tone = selected?.node ? worse(HEALTH_TONE[selected.node.health], byNode.get(selected.node.id)?.tone) : "ok";

  const detail = selected?.node ? (
    <>
              <div className="flex items-center gap-2.5 border-b border-rule px-3.5 py-2.5">
                <span aria-hidden className="grid size-[22px] place-items-center rounded-full border-[1.5px] border-accent font-mono text-[11px] font-semibold text-accent">A</span>
                <span className="min-w-0 flex-1 truncate text-md font-bold">{selected.name}</span>
                <button ref={closeRef} type="button" onClick={() => setSel(null)} aria-label="Close detail" className="grid size-7 place-items-center rounded-[3px] border border-rule-strong text-ink-2 hover:border-accent hover:text-ink">
                  <X className="size-3.5" aria-hidden />
                </button>
              </div>
              <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 px-3.5 py-3 text-sm">
                <dt className="text-ink-3">State</dt>
                <dd className={clsx("font-semibold", selTone === "crit" ? "text-crit" : selTone === "warn" ? "text-warn-text" : "text-ok")}>
                  {HEALTH_WORD[selected.node.health]}
                  {selected.node.alerts ? ` · ${selected.node.alerts} alert${selected.node.alerts > 1 ? "s" : ""}` : ""}
                </dd>
                <dt className="text-ink-3">Model</dt>
                <dd className="font-mono text-xs leading-5">{selected.node.model ?? "—"}</dd>
                <dt className="text-ink-3">Address</dt>
                <dd className="font-mono text-xs leading-5">{selected.node.ip_address ?? "—"}</dd>
                <dt className="text-ink-3">Clients</dt>
                <dd className="font-mono text-xs leading-5 tabular">{selected.node.clients ?? 0}</dd>
                <dt className="text-ink-3">Uplink</dt>
                <dd className="font-mono text-xs leading-5 tabular">{up?.tx_bps != null ? `↓${bps(up.rx_bps)} ↑${bps(up.tx_bps)}` : "—"}</dd>
              </dl>
              <div className="flex flex-wrap gap-2 px-3.5 pb-3">
                <Link href={`/inventory/devices/${selected.node.id}`} className={buttonClass("primary", "sm")}>Open device</Link>
                <Link href="/topology" className={buttonClass("secondary", "sm")}>Show on topology</Link>
              </div>
              <p className="border-t border-rule px-3.5 py-2 text-xs text-ink-3">From the controller, as last synced</p>
    </>
  ) : null;

  return (
    <div onKeyDown={(e) => e.key === "Escape" && setSel(null)}>
    <div ref={scroller} className="relative overflow-x-auto">
      <div className="relative mx-auto" style={narrow ? { width, height } : { width: Math.max(width, selected && side === "right" ? selected.x + W + 24 + CW : 0), height: Math.max(height, selected ? selected.y + (side === "below" ? H + 340 : 300) : 0) }}>
        <svg width={width} height={height} className="absolute top-0 left-0 overflow-visible" role="img" aria-label={label}>
          {links.map((l, i) => {
            const d = path(l);
            const delay = `${l.from.depth * 110}ms`;
            const lt = worse(l.tone, byNode.get(l.to.id)?.tone);
            const broken = lt === "crit";
            const warn = lt === "warn";
            const flow = !broken ? flowSeconds(l.edge) : null;
            const heavy = l.edge && (l.edge.rx_bps ?? 0) + (l.edge.tx_bps ?? 0) > 2e8;
            return (
              <g key={l.id}>
                <path
                  d={d}
                  pathLength={broken || warn ? undefined : 1}
                  fill="none"
                  stroke={broken ? "var(--crit)" : warn ? "var(--warn-line)" : "var(--line)"}
                  strokeWidth={heavy ? 3 : 1.5}
                  strokeDasharray={broken || warn ? "5 4" : undefined}
                  className={clsx(ink && !broken && !warn && "ink-draw")}
                  style={ink ? { animationDelay: delay } : undefined}
                />
                {flow && <path d={d} className="traffic" style={{ animationDuration: `${flow}s`, animationDelay: ink ? `${700 + i * 20}ms` : undefined }} />}
              </g>
            );
          })}
          {links.map((l) =>
            l.edge && (l.edge.rx_bps ?? 0) + (l.edge.tx_bps ?? 0) > 1e6 ? (
              <text key={`r-${l.id}`} x={l.to.x + 1} y={l.to.y - 6} className="fill-ink-3 font-mono text-[10px] tabular">
                {bps((l.edge.rx_bps ?? 0) + (l.edge.tx_bps ?? 0))}
              </text>
            ) : null,
          )}
        </svg>
        {placed.map((p) => {
          const n = p.node;
          const c = n ? byNode.get(n.id) : undefined;
          // A firing alert's severity wins over the device's own state, so one fault reads the same everywhere.
          const tone: Tone = n ? worse(HEALTH_TONE[n.health], c?.tone) : "ok";
          const style = { left: p.x, top: p.y, width: W, height: H, animationDelay: ink ? `${p.depth * 110 + 60}ms` : undefined };
          const cls = clsx(
            "absolute flex flex-col justify-center rounded-[3px] border-[1.5px] bg-surface px-2.5 text-left transition-[background-color,box-shadow,border-color] duration-[var(--dur)]",
            ink && "anim-rise",
            tone === "crit" ? "border-dashed border-crit" : tone === "warn" ? "border-dashed border-warn-line" : "border-line",
            (lit === p.id || sel === p.id) && "bg-surface-3 shadow-[0_0_0_4px_var(--accent-soft),var(--elev-2)]",
            sel === p.id && "border-solid border-accent",
          );
          const body = (
            <>
              <span className="truncate text-sm font-semibold">{p.name}</span>
              <span className={clsx("truncate font-mono text-[10.5px]", tone === "crit" ? "text-crit" : tone === "warn" ? "text-warn-text" : "text-ink-3")}>
                {n && tone !== "ok" ? (c && RANK[c.tone] > RANK[HEALTH_TONE[n.health]] ? (c.tone === "crit" ? "Critical alert" : "Alert firing") : HEALTH_WORD[n.health]) : p.sub}
              </span>
            </>
          );
          return (
            <div key={p.id} className="contents">
              {n ? (
                <button
                  type="button"
                  className={clsx(cls, "cursor-pointer hover:bg-surface-3")}
                  style={style}
                  aria-pressed={sel === p.id}
                  aria-label={`${p.name}, ${HEALTH_WORD[n.health]}${n.clients ? `, ${n.clients} clients` : ""}`}
                  onClick={() => setSel(sel === p.id ? null : p.id)}
                >
                  {body}
                </button>
              ) : p.folded ? (
                <Link href="/inventory/devices?role=accessPoint" className={clsx(cls, "border-dashed hover:bg-surface-3")} style={style}>
                  {body}
                </Link>
              ) : (
                <div className={cls} style={style}>
                  {body}
                </div>
              )}
              {c && (
                <span
                  aria-hidden
                  className={clsx(
                    "absolute z-10 grid size-[22px] place-items-center rounded-full border-[1.5px] bg-surface font-mono text-[11px] font-semibold",
                    c.tone === "crit" ? "border-crit text-crit" : c.tone === "warn" ? "border-warn-line text-warn-text" : "border-accent text-accent",
                  )}
                  style={{ left: p.x + W - 11, top: p.y - 11 }}
                >
                  {c.n}
                </span>
              )}
            </div>
          );
        })}
        {selected?.node && !narrow && (
          <>
            {/* The leader: a drawn line from the node to its callout, ending in a node dot. */}
            {side === "below" ? (
              <span aria-hidden className="absolute z-20 w-[1.5px] origin-top animate-[fw-route-draw-y_160ms_var(--ease-out)_both] bg-accent" style={{ left: selected.x + W - 24, top: selected.y + H, height: 22 }} />
            ) : (
              <span
                aria-hidden
                className="absolute z-20 h-[1.5px] origin-left animate-[fw-route-draw_160ms_var(--ease-out)_both] bg-accent"
                style={{ left: side === "right" ? selected.x + W : selected.x - 24, top: selected.y + H / 2, width: 24 }}
              />
            )}
            <span
              aria-hidden
              className="absolute z-20 size-[7px] -translate-1/2 rounded-full bg-accent"
              style={side === "below" ? { left: selected.x + W - 23.25, top: selected.y + H } : { left: side === "right" ? selected.x + W : selected.x, top: selected.y + H / 2 }}
            />
            <div
              role="dialog"
              aria-label={`${selected.name} detail`}
              className="dialog anim-callout absolute z-30 w-[300px]"
              style={
                side === "below"
                  ? { left: Math.max(0, selected.x + W - CW), top: selected.y + H + 22, transformOrigin: "right top" }
                  : { left: side === "right" ? selected.x + W + 24 : selected.x - 24 - CW, top: Math.max(0, selected.y - 24), transformOrigin: side === "right" ? "left top" : "right top" }
              }
            >
              {detail}
            </div>
          </>
        )}
      </div>
    </div>
    {selected?.node && narrow && (
      <div role="dialog" aria-label={`${selected.name} detail`} className="dialog anim-callout mt-3" style={{ transformOrigin: "top" }}>
        {detail}
      </div>
    )}
    </div>
  );
}
