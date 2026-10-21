"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronLeft, Plus, Zap } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { type Hop, RouteSign } from "@/components/route-sign";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { EmptyState, KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DeviceState, ProvenanceLine, StateLabel, deviceTone } from "@/components/ui/signs";
import { ApiError, get, patch } from "@/lib/api";
import { describeEvent } from "@/lib/events";
import { ROLE_LABEL, ago, bps, dateTime, deviceRole, speed, uptime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DeviceDetail, DeviceSummary, Port } from "@/lib/types";

export default function DevicePage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["device", id], queryFn: () => get<DeviceDetail>(`/api/inventory/devices/${id}`) });
  const all = useQuery({ queryKey: ["devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });

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
        <Panel title="Upstream route" id="route">
          <RouteSign hops={upstream(d, all.data?.items ?? [])} label={`Path from ${d.name} to the internet`} />
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
          <MetadataPanel d={d} />
        </div>

        {d.ports.length > 0 && (
          <Panel title={`Ports (${d.ports.filter((p) => p.state === "UP").length} of ${d.ports.length} up)`} id="ports">
            <PortStrip ports={d.ports} />
            {hasSfp && (
              <Notice tone="info" className="mt-4" title="Optics, FEC and error counters aren't available yet">
                The UniFi Integration API doesn&apos;t expose SFP optical power, FEC mode or CRC/discard counters. They need the SNMP/SSH collectors planned for the Fiber & High-Speed Diagnostics module.
              </Notice>
            )}
          </Panel>
        )}

        {d.radios.length > 0 && (
          <Panel title="Radios" id="radios" bodyClassName="p-0">
            <table className="w-full text-left">
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
                    <Link href={`/inventory/clients?q=${encodeURIComponent(c.name)}`} className="font-semibold hover:underline">{c.name}</Link>
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
  const hops: Hop[] = chain.map((x) => ({
    key: x.id,
    label: x.name,
    sub: x.model,
    href: x.id === d.id ? undefined : `/inventory/devices/${x.id}`,
    tone: deviceTone(x.state),
  }));
  if (chain.at(-1)?.features.includes("gateway")) hops.push({ key: "wan", label: "Internet", sub: "via WAN", tone: "muted" });
  return hops;
}

function portLabel(p: Port) {
  return `Port ${p.idx}, ${p.connector}, ${p.state === "UP" ? `up at ${speed(p.speed_mbps)}b/s` : "down"}${p.poe ? `, PoE ${p.poe.state === "UP" ? "delivering" : "idle"}` : ""}`;
}

function PortStrip({ ports }: { ports: Port[] }) {
  return (
    <div>
      <ul className="flex flex-wrap gap-1.5" aria-label="Port states">
        {ports.map((p) => {
          const up = p.state === "UP";
          const sfp = p.connector !== "RJ45";
          return (
            <li
              key={p.idx}
              title={portLabel(p)}
              aria-label={portLabel(p)}
              className={clsx(
                "relative flex h-12 flex-col items-center justify-between rounded-[3px] py-1 text-[11px] leading-none font-bold",
                sfp ? "w-14" : "w-11",
                up ? "bg-[#00704a] text-white" : "border border-dashed border-rule-strong text-ink-3",
              )}
            >
              <span className="tabular">{p.idx}</span>
              <span className="tabular">{up ? speed(p.speed_mbps) : sfp ? p.connector.replace("PLUS", "+") : "—"}</span>
              {p.poe?.state === "UP" && <Zap className="absolute -top-1.5 -right-1.5 size-4 rounded-full bg-warn p-0.5 text-black" aria-hidden />}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-3">
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] bg-[#00704a]" /> Link up (speed)</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-[2px] border border-dashed border-rule-strong" /> Down</span>
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
              <button type="button" className="ml-1.5 text-ink-3 hover:text-crit" aria-label={`Remove label ${l}`} onClick={() => save.mutate({ labels: d.labels.filter((x) => x !== l) })}>
                ×
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
