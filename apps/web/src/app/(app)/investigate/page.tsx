"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Bell, FileClock, FolderOpen, MapPin, ScanSearch, ScrollText, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag } from "@/components/ui/signs";
import { get } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Result {
  query: string;
  kind: "ip" | "mac" | "name";
  addresses: string[];
  entities: { type: string; id: string; name: string; detail: string | null; href: string; is_demo: boolean }[];
  timeline: { at: string; kind: string; title: string; detail: unknown; severity: string; source: string; is_demo?: boolean }[];
  days: number;
}

const KIND = { event: { icon: Zap, word: "Event" }, sighting: { icon: MapPin, word: "Seen" }, log: { icon: ScrollText, word: "Log" }, alert: { icon: Bell, word: "Alert" }, audit: { icon: FileClock, word: "Change" }, case: { icon: FolderOpen, word: "Case" } } as const;
const SEVERE = new Set(["emergency", "alert", "critical", "error", "high"]);

export default function InvestigatePage() {
  return (
    <Suspense>
      <Investigate />
    </Suspense>
  );
}

function Investigate() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("investigations");
  const params = useSearchParams();
  const router = useRouter();
  const q = params.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  const [days, setDays] = useState("7");
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const res = useQuery({ queryKey: ["investigate", q, days], queryFn: () => get<Result>(`/api/investigate?q=${encodeURIComponent(q)}&days=${days}`), enabled: on && q.length >= 2 });
  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Investigate" name="Investigation Workbench" canManage={can("modules:manage")}>
        Follow an IP, MAC or name across inventory, logs, alerts, changes and cases on one timeline.
      </ModuleOff>
    );
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (draft.trim().length >= 2) router.replace(`/investigate?q=${encodeURIComponent(draft.trim())}`);
  };
  const r = res.data;
  const shown = r?.timeline.filter((t) => !kinds.size || kinds.has(t.kind)) ?? [];
  const counts = r ? r.timeline.reduce<Record<string, number>>((m, t) => ({ ...m, [t.kind]: (m[t.kind] ?? 0) + 1 }), {}) : {};
  return (
    <>
      <PageHeader title="Investigate" description="Start from an IP address, MAC address or name. Everything Fabric Warden knows about it — inventory, sightings, log lines, alerts, changes and cases — lands on one timeline." />
      <form onSubmit={submit} className="mb-[var(--gap)] flex flex-wrap items-center gap-2">
        <label htmlFor="inv-q" className="sr-only">IP, MAC or name</label>
        <Input id="inv-q" autoFocus className="w-full max-w-md font-mono" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="10.0.30.101, 02:c1:00:00:00:01 or unraid-tower" />
        <Select aria-label="Time window" className="w-36" value={days} onChange={(e) => setDays(e.target.value)}>
          <option value="1">Last day</option>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </Select>
        <Button type="submit" variant="primary" icon={<ScanSearch />}>Investigate</Button>
      </form>
      {res.error && <Notice tone="crit">{(res.error as Error).message}</Notice>}
      {!q ? (
        <Panel><EmptyState icon={<ScanSearch />} title="Nothing to follow yet">Enter an address or a name. From Logs, SOC and device pages, addresses link here.</EmptyState></Panel>
      ) : !r ? (
        <Skeleton className="h-80" />
      ) : (
        <div className="grid gap-[var(--gap)] xl:grid-cols-[18rem_minmax(0,1fr)] xl:items-start">
          <Panel title="Matches" bodyClassName="p-0" className="xl:sticky xl:top-4">
            {r.entities.length === 0 ? (
              <p className="px-4 py-3 text-sm text-ink-3">No inventory object matches {r.kind === "ip" ? "this address" : r.kind === "mac" ? "this MAC" : "this name"}. Log lines that mention it are still shown.</p>
            ) : (
              <ul>
                {r.entities.map((e) => (
                  <li key={`${e.type}-${e.id}`} className="border-b border-rule last:border-b-0">
                    <Link href={e.href} className="flex flex-col px-4 py-2 hover:bg-surface-2">
                      <span className="flex items-center gap-2"><span className="font-bold">{e.name}</span>{e.is_demo && <DemoTag />}</span>
                      <span className="text-sm text-ink-3">{e.type}{e.detail ? ` · ${e.detail}` : ""}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {r.addresses.length > 0 && <p className="border-t border-rule px-4 py-2 text-xs text-ink-3">Searching <span className="font-mono">{r.addresses.join(", ")}</span></p>}
          </Panel>
          <Panel
            title={`Timeline · ${r.timeline.length} entr${r.timeline.length === 1 ? "y" : "ies"} in ${r.days} day${r.days > 1 ? "s" : ""}`}
            actions={
              <span className="flex flex-wrap gap-1" role="group" aria-label="Filter by kind">
                {Object.entries(counts).map(([k, n]) => (
                  <button key={k} type="button" aria-pressed={kinds.has(k)} onClick={() => setKinds((s) => { const x = new Set(s); if (x.has(k)) x.delete(k); else x.add(k); return x; })}>
                    <Chip className={kinds.has(k) ? "border-accent bg-accent-soft text-ink" : undefined}>{KIND[k as keyof typeof KIND]?.word ?? k} {n}</Chip>
                  </button>
                ))}
              </span>
            }
            bodyClassName="p-0"
          >
            {shown.length === 0 ? (
              <p className="px-4 py-3 text-sm text-ink-3">Nothing in this window.</p>
            ) : (
              <ol>
                {shown.map((t, i) => {
                  const k = KIND[t.kind as keyof typeof KIND] ?? KIND.event;
                  return (
                    <li key={i} className={clsx("grid grid-cols-[1.25rem_1fr] gap-x-3 border-b border-rule px-4 py-2 last:border-b-0 md:grid-cols-[1.25rem_10rem_1fr]", SEVERE.has(t.severity) && "bg-crit-soft")}>
                      <k.icon className="mt-0.5 size-4 text-ink-3" aria-label={k.word} />
                      <span className="text-xs text-ink-3 md:pt-0.5">{dateTime(t.at)}</span>
                      <span className="col-start-2 min-w-0 md:col-start-3">
                        <span className="block break-words text-sm">{t.title}</span>
                        <span className="text-xs text-ink-3">{t.source}{SEVERE.has(t.severity) ? ` · ${t.severity}` : ""}</span>
                        {t.is_demo && <DemoTag className="ml-2 align-middle" />}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </Panel>
        </div>
      )}
    </>
  );
}
