"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronLeft, CircleDashed, Network, Pencil, Plus, RotateCw, Router, TriangleAlert, Wifi, X, Zap } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";
import { MetricWidget, RangePicker, metricQuery } from "@/components/analytics";
import { ChangeState, ProposeChange, useCanPropose } from "@/components/changes";
import { DevicePhysical } from "@/components/device-physical";
import { type Hop, RouteTrace } from "@/components/route-trace";
import { DevicePortCounters, perMinute, useDeviceSnmp } from "@/components/snmp";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DeviceState, ProvenanceLine, StateLabel, deviceTone } from "@/components/ui/signs";
import { ApiError, get, patch } from "@/lib/api";
import { describeEvent } from "@/lib/events";
import { ROLE_LABEL, ago, bps, dateTime, deviceRole, speed, uptime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AlertItem, AssetRecord, ChangeRequest, DeviceDetail, DeviceSummary, Port, RangeKey, SnmpInterface, Widget } from "@/lib/types";

export default function DevicePage() {
  const { id } = useParams<{ id: string }>();
  const { moduleEnabled } = useSession();
  const q = useQuery({ queryKey: ["device", id], queryFn: () => get<DeviceDetail>(`/api/inventory/devices/${id}`) });
  const all = useQuery({ queryKey: ["devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });
  const snmp = useDeviceSnmp(id);
  const canPropose = useCanPropose();

  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this device">{(q.error as Error).message}</Notice>
      </>
    );
  }
  const d = q.data;
  if (!d) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-72" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }

  const stats = d.stats as {
    loadAverage1Min?: number; loadAverage5Min?: number; loadAverage15Min?: number;
    memoryUtilizationPct?: number; uplink?: { txRateBps?: number; rxRateBps?: number }; lastHeartbeatAt?: string;
  };
  const role = deviceRole(d.features);
  const hasSfp = d.ports.some((p) => p.connector.startsWith("SFP") || p.connector.startsWith("QSFP"));

  return (
    <>
      <BackLink />
      <PageHeader
        icon={role === "gateway" ? <Router /> : role === "accessPoint" ? <Wifi /> : <Network />}
        title={d.name}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <DeviceState state={d.state} />
            <span>{d.model}</span>
            <span className="text-ink-3">{ROLE_LABEL[role]}</span>
          </span>
        }
        meta={<ProvenanceLine p={d.provenance} />}
        actions={
          canPropose &&
          d.state === "ONLINE" && (
            <ProposeChange
              operation="device.restart"
              targetId={d.id}
              siteId={d.site_id}
              title={`Propose a restart of ${d.name}`}
              summary={
                role === "gateway"
                  ? "This is the gateway: routing, internet access and DNS stop for every network while it restarts, usually one to three minutes."
                  : "The device is unavailable for one to three minutes, and everything that reaches the network through it loses connectivity meanwhile."
              }
              trigger={<Button icon={<RotateCw />}>Propose restart</Button>}
            />
          )
        }
      />

      <div className="flex flex-col gap-[var(--gap)]">
        <section aria-label="Title block" className="plate marks grid grid-cols-[repeat(auto-fit,minmax(11rem,1fr))] overflow-hidden">
          {(
            [
              ["Model", <span key="m" className="font-mono text-sm">{d.model}</span>],
              ["Firmware", <span key="f" className="font-mono text-sm">{d.firmware_version ?? "—"}{d.firmware_updatable && <span className="ml-2 font-sans text-xs font-semibold text-accent">update available</span>}</span>],
              ["Address", <span key="a" className="font-mono text-sm">{d.ip_address ?? "—"}</span>],
              ["MAC", <span key="c" className="font-mono text-sm">{d.mac_address}</span>],
              ["Up for", uptime(d.uptime_sec)],
              ["Source", d.provenance.source],
              ["Observed", ago(d.provenance.observed_at)],
            ] as [string, ReactNode][]
          ).map(([k, v]) => (
            <div key={k} className="min-w-0 px-4 py-2.5 shadow-[1px_0_0_var(--rule),0_1px_0_var(--rule)]">
              <span className="block text-xs text-ink-3">{k}</span>
              <span className="block break-words">{v}</span>
            </div>
          ))}
        </section>

        {d.ports.length > 0 && (
          <Panel
            title="Front panel"
            id="ports"
            variant="raised"
            actions={
              <span className="font-mono text-xs text-ink-3">
                {d.ports.filter((p) => p.state === "UP").length} of {d.ports.length} up
                {d.ports.some((p) => p.poe) && ` · PoE on ${d.ports.filter((p) => p.poe?.state === "UP").length}`}
              </span>
            }
          >
            <PortStrip ports={d.ports} counters={snmp.byPort} />
            {canPropose && d.state === "ONLINE" && d.ports.some((p) => p.poe?.enabled) && <PoeCycle d={d} />}
            {snmp.error ? (
              <Notice tone="crit" className="mt-4" title="Couldn't load the port counters">{snmp.error.message}</Notice>
            ) : snmp.target && snmp.interfaces ? (
              <DevicePortCounters target={snmp.target} interfaces={snmp.interfaces} />
            ) : (
              !snmp.loading && (
                <Notice tone="info" className="mt-4" title={hasSfp ? "Optics, FEC and error counters aren't available here" : "Per-port throughput and error counters aren't available here"}>
                  The UniFi Integration API doesn&apos;t expose per-port traffic, errors or discards{hasSfp ? ", SFP optical power or FEC mode" : ""}.{" "}
                  {snmp.on ? (
                    <>This device isn&apos;t polled yet: add it on <Link href="/snmp" className="font-semibold text-accent hover:underline">Port counters</Link>.</>
                  ) : (
                    <>The SNMP Port Counters module reads traffic and errors over SNMP.</>
                  )}
                  {hasSfp && " Optical power and FEC stay unavailable: UniFi publishes no MIB for them."}
                </Notice>
              )
            )}
          </Panel>
        )}

        <Panel title="Route from the internet" id="route">
          <RouteTrace
            hops={upstream(d, all.data?.items ?? [])}
            label={`Path from the internet to ${d.name}`}
            footer={hopsFooter(d, all.data?.items ?? [])}
          />
        </Panel>

        <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Panel title="Health" id="details">
            <KeyValue
              items={[
                ["CPU", d.cpu_pct != null ? `${d.cpu_pct.toFixed(1)}%` : "—"],
                ["Memory", stats.memoryUtilizationPct != null ? `${stats.memoryUtilizationPct.toFixed(1)}%` : "—"],
                ["Load (1/5/15 min)", stats.loadAverage1Min != null ? <span key="l" className="tabular">{stats.loadAverage1Min} / {stats.loadAverage5Min} / {stats.loadAverage15Min}</span> : "—"],
                ["Uplink rate", stats.uplink ? <span key="u" className="tabular">↑ {bps(stats.uplink.txRateBps)} · ↓ {bps(stats.uplink.rxRateBps)}</span> : "—"],
                ["Statistics observed", d.stats_observed_at ? `${ago(d.stats_observed_at)} (latest only; the API keeps no history)` : "Not available while the device is not online"],
                ["First seen", dateTime(d.provenance.first_seen_at)],
              ]}
            />
          </Panel>
          <div className="flex flex-col gap-[var(--gap)]">
            <AssetPanel d={d} />
            <MetadataPanel d={d} />
          </div>
        </div>

        <DeviceHistory d={d} />

        <DevicePhysical deviceId={d.id} name={d.name} />


        {d.radios.length > 0 && (
          <Panel title="Radios" id="radios" bodyClassName="overflow-x-auto p-0">
            <table className="w-full text-left whitespace-nowrap">
              <thead>
                <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                  <th scope="col" className="h-9 px-4">Band</th>
                  <th scope="col" className="px-4">Standard</th>
                  <th scope="col" className="px-4 text-right">Channel</th>
                  <th scope="col" className="px-4 text-right">Width</th>
                  <th scope="col" className="px-4 text-right">TX retries</th>
                </tr>
              </thead>
              <tbody>
                {d.radios.map((r) => (
                  <tr key={r.frequency_ghz} className="border-b border-rule last:border-b-0">
                    <th scope="row" className="h-[var(--row-h)] px-4 font-bold">{r.frequency_ghz} GHz</th>
                    <td className="px-4">{r.wlan_standard}</td>
                    <td className="tabular px-4 text-right">{r.channel ?? "—"}</td>
                    <td className="tabular px-4 text-right">{r.channel_width_mhz} MHz</td>
                    <td className={clsx("tabular px-4 text-right", (r.tx_retries_pct ?? 0) > 10 && "font-bold text-warn-text")}>
                      {r.tx_retries_pct != null ? `${r.tx_retries_pct}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}

        <div className="grid grid-cols-1 items-start gap-[var(--gap)] xl:grid-cols-2">
          <Panel title={`Connected here (${d.clients.length + d.downstream.length})`} id="connected" bodyClassName="p-0">
            {d.clients.length + d.downstream.length === 0 ? (
              <EmptyState title="Nothing reports this device as its uplink" />
            ) : (
              <ul>
                {d.downstream.map((x) => (
                  <li key={x.id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-4 py-1.5">
                    <Link href={`/inventory/devices/${x.id}`} className="font-bold hover:underline">{x.name}</Link>
                    <span className="text-sm text-ink-3">{x.model}</span>
                    <span className="ml-auto"><DeviceState state={x.state} /></span>
                  </li>
                ))}
                {d.clients.map((c) => (
                  <li key={c.id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-4 py-1.5 last:border-b-0">
                    <Link href={moduleEnabled("client-intel") ? `/inventory/clients/${c.id}` : `/inventory/clients?q=${encodeURIComponent(c.name)}`} className="font-semibold hover:underline">{c.name}</Link>
                    <span className="font-mono text-sm text-ink-3">{c.ip_address}</span>
                    <span className="ml-auto text-sm text-ink-3">{c.type.toLowerCase()}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <DeviceRevisions id={d.id} />
          <Panel title="Events" id="history" bodyClassName="p-0">
            {d.history.length === 0 ? (
              <EmptyState title="No recorded changes" />
            ) : (
              <ol>
                {d.history.map((e) => (
                  <li key={e.id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-4 py-1.5 last:border-b-0">
                    <time dateTime={e.occurred_at} className="w-36 shrink-0 text-sm text-ink-3">{dateTime(e.occurred_at)}</time>
                    {e.severity === "warning" ? <StateLabel tone="warn"><span className="font-normal text-ink">{describeEvent(e)}</span></StateLabel> : <span>{describeEvent(e)}</span>}
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}

/** Changes made through the change engine whose target is this device. */
function DeviceRevisions({ id }: { id: string }) {
  const { moduleEnabled } = useSession();
  const on = moduleEnabled("change-engine");
  const q = useQuery({
    queryKey: ["changes", "device", id],
    queryFn: async () => {
      const [open, hist] = await Promise.all([get<{ items: ChangeRequest[] }>("/api/changes?view=open"), get<{ items: ChangeRequest[] }>("/api/changes?view=history")]);
      return [...open.items, ...hist.items].filter((c) => c.target.id === id);
    },
    enabled: on,
  });
  if (!on) return null;
  return (
    <Panel title="Revisions touching this device" id="revisions" bodyClassName="p-0">
      {!q.data ? (
        <div className="p-4"><Skeleton className="h-20 w-full" /></div>
      ) : q.data.length === 0 ? (
        <EmptyState title="No changes made to this device here">Restarts and port changes proposed through Fabric Warden are listed here with their outcome.</EmptyState>
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left">
            <thead>
              <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                <th scope="col" className="h-9 px-4">Ref</th>
                <th scope="col" className="px-2">When</th>
                <th scope="col" className="px-2">Change</th>
                <th scope="col" className="px-2">By</th>
                <th scope="col" className="px-4">State</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((c) => (
                <tr key={c.id} className="border-b border-rule last:border-b-0 hover:bg-accent-soft">
                  <td className="h-[var(--row-h)] px-4 font-mono text-xs"><Link href={`/changes/${c.id}`} className="text-accent hover:underline">{c.ref}</Link></td>
                  <td className="px-2 text-sm whitespace-nowrap text-ink-3">{ago(c.finished_at ?? c.applied_at ?? c.proposed_at)}</td>
                  <td className="px-2 py-2 font-semibold">{c.title}</td>
                  <td className="px-2 text-sm">{c.applied_by ?? c.proposed_by}</td>
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

function BackLink() {
  return (
    <Link href="/inventory/devices" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> Devices
    </Link>
  );
}

function upstream(d: DeviceDetail, all: DeviceSummary[]): Hop[] {
  const byId = new Map(all.map((x) => [x.id, x]));
  const chain: DeviceSummary[] = [];
  let cur = byId.get(d.id);
  const seen = new Set<string>();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    chain.push(cur);
    cur = cur.uplink_id ? byId.get(cur.uplink_id) : undefined;
  }
  const hops: Hop[] = chain.reverse().map((x) => ({
    key: x.id,
    label: x.name,
    sub: x.id === d.id ? "You are here" : x.model,
    href: x.id === d.id ? undefined : `/inventory/devices/${x.id}`,
    tone: deviceTone(x.state),
    current: x.id === d.id,
  }));
  if (chain[0]?.features.includes("gateway")) hops.unshift({ key: "wan", label: "Internet", sub: "via WAN", tone: "muted" });
  return hops;
}

function hopsFooter(d: DeviceDetail, all: DeviceSummary[]) {
  const hops = upstream(d, all);
  const reachesInternet = hops[0]?.key === "wan";
  const n = hops.length - 1;
  return reachesInternet
    ? `${n} hop${n === 1 ? "" : "s"} from the internet, following uplinks reported by the controller.`
    : "The controller doesn't report a complete uplink chain to the gateway for this device.";
}

function portLabel(p: Port, c?: SnmpInterface) {
  const base = `Port ${p.idx}, ${p.connector}, ${p.state === "UP" ? `up at ${speed(p.speed_mbps)}b/s` : "down"}${p.poe ? `, PoE ${p.poe.state === "UP" ? "delivering" : "idle"}` : ""}`;
  if (!c || p.state !== "UP") return base;
  const rates = c.rx_bps != null ? `, receiving ${bps(c.rx_bps)}, transmitting ${bps(c.tx_bps)}` : "";
  return `${base}${rates}${c.errors_per_min ? `, ${perMinute(c.errors_per_min)} errors per minute` : ""}`;
}

function PortStrip({ ports, counters }: { ports: Port[]; counters: Map<number, SnmpInterface> }) {
  const anyErrors = ports.some((p) => (counters.get(p.idx)?.errors_per_min ?? 0) > 0);
  return (
    <div>
      {/* The faceplate: ports drawn inside the device's outline, in their real order. */}
      <ul className="flex flex-wrap gap-1.5 rounded-[3px] border-[1.5px] border-line p-3" aria-label="Port states">
        {ports.map((p) => {
          const up = p.state === "UP";
          const sfp = p.connector !== "RJ45";
          const c = counters.get(p.idx);
          const errors = up && (c?.errors_per_min ?? 0) > 0;
          return (
            <li
              key={p.idx}
              title={portLabel(p, c)}
              aria-label={portLabel(p, c)}
              className={clsx(
                "relative flex h-12 flex-col items-center justify-between rounded-[2px] py-1 font-mono text-[10.5px] leading-none font-medium",
                sfp ? "w-14" : "w-11",
                errors
                  ? "border-[1.5px] border-dashed border-warn-line bg-warn-soft text-warn-text"
                  : up
                    ? sfp
                      ? "border-[1.5px] border-line bg-line/25 text-ink"
                      : "border-[1.5px] border-accent bg-accent-soft text-ink"
                    : "border border-dashed border-construct text-ink-3",
              )}
              
            >
              <span className="flex items-center gap-0.5 tabular">{errors && <TriangleAlert className="size-3" aria-hidden strokeWidth={2.75} />}{p.idx}</span>
              <span className="tabular">{up ? speed(p.speed_mbps) : sfp ? p.connector.replace("PLUS", "+") : "—"}</span>
              {p.poe?.state === "UP" && <Zap className="absolute -top-1.5 -right-1.5 size-4 rounded-full border border-ok bg-surface p-0.5 text-ok" aria-hidden />}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-3">
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] border-[1.5px] border-accent bg-accent-soft" /> Link up (speed)</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] border-[1.5px] border-line bg-line/25" /> SFP up</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] border border-dashed border-construct" /> No link</span>
        {anyErrors && <span className="inline-flex items-center gap-1.5"><TriangleAlert className="size-3.5 rounded-[2px] bg-warn p-0.5 text-black" aria-hidden /> Logging errors (SNMP)</span>}
        <span className="inline-flex items-center gap-1.5"><Zap className="size-3.5 rounded-full border border-ok p-0.5 text-ok" /> PoE delivering</span>
        <span>Wider ports are SFP cages.</span>
      </p>
    </div>
  );
}

/** Propose a PoE power-cycle for one port; the change is reviewed and approved on its own page. */
function PoeCycle({ d }: { d: DeviceDetail }) {
  const poe = d.ports.filter((p) => p.poe?.enabled);
  const [idx, setIdx] = useState(poe[0].idx);
  return (
    <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-rule pt-4">
      <Field label="Power-cycle PoE" hint="The powered device loses power for a few seconds and reboots" className="w-56">
        {(p) => (
          <Select {...p} value={idx} onChange={(e) => setIdx(Number(e.target.value))}>
            {poe.map((x) => <option key={x.idx} value={x.idx}>Port {x.idx}{x.poe?.state === "UP" ? ", delivering" : ", idle"}</option>)}
          </Select>
        )}
      </Field>
      <ProposeChange
        operation="port.power_cycle"
        targetId={d.id}
        siteId={d.site_id}
        params={{ port_idx: idx }}
        title={`Propose a PoE power-cycle of ${d.name} port ${idx}`}
        summary="Whatever is powered by this port loses power for a few seconds and reboots. The UniFi API does not say which device is on a port, so check that first."
        trigger={<Button icon={<Zap />}>Propose power-cycle</Button>}
      />
    </div>
  );
}

function MetadataPanel({ d }: { d: DeviceDetail }) {
  const { can } = useSession();
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState(d.notes);
  const [err, setErr] = useState<string | null>(null);
  const editable = can("inventory:edit");
  const save = useMutation({
    mutationFn: (body: { labels?: string[]; notes?: string }) => patch(`/api/inventory/devices/${d.id}`, body),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ["device", d.id] }); qc.invalidateQueries({ queryKey: ["devices"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const addLabel = (e: FormEvent) => {
    e.preventDefault();
    const v = label.trim();
    if (!v) return;
    save.mutate({ labels: [...d.labels, v] });
    setLabel("");
  };
  return (
    <Panel title="Labels & notes" id="meta">
      <p className="mb-3 text-sm text-ink-3">Yours, not the controller&apos;s: discovery never overwrites them.</p>
      <div className="flex flex-wrap items-center gap-1.5">
        {d.labels.length === 0 && <span className="text-sm text-ink-3">No labels.</span>}
        {d.labels.map((l) => (
          <Chip key={l}>
            {l}
            {editable && (
              <button type="button" className="-mr-1 ml-1 rounded-[2px] p-0.5 text-ink-3 hover:text-crit" aria-label={`Remove label ${l}`} onClick={() => save.mutate({ labels: d.labels.filter((x) => x !== l) })}>
                <X className="size-3.5" aria-hidden />
              </button>
            )}
          </Chip>
        ))}
      </div>
      {editable && (
        <form onSubmit={addLabel} className="mt-3 flex gap-2">
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Add a label, e.g. rack-a" aria-label="New label" maxLength={48} className="max-w-60" />
          <Button type="submit" size="md" icon={<Plus />} disabled={!label.trim()}>Add</Button>
        </form>
      )}
      <div className="mt-5">
        <label htmlFor="notes" className="text-sm font-semibold">Notes</label>
        <Textarea id="notes" className="mt-1.5" value={notes} onChange={(e) => setNotes(e.target.value)} readOnly={!editable} placeholder={editable ? "Where it lives, what it feeds, quirks…" : "No notes."} />
        {editable && (
          <div className="mt-2 flex items-center gap-3">
            <Button size="sm" variant="primary" onClick={() => save.mutate({ notes })} loading={save.isPending} disabled={notes === d.notes}>Save notes</Button>
            {err && <span className="text-sm font-semibold text-crit">{err}</span>}
          </div>
        )}
      </div>
    </Panel>
  );
}

const ASSET_FIELDS: { key: keyof AssetRecord; label: string; date?: boolean; mono?: boolean; placeholder?: string }[] = [
  { key: "asset_tag", label: "Asset tag", mono: true, placeholder: "FW-0042" },
  { key: "serial_number", label: "Serial number", mono: true, placeholder: "From the device label" },
  { key: "location", label: "Location", placeholder: "Rack A, U12" },
  { key: "owner", label: "Owner", placeholder: "Person or team" },
  { key: "supplier", label: "Supplier" },
  { key: "purchased_on", label: "Purchased", date: true },
  { key: "warranty_until", label: "Warranty until", date: true },
];

function warranty(until?: string) {
  if (!until) return null;
  const days = Math.ceil((new Date(`${until}T00:00:00`).getTime() - Date.now()) / 86_400_000);
  if (days < 0) return <StateLabel tone="warn">Expired {Math.abs(days)} day{days === -1 ? "" : "s"} ago</StateLabel>;
  if (days <= 90) return <StateLabel tone="warn">Ends in {days} day{days === 1 ? "" : "s"}</StateLabel>;
  return <StateLabel tone="ok">In warranty</StateLabel>;
}

function AssetPanel({ d }: { d: DeviceDetail }) {
  const { can } = useSession();
  const qc = useQueryClient();
  const editable = can("inventory:edit");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<AssetRecord>(d.asset);
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      patch(`/api/inventory/devices/${d.id}`, {
        asset: Object.fromEntries(Object.entries(form).map(([k, v]) => [k, typeof v === "string" && v.trim() ? v.trim() : null])),
      }),
    onSuccess: () => {
      setErr(null);
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["device", d.id] });
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const empty = ASSET_FIELDS.every((f) => !d.asset[f.key]);
  return (
    <Panel
      title="Asset record"
      id="asset"
      actions={editable && !editing && <Button size="sm" icon={<Pencil />} onClick={() => { setForm(d.asset); setEditing(true); }}>{empty ? "Add" : "Edit"}</Button>}
    >
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="grid gap-4 sm:grid-cols-2"
        >
          {ASSET_FIELDS.map((f) => (
            <Field key={f.key} label={f.label}>
              {(p) => (
                <Input
                  {...p}
                  type={f.date ? "date" : "text"}
                  className={f.mono ? "font-mono" : undefined}
                  value={form[f.key] ?? ""}
                  maxLength={128}
                  placeholder={f.placeholder}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                />
              )}
            </Field>
          ))}
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <Button type="submit" variant="primary" size="sm" loading={save.isPending}>Save asset record</Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
            {err && <span role="alert" className="text-sm font-semibold text-crit">{err}</span>}
          </div>
        </form>
      ) : empty ? (
        <p className="text-sm text-ink-3">
          No asset details yet. The UniFi API doesn&apos;t report serial numbers or purchase data, so they come from you.
        </p>
      ) : (
        <KeyValue
          items={ASSET_FIELDS.filter((f) => d.asset[f.key]).map((f) => [
            f.label,
            f.key === "warranty_until" ? (
              <span key={f.key} className="inline-flex flex-wrap items-center gap-x-3">{dateOnly(d.asset[f.key])} {warranty(d.asset.warranty_until)}</span>
            ) : f.date ? (
              dateOnly(d.asset[f.key])
            ) : (
              <span key={f.key} className={f.mono ? "font-mono" : undefined}>{d.asset[f.key]}</span>
            ),
          ])}
        />
      )}
    </Panel>
  );
}

function dateOnly(v?: string) {
  return v ? new Date(`${v}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—";
}

const METRIC_MODULES = ["analytics-studio", "monitoring", "wan", "snmp"];

/** "CPU, Memory or Load": a short list in words. */
function listWords(items: string[]): string {
  return items.length < 2 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

/** Metric history for this device, when a module that records it is enabled. */
function DeviceHistory({ d }: { d: DeviceDetail }) {
  const { moduleEnabled } = useSession();
  const [range, setRange] = useState<RangeKey>("24h");
  const on = METRIC_MODULES.some(moduleEnabled);
  const alerts = useQuery({
    queryKey: ["alerts", "entity", d.id],
    queryFn: () => get<{ items: AlertItem[] }>(`/api/monitoring/alerts?entity_id=${d.id}`),
    enabled: moduleEnabled("monitoring"),
    refetchInterval: 60_000,
  });
  const w = (id: string, metric: string, title: string, entity_ids = [d.id], size: Widget["size"] = "third"): Widget => ({ id, metric, title, kind: "line", entity_ids, size, limit: 6 });
  const widgets: Widget[] = [w("cpu", "device.cpu_pct", "CPU"), w("mem", "device.mem_pct", "Memory"), w("load", "device.load_1m", "Load (1 min)")];
  if (d.uplink) widgets.push(w("rx", "device.uplink_rx_bps", "Uplink receive", [d.id], "half"), w("tx", "device.uplink_tx_bps", "Uplink transmit", [d.id], "half"));
  if (d.radios.length) widgets.push(w("retries", "radio.tx_retries_pct", "Wi-Fi retries per radio", d.radios.map((r) => r.id), "half"), w("clients", "device.clients", "Attached clients", [d.id], "half"));
  // The same cached queries the widgets load: when none has any series in range, one notice replaces a grid of empty charts.
  const results = useQueries({ queries: widgets.map((x) => metricQuery({ metric: x.metric, entity_ids: x.entity_ids, range, limit: x.limit }, on)) });
  const isEmpty = (i: number) => !!results[i]?.data && results[i].data!.series.length === 0;
  const empty = widgets.filter((_, i) => isEmpty(i));
  const shown = widgets.filter((_, i) => !isEmpty(i));
  if (!on) return null;
  const firing = alerts.data?.items.filter((a) => a.state === "firing") ?? [];
  return (
    <section aria-labelledby="metric-history-h" className="flex flex-col gap-[var(--gap)]">
      {firing.map((a) => (
        <Notice key={a.id} tone={a.severity === "critical" ? "crit" : "warn"} title={`${a.rule} is firing`} action={<Link href="/alerts" className="font-semibold text-accent hover:underline">Open alerts</Link>}>
          {a.summary} · since {ago(a.fired_at)}{a.acknowledged_by ? ` · acknowledged by ${a.acknowledged_by}` : ""}
        </Notice>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="metric-history-h" className="text-md font-bold">Metric history</h2>
        <RangePicker value={range} onChange={setRange} />
      </div>
      {shown.length > 0 && (
        <div className="grid grid-cols-1 gap-[var(--gap)] md:grid-cols-6">
          {shown.map((x) => (
            <MetricWidget key={x.id} widget={x} range={range} label={x.title} height={160} />
          ))}
        </div>
      )}
      {empty.length > 0 && (
        <div className="plate-quiet flex gap-3 px-4 py-3 text-sm">
          <CircleDashed className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
          <p className="text-ink-2">
            <b className="text-ink">No {listWords(empty.map((x) => x.title))} history in this range.</b>{" "}
            {d.state !== "ONLINE"
              ? `${d.name} is ${d.state === "CONNECTION_INTERRUPTED" ? "interrupted" : d.state.toLowerCase().replaceAll("_", " ")}; nothing is sampled while it is not online.`
              : "History builds one sample per sync from the moment a metrics module is enabled; try a shorter range."}
          </p>
        </div>
      )}
    </section>
  );
}
