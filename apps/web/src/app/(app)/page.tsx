"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ArrowDown, ArrowUp, Bot, CircleCheck, Globe, Laptop, Network, RefreshCw, Router, Settings2, Siren, TriangleAlert, Wifi } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { type Hop, RouteTrace } from "@/components/route-trace";
import { Button, ButtonLink } from "@/components/ui/button";
import { useMetric } from "@/components/analytics";
import { FigureStrip, KpiPlate } from "@/components/ui/kpi";
import { ChangeState } from "@/components/changes";
import { type Callout, Schematic } from "@/components/schematic";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, type Tone, VlanPatch, controllerTone, deviceTone } from "@/components/ui/signs";
import { get, post } from "@/lib/api";
import { describeEvent, eventHref } from "@/lib/events";
import { ago, titleCase } from "@/lib/format";
import { DEFAULT_PREFS, useSession } from "@/lib/session";
import type { AiRun, AlertItem, ChangeRequest, ClientRow, DeviceSummary, NetworkRow, OverviewPanel, Summary, Topology, WanOverview } from "@/lib/types";

const PANEL_LABEL: Record<OverviewPanel, string> = {
  attention: "Needs attention",
  route: "Route from the internet",
  health: "Estate health",
  changes: "Revisions",
  controllers: "Controllers",
  clients: "Clients by network",
};

const STALE_AFTER_S = 300;
const WIDE = new Set<OverviewPanel>(["attention", "route"]);

/** Approximate rendered heights in rows, used to split panels into two even columns. */
function panelWeight(id: OverviewPanel, s?: Summary): number {
  switch (id) {
    case "health": return 6;
    case "changes": return 2 + Math.min(s?.recent_events.length ?? 8, 8);
    case "controllers": return 2 + (s?.controllers.length ?? 1) * 1.5;
    case "clients": return 3 + (s ? Math.min(s.networks, 12) * 0.8 : 6);
    default: return 4;
  }
}

/**
 * Greedy split by weight. The user's first panel always leads the left column;
 * each column keeps the user's chosen order.
 */
function balance(ids: OverviewPanel[], s?: Summary): [OverviewPanel[], OverviewPanel[]] {
  const cols: [Set<OverviewPanel>, Set<OverviewPanel>] = [new Set(), new Set()];
  const h = [0, 0];
  if (ids.length) {
    cols[0].add(ids[0]);
    h[0] = panelWeight(ids[0], s);
  }
  const sorted = ids.slice(1).sort((a, b) => panelWeight(b, s) - panelWeight(a, s));
  for (const id of sorted) {
    const i = h[0] <= h[1] ? 0 : 1;
    cols[i].add(id);
    h[i] += panelWeight(id, s);
  }
  return [ids.filter((id) => cols[0].has(id)), ids.filter((id) => cols[1].has(id))];
}

