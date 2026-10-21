"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Camera, History, Pin } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel } from "@/components/ui/signs";
import { get, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ConfigDiff, SnapshotRow } from "@/lib/types";

const LABEL: Record<string, string> = { networks: "Networks", zones: "Firewall zones", policies: "Firewall policies", wans: "WANs", devices: "Device firmware" };

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** A semantic diff: added and removed objects by name, changed objects with each field's before and after. */
function DiffView({ diff }: { diff: ConfigDiff }) {
  const sections = Object.entries(diff);
  if (!sections.length) return <p className="text-ink-3">No configuration changes.</p>;
  return (
    <div className="flex flex-col gap-4">
      {sections.map(([section, parts]) => (
        <section key={section} aria-label={LABEL[section] ?? section}>
          <h3 className="mb-1.5 text-xs font-bold tracking-wide text-ink-2 uppercase">{LABEL[section] ?? section}</h3>
          <ul className="flex flex-col rounded-[3px] border border-rule">
            {parts.added.map((e) => (
              <li key={`a${e.id}`} className="flex items-baseline gap-2 border-b border-rule bg-ok-soft px-3 py-1.5 last:border-b-0">
                <b aria-hidden className="w-4 text-ok">+</b><span className="font-semibold">{e.name ?? e.id}</span><span className="text-sm text-ink-2">added</span>
              </li>
            ))}
            {parts.removed.map((e) => (
              <li key={`r${e.id}`} className="flex items-baseline gap-2 border-b border-rule bg-crit-soft px-3 py-1.5 last:border-b-0">
                <b aria-hidden className="w-4 text-crit">−</b><span className="font-semibold">{e.name ?? e.id}</span><span className="text-sm text-ink-2">removed</span>
              </li>
            ))}
            {parts.changed.map((e) => (
              <li key={`c${e.id}`} className="border-b border-rule px-3 py-1.5 last:border-b-0">
                <span className="flex items-baseline gap-2"><b aria-hidden className="w-4 text-accent">~</b><span className="font-semibold">{e.name ?? e.id}</span><span className="text-sm text-ink-2">changed</span></span>
                <dl className="mt-1 grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-0.5 pl-6 text-sm">
                  {e.fields?.map((f) => (
                    <div key={f.field} className="contents">
                      <dt className="text-ink-3">{f.field.replace(/_/g, " ")}</dt>
                      <dd className="min-w-0 font-mono text-xs break-words"><span className="text-crit line-through decoration-crit/60">{show(f.from)}</span> <span aria-hidden className="text-ink-3">→</span><span className="sr-only">to</span> <span className="font-bold text-ink">{show(f.to)}</span></dd>
                    </div>
                  ))}
                </dl>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ChangeChips({ changes }: { changes: SnapshotRow["changes"] }) {
  const parts = Object.entries(changes ?? {});
  if (!parts.length) return <span className="text-sm text-ink-3">First snapshot</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {parts.map(([s, c]) => (
        <Chip key={s}>{LABEL[s] ?? s}: {[c.added && `+${c.added}`, c.removed && `−${c.removed}`, c.changed && `~${c.changed}`].filter(Boolean).join(" ")}</Chip>
      ))}
    </span>
  );
}

export default function ConfigPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("config-history");
  const editable = can("inventory:edit");
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const snaps = useQuery({ queryKey: ["config", "snapshots"], queryFn: () => get<{ items: SnapshotRow[]; sites: { id: string; name: string }[] }>("/api/config/snapshots"), enabled: on, refetchInterval: 60_000 });
  const drift = useQuery({ queryKey: ["config", "drift"], queryFn: () => get<{ items: { site: { id: string; name: string; is_demo: boolean }; baseline: SnapshotRow | null; latest: SnapshotRow; diff: ConfigDiff }[] }>("/api/config/drift"), enabled: on });
  const detail = useQuery({ queryKey: ["config", "snapshot", selected], queryFn: () => get<SnapshotRow & { diff: ConfigDiff; previous_id: string | null }>(`/api/config/snapshots/${selected}`), enabled: !!selected });
  const done = () => qc.invalidateQueries({ queryKey: ["config"] });
  const pin = useMutation({ mutationFn: (id: string) => post(`/api/config/snapshots/${id}/baseline`, { note: "" }), onSuccess: done });
  const capture = useMutation({ mutationFn: (siteId: string) => post(`/api/config/capture?site_id=${siteId}`), onSuccess: done });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Config history" name="Configuration History & Backup" canManage={can("modules:manage")}>
        Keep a snapshot of each site&apos;s configuration whenever it changes, and see exactly what changed.
      </ModuleOff>
    );
  }
  const items = snaps.data?.items;
  return (
    <>
      <PageHeader
        title="Config history"
        description="A snapshot of each site's configuration whenever it changes: networks, firewall zones and policies, WANs and device firmware. The controller keeps no change log, so a diff shows what changed between two syncs, not who changed it."
        meta={items?.some((s) => s.is_demo) ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Simulated configuration</span> : undefined}
        actions={editable && snaps.data?.sites[0] && <Button icon={<Camera />} loading={capture.isPending} onClick={() => capture.mutate(snaps.data!.sites[0].id)}>Capture now</Button>}
      />
      {(snaps.error || pin.error) && <Notice tone="crit" className="mb-4">{((snaps.error || pin.error) as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {drift.data?.items.map((d) => {
          const n = Object.values(d.diff).reduce((a, p) => a + p.added.length + p.removed.length + p.changed.length, 0);
          return (
            <Panel
              key={d.site.id}
              title={`Drift from baseline${drift.data!.items.length > 1 ? ` · ${d.site.name}` : ""}`}
              actions={n ? <StateLabel tone="warn">{`${n} change${n > 1 ? "s" : ""} not in the baseline`}</StateLabel> : <StateLabel tone="ok">Matches the baseline</StateLabel>}
            >
              <p className="mb-3 text-sm text-ink-2">
                Baseline {d.baseline ? <>taken {dateTime(d.baseline.taken_at)}{d.baseline.note ? ` · “${d.baseline.note}”` : ""}</> : "not set"}; latest {ago(d.latest.taken_at)}. Pin a newer snapshot as the baseline once you have reviewed its changes.
              </p>
              <DiffView diff={d.diff} />
            </Panel>
          );
        })}
        <div className={clsx("grid gap-[var(--gap)]", selected && "2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] 2xl:items-start")}>
          <Panel title="Snapshots" bodyClassName="p-0">
            {snaps.isLoading ? (
              <div className="p-4"><Skeleton className="h-32" /></div>
            ) : !items?.length ? (
              <EmptyState icon={<History />} title="No snapshots yet">The first snapshot is taken at the next sync.</EmptyState>
            ) : (
              <ol>
                {items.map((s) => (
                  <li key={s.id} className={clsx("flex flex-col gap-2 border-b border-rule px-4 py-2.5 last:border-b-0 md:flex-row md:items-center md:gap-4", selected === s.id && "bg-accent-soft")}>
                    <span className="flex flex-col md:w-52 md:shrink-0">
                      <span className="font-semibold">{dateTime(s.taken_at)}</span>
                      <span className="text-sm text-ink-3">{s.site} · {s.trigger === "manual" ? "captured by hand" : "after a sync"} · <span className="font-mono text-xs">{s.hash}</span></span>
                    </span>
                    <span className="min-w-0 flex-1"><ChangeChips changes={s.changes} /></span>
                    {s.is_baseline && <span className="sign inline-flex h-6 items-center rounded-[3px] bg-accent px-2 text-xs font-bold text-accent-ink">Baseline</span>}
                    {s.is_demo && <DemoTag />}
                    <span className="flex gap-1">
                      {s.change_total > 0 && <Button size="sm" variant={selected === s.id ? "secondary" : "ghost"} aria-pressed={selected === s.id} onClick={() => setSelected(selected === s.id ? null : s.id)}>Changes</Button>}
                      {editable && !s.is_baseline && <Button size="sm" variant="ghost" icon={<Pin />} loading={pin.isPending && pin.variables === s.id} onClick={() => pin.mutate(s.id)}>Make baseline</Button>}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
          {selected && (
            <Panel title="Changes in this snapshot" className="2xl:sticky 2xl:top-4">
              {detail.data ? <DiffView diff={detail.data.diff} /> : <Skeleton className="h-32" />}
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
