"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { FolderOpen, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { type Hop, RouteTrace, pathHops } from "@/components/route-trace";
import { Button } from "@/components/ui/button";
import { Field, Select, Textarea } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel, type Tone } from "@/components/ui/signs";
import { get, patch, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ClientDetail, Topology } from "@/lib/types";
import { deviceTone } from "@/components/ui/signs";

type Sev = "low" | "medium" | "high" | "critical";
interface Entity { type: string; id: string | null; label: string | null }
interface Signal { key: string; kind: string; severity: Sev; title: string; detail: string; source: string; entities: Entity[]; seen_at: string | null; is_demo: boolean; case: { id: string; status: string; title: string } | null }
interface Case { id: string; title: string; severity: Sev; status: "open" | "investigating" | "resolved" | "false_positive"; assignee: string | null; summary: string; entities: Entity[]; signal_key: string | null; timeline: { at: string; author: string; kind: string; text: string }[]; created_at: string; updated_at: string; resolved_at: string | null; is_demo: boolean }

const SEV_TONE: Record<Sev, Tone> = { critical: "crit", high: "crit", medium: "warn", low: "info" };
const SEV_ORDER: Sev[] = ["critical", "high", "medium", "low"];
const SEV_WORD: Record<Sev, string> = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
// High and critical share the red; their shapes (inverted triangle, diamond) tell them apart.
const SEV_FILL: Record<Sev, string> = { critical: "bg-crit", high: "bg-crit", medium: "bg-warn-line", low: "bg-ink-3" };

/** Each severity has its own shape, so it never rests on colour: ring, triangle, inverted triangle, diamond. */
function SevMark({ s, className }: { s: Sev; className?: string }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "inline-block size-2.5 shrink-0",
        s === "low" && "rounded-full border-[1.5px] border-ink-3",
        s === "medium" && "bg-warn-line [clip-path:polygon(50%_0,100%_100%,0_100%)]",
        s === "high" && "bg-crit [clip-path:polygon(0_0,100%_0,50%_100%)]",
        s === "critical" && "scale-[0.8] rotate-45 bg-crit",
        className,
      )}
    />
  );
}

function SevLabel({ s }: { s: Sev }) {
  return (
    <span className={clsx("inline-flex items-center gap-2 font-semibold", s === "critical" || s === "high" ? "text-crit" : s === "medium" ? "text-warn-text" : "text-ink-2")}>
      <SevMark s={s} />
      {SEV_WORD[s]}
    </span>
  );
}

/**
 * Signals by the hour they were last seen, stacked by severity, over the last
 * 24 hours. Only signals with a seen time are counted; the rest are listed
 * below and said so.
 */
