"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowRight, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Field, Select } from "@/components/ui/field";
import { FigureStrip, KpiPlate } from "@/components/ui/kpi";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DataTable } from "@/components/ui/table";
import { Chip, DemoTag, StateLabel } from "@/components/ui/signs";
import { del, get, post } from "@/lib/api";
import { ago, bytes, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Controller } from "@/lib/types";

interface Addr { ip: string; scope: "private" | "public"; entity: { type: "client" | "device"; id: string; name: string } | null }
interface Summary {
  hours: number;
  totals: { bytes: number; packets: number; flows: number; conversations: number };
  sampling: { rates: number[]; unreported_rows: number; rows: number };
  has_demo: boolean;
  bucket_seconds: number;
  series: { t: number; bytes: number; packets: number }[];
  talkers: (Addr & { sent: number; received: number })[];
  conversations: { src: Addr; dst: Addr; protocol: string; port: number | null; bytes: number; packets: number; flows: number; last_seen: string }[];
  ports: { protocol: string; port: number | null; bytes: number; flows: number; sources: number }[];
  by_ip_version: Record<string, number>;
}
interface Exporter { address: string; domain: number; version: number; sampling: number | null; packets: number; flows: number; undecoded_sets: number; templates: number; fields: string[]; last_at: string | null }
interface Status {
  enabled: boolean;
  listening: string | null;
  error: string | null;
  port: number | null;
  datagrams: number;
  flows: number;
  stored: number;
  dropped: number;
  malformed: number;
  rejected: number;
  last_received_at: string | null;
  retention_days: number;
  assumed_sampling: number | null;
  exporters: Exporter[];
}

const WINDOWS = [
  { hours: 1, label: "Last hour" },
  { hours: 24, label: "Last 24 hours" },
  { hours: 168, label: "Last 7 days" },
];
const VERSION_LABEL: Record<number, string> = { 5: "NetFlow v5", 9: "NetFlow v9", 10: "IPFIX (v10)" };
const count = (n: number) => n.toLocaleString();
const compact = (n: number) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
const service = (p: { protocol: string; port: number | null }) => (p.port ? `${p.protocol}/${p.port}` : p.protocol);

function AddrLabel({ a, onPick }: { a: Addr; onPick: (ip: string) => void }) {
  return (
    <button type="button" onClick={() => onPick(a.ip)} className="min-w-0 truncate text-left hover:underline" title={`Show only ${a.ip}`}>
      {a.entity ? (
        <span className="text-sm font-semibold text-accent">{a.entity.name}</span>
      ) : (
        <span className="font-mono text-xs font-semibold text-accent">{a.ip}</span>
      )}
      {a.entity ? <span className="ml-1.5 font-mono text-[11px] text-ink-3">{a.ip}</span> : a.scope === "public" && <span className="ml-1.5 text-xs text-ink-3">public</span>}
    </button>
  );
}

/** Bytes per bucket as plain bars, one series. */
function Volume({ s }: { s: Summary }) {
  const max = Math.max(1, ...s.series.map((p) => p.bytes));
  const span = s.hours >= 48 ? `${s.hours / 24} days ago` : `${s.hours} h ago`;
  return (
    <figure>
      <div className="flex h-24 items-end gap-px" role="img" aria-label={`${bytes(s.totals.bytes)} in the window; busiest interval ${bytes(max)}`}>
        {s.series.map((p) => (
          <span
            key={p.t}
            className="min-w-[3px] flex-1 rounded-t-[1px] bg-[var(--series-1)]"
            style={{ height: p.bytes ? `${Math.max(4, (100 * p.bytes) / max)}%` : "1px", opacity: p.bytes ? 1 : 0.35 }}
            title={`${new Date(p.t * 1000).toLocaleString()}: ${bytes(p.bytes)}`}
          />
        ))}
      </div>
      <figcaption className="mt-1 flex justify-between font-mono text-[11px] text-ink-3">
        <span>{span}</span>
        <span>busiest {bytes(max)} per {s.bucket_seconds >= 3600 ? `${s.bucket_seconds / 3600} h` : `${Math.round(s.bucket_seconds / 60)} min`}</span>
        <span>now</span>
      </figcaption>
    </figure>
  );
}

