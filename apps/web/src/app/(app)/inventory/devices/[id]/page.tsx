"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronLeft, Pencil, Plus, TriangleAlert, X, Zap } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { MetricWidget, RangePicker } from "@/components/analytics";
import { DevicePhysical } from "@/components/device-physical";
import { type Hop, RouteSign } from "@/components/route-sign";
import { DevicePortCounters, perMinute, useDeviceSnmp } from "@/components/snmp";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { EmptyState, KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DeviceState, ProvenanceLine, StateLabel, deviceTone } from "@/components/ui/signs";
import { ApiError, get, patch } from "@/lib/api";
import { describeEvent } from "@/lib/events";
import { ROLE_LABEL, ago, bps, dateTime, deviceRole, speed, uptime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AlertItem, AssetRecord, DeviceDetail, DeviceSummary, Port, RangeKey, SnmpInterface, Widget } from "@/lib/types";

export default function DevicePage() {
  const { id } = useParams<{ id: string }>();
  const { moduleEnabled } = useSession();
  const q = useQuery({ queryKey: ["device", id], queryFn: () => get<DeviceDetail>(`/api/inventory/devices/${id}`) });
  const all = useQuery({ queryKey: ["devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });
  const snmp = useDeviceSnmp(id);

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
        title={d.name}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <DeviceState state={d.state} />
            <span>{d.model}</span>
            <span className="text-ink-3">{ROLE_LABEL[role]}</span>
          </span>
        }
        meta={<ProvenanceLine p={d.provenance} />}
      />

      <div className="flex flex-col gap-[var(--gap)]">
        <Panel title="Route from the internet" id="route">
          <RouteSign
            hops={upstream(d, all.data?.items ?? [])}
            label={`Path from the internet to ${d.name}`}
            footer={hopsFooter(d, all.data?.items ?? [])}
          />
        </Panel>

        <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Panel title="Details" id="details">
            <KeyValue
              items={[
                ["IP address", <span key="ip" className="font-mono">{d.ip_address ?? "—"}</span>],
                ["MAC address", <span key="mac" className="font-mono">{d.mac_address}</span>],
                ["Firmware", <span key="fw" className="font-mono">{d.firmware_version ?? "—"}{d.firmware_updatable && <span className="ml-2 font-sans text-sm font-bold text-accent">update available</span>}</span>],
                ["Uptime", uptime(d.uptime_sec)],
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

        {d.ports.length > 0 && (
          <Panel title={`Ports (${d.ports.filter((p) => p.state === "UP").length} of ${d.ports.length} up)`} id="ports">
            <PortStrip ports={d.ports} counters={snmp.byPort} />
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

        {d.radios.length > 0 && (
          <Panel title="Radios" id="radios" bodyClassName="overflow-x-auto p-0">
            <table className="w-full text-left whitespace-nowrap">
              <thead>
                <tr className="border-b border-rule text-xs font-bold tracking-wide text-ink-2 uppercase">
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

        <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-2">
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
          <Panel title="History" id="history" bodyClassName="p-0">
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
      <ul className="flex flex-wrap gap-1.5" aria-label="Port states">
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
                "relative flex h-12 flex-col items-center justify-between rounded-[3px] py-1 text-[11px] leading-none font-bold",
                sfp ? "w-14" : "w-11",
                errors ? "sign bg-warn text-warn-ink" : up ? "bg-[#00704a] text-white" : "border border-dashed border-rule-strong text-ink-3",
              )}
              style={errors ? { outlineColor: "#14171a" } : undefined}
            >
              <span className="flex items-center gap-0.5 tabular">{errors && <TriangleAlert className="size-3" aria-hidden strokeWidth={2.75} />}{p.idx}</span>
              <span className="tabular">{up ? speed(p.speed_mbps) : sfp ? p.connector.replace("PLUS", "+") : "—"}</span>
              {p.poe?.state === "UP" && <Zap className="absolute -top-1.5 -right-1.5 size-4 rounded-full bg-warn p-0.5 text-black" aria-hidden />}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-3">
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] bg-[#00704a]" /> Link up (speed)</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] border border-dashed border-rule-strong" /> Down</span>
        {anyErrors && <span className="inline-flex items-center gap-1.5"><TriangleAlert className="size-3.5 rounded-[2px] bg-warn p-0.5 text-black" aria-hidden /> Logging errors (SNMP)</span>}
        <span className="inline-flex items-center gap-1.5"><Zap className="size-3.5 rounded-full bg-warn p-0.5 text-black" /> PoE delivering</span>
        <span>Wider ports are SFP cages.</span>
      </p>
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
  if (!on) return null;
  const firing = alerts.data?.items.filter((a) => a.state === "firing") ?? [];
  const w = (id: string, metric: string, title: string, entity_ids = [d.id], size: Widget["size"] = "third"): Widget => ({ id, metric, title, kind: "line", entity_ids, size, limit: 6 });
  const widgets: Widget[] = [w("cpu", "device.cpu_pct", "CPU"), w("mem", "device.mem_pct", "Memory"), w("load", "device.load_1m", "Load (1 min)")];
  if (d.uplink) widgets.push(w("rx", "device.uplink_rx_bps", "Uplink receive", [d.id], "half"), w("tx", "device.uplink_tx_bps", "Uplink transmit", [d.id], "half"));
  if (d.radios.length) widgets.push(w("retries", "radio.tx_retries_pct", "Wi-Fi retries per radio", d.radios.map((r) => r.id), "half"), w("clients", "device.clients", "Attached clients", [d.id], "half"));
  return (
    <section aria-labelledby="history-h" className="flex flex-col gap-[var(--gap)]">
      {firing.map((a) => (
        <Notice key={a.id} tone={a.severity === "critical" ? "crit" : "warn"} title={`${a.rule} is firing`} action={<Link href="/alerts" className="font-semibold text-accent hover:underline">Open alerts</Link>}>
          {a.summary} · since {ago(a.fired_at)}{a.acknowledged_by ? ` · acknowledged by ${a.acknowledged_by}` : ""}
        </Notice>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="history-h" className="text-md font-bold">Metric history</h2>
        <RangePicker value={range} onChange={setRange} />
      </div>
      <div className="grid grid-cols-1 gap-[var(--gap)] md:grid-cols-6">
        {widgets.map((x) => (
          <MetricWidget key={x.id} widget={x} range={range} label={x.title} height={160} />
        ))}
      </div>
    </section>
  );
}
