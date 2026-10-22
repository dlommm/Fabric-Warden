"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ScrollText } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ConnectionsPanel } from "@/components/connections";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, type Tone } from "@/components/ui/signs";
import { del, get, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Controller } from "@/lib/types";

interface LogRow {
  id: string;
  received_at: string;
  source_ip: string;
  host: string;
  app: string;
  severity: number;
  severity_name: string;
  category: string;
  message: string;
  fmt: string;
  fields: Record<string, unknown>;
  device: { id: string; name: string } | null;
  client: { id: string; name: string } | null;
  is_demo: boolean;
}
interface Stats {
  total: number;
  by_category: Record<string, number>;
  by_severity: Record<string, number>;
  top_sources: { source_ip: string; host: string; count: number }[];
  bucket_seconds: number;
  series: { t: number; count: number }[];
}
interface Status { enabled: boolean; listening: string | null; error: string | null; port: number | null; received: number; stored: number; dropped: number; last_received_at: string | null; retention_days: number }

const CATS = ["firewall", "ids", "auth", "system", "other"];
const CAT_LABEL: Record<string, string> = { firewall: "Firewall", ids: "IDS", auth: "Auth", system: "System", other: "Other" };
const PAGE = 50;

/** Consecutive identical lines (same sender, severity and message) fold into one row with a count. */
function collapse(rows: LogRow[]): (LogRow & { repeat: number; first: string })[] {
  const out: (LogRow & { repeat: number; first: string })[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (last && last.source_ip === r.source_ip && last.severity === r.severity && last.message === r.message) {
      last.repeat += 1;
      last.first = r.received_at;
    } else out.push({ ...r, repeat: 1, first: r.received_at });
  }
  return out;
}
const sevTone = (s: number): Tone => (s <= 3 ? "crit" : s === 4 ? "warn" : s <= 6 ? "muted" : "muted");

/** Volume per bucket as plain bars: a count over time, so one ink-2 series, no state colour. */
function Volume({ s }: { s: Stats }) {
  const max = Math.max(1, ...s.series.map((p) => p.count));
  if (!s.series.length) return <p className="text-sm text-ink-3">No events in this window.</p>;
  return (
    <figure>
      <div className="flex h-20 items-end gap-px" role="img" aria-label={`${s.total} events; busiest bucket ${max}`}>
        {s.series.map((p) => (
          <span key={p.t} className="min-w-[3px] flex-1 rounded-t-[1px] bg-[var(--series-1)]" style={{ height: `${Math.max(4, (100 * p.count) / max)}%` }} title={`${new Date(p.t * 1000).toLocaleTimeString()}: ${p.count}`} />
        ))}
      </div>
      <figcaption className="mt-1 flex justify-between font-mono text-[11px] text-ink-3">
        <span>24 h ago</span>
        <span>busiest {max} per {Math.round(s.bucket_seconds / 60)} min</span>
        <span>now</span>
      </figcaption>
    </figure>
  );
}