/** Sent and received as two series on one bar, scaled to the busiest talker. */
function Talkers({ s, onPick }: { s: Summary; onPick: (ip: string) => void }) {
  const max = Math.max(1, ...s.talkers.map((t) => t.sent + t.received));
  return (
    <>
      <p className="flex gap-4 px-4 pt-3 text-xs text-ink-3">
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-[1px] bg-[var(--series-1)]" aria-hidden /> Sent</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-[1px] bg-[var(--series-2)]" aria-hidden /> Received</span>
      </p>
      <ol className="px-4 pb-2">
        {s.talkers.map((t) => (
          <li key={t.ip} className="border-b border-rule py-1.5 last:border-b-0">
            <div className="flex items-baseline justify-between gap-3">
              <AddrLabel a={t} onPick={onPick} />
              <span className="shrink-0 font-mono text-xs tabular text-ink-2">{bytes(t.sent + t.received)}</span>
            </div>
            <div className="mt-1 flex h-1.5 gap-px" role="img" aria-label={`Sent ${bytes(t.sent)}, received ${bytes(t.received)}`}>
              <span className="rounded-l-[1px] bg-[var(--series-1)]" style={{ width: `${(100 * t.sent) / max}%` }} />
              <span className="rounded-r-[1px] bg-[var(--series-2)]" style={{ width: `${(100 * t.received) / max}%` }} />
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

function SamplingState({ s, st }: { s: Summary; st?: Status }) {
  if (s.sampling.rates.length) {
    return <span className="text-ink-2">estimated from 1 in {s.sampling.rates.map(count).join(", 1 in ")} packets</span>;
  }
  if (s.sampling.unreported_rows) {
    return <StateLabel tone="warn">{st?.assumed_sampling ? `assumed 1 in ${st.assumed_sampling}` : "sampling not reported"}</StateLabel>;
  }
  return <span className="text-ink-3">as exported</span>;
}

function Exporters({ st }: { st: Status }) {
  return (
    <Panel title="Exporters" variant="quiet" bodyClassName="p-0">
      <ul>
        {st.exporters.map((e) => (
          <li key={`${e.address}-${e.domain}`} className="border-b border-rule px-4 py-2.5 last:border-b-0">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
              <Link href={`/investigate?q=${e.address}`} className="font-mono text-xs font-semibold text-accent hover:underline">{e.address}</Link>
              <span className="font-semibold">{VERSION_LABEL[e.version] ?? `v${e.version}`}</span>
              <span className="text-ink-2">{e.sampling ? <>1 in <b className="font-mono tabular text-ink">{count(e.sampling)}</b> sampled</> : "sampling not reported"}</span>
              <span className="text-ink-2"><b className="font-mono tabular text-ink">{count(e.flows)}</b> flows from <span className="font-mono tabular">{count(e.packets)}</span> exports</span>
              <span className="text-ink-3">last {e.last_at ? ago(e.last_at) : "never"}</span>
            </div>
            {e.version === 5 && <p className="mt-1 text-sm text-ink-2">NetFlow v5 carries IPv4 only. Choose v9 or IPFIX on the gateway to see IPv6 traffic.</p>}
            {e.undecoded_sets > 0 && (
              <p className="mt-1 text-sm"><StateLabel tone="warn">{`${count(e.undecoded_sets)} data sets arrived before their template`}</StateLabel> <span className="text-ink-2">They are skipped until the exporter resends its templates.</span></p>
            )}
            {e.fields.length > 0 && (
              <details className="mt-1.5 text-sm">
                <summary className="cursor-pointer text-ink-2 hover:text-ink">{e.templates} templates · {e.fields.length} fields exported</summary>
                <p className="mt-1.5 flex flex-wrap gap-1.5">{e.fields.map((f) => <Chip key={f} className="font-mono font-normal">{f}</Chip>)}</p>
              </details>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

export default function FlowsPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("flows");
  const manage = can("soc:manage");
  const qc = useQueryClient();
  const router = useRouter();
  const search = useSearchParams();
  const ip = search.get("ip") ?? "";
  const hours = Number(search.get("hours")) || 24;
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(search);
    if (v) next.set(k, v);
    else next.delete(k);
    router.replace(`/flows${next.size ? `?${next}` : ""}`, { scroll: false });
  };
  const params = new URLSearchParams({ hours: String(hours), ...(ip ? { ip } : {}) });
  const summary = useQuery({ queryKey: ["flows", "summary", params.toString()], queryFn: () => get<Summary>(`/api/flows/summary?${params}`), enabled: on, refetchInterval: 30_000, placeholderData: (p) => p });
  const status = useQuery({ queryKey: ["flows", "status"], queryFn: () => get<Status>("/api/flows/status"), enabled: on, refetchInterval: 15_000 });
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers"), enabled: on });
  const done = () => qc.invalidateQueries({ queryKey: ["flows"] });
  const demo = useMutation({ mutationFn: () => post("/api/flows/demo"), onSuccess: done });
  const undemo = useMutation({ mutationFn: () => del("/api/flows/demo"), onSuccess: done });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Flows" name="Traffic & Flow Analytics" canManage={can("modules:manage")}>
        Collect NetFlow or IPFIX from your gateway and see who talks to whom, on which ports, and how much.
      </ModuleOff>
    );
  }
  const st = status.data;
  const s = summary.data;
  const err = summary.error || demo.error || undemo.error;
  const v6 = s ? (s.by_ip_version.IPv6 ?? 0) : 0;
  return (
    <>
      <PageHeader
        title="Flows"
        description="Traffic your gateway exports as NetFlow v5, v9 or IPFIX, summed per minute and matched to the client or device behind each address. Only routed traffic reaches the gateway, so traffic between two devices on the same network is not here."
        meta={s?.has_demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes demo flows</span> : undefined}
        actions={
          manage && (s?.has_demo ? <Button variant="ghost" loading={undemo.isPending} onClick={() => undemo.mutate()}>Remove demo flows</Button> : controllers.data?.some((c) => c.is_demo) && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Load demo flows</Button>)
        }
      />
      {err && <Notice tone="crit" className="mb-4">{(err as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {st && (
          st.error ? (
            <Notice tone="crit" title="The flow collector is not listening">{st.error}</Notice>
          ) : !st.enabled ? (
            <Notice tone="info" title="The flow collector is off">
              Set <code className="font-mono">WARDEN_FLOW_PORT</code> to receive flows. With Docker Compose it listens on 2055/UDP, published on localhost; set <code className="font-mono">WARDEN_FLOW_BIND=0.0.0.0</code> to accept exports from the LAN. In UniFi, NetFlow is under Settings, CyberSecure, Traffic Logging: point the collector address and port at this host.
            </Notice>
          ) : (
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
              <StateLabel tone="ok">{`Listening on ${st.listening}`}</StateLabel>
              <span>
                <b className="text-ink tabular">{count(st.flows)}</b> flows from <b className="text-ink tabular">{count(st.datagrams)}</b> exports
                {st.malformed ? <> · <b className="text-warn-text tabular">{count(st.malformed)}</b> unreadable</> : null}
                {st.rejected ? <> · <b className="text-warn-text tabular">{count(st.rejected)}</b> from senders not allowed</> : null}
                {st.dropped ? <> · <b className="text-warn-text tabular">{count(st.dropped)}</b> dropped</> : null}
              </span>
              <span>last {st.last_received_at ? ago(st.last_received_at) : "never"} · kept {st.retention_days} days</span>
            </p>
          )
        )}
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Window" className="w-44">
            {(p) => (
              <Select {...p} value={String(hours)} onChange={(e) => setParam("hours", e.target.value === "24" ? "" : e.target.value)}>
                {WINDOWS.map((w) => <option key={w.hours} value={w.hours}>{w.label}</option>)}
              </Select>
            )}
          </Field>
          {ip && (
            <span className="inline-flex h-9 items-center gap-2 rounded-[3px] border border-accent bg-accent-soft pr-1 pl-3 text-sm">
              Only <span className="font-mono text-xs font-semibold">{ip}</span>
              <button type="button" onClick={() => setParam("ip", "")} className="grid size-7 place-items-center rounded-[3px] text-ink-2 hover:bg-surface-2 hover:text-ink" aria-label="Show all addresses"><X className="size-4" /></button>
            </span>
          )}
          {ip && <Link href={`/investigate?q=${ip}`} className="self-center text-sm font-semibold text-accent hover:underline">Investigate {ip}</Link>}
        </div>
        {!s ? (
          <Skeleton className="h-28" />
        ) : s.sampling.rows === 0 ? (
          <Panel>
            <EmptyState icon={<Activity />} title={ip ? `No flows for ${ip} in this window` : "No flows in this window"}>
              {ip ? "Pick a longer window, or clear the address filter." : "Point your gateway's NetFlow export at this host, or load demo flows to see the page with data."}
            </EmptyState>
          </Panel>
        ) : (
          <>
            <FigureStrip label="Traffic in numbers">
              <KpiPlate name="Traffic" value={bytes(s.totals.bytes)} state={<SamplingState s={s} st={st} />} />
              <KpiPlate name="Packets" value={<span title={count(s.totals.packets)}>{compact(s.totals.packets)}</span>} />
              <KpiPlate name="Flow records" value={<span title={count(s.totals.flows)}>{compact(s.totals.flows)}</span>} />
              <KpiPlate name="Conversations" value={count(s.totals.conversations)} of="address pairs" />
              <KpiPlate name="IPv6 share" value={s.totals.bytes ? `${Math.round((100 * v6) / s.totals.bytes)}%` : "—"} state={<span className="text-ink-3">{bytes(v6)}</span>} />
            </FigureStrip>
            <Panel title={WINDOWS.find((w) => w.hours === hours)?.label ?? `Last ${hours} hours`}>
              <Volume s={s} />
            </Panel>
            <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <Panel title="Top talkers" bodyClassName="p-0">
                <Talkers s={s} onPick={(a) => setParam("ip", a)} />
              </Panel>
              <Panel title="Service ports" bodyClassName="p-0">
                <ol className="px-4 py-2">
                  {s.ports.map((p) => (
                    <li key={service(p)} className="flex items-baseline justify-between gap-3 border-b border-rule py-1.5 last:border-b-0">
                      <span className="font-mono text-xs font-semibold">{service(p)}</span>
                      <span className="min-w-0 flex-1 truncate text-sm text-ink-3">{count(p.sources)} {p.sources === 1 ? "source" : "sources"} · {count(p.flows)} flows</span>
                      <span className="shrink-0 font-mono text-xs tabular text-ink-2">{bytes(p.bytes)}</span>
                    </li>
                  ))}
                </ol>
                <p className="border-t border-rule px-4 py-2 text-xs text-ink-3">The service port is the lower of the two ports in each flow, a heuristic that holds for most client-to-server traffic.</p>
              </Panel>
            </div>
            <Panel title="Conversations" bodyClassName="p-0">
              <DataTable
                caption="Largest conversations in the window, one direction per row"
                rows={s.conversations.map((c) => ({ ...c, id: `${c.src.ip}>${c.dst.ip}/${service(c)}` }))}
                initialSort={{ key: "bytes", dir: "desc" }}
                columns={[
                  {
                    key: "path",
                    header: "Source → destination",
                    cell: (c) => (
                      <span className="flex min-w-0 items-center gap-2">
                        <AddrLabel a={c.src} onPick={(a) => setParam("ip", a)} />
                        <ArrowRight className="size-3.5 shrink-0 text-ink-3" aria-label="to" />
                        <AddrLabel a={c.dst} onPick={(a) => setParam("ip", a)} />
                      </span>
                    ),
                  },
                  { key: "service", header: "Service", cell: (c) => <span className="font-mono text-xs">{service(c)}</span>, sort: (c) => service(c) },
                  { key: "bytes", header: "Bytes", align: "right", cell: (c) => <span className="font-mono text-xs tabular">{bytes(c.bytes)}</span>, sort: (c) => c.bytes },
                  { key: "packets", header: "Packets", align: "right", cell: (c) => <span className="font-mono text-xs tabular">{count(c.packets)}</span>, sort: (c) => c.packets },
                  { key: "last", header: "Last seen", cell: (c) => <span className="text-sm text-ink-2" title={dateTime(c.last_seen)}>{ago(c.last_seen)}</span>, sort: (c) => c.last_seen },
                ]}
              />
            </Panel>
          </>
        )}
        {st && st.exporters.length > 0 && <Exporters st={st} />}
      </div>
    </>
  );
}