export default function OverviewPage() {
  const { prefs, setPrefs, can, moduleEnabled, modules } = useSession();
  const [editing, setEditing] = useState(false);
  const inventoryOn = moduleEnabled("inventory");
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => get<Summary>("/api/inventory/summary"), enabled: inventoryOn, refetchInterval: 30_000 });
  const devices = useQuery({ queryKey: ["devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices"), enabled: inventoryOn });
  const alerts = useQuery({
    queryKey: ["alerts", "active"],
    queryFn: () => get<{ items: AlertItem[] }>("/api/monitoring/alerts"),
    enabled: moduleEnabled("monitoring"),
    refetchInterval: 30_000,
  });

  if (modules.length > 0 && !inventoryOn) {
    return (
      <>
        <PageHeader title="Overview" />
        <Panel>
          <EmptyState
            title="Inventory is switched off"
            action={can("modules:manage") ? <ButtonLink href="/modules" variant="primary">Open Module Center</ButtonLink> : undefined}
          >
            The Overview is built from the Asset & Device Inventory module. Enable it (with the UniFi Network Integration) to see your network here.
          </EmptyState>
        </Panel>
      </>
    );
  }

  const s = summary.data;
  const panels = prefs.overview_panels;
  const renderPanel = (id: OverviewPanel) => (
    <>
      {id === "attention" && <AttentionPanel s={s} alerts={alerts.data?.items} />}
      {id === "health" && <HealthPanel s={s} devices={devices.data?.items} />}
      {id === "changes" && (moduleEnabled("change-engine") ? <RevisionsPanel /> : <ChangesPanel s={s} />)}
      {id === "controllers" && <ControllersPanel s={s} />}
      {id === "clients" && <ClientsPanel />}
    </>
  );
  const noController = s && s.controllers.length === 0;
  const columns = panels.filter((id) => !WIDE.has(id));

  return (
    <>
      <CommandGantry
        s={s}
        devices={devices.data?.items}
        alerts={alerts.data?.items}
        editing={editing}
        onCustomize={() => setEditing((e) => !e)}
      />
      {editing && <Customizer panels={panels} onChange={(p) => setPrefs({ overview_panels: p })} />}
      {noController ? (
        <Panel>
          <EmptyState
            title="No controller connected yet"
            action={can("controllers:manage") ? <ButtonLink href="/controllers" variant="primary">Connect a controller</ButtonLink> : undefined}
          >
            Fabric Warden reads your UniFi console through its official API. Connect one, or add the demo controller to explore.
          </EmptyState>
        </Panel>
      ) : (
        <div className="flex flex-col gap-[var(--gap)]">
          {panels.includes("route") ? (
            <EstateSheet s={s} devices={devices.data?.items} alerts={alerts.data?.items} withIssues={panels.includes("attention")} />
          ) : (
            panels.includes("attention") && <AttentionPanel s={s} alerts={alerts.data?.items} />
          )}
          <KpiStrip s={s} devices={devices.data?.items} alerts={alerts.data?.items} />
          {/* Below xl: the user's order, one column. At xl: two balanced columns. */}
          <div className="flex flex-col gap-[var(--gap)] xl:hidden">
            {columns.map((id) => (
              <div key={id}>{renderPanel(id)}</div>
            ))}
          </div>
          <div className="hidden items-start gap-[var(--gap)] xl:grid xl:grid-cols-2">
            {balance(columns, s).map((col, i) => (
              <div key={i} className="flex flex-col gap-[var(--gap)]">
                {col.map((id) => (
                  <div key={id}>{renderPanel(id)}</div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function worstTone(issues: Issue[]): Tone {
  return issues.some((i) => i.tone === "crit") ? "crit" : issues.some((i) => i.tone === "warn") ? "warn" : "ok";
}

/**
 * The Overview's heading: the estate's name, its state in words, when the
 * data was last read, and the two things you do from here.
 */
function CommandGantry({
  s,
  alerts,
  editing,
  onCustomize,
}: {
  s?: Summary;
  devices?: DeviceSummary[];
  alerts?: AlertItem[];
  editing: boolean;
  onCustomize: () => void;
}) {
  const qc = useQueryClient();
  const { can } = useSession();
  const sync = useMutation({
    mutationFn: () => Promise.all((s?.controllers ?? []).map((c) => post(`/api/controllers/${c.id}/sync`))),
    onSettled: () => qc.invalidateQueries(),
  });
  const issues = s ? issuesFrom(s, alerts) : [];
  const tone = worstTone(issues);
  const severe = issues.filter((i) => i.tone === "crit" || i.tone === "warn").length;
  const critical = issues.filter((i) => i.tone === "crit").length;
  const lastSync = s?.controllers.map((c) => c.last_sync_at).filter((x): x is string => !!x).sort().at(-1);
  const one = s?.controllers.length === 1 ? s.controllers[0] : undefined;
  return (
    <header aria-label="Estate status" className="mb-[var(--gap)] flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-[-0.01em]"><span className="sr-only">Overview: </span>{one ? one.name : s ? `${s.controllers.length} controllers` : "Overview"}</h1>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-base text-ink-2">
          {!s ? (
            <Skeleton className="h-5 w-72" />
          ) : (
            <>
              <HealthWord tone={tone} severe={severe} critical={critical} />
              <span>{s.devices.total} devices · {s.clients.total} clients · {s.networks} networks</span>
              <span className="text-sm text-ink-3">
                {one?.application_version && <>Network <span className="font-mono text-xs">{one.application_version}</span> · </>}synced {ago(lastSync)}
              </span>
              {s.demo_only && <DemoTag />}
            </>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {can("jobs:run") && s && s.controllers.length > 0 && (
          <Button variant="primary" icon={<RefreshCw className={clsx(sync.isPending && "animate-spin")} />} onClick={() => sync.mutate()} disabled={sync.isPending}>
            {sync.isPending ? "Syncing…" : "Sync now"}
          </Button>
        )}
        <Button icon={<Settings2 />} onClick={onCustomize} aria-expanded={editing}>
          {editing ? "Done" : "Customize"}
        </Button>
      </div>
    </header>
  );
}

/** The estate's state in words with its shape: never colour alone. */
function HealthWord({ tone, severe, critical }: { tone: Tone; severe: number; critical: number }) {
  const word =
    tone === "crit" ? `${critical} critical${severe > critical ? ` · ${severe - critical} warning` : ""}` : tone === "warn" ? `${severe} need${severe === 1 ? "s" : ""} attention` : "All clear";
  return <StateLabel tone={tone}>{word}</StateLabel>;
}

/**
 * The live schematic sheet: the estate drawn from the internet down, with
 * what needs attention called out beside it. Each callout's number is drawn
 * on its device; hovering or focusing a callout lights the device. Without the
 * Topology module the sheet falls back to the route trace.
 */
function EstateSheet({ s, devices, alerts, withIssues }: { s?: Summary; devices?: DeviceSummary[]; alerts?: AlertItem[]; withIssues: boolean }) {
  const { moduleEnabled } = useSession();
  const topoOn = moduleEnabled("topology");
  const topo = useQuery({ queryKey: ["topology", "physical", null], queryFn: () => get<Topology>("/api/topology?layer=physical"), enabled: topoOn, refetchInterval: 60_000 });
  const [lit, setLit] = useState<string | null>(null);
  if (!s) return <Skeleton className="h-80 w-full" />;
  const issues = withIssues ? issuesFrom(s, alerts) : [];
  const numbered = issues.map((i, k) => ({ ...i, n: k + 1 }));
  const callouts: Callout[] = numbered.filter((i) => i.node).map((i) => ({ nodeId: i.node!, n: i.n, tone: i.tone }));
  const reported = topo.data?.edges.some((e) => e.tx_bps != null);
  return (
    <section aria-labelledby="estate-h" className="plate marks">
      <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule px-4 py-1.5">
        <h2 id="estate-h" className="flex items-center gap-2.5 text-base font-semibold tracking-[0.01em]">
          <span className="junction" aria-hidden />
          The estate, as drawn from the controller
        </h2>
        <span className="ml-auto font-mono text-xs text-ink-3">
          {topoOn && topo.data ? (reported ? "traffic from the last reported uplink rates" : "no uplink rates reported") : "route from the internet"}
        </span>
      </div>
      {/* The drawing takes the sheet's full width; callouts sit beside it only where both fit. */}
      <div className="flex flex-col 2xl:flex-row">
        <div className="min-w-0 flex-[3_1_0%] p-4">
          {topoOn ? (
            topo.data ? (
              topo.data.nodes.some((n) => n.type === "device") ? (
                <Schematic t={topo.data} wans={s.wans} callouts={callouts} lit={lit} label="Devices from the internet down, with their uplinks and live health" />
              ) : (
                <EmptyState title="No devices to draw">The drawing is built from the uplinks devices report after a sync.</EmptyState>
              )
            ) : (
              <Skeleton className="h-64 w-full" />
            )
          ) : (
            <RouteBody devices={devices} s={s} />
          )}
        </div>
        {withIssues && (
          <aside aria-labelledby="attention-h" className="flex min-w-0 flex-col border-t border-rule 2xl:w-[22rem] 2xl:shrink-0 2xl:border-t-0 2xl:border-l">
            <h3 id="attention-h" className="px-4 pt-3.5 pb-1.5 text-sm font-semibold text-ink-2">
              {issues.length ? "Called out on the drawing" : "Nothing called out"}
            </h3>
            {issues.length === 0 ? (
              <p className="flex items-center gap-2 px-4 pb-4 text-base text-ink-2">
                <CircleCheck className="size-4 text-ok" aria-hidden /> All devices online, controllers syncing, data fresh.
              </p>
            ) : (
              <ol className="grid grid-cols-[repeat(auto-fill,minmax(18rem,1fr))] px-1.5 pb-2 2xl:flex 2xl:flex-col">
                {numbered.map((i) => (
                  <li key={i.key}>
                    <Link
                      href={i.href ?? "#"}
                      onMouseEnter={() => setLit(i.node ?? null)}
                      onMouseLeave={() => setLit(null)}
                      onFocus={() => setLit(i.node ?? null)}
                      onBlur={() => setLit(null)}
                      className="grid grid-cols-[28px_minmax(0,1fr)] gap-2.5 rounded-[3px] px-2.5 py-2.5 transition-colors duration-[var(--dur-fast)] hover:bg-accent-soft"
                    >
                      <span
                        aria-hidden
                        className={clsx(
                          "grid size-[22px] place-items-center rounded-full border-[1.5px] font-mono text-[11px] font-semibold",
                          i.tone === "crit" ? "border-crit text-crit" : i.tone === "warn" ? "border-warn-line text-warn-text" : "border-accent text-accent",
                        )}
                      >
                        {i.n}
                      </span>
                      <span className="min-w-0">
                        <StateLabel tone={i.tone}>
                          <span className="text-ink">{i.title}</span>
                        </StateLabel>
                        <span className="block text-sm text-ink-2">{i.detail}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ol>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}

function pointsOf(q: ReturnType<typeof useMetric>) {
  const series = q.data?.series[0];
  return series && series.points.length > 1 ? { points: series.points, step: q.data!.step, summary: series.summary } : null;
}

/** The KPI strip: real counts from the inventory, alerts, WAN probe and agent runs. Trends only where history is recorded. */
function KpiStrip({ s, devices, alerts }: { s?: Summary; devices?: DeviceSummary[]; alerts?: AlertItem[] }) {
  const { moduleEnabled, can } = useSession();
  const analytics = moduleEnabled("analytics-studio");
  const clientsTrend = pointsOf(useMetric({ metric: "site.clients", range: "24h", limit: 1 }, analytics));
  const wanOn = moduleEnabled("wan");
  const wan = useQuery({ queryKey: ["wan"], queryFn: () => get<WanOverview>("/api/wan"), enabled: wanOn, refetchInterval: 60_000 });
  const aiOn = moduleEnabled("ai-agents") && can("ai:use");
  const runs = useQuery({ queryKey: ["ai", "runs"], queryFn: () => get<{ items: AiRun[]; total: number }>("/api/ai/runs?limit=100"), enabled: aiOn, refetchInterval: 30_000 });
  if (!s || !devices) {
    return (
      <Skeleton className="h-[98px] w-full" />
    );
  }
  const role = (pred: (d: DeviceSummary) => boolean) => devices.filter(pred);
  const groups: [string, typeof Router, DeviceSummary[], string][] = [
    ["Gateways", Router, role((d) => d.features.includes("gateway")), "gateway"],
    ["Switches", Network, role((d) => !d.features.includes("gateway") && !d.features.includes("accessPoint")), "switching"],
    ["Access points", Wifi, role((d) => d.features.includes("accessPoint")), "accessPoint"],
  ];
  const firing = (alerts ?? []).filter((a) => a.state === "firing" && !a.silenced);
  const crit = firing.filter((a) => a.severity === "critical").length;
  const warn = firing.filter((a) => a.severity === "warning").length;
  const probe = wan.data?.probe.results.filter((r) => r.latency_ms != null && !r.stale) ?? [];
  const latency = probe.length ? Math.round(probe.reduce((a, r) => a + (r.latency_ms ?? 0), 0) / probe.length) : null;
  const dayAgo = Date.now() - 86_400_000;
  const recentRuns = runs.data?.items.filter((r) => new Date(r.started_at).getTime() > dayAgo) ?? [];
  const running = runs.data?.items.filter((r) => r.status === "running").length ?? 0;
  return (
    <FigureStrip label="Estate in numbers">
      {groups.map(([name, Icon, items, r]) => {
        const bad = items.filter((d) => deviceTone(d.state) !== "ok");
        // A firing alert on one of these devices sets the severity, so the figure agrees with the drawing and Alerts.
        const alerted = firing.filter((a) => a.entity_type === "device" && items.some((d) => d.id === a.entity_id));
        const t = alerted.some((a) => a.severity === "critical") ? "crit" : worst(items);
        return (
          <KpiPlate
            key={name}
            name={name}
            icon={<Icon />}
            value={items.length}
            href={`/inventory/devices?role=${r}`}
            state={
              items.length === 0 ? (
                <span className="text-ink-3">None adopted</span>
              ) : bad.length === 0 ? (
                <StateLabel tone="ok">All online</StateLabel>
              ) : (
                <StateLabel tone={t}>{`${bad.length} not online`}</StateLabel>
              )
            }
          />
        );
      })}
      <KpiPlate
        name="WAN"
        icon={<Globe />}
        value={s.wans}
        of={s.wans === 1 ? "link" : "links"}
        href={wanOn ? "/wan" : undefined}
        state={
          !wanOn ? (
            <span className="text-ink-3">WAN module off</span>
          ) : latency != null ? (
            <span className="text-ink-2">
              Probe <b className="tabular text-ink">{latency} ms</b> average
            </span>
          ) : (
            <span className="text-ink-3">No latency probe results</span>
          )
        }
      />
      <KpiPlate
        name="Clients"
        icon={<Laptop />}
        value={s.clients.total}
        href="/inventory/clients"
        state={<span className="text-ink-2">{clientsTrend && <span className="text-ink-3">24 h range {clientsTrend.summary.min ?? "—"}–{clientsTrend.summary.max ?? "—"} · </span>}{Object.entries(s.clients.by_type).map(([t, n]) => `${n} ${t === "WIRELESS" ? "Wi-Fi" : t.toLowerCase()}`).join(" · ") || "None connected"}</span>}

      />
      {moduleEnabled("monitoring") && (
        <KpiPlate
          name="Alerts firing"
          icon={<Siren />}
          value={firing.length}
          href="/alerts"
          state={
            firing.length === 0 ? (
              <StateLabel tone="ok">None firing</StateLabel>
            ) : (
              <StateLabel tone={crit ? "crit" : warn ? "warn" : "info"}>{[crit && `${crit} critical`, warn && `${warn} warning`, firing.length - crit - warn && `${firing.length - crit - warn} info`].filter(Boolean).join(" · ")}</StateLabel>
            )
          }
        />
      )}
      {aiOn && (
        <KpiPlate
          name="Agent runs"
          icon={<Bot />}
          value={recentRuns.length}
          of="in 24 h"
          href="/ai/agents"
          state={
            running ? (
              <span className="inline-flex items-center gap-2 font-semibold text-ink">
                <span aria-hidden className="beacon size-2 rounded-full bg-accent text-accent" />
                {running} running now
              </span>
            ) : (
              <span className="text-ink-3">None running</span>
            )
          }
        />
      )}
    </FigureStrip>
  );
}

function Customizer({ panels, onChange }: { panels: OverviewPanel[]; onChange: (p: OverviewPanel[]) => void }) {
  const all = DEFAULT_PREFS.overview_panels;
  const hidden = all.filter((p) => !panels.includes(p));
  const move = (i: number, d: -1 | 1) => {
    const next = [...panels];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  return (
    <Panel title="Customize the Overview" className="mb-[var(--gap)]" actions={<Button size="sm" variant="ghost" onClick={() => onChange(all)}>Reset to default</Button>}>
      <p className="mb-3 text-base text-ink-2">Choose which panels appear and in what order. Saved to your account.</p>
      <ul className="flex flex-col divide-y divide-rule rounded-[4px] border border-rule">
        {panels.map((p, i) => (
          <li key={p} className="flex h-11 items-center gap-3 px-3">
            <input type="checkbox" checked onChange={() => onChange(panels.filter((x) => x !== p))} className="size-4 accent-[var(--accent)]" aria-label={`Show ${PANEL_LABEL[p]}`} />
            <span className="flex-1 font-semibold">{PANEL_LABEL[p]}</span>
            <Button size="sm" variant="ghost" aria-label={`Move ${PANEL_LABEL[p]} up`} disabled={i === 0} onClick={() => move(i, -1)} icon={<ArrowUp />} />
            <Button size="sm" variant="ghost" aria-label={`Move ${PANEL_LABEL[p]} down`} disabled={i === panels.length - 1} onClick={() => move(i, 1)} icon={<ArrowDown />} />
          </li>
        ))}
        {hidden.map((p) => (
          <li key={p} className="flex h-11 items-center gap-3 px-3 text-ink-3">
            <input type="checkbox" checked={false} onChange={() => onChange([...panels, p])} className="size-4 accent-[var(--accent)]" aria-label={`Show ${PANEL_LABEL[p]}`} />
            <span className="flex-1">{PANEL_LABEL[p]} (hidden)</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

const SEVERITY_TONE: Record<AlertItem["severity"], Tone> = { critical: "crit", warning: "warn", info: "info" };

interface Issue {
  key: string;
  tone: Tone;
  title: string;
  detail: string;
  href?: string;
  /** The device this issue is about, for its callout on the drawing. */
  node?: string;
}

function issuesFrom(s: Summary, alerts: AlertItem[] = []): Issue[] {
  const out: Issue[] = [];
  // Firing alerts lead; a device already listed for its state is not repeated.
  const listed = new Set(s.attention.filter((d) => d.state !== "ONLINE").map((d) => d.id));
  const offlineAlert = new Map<string, AlertItem>();
  for (const a of alerts) {
    if (a.state !== "firing" || a.silenced) continue;
    if (a.entity_type === "device" && listed.has(a.entity_id) && a.metric === "device.online") {
      offlineAlert.set(a.entity_id, a);
      continue;
    }
    out.push({
      key: `a-${a.id}`,
      tone: a.severity === "critical" ? "crit" : a.severity === "warning" ? "warn" : "info",
      title: `${a.rule}: ${a.label}`,
      detail: `${a.summary.replace(`${a.label}: `, "")} · fired ${ago(a.fired_at)}${a.acknowledged_by ? ` · acknowledged by ${a.acknowledged_by}` : ""}`,
      href: "/alerts",
      node: a.entity_type === "device" ? a.entity_id : undefined,
    });
  }
  for (const c of s.controllers) {
    if (c.status !== "ok" && c.status !== "unknown") {
      out.push({ key: `c-${c.id}`, tone: controllerTone(c.status), title: `${c.name} is ${c.status}`, detail: c.status_detail || "The last sync failed.", href: `/controllers/${c.id}` });
    }
    const age = c.last_sync_at ? (Date.now() - new Date(c.last_sync_at).getTime()) / 1000 : Infinity;
    if (age > STALE_AFTER_S) {
      out.push({ key: `s-${c.id}`, tone: "warn", title: `${c.name}: data is stale`, detail: c.last_sync_at ? `Last successful sync ${ago(c.last_sync_at)}.` : "Never synced.", href: `/controllers/${c.id}` });
    }
  }
  for (const d of s.attention) {
    if (d.state !== "ONLINE") {
      // When an offline alert is firing for this device, the row carries the alert's severity so Overview and Alerts agree.
      const a = offlineAlert.get(d.id);
      out.push({
        key: `d-${d.id}`,
        tone: a ? SEVERITY_TONE[a.severity] : deviceTone(d.state),
        title: `${d.name} is ${d.state === "CONNECTION_INTERRUPTED" ? "interrupted" : d.state.toLowerCase()}`,
        detail: `${d.model} · ${d.ip_address ?? d.mac_address}${a ? ` · ${a.rule} alert firing, fired ${ago(a.fired_at)}` : ""}`,
        href: a ? "/alerts" : `/inventory/devices/${d.id}`,
        node: d.id,
      });
    }
  }
  const fw = s.attention.filter((d) => d.firmware_updatable);
  if (fw.length) {
    out.push({ key: "fw", tone: "info", title: `Firmware update available on ${fw.length} device${fw.length > 1 ? "s" : ""}`, detail: fw.map((d) => d.name).join(", "), href: "/inventory/devices" });
  }
  const order: Record<Tone, number> = { crit: 0, warn: 1, info: 2, muted: 3, ok: 4 };
  return out.sort((a, b) => order[a.tone] - order[b.tone]);
}

function AttentionPanel({ s, alerts }: { s?: Summary; alerts?: AlertItem[] }) {
  if (!s) return <Skeleton className="h-28 w-full" />;
  const issues = issuesFrom(s, alerts);
  if (issues.length === 0) {
    return (
      <section aria-label="Needs attention" className="plate flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-4">
        <CircleCheck className="size-5 text-ok" aria-hidden />
        <p className="font-bold">Nothing needs attention.</p>
        <p className="text-ink-2">All devices online, controllers syncing, data fresh.</p>
      </section>
    );
  }
  const severe = issues.filter((i) => i.tone === "crit" || i.tone === "warn").length;
  const notes = issues.length - severe;
  return (
    <section aria-labelledby="attention-h" className="attention-plate">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 pt-4 pb-2">
        <TriangleAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
        <h2 id="attention-h" className="text-lg font-bold">
          {severe > 0 ? `${severe} thing${severe > 1 ? "s" : ""} need${severe > 1 ? "" : "s"} attention` : "Nothing urgent"}
        </h2>
        {notes > 0 && <span className="text-base font-semibold">and {notes} notice{notes > 1 ? "s" : ""}</span>}
      </div>
      <ul className="mx-2 mb-2 overflow-hidden rounded-[3px] border border-rule bg-surface text-ink">
        {issues.map((i) => (
          <li key={i.key} className="border-b border-rule last:border-b-0">
            <Link href={i.href ?? "#"} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-0.5 px-3 py-2 transition-colors duration-[var(--dur-fast)] hover:bg-warn-soft">
              <span className="font-bold sm:min-w-[13rem]">
                <StateLabel tone={i.tone}>
                  <span className="text-ink">{i.title}</span>
                </StateLabel>
              </span>
              <span className="text-ink-2">{i.detail}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function worst(devs: DeviceSummary[]): Tone {
  const tones = devs.map((d) => deviceTone(d.state));
  return tones.includes("crit") ? "crit" : tones.includes("warn") ? "warn" : "ok";
}

function RouteBody({ devices, s }: { devices?: DeviceSummary[]; s?: Summary }) {
  if (!devices || !s) return <Skeleton className="h-24 w-full" />;
  const gateway = devices.find((d) => d.features.includes("gateway"));
  const switches = devices.filter((d) => !d.features.includes("gateway") && !d.features.includes("accessPoint"));
  const core = switches.filter((d) => d.uplink_id === gateway?.id);
  const access = switches.filter((d) => !core.includes(d));
  const aps = devices.filter((d) => d.features.includes("accessPoint"));
  const hops: Hop[] = [{ key: "wan", label: "Internet", sub: `${s.wans} WAN${s.wans === 1 ? "" : "s"}`, tone: "muted" }];
  if (gateway) hops.push({ key: "gw", label: gateway.name, sub: gateway.model, href: `/inventory/devices/${gateway.id}`, tone: deviceTone(gateway.state) });
  if (core.length) hops.push({ key: "core", label: core.length === 1 ? core[0].name : "Core switching", sub: core.length === 1 ? core[0].model : undefined, count: core.length > 1 ? core.length : undefined, href: core.length === 1 ? `/inventory/devices/${core[0].id}` : "/inventory/devices?role=switching", tone: worst(core) });
  if (access.length) hops.push({ key: "access", label: "Access switches", sub: access.map((d) => d.name).join(", "), count: access.length, href: "/inventory/devices?role=switching", tone: worst(access) });
  if (aps.length) {
    const bad = aps.filter((a) => a.state !== "ONLINE").length;
    hops.push({ key: "aps", label: "Access points", sub: bad ? `${bad} of ${aps.length} not online` : `${aps.length} online`, count: aps.length, href: "/inventory/devices?role=accessPoint", tone: worst(aps) });
  }
  hops.push({ key: "clients", label: "Clients", sub: Object.entries(s.clients.by_type).map(([t, n]) => `${n} ${t.toLowerCase()}`).join(" · "), count: s.clients.total, href: "/inventory/clients", tone: "muted" });
  return (
    <RouteTrace
      bare
      hops={hops}
      label="Route from the internet to clients"
      footer="Route from the internet: upstream links as reported by the controller. A yellow or red destination has devices that are not online."
    />
  );
}

function HealthPanel({ s, devices }: { s?: Summary; devices?: DeviceSummary[] }) {
  if (!s || !devices) return <Skeleton className="h-56 w-full" />;
  const groups = [
    { label: "Gateways", role: "gateway", items: devices.filter((d) => d.features.includes("gateway")) },
    { label: "Switches", role: "switching", items: devices.filter((d) => !d.features.includes("gateway") && !d.features.includes("accessPoint")) },
    { label: "Access points", role: "accessPoint", items: devices.filter((d) => d.features.includes("accessPoint")) },
  ];
  return (
    <Panel title="Estate health" id="health" bodyClassName="p-0">
      <div className="relative overflow-x-auto">
      <table className="w-full text-left">
        <caption className="sr-only">Devices by role and state</caption>
        <thead>
          <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
            <th scope="col" className="h-9 px-2 sm:px-4">Role</th>
            <th scope="col" className="px-2 text-right sm:px-4">Online</th>
            <th scope="col" className="px-2 text-right sm:px-4"><span className="sm:hidden">Attn.</span><span className="hidden sm:inline">Attention</span></th>
            <th scope="col" className="px-2 text-right sm:px-4">Offline</th>
            <th scope="col" className="px-2 text-right sm:px-4">CPU</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const on = g.items.filter((d) => deviceTone(d.state) === "ok").length;
            const warn = g.items.filter((d) => deviceTone(d.state) === "warn").length;
            const off = g.items.filter((d) => deviceTone(d.state) === "crit").length;
            const cpus = g.items.map((d) => d.cpu_pct).filter((x): x is number => x != null);
            return (
              <tr key={g.label} className="border-b border-rule last:border-b-0">
                <th scope="row" className="h-[var(--row-h)] px-2 font-bold sm:px-4">
                  <Link href={`/inventory/devices?role=${g.role}`} className="whitespace-nowrap hover:underline">{g.label}</Link>{" "}
                  <span className="font-normal text-ink-3">{g.items.length}</span>
                </th>
                <td className="tabular px-2 text-right font-semibold text-ok sm:px-4">{on}</td>
                <td className={clsx("tabular px-2 text-right font-semibold sm:px-4", warn ? "text-warn-text" : "text-ink-3")}>{warn}</td>
                <td className={clsx("tabular px-2 text-right font-semibold sm:px-4", off ? "text-crit" : "text-ink-3")}>{off}</td>
                <td className="tabular px-2 text-right text-ink-2 sm:px-4">{cpus.length ? `${Math.round(cpus.reduce((a, b) => a + b, 0) / cpus.length)}%` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-rule px-4 py-3 text-sm text-ink-2">
        <span><b className="text-ink">{s.networks}</b> networks</span>
        <span><b className="text-ink">{s.clients.total}</b> clients</span>
        <span><b className="text-ink">{s.events_24h}</b> events in 24 h</span>
      </div>
    </Panel>
  );
}

/**
 * Revisions: changes made through the change engine, newest first, with
 * their reference, what they did, who asked and where they stand.
 */
function RevisionsPanel() {
  const open = useQuery({ queryKey: ["changes", "open"], queryFn: () => get<{ items: ChangeRequest[] }>("/api/changes?view=open"), refetchInterval: 30_000 });
  const history = useQuery({ queryKey: ["changes", "history"], queryFn: () => get<{ items: ChangeRequest[] }>("/api/changes?view=history"), refetchInterval: 60_000 });
  if (!open.data || !history.data) return <Skeleton className="h-56 w-full" />;
  const rows = [...open.data.items, ...history.data.items]
    .sort((a, b) => (b.finished_at ?? b.applied_at ?? b.proposed_at).localeCompare(a.finished_at ?? a.applied_at ?? a.proposed_at))
    .slice(0, 8);
  return (
    <Panel title="Revisions" id="changes" bodyClassName="p-0" actions={<Link href="/changes" className="text-sm font-semibold text-accent hover:underline">All changes</Link>}>
      {rows.length === 0 ? (
        <EmptyState title="No changes made here yet">Changes proposed, approved and applied through Fabric Warden are listed here with their outcome.</EmptyState>
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[30rem] text-left">
            <caption className="sr-only">Recent changes made through the change engine</caption>
            <thead>
              <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                <th scope="col" className="h-9 px-4">Ref</th>
                <th scope="col" className="px-2">When</th>
                <th scope="col" className="px-2">Change</th>
                <th scope="col" className="px-4">State</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-b border-rule transition-colors duration-[var(--dur-fast)] last:border-b-0 hover:bg-accent-soft">
                  <td className="h-[var(--row-h)] px-4 font-mono text-xs whitespace-nowrap">
                    <Link href={`/changes/${c.id}`} className="text-accent hover:underline">{c.ref}</Link>
                  </td>
                  <td className="px-2 text-sm whitespace-nowrap text-ink-3">
                    <time dateTime={c.finished_at ?? c.applied_at ?? c.proposed_at}>{ago(c.finished_at ?? c.applied_at ?? c.proposed_at)}</time>
                  </td>
                  <td className="px-2 py-2">
                    <span className="block font-semibold">{c.title}</span>
                    <span className="block text-sm text-ink-3">{c.applied_by ?? c.proposed_by}</span>
                  </td>
                  <td className="px-4 whitespace-nowrap"><ChangeState status={c.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function ChangesPanel({ s }: { s?: Summary }) {
  if (!s) return <Skeleton className="h-56 w-full" />;
  return (
    <Panel title="Recent changes" id="changes" bodyClassName="p-0">
      {s.recent_events.length === 0 ? (
        <EmptyState title="No changes recorded yet">Device state, firmware and client changes appear here after each sync.</EmptyState>
      ) : (
        <ol className="relative py-1 before:absolute before:top-4 before:bottom-4 before:left-[31px] before:w-px before:bg-construct">
          {s.recent_events.slice(0, 8).map((e) => {
            const href = eventHref(e);
            const tone: Tone = e.severity === "warning" ? "warn" : e.severity === "critical" ? "crit" : "muted";
            const text = describeEvent(e);
            return (
              <li key={e.id} className="relative flex min-h-[var(--row-h)] items-center gap-3 px-4 py-1.5 transition-colors duration-[var(--dur-fast)] hover:bg-accent-soft">
                <span
                  aria-hidden
                  className={clsx(
                    "relative ml-3 size-2.5 shrink-0 rounded-full border-[1.5px] bg-surface shadow-[0_0_0_3px_var(--surface)]",
                    tone === "crit" ? "border-crit bg-crit" : tone === "warn" ? "border-dashed border-warn-line" : "border-line",
                  )}
                />
                <time dateTime={e.occurred_at} className="w-24 shrink-0 text-sm text-ink-3" title={new Date(e.occurred_at).toLocaleString()}>{ago(e.occurred_at)}</time>
                <span className="min-w-0 flex-1">
                  {tone !== "muted" ? <StateLabel tone={tone}><span className="font-normal text-ink">{text}</span></StateLabel> : text}
                </span>
                {href && <Link href={href} className="shrink-0 text-sm font-semibold text-accent hover:underline">View</Link>}
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

function ControllersPanel({ s }: { s?: Summary }) {
  const qc = useQueryClient();
  const { can } = useSession();
  const sync = useMutation({ mutationFn: (id: string) => post(`/api/controllers/${id}/sync`), onSettled: () => qc.invalidateQueries() });
  if (!s) return <Skeleton className="h-40 w-full" />;
  return (
    <Panel title="Controllers" id="controllers" bodyClassName="p-0" actions={<Link href="/controllers" className="text-sm font-semibold text-accent hover:underline">Manage</Link>}>
      <ul>
        {s.controllers.map((c) => (
          <li key={c.id} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-4 py-2 last:border-b-0">
            <Link href={`/controllers/${c.id}`} className="min-w-40 font-bold hover:underline">{c.name}</Link>
            {c.is_demo && <DemoTag />}
            <StateLabel tone={controllerTone(c.status)}>{titleCase(c.status)}</StateLabel>
            <span className="text-sm text-ink-3">{c.application_version ? <>Network <span className="font-mono">{c.application_version}</span></> : "version unknown"}</span>
            <span className="text-sm text-ink-3">synced {ago(c.last_sync_at)}</span>
            {can("jobs:run") && (
              <Button size="sm" variant="ghost" className="ml-auto" icon={<RefreshCw className={clsx(sync.isPending && sync.variables === c.id && "animate-spin")} />} onClick={() => sync.mutate(c.id)} disabled={sync.isPending}>
                Sync now
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ClientsPanel() {
  const nets = useQuery({ queryKey: ["networks"], queryFn: () => get<{ items: NetworkRow[] }>("/api/inventory/networks") });
  const clients = useQuery({ queryKey: ["clients", ""], queryFn: () => get<{ items: ClientRow[] }>("/api/inventory/clients") });
  if (!nets.data || !clients.data) return <Skeleton className="h-56 w-full" />;
  const rows = nets.data.items.filter((n) => n.subnet);
  const max = Math.max(1, ...rows.map((n) => n.active_clients));
  const unmatched = clients.data.items.filter((c) => !c.network).length;
  return (
    <Panel title="Clients by network" id="clients" actions={<Link href="/inventory/clients" className="text-sm font-semibold text-accent hover:underline">All clients</Link>}>
      {rows.length === 0 ? (
        <Notice tone="info">Network details aren&apos;t available from this controller version, so clients can&apos;t be grouped by subnet.</Notice>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {rows.map((n) => (
            <li key={n.id} className="grid grid-cols-[10rem_1fr_2.5rem] items-center gap-3">
              <Link href={`/inventory/clients?network=${n.id}`} className="truncate font-semibold hover:underline">
                {n.vlan_id != null && <VlanPatch id={n.vlan_id} />} {n.name}
              </Link>
              <span className="h-2 rounded-r-[4px]" aria-hidden>
                <span className="block h-full origin-left animate-[fw-route-draw_700ms_var(--ease-out)_both] rounded-r-[4px] bg-[var(--series-1)]" style={{ width: `${(n.active_clients / max) * 100}%` }} />
              </span>
              <span className="tabular text-right font-semibold">{n.active_clients}</span>
            </li>
          ))}
        </ul>
      )}
      {unmatched > 0 && <p className="mt-3 text-sm text-ink-3">{unmatched} client{unmatched > 1 ? "s" : ""} (VPN or unknown address) not in any listed subnet.</p>}
    </Panel>
  );
}