function SignalsChart({ signals }: { signals: Signal[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const now = Date.now();
  const hourMs = 3_600_000;
  const end = Math.ceil(now / hourMs) * hourMs;
  const buckets = Array.from({ length: 24 }, () => ({ critical: 0, high: 0, medium: 0, low: 0 }) as Record<Sev, number>);
  let counted = 0;
  for (const sg of signals) {
    if (!sg.seen_at) continue;
    const t = new Date(sg.seen_at).getTime();
    const i = 23 - Math.floor((end - t) / hourMs);
    if (i < 0 || i > 23) continue;
    buckets[i][sg.severity]++;
    counted++;
  }
  const totals = buckets.map((b) => SEV_ORDER.reduce((a, k) => a + b[k], 0));
  const max = Math.max(4, ...totals);
  const top = Math.ceil(max / 4) * 4;
  const label = (i: number) => new Date(end - (24 - i) * hourMs).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const undated = signals.filter((x) => !x.seen_at).length;
  if (!counted) return null;
  return (
    <Panel
      title="Signals over 24 hours"
      actions={<span className="font-mono text-xs text-ink-3">{counted} with a seen time{undated ? ` · ${undated} without` : ""}</span>}
    >
      <div className="relative pl-8">
        <div className="relative h-40">
          {[0, 0.5, 1].map((f) => (
            <div key={f} className="absolute right-0 -left-8 border-t border-rule" style={{ bottom: `${f * 100}%` }}>
              <span className="absolute -top-2 left-0 font-mono text-[10.5px] text-ink-3 tabular">{Math.round(top * f)}</span>
            </div>
          ))}
          <div className="absolute inset-0 flex items-end gap-[3px]" onMouseLeave={() => setHover(null)}>
            {buckets.map((b, i) => (
              <button
                key={i}
                type="button"
                className={clsx("relative flex h-full min-w-0 flex-1 cursor-crosshair flex-col justify-end rounded-t-[2px] focus-visible:outline-offset-0", hover === i && "bg-accent-soft")}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                aria-label={`${label(i)}: ${totals[i]} signal${totals[i] === 1 ? "" : "s"}${totals[i] ? `, ${SEV_ORDER.filter((k) => b[k]).map((k) => `${b[k]} ${k}`).join(", ")}` : ""}`}
              >
                <span className="flex origin-bottom animate-[fw-route-draw-y_600ms_var(--ease-out)_both] flex-col-reverse gap-[2px]" style={{ height: `${(totals[i] / top) * 100}%`, animationDelay: `${i * 15}ms` }}>
                  {[...SEV_ORDER].reverse().filter((k) => b[k]).map((k, j, arr) => (
                    <span key={k} className={clsx("block min-h-[2px]", SEV_FILL[k], j === arr.length - 1 && "rounded-t-[2px]")} style={{ flexGrow: b[k] }} />
                  ))}
                </span>
              </button>
            ))}
          </div>
          {hover !== null && (
            <div
              className="float pointer-events-none absolute z-10 min-w-36 -translate-x-1/2 px-2.5 py-2 text-sm"
              style={{ left: `${((hover + 0.5) / 24) * 100}%`, bottom: `calc(${(totals[hover] / top) * 100}% + 10px)` }}
            >
              <p className="mb-1 font-mono text-xs font-medium">{label(hover)} · {totals[hover]}</p>
              {SEV_ORDER.map((k) => (
                <p key={k} className="flex items-center gap-2">
                  <SevMark s={k} />
                  <span className="flex-1">{SEV_WORD[k]}</span>
                  <span className="tabular font-mono">{buckets[hover][k]}</span>
                </p>
              ))}
            </div>
          )}
        </div>
        <div className="flex justify-between pt-1.5 font-mono text-[10.5px] text-ink-3 tabular">
          <span>{label(0)}</span>
          <span>{label(8)}</span>
          <span>{label(16)}</span>
          <span>now</span>
        </div>
      </div>
      <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2">
        {SEV_ORDER.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5"><SevMark s={k} />{SEV_WORD[k]}</span>
        ))}
      </p>
    </Panel>
  );
}

/**
 * The trace for a signal: when it names a client, the path that client takes
 * from the internet, drawn hop by hop, with the signal's evidence beside it.
 */
