"use client";

// Firewall log lines read as connections: top sources, destinations, ports and
// the rules that fire. Counts are logged connections, not bytes.
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";
import { Panel, Skeleton } from "@/components/ui/layout-bits";
import { get } from "@/lib/api";

type Counts = { allow: number; block: number; unknown: number };
interface Connections {
  lines: number;
  totals: Counts;
  sources: (Counts & { src: string; client: { id: string; name: string } | null })[];
  destinations: (Counts & { dst: string })[];
  ports: (Counts & { port: string })[];
  rules: { rule: string; rule_id: string | null; ruleset: string | null; action: string; hits: number; sources: number }[];
  note: string;
}

function Split({ c }: { c: Counts }) {
  return (
    <span className="tabular text-sm whitespace-nowrap">
      {c.block > 0 && <b className="text-ink">{c.block.toLocaleString()} blocked</b>}
      {c.block > 0 && c.allow > 0 && <span className="text-ink-3"> · </span>}
      {c.allow > 0 && <span className="text-ink-2">{c.allow.toLocaleString()} allowed</span>}
    </span>
  );
}

function List<T>({ title, rows, label }: { title: string; rows: (T & Counts)[]; label: (r: T) => ReactNode }) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-1 text-sm font-semibold text-ink-3">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-3">None.</p>
      ) : (
        <ol>
          {rows.slice(0, 8).map((r, i) => (
            <li key={i} className="flex items-baseline justify-between gap-3 border-b border-rule py-1 last:border-b-0">
              <span className="min-w-0 truncate">{label(r)}</span>
              <Split c={r} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function ConnectionsPanel() {
  const q = useQuery({ queryKey: ["logs", "connections"], queryFn: () => get<Connections>("/api/logs/connections?hours=24"), refetchInterval: 60_000 });
  const d = q.data;
  return (
    <Panel
      title={d ? `Connections · ${d.lines.toLocaleString()} firewall lines in 24 h` : "Connections"}
      actions={d && <span className="text-sm text-ink-2"><b className="tabular text-ink">{d.totals.block.toLocaleString()}</b> blocked · <span className="tabular">{d.totals.allow.toLocaleString()}</span> allowed</span>}
    >
      {!d ? (
        <Skeleton className="h-32" />
      ) : d.lines === 0 ? (
        <p className="text-sm text-ink-3">No firewall lines yet. Turn on logging for the policies you care about in UniFi, and send Activity Logging to this receiver.</p>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid gap-5 md:grid-cols-2 2xl:grid-cols-4">
            <List
              title="Sources"
              rows={d.sources}
              label={(r) => (
                <Link href={`/investigate?q=${r.src}`} className="font-mono text-xs font-semibold text-accent hover:underline">
                  {r.client ? <span className="font-sans text-sm">{r.client.name}</span> : r.src}
                </Link>
              )}
            />
            <List title="Destinations" rows={d.destinations} label={(r) => <Link href={`/investigate?q=${r.dst}`} className="font-mono text-xs font-semibold text-accent hover:underline">{r.dst}</Link>} />
            <List title="Ports" rows={d.ports} label={(r) => <span className="font-mono text-xs">{r.port}</span>} />
            <section aria-label="Rules" className="min-w-0">
              <h3 className="mb-1 text-sm font-semibold text-ink-3">Rules that fire</h3>
              <ol>
                {d.rules.slice(0, 8).map((r) => (
                  <li key={r.rule} className="flex items-baseline justify-between gap-3 border-b border-rule py-1 last:border-b-0">
                    <span className="min-w-0 truncate text-sm" title={`${r.ruleset ?? ""} ${r.rule_id ?? ""}`}>
                      <span className={r.action === "block" ? "font-semibold" : "text-ink-2"}>{r.rule}</span>
                    </span>
                    <span className="tabular text-sm whitespace-nowrap text-ink-2">{r.hits.toLocaleString()} · {r.sources} src</span>
                  </li>
                ))}
              </ol>
            </section>
          </div>
          <p className="text-xs text-ink-3">{d.note}</p>
        </div>
      )}
    </Panel>
  );
}