export default function LogsPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("logs");
  const manage = can("soc:manage");
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [sev, setSev] = useState("7");
  const [shown, setShown] = useState(PAGE);
  const params = new URLSearchParams({ hours: "24", max_severity: sev, ...(q.trim() ? { q: q.trim() } : {}), ...(cat ? { category: cat } : {}) });
  const list = useQuery({ queryKey: ["logs", "search", params.toString()], queryFn: () => get<{ items: LogRow[] }>(`/api/logs?${params}`), enabled: on, refetchInterval: 15_000, placeholderData: (p) => p });
  const stats = useQuery({ queryKey: ["logs", "stats"], queryFn: () => get<Stats>("/api/logs/stats"), enabled: on, refetchInterval: 30_000 });
  const status = useQuery({ queryKey: ["logs", "status"], queryFn: () => get<Status>("/api/logs/status"), enabled: on, refetchInterval: 15_000 });
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers"), enabled: on });
  const done = () => qc.invalidateQueries({ queryKey: ["logs"] });
  const demo = useMutation({ mutationFn: () => post("/api/logs/demo"), onSuccess: done });
  const undemo = useMutation({ mutationFn: () => del("/api/logs/demo"), onSuccess: done });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Logs" name="Log Management" canManage={can("modules:manage")}>
        Receive syslog from your gateway and servers, search it and see who is noisy.
      </ModuleOff>
    );
  }
  const st = status.data;
  const s = stats.data;
  const rows = list.data?.items;
  const folded = rows ? collapse(rows) : undefined;
  const hasDemo = rows?.some((r) => r.is_demo);
  return (
    <>
      <PageHeader
        title="Logs"
        description="Syslog from your gateway, switches and servers (RFC 3164, RFC 5424 and CEF), matched to the device or client that sent it. Categories are a keyword heuristic."
        meta={hasDemo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes demo log lines</span> : undefined}
        actions={
          manage && (hasDemo ? <Button variant="ghost" loading={undemo.isPending} onClick={() => undemo.mutate()}>Remove demo logs</Button> : controllers.data?.some((c) => c.is_demo) && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Load demo logs</Button>)
        }
      />
      {(list.error || demo.error) && <Notice tone="crit" className="mb-4">{((list.error || demo.error) as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {st && (
          st.error ? (
            <Notice tone="crit" title="The syslog receiver is not listening">{st.error}</Notice>
          ) : !st.enabled ? (
            <Notice tone="info" title="The syslog receiver is off">Set <code className="font-mono">WARDEN_SYSLOG_PORT</code> to receive logs. With Docker Compose it listens on 5514/UDP, published on localhost; set <code className="font-mono">WARDEN_SYSLOG_BIND=0.0.0.0</code> in deploy/.env to accept logs from the LAN.</Notice>
          ) : (
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
              <StateLabel tone="ok">{`Listening on ${st.listening}`}</StateLabel>
              <span><b className="text-ink tabular">{st.received.toLocaleString()}</b> received · <b className="text-ink tabular">{st.stored.toLocaleString()}</b> stored{st.dropped ? <> · <b className="text-warn-text tabular">{st.dropped}</b> dropped</> : null}</span>
              <span>last {st.last_received_at ? ago(st.last_received_at) : "never"} · kept {st.retention_days} days</span>
            </p>
          )
        )}
        <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel title={s ? `Last 24 hours · ${s.total.toLocaleString()} events` : "Last 24 hours"}>
            {s ? <Volume s={s} /> : <Skeleton className="h-20" />}
            {s && (
              <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Filter by category">
                {CATS.map((c) => (
                  <button key={c} type="button" aria-pressed={cat === c} onClick={() => setCat(cat === c ? "" : c)} className={clsx("inline-flex h-7 items-center gap-2 rounded-[3px] border px-2.5 text-sm font-semibold", cat === c ? "border-accent bg-accent-soft text-ink" : "border-rule-strong bg-surface text-ink-2 hover:bg-surface-2")}>
                    {CAT_LABEL[c]} <span className="tabular text-ink-3">{s.by_category[c] ?? 0}</span>
                  </button>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Noisiest senders" bodyClassName="p-0">
            {!s?.top_sources.length ? (
              <p className="px-4 py-3 text-sm text-ink-3">Nothing received yet.</p>
            ) : (
              <ol>
                {s.top_sources.map((t) => (
                  <li key={t.source_ip} className="flex items-center gap-3 border-b border-rule px-4 py-1.5 last:border-b-0">
                    <Link href={`/investigate?q=${t.source_ip}`} className="font-mono text-xs font-semibold text-accent hover:underline">{t.source_ip}</Link>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink-2">{t.host}</span>
                    <span className="tabular text-sm">{t.count.toLocaleString()}</span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
        <ConnectionsPanel />
        <Panel bodyClassName="p-0">
          <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
            <Field label="Search" className="w-full sm:w-80">{(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Text, host, app or address" />}</Field>
            <Field label="Severity" className="w-48">
              {(p) => (
                <Select {...p} value={sev} onChange={(e) => setSev(e.target.value)}>
                  <option value="7">Everything</option>
                  <option value="4">Warning and worse</option>
                  <option value="3">Error and worse</option>
                  <option value="2">Critical and worse</option>
                </Select>
              )}
            </Field>
            <span className="ml-auto self-center text-sm text-ink-3">{rows ? `${rows.length} newest${rows.length === 200 ? " (narrow the search to see older)" : ""}` : ""}</span>
          </div>
          {!rows ? (
            <div className="p-4"><Skeleton className="h-40" /></div>
          ) : rows.length === 0 ? (
            <EmptyState icon={<ScrollText />} title="No log lines match">Change the search, severity or category, or point a device&apos;s syslog at this host.</EmptyState>
          ) : (
            <>
            <ol className="font-mono text-xs">
              {folded!.slice(0, shown).map((r) => (
                <li key={r.id} className="grid grid-cols-[auto_1fr] gap-x-3 border-b border-rule px-4 py-1.5 last:border-b-0 md:grid-cols-[9rem_8rem_10rem_1fr]">
                  <span className="text-ink-3" title={dateTime(r.received_at)}>{new Date(r.received_at).toLocaleTimeString()}</span>
                  <span className="md:order-none"><StateLabel tone={sevTone(r.severity)} className="text-[11px]">{r.severity_name}</StateLabel></span>
                  <span className="col-span-2 truncate md:col-span-1" title={r.source_ip}>
                    {r.device ? <Link href={`/inventory/devices/${r.device.id}`} className="font-sans font-semibold text-accent hover:underline">{r.device.name}</Link> : r.client ? <Link href={`/inventory/clients/${r.client.id}`} className="font-sans font-semibold text-accent hover:underline">{r.client.name}</Link> : <span>{r.host || r.source_ip}</span>}
                    {r.app && <span className="text-ink-3"> {r.app}</span>}
                  </span>
                  <span className="col-span-2 break-words text-ink md:col-span-1">
                    <span className="mr-2 font-sans text-[11px] font-semibold text-ink-3 uppercase">{r.category}</span>
                    {r.message}
                    {r.repeat > 1 && <span className="ml-2 font-sans text-[11px] font-bold text-ink-2" title={`First at ${dateTime(r.first)}`}>× {r.repeat}</span>}
                    {r.is_demo && <DemoTag className="ml-2 align-middle" />}
                  </span>
                </li>
              ))}
            </ol>
            {folded!.length > shown && (
              <div className="border-t border-rule px-4 py-2">
                <Button size="sm" variant="ghost" onClick={() => setShown((n) => n + PAGE)}>Show {Math.min(PAGE, folded!.length - shown)} more of {folded!.length - shown}</Button>
              </div>
            )}
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