function SignalTrace({ s, manage, onOpenCase, opening }: { s: Signal; manage: boolean; onOpenCase: () => void; opening: boolean }) {
  const { moduleEnabled, modules } = useSession();
  const client = s.entities.find((e) => e.type === "client" && e.id);
  const device = !client ? s.entities.find((e) => e.type === "device" && e.id) : undefined;
  const intel = modules.length === 0 || moduleEnabled("client-intel");
  const topoOn = modules.length === 0 || moduleEnabled("topology");
  const q = useQuery({ queryKey: ["client", client?.id], queryFn: () => get<ClientDetail>(`/api/client-intel/clients/${client!.id}`), enabled: !!client && intel });
  const topo = useQuery({ queryKey: ["topology", "physical", null], queryFn: () => get<Topology>("/api/topology?layer=physical"), enabled: !!device && topoOn });
  let hops: Hop[] | null = null;
  if (q.data) {
    hops = [...pathHops(q.data.path), { key: q.data.id, label: q.data.alias || q.data.name, sub: q.data.ip_address ?? q.data.mac_address ?? undefined, tone: SEV_TONE[s.severity], current: true }];
  } else if (device && topo.data) {
    // A device's path: walk its reported uplinks up to the gateway, then the internet.
    const byId = new Map(topo.data.nodes.map((n) => [n.id, n]));
    const up = new Map(topo.data.edges.filter((e) => e.kind === "uplink").map((e) => [e.target, e.source]));
    const chain = [];
    for (let id: string | undefined = device.id!; id && byId.has(id) && chain.length < 12; id = up.get(id)) chain.unshift(byId.get(id)!);
    if (chain.length) {
      hops = [
        ...(chain[0].role === "gateway" ? [{ key: "wan", label: "Internet", sub: "via WAN", tone: "muted" as Tone }] : []),
        ...chain.map((n, i) => ({
          key: n.id,
          label: n.name,
          sub: n.model ?? n.role,
          href: `/inventory/devices/${n.id}`,
          tone: i === chain.length - 1 ? SEV_TONE[s.severity] : n.state ? deviceTone(n.state) : ("ok" as Tone),
          current: i === chain.length - 1,
        })),
      ];
    }
  }
  const pathFor = client ?? device;
  const loading = (client && q.isLoading) || (device && topo.isLoading);
  return (
    <Panel title="Trace" variant="raised" actions={s.seen_at && <span className="font-mono text-xs text-ink-3">{dateTime(s.seen_at)}</span>} className="anim-rise self-start xl:sticky xl:top-[4.5rem]">
      <div className="flex flex-col gap-4">
        <div>
          <SevLabel s={s.severity} />
          <p className="mt-1 font-semibold">{s.title}</p>
        </div>
        {s.detail && (
          <div>
            <p className="mb-1.5 text-sm text-ink-3">Evidence</p>
            <p className="rounded-[3px] border border-rule bg-ground px-3 py-2 text-sm break-words text-ink-2">{s.detail}</p>
          </div>
        )}
        <div>
          <p className="mb-2 text-sm text-ink-3">Path</p>
          {hops ? (
            <RouteTrace vertical bare hops={hops} label={`Path from the internet to ${pathFor?.label ?? "the entity"}`} />
          ) : loading ? (
            <Skeleton className="h-32" />
          ) : (
            <p className="text-sm text-ink-2">
              {pathFor ? "The path isn't available from the data on hand." : "This signal names no device or client, so there is no network path to draw."}
            </p>
          )}
        </div>
        {s.entities.length > 0 && (
          <div>
            <p className="mb-1.5 text-sm text-ink-3">Involved</p>
            <Entities list={s.entities} />
          </div>
        )}
        <p className="text-sm text-ink-3">From {s.source}{s.seen_at ? `, seen ${ago(s.seen_at)}` : ""}{s.is_demo ? " · demo data" : ""}</p>
        {manage && !s.case && (
          <span><Button variant="primary" size="sm" icon={<FolderOpen />} loading={opening} onClick={onOpenCase}>Open case</Button></span>
        )}
        <Link href={`/investigate${s.entities.find((e) => e.type === "ip" && e.id) ? `?q=${s.entities.find((e) => e.type === "ip")!.id}` : ""}`} className="text-sm font-semibold text-accent hover:underline">
          Pivot in Investigate
        </Link>
      </div>
    </Panel>
  );
}
const STATUS_WORD = { open: "Open", investigating: "Investigating", resolved: "Resolved", false_positive: "False positive" };

function entityHref(e: Entity): string | undefined {
  if (e.type === "ip" && e.id) return `/investigate?q=${e.id}`;
  if (e.type === "device" && e.id) return `/inventory/devices/${e.id}`;
  if (e.type === "client" && e.id) return `/inventory/clients/${e.id}`;
  if (e.type === "policy") return "/firewall";
  if (e.type === "control") return "/compliance";
  return undefined;
}

