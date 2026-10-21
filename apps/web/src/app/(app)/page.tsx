"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ArrowDown, ArrowUp, CircleCheck, RefreshCw, Settings2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { type Hop, RouteSign } from "@/components/route-sign";
import { Button, ButtonLink } from "@/components/ui/button";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, type Tone, VlanPatch, controllerTone, deviceTone } from "@/components/ui/signs";
import { get, post } from "@/lib/api";
import { describeEvent, eventHref } from "@/lib/events";
import { ago, titleCase } from "@/lib/format";
import { DEFAULT_PREFS, useSession } from "@/lib/session";
import type { ClientRow, DeviceSummary, NetworkRow, OverviewPanel, Summary } from "@/lib/types";

const PANEL_LABEL: Record<OverviewPanel, string> = {
  attention: "Needs attention",
  route: "Route from the internet",
  health: "Estate health",
  changes: "Recent changes",
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

/** Greedy split by weight; each column keeps the user's chosen order. */
function balance(ids: OverviewPanel[], s?: Summary): [OverviewPanel[], OverviewPanel[]] {
  const sorted = [...ids].sort((a, b) => panelWeight(b, s) - panelWeight(a, s));
  const cols: [Set<OverviewPanel>, Set<OverviewPanel>] = [new Set(), new Set()];
  const h = [0, 0];
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
      {id === "attention" && <AttentionPanel s={s} />}
      {id === "route" && <RoutePanel devices={devices.data?.items} s={s} />}
      {id === "health" && <HealthPanel s={s} devices={devices.data?.items} />}
      {id === "changes" && <ChangesPanel s={s} />}
      {id === "controllers" && <ControllersPanel s={s} />}
      {id === "clients" && <ClientsPanel />}
    </>
  );

  return (
    <>
      <PageHeader
        title="Overview"
        meta={s?.demo_only ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Everything here comes from the simulated controller.</span> : undefined}
        actions={
          <Button icon={<Settings2 />} onClick={() => setEditing((e) => !e)} aria-expanded={editing}>
            {editing ? "Done" : "Customize"}
          </Button>
        }
      />
      {editing && <Customizer panels={panels} onChange={(p) => setPrefs({ overview_panels: p })} />}
      {s && s.controllers.length === 0 ? (
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
          {panels.filter((id) => WIDE.has(id)).map((id) => (
            <div key={id}>{renderPanel(id)}</div>
          ))}
          <div className="grid grid-cols-1 items-start gap-[var(--gap)] xl:grid-cols-2">
            {balance(panels.filter((id) => !WIDE.has(id)), s).map((col, i) => (
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

interface Issue {
  key: string;
  tone: Tone;
  title: string;
  detail: string;
  href?: string;
}

function issuesFrom(s: Summary): Issue[] {
  const out: Issue[] = [];
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
      out.push({ key: `d-${d.id}`, tone: deviceTone(d.state), title: `${d.name} is ${d.state === "CONNECTION_INTERRUPTED" ? "interrupted" : d.state.toLowerCase()}`, detail: `${d.model} · ${d.ip_address ?? d.mac_address}`, href: `/inventory/devices/${d.id}` });
    }
  }
  const fw = s.attention.filter((d) => d.firmware_updatable);
  if (fw.length) {
    out.push({ key: "fw", tone: "info", title: `Firmware update available on ${fw.length} device${fw.length > 1 ? "s" : ""}`, detail: fw.map((d) => d.name).join(", "), href: "/inventory/devices" });
  }
  const order: Record<Tone, number> = { crit: 0, warn: 1, info: 2, muted: 3, ok: 4 };
  return out.sort((a, b) => order[a.tone] - order[b.tone]);
}

function AttentionPanel({ s }: { s?: Summary }) {
  if (!s) return <Skeleton className="h-28 w-full" />;
  const issues = issuesFrom(s);
  if (issues.length === 0) {
    return (
      <section aria-label="Needs attention" className="flex items-center gap-3 rounded-[var(--radius)] border border-ok/40 bg-ok-soft px-5 py-4">
        <CircleCheck className="size-5 text-ok" aria-hidden />
        <p className="font-bold">Nothing needs attention.</p>
        <p className="text-ink-2">All devices online, controllers syncing, data fresh.</p>
      </section>
    );
  }
  const severe = issues.filter((i) => i.tone === "crit" || i.tone === "warn").length;
  const notes = issues.length - severe;
  return (
    <section aria-labelledby="attention-h" className="sign rounded-[var(--radius)] bg-warn text-warn-ink" style={{ outlineColor: "#14171a" }}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 pt-4 pb-2">
        <TriangleAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
        <h2 id="attention-h" className="text-lg font-bold">
          {severe > 0 ? `${severe} thing${severe > 1 ? "s" : ""} need${severe > 1 ? "" : "s"} attention` : "Nothing urgent"}
        </h2>
        {notes > 0 && <span className="text-base font-semibold">and {notes} notice{notes > 1 ? "s" : ""}</span>}
      </div>
      <ul className="mx-2 mb-2 rounded-[3px] bg-surface text-ink">
        {issues.map((i) => (
          <li key={i.key} className="border-b border-rule last:border-b-0">
            <Link href={i.href ?? "#"} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-0.5 px-3 py-2 hover:bg-warn-soft">
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

function RoutePanel({ devices, s }: { devices?: DeviceSummary[]; s?: Summary }) {
  if (!devices || !s) return <Skeleton className="h-32 w-full" />;
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
    <Panel title="Route from the internet" id="route">
      <RouteSign
        hops={hops}
        label="Network path from the internet to clients"
        footer="Upstream links as reported by the controller. A yellow or red destination has devices that are not online."
      />
    </Panel>
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
      <div className="overflow-x-auto">
      <table className="w-full text-left">
        <caption className="sr-only">Devices by role and state</caption>
        <thead>
          <tr className="border-b border-rule text-xs font-bold tracking-wide text-ink-2 uppercase">
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

function ChangesPanel({ s }: { s?: Summary }) {
  if (!s) return <Skeleton className="h-56 w-full" />;
  return (
    <Panel title="Recent changes" id="changes" bodyClassName="p-0">
      {s.recent_events.length === 0 ? (
        <EmptyState title="No changes recorded yet">Device state, firmware and client changes appear here after each sync.</EmptyState>
      ) : (
        <ol>
          {s.recent_events.slice(0, 8).map((e) => {
            const href = eventHref(e);
            const tone: Tone = e.severity === "warning" ? "warn" : e.severity === "critical" ? "crit" : "muted";
            const text = describeEvent(e);
            return (
              <li key={e.id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-4 py-1.5 last:border-b-0">
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
              <span className="h-3 rounded-[2px] bg-surface-3" aria-hidden>
                <span className="block h-full rounded-[2px] bg-[#00704a]" style={{ width: `${(n.active_clients / max) * 100}%` }} />
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