function Entities({ list }: { list: Entity[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {list.map((e, i) => {
        const href = entityHref(e);
        const body = <Chip className={href ? "hover:border-accent" : undefined}><span className="mr-1 text-ink-3">{e.type}</span>{e.label ?? e.id}</Chip>;
        return href ? <Link key={i} href={href}>{body}</Link> : <span key={i}>{body}</span>;
      })}
    </span>
  );
}

export default function SocPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("soc");
  const manage = can("soc:manage");
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [traced, setTraced] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["soc"], queryFn: () => get<{ signals: Signal[]; cases: Case[]; counts: { signals: number; open_cases: number } }>("/api/soc"), enabled: on, refetchInterval: 30_000 });
  const openCase = useMutation({
    mutationFn: (s: Signal) => post<Case>("/api/soc/cases", { title: s.title, severity: s.severity, summary: s.detail, entities: s.entities, signal_key: s.key }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["soc"] });
      setSelected(c.id);
    },
  });
  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="SOC" name="Security Operations Center" canManage={can("modules:manage")}>
        One queue of security signals from firewall analysis, compliance, alerts and logs, and cases to work them.
      </ModuleOff>
    );
  }
  const d = q.data;
  const fresh = d?.signals.filter((s) => !s.case) ?? [];
  const handled = d?.signals.filter((s) => s.case) ?? [];
  const current = d?.cases.find((c) => c.id === selected) ?? null;
  const signal = !current ? (fresh.find((x) => x.key === traced) ?? fresh[0] ?? null) : null;
  return (
    <>
      <PageHeader
        title="SOC"
        description="Security signals gathered from firewall analysis, compliance, alerts and logs, worked as cases. Signals are computed live; a case records what you decided and why."
        meta={d && <span className="flex flex-wrap gap-x-4 text-sm"><StateLabel tone={fresh.length ? "warn" : "muted"}>{`${fresh.length} signal${fresh.length === 1 ? "" : "s"} without a case`}</StateLabel><StateLabel tone={d.counts.open_cases ? "info" : "muted"}>{`${d.counts.open_cases} open case${d.counts.open_cases === 1 ? "" : "s"}`}</StateLabel></span>}
      />
      {(q.error || openCase.error) && <Notice tone="crit" className="mb-4">{((q.error || openCase.error) as Error).message}</Notice>}
      {!d ? (
        <Skeleton className="h-96" />
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] xl:items-start">
          <div className="flex flex-col gap-[var(--gap)]">
            {d.signals.length > 0 && <SignalsChart signals={d.signals} />}
            <Panel title="Signals without a case" bodyClassName="p-0" actions={fresh.length > 0 && <span className="text-sm text-ink-3">Select one to trace it</span>}>
              {fresh.length === 0 ? (
                <EmptyState icon={<ShieldCheck />} title="Nothing waiting">Every signal has a case, or nothing needs attention.</EmptyState>
              ) : (
                <ul>
                  {fresh.map((s) => {
                    const on = signal?.key === s.key;
                    return (
                      <li key={s.key} className={clsx("relative border-b border-rule last:border-b-0", on && "bg-accent-soft")}>
                        <button
                          type="button"
                          aria-pressed={on}
                          onClick={() => {
                            setSelected(null);
                            setTraced(s.key);
                          }}
                          className="flex w-full flex-col gap-2 px-4 py-3 text-left transition-colors duration-[var(--dur-fast)] hover:bg-accent-soft md:flex-row md:items-start md:gap-4"
                        >
                          <span className="md:w-24 md:shrink-0"><SevLabel s={s.severity} /></span>
                          <span className="flex min-w-0 flex-1 flex-col gap-1">
                            <span className="font-semibold">{s.title}</span>
                            {s.detail && <span className="line-clamp-2 break-words text-sm text-ink-2">{s.detail}</span>}
                            <span className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
                              <span>{s.source}</span>
                              {s.seen_at && <span>· {ago(s.seen_at)}</span>}
                              {s.is_demo && <DemoTag />}
                            </span>
                          </span>
                          <span className="md:w-48 md:shrink-0" aria-label="Involved">
                            {s.entities.some((e) => !s.title.includes(e.label ?? "\u0000")) ? (
                              <span className="flex flex-wrap gap-1">
                                {s.entities.filter((e) => !s.title.includes(e.label ?? "\u0000")).slice(0, 3).map((e, i) => (
                                  <Chip key={i} className="h-auto min-h-6 py-0.5 text-left"><span className="mr-1 text-ink-3">{e.type}</span>{e.label ?? e.id}</Chip>
                                ))}
                              </span>
                            ) : (
                              <span className="text-xs text-ink-3">{s.entities.length ? "named in the title" : "no entity"}</span>
                            )}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Panel>
            <Panel title={`Cases (${d.cases.length})`} bodyClassName="p-0">
              {d.cases.length === 0 ? (
                <p className="px-4 py-3 text-sm text-ink-3">No cases yet. Open one from a signal.</p>
              ) : (
                <ul>
                  {d.cases.map((c) => (
                    <li key={c.id} className="border-b border-rule last:border-b-0">
                      <button type="button" onClick={() => setSelected(c.id === selected ? null : c.id)} aria-pressed={c.id === selected} className={clsx("flex w-full flex-col gap-1 px-4 py-2.5 text-left transition-colors duration-[var(--dur-fast)] hover:bg-accent-soft md:flex-row md:items-center md:gap-4", c.id === selected && "bg-accent-soft")}>
                        <span className="md:w-24 md:shrink-0"><SevLabel s={c.severity} /></span>
                        <span className="min-w-0 flex-1 truncate font-semibold">{c.title}</span>
                        <StateLabel tone={c.status === "resolved" ? "ok" : c.status === "false_positive" ? "muted" : c.status === "investigating" ? "info" : "warn"}>{STATUS_WORD[c.status]}</StateLabel>
                        <span className="text-sm text-ink-3">{c.assignee ?? "unassigned"} · {ago(c.updated_at)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            {handled.length > 0 && <p className="text-sm text-ink-3">{handled.length} more signal{handled.length > 1 ? "s are" : " is"} already tied to a case.</p>}
          </div>
          {current ? (
            <CasePanel key={current.id} c={current} manage={manage} />
          ) : (
            signal && <SignalTrace key={signal.key} s={signal} manage={manage} opening={openCase.isPending && openCase.variables?.key === signal.key} onOpenCase={() => openCase.mutate(signal)} />
          )}
        </div>
      )}
    </>
  );
}

function CasePanel({ c, manage }: { c: Case; manage: boolean }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Case>(`/api/soc/cases/${c.id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["soc"] });
      setNote("");
    },
  });
  return (
    <Panel title={c.title} variant="raised" actions={c.is_demo && <DemoTag />} className="anim-rise self-start xl:sticky xl:top-[4.5rem]">
      <div className="flex flex-col gap-4">
        {c.summary && <p className="text-sm text-ink-2">{c.summary}</p>}
        {c.entities.length > 0 && <Entities list={c.entities} />}
        {manage && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Status">
              {(p) => (
                <Select {...p} value={c.status} onChange={(e) => update.mutate({ status: e.target.value })}>
                  {Object.entries(STATUS_WORD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Severity">
              {(p) => (
                <Select {...p} value={c.severity} onChange={(e) => update.mutate({ severity: e.target.value })}>
                  {["low", "medium", "high", "critical"].map((s) => <option key={s} value={s} className="capitalize">{s[0].toUpperCase() + s.slice(1)}</option>)}
                </Select>
              )}
            </Field>
          </div>
        )}
        <ol className="flex flex-col gap-0 border-l border-line pl-4" aria-label="Case timeline">
          {c.timeline.map((t, i) => (
            <li key={i} className="relative pb-3 last:pb-0">
              <span aria-hidden className="absolute top-1.5 -left-[21.5px] size-2.5 rounded-full border-[1.5px] border-line bg-surface" />
              <p className="text-xs text-ink-3">{dateTime(t.at)} · {t.author}</p>
              <p className={clsx("text-sm", t.kind === "note" ? "text-ink" : "font-semibold text-ink-2")}>{t.text}</p>
            </li>
          ))}
        </ol>
        {manage && (
          <form onSubmit={(e) => { e.preventDefault(); if (note.trim()) update.mutate({ note: note.trim() }); }} className="flex flex-col gap-2">
            <Field label="Add a note">{(p) => <Textarea {...p} className="min-h-16" value={note} maxLength={5000} onChange={(e) => setNote(e.target.value)} placeholder="What you checked, what you found, what you did" />}</Field>
            <span><Button type="submit" size="sm" variant="primary" loading={update.isPending} disabled={!note.trim()}>Add note</Button></span>
          </form>
        )}
        {update.error && <Notice tone="crit">{(update.error as Error).message}</Notice>}
      </div>
    </Panel>
  );
}
