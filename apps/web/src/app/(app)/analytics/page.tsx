"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChartLine, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { ApiError, get, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { RANGES } from "@/lib/metrics";
import { type Column, DataTable } from "@/components/ui/table";
import { useSession } from "@/lib/session";
import { clsx } from "clsx";
import type { Dashboard, DashboardRow, DashboardTemplate, MetricCatalog, Widget } from "@/lib/types";

const SPAN = { third: "col-span-2", half: "col-span-3", full: "col-span-6" } as const;

/**
 * A template's layout drawn to scale on the six-column grid in the world's
 * own marks: a reading is a title strip, a line chart a drawn stroke, a ranked
 * list three usage bars. A schematic of the layout, not data.
 */
function LayoutPreview({ widgets, className }: { widgets: Widget[]; className?: string }) {
  return (
    <div aria-hidden className={clsx("grid grid-cols-6 gap-1 rounded-[var(--r-control)] border border-rule bg-surface-2 p-1.5 shadow-[inset_0_1px_2px_rgb(var(--shadow-tint)/0.06)]", className)}>
      {widgets.map((w) =>
        w.kind === "stat" ? (
          <span key={w.id} className="col-span-2 flex h-3 items-center justify-end rounded-[2px] border border-line px-1">
            <span className="h-[3px] w-2 rounded-[1px] bg-accent" />
          </span>
        ) : w.kind === "line" ? (
          <span key={w.id} className={clsx(SPAN[w.size], "h-7 overflow-hidden rounded-[3px] border border-rule bg-surface px-1")}>
            <svg viewBox="0 0 60 20" preserveAspectRatio="none" className="h-full w-full text-accent">
              <path d="M2 15 H16 V9 H30 V12 H44 V6 H58" fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
            </svg>
          </span>
        ) : (
          <span key={w.id} className={clsx(SPAN[w.size], "flex h-7 flex-col justify-center gap-[3px] rounded-[3px] border border-rule bg-surface px-1.5")}>
            {[85, 60, 38].map((p) => (
              <span key={p} className="h-[3px] rounded-[1px] bg-[var(--series-1)]" style={{ width: `${p}%` }} />
            ))}
          </span>
        ),
      )}
    </div>
  );
}

export default function DashboardsPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("analytics-studio");
  const editable = can("analytics:edit");
  const router = useRouter();
  const qc = useQueryClient();
  const [naming, setNaming] = useState(false);
  const list = useQuery({ queryKey: ["dashboards"], queryFn: () => get<{ items: DashboardRow[] }>("/api/analytics/dashboards"), enabled: on });
  const templates = useQuery({ queryKey: ["dashboard-templates"], queryFn: () => get<{ items: DashboardTemplate[] }>("/api/analytics/templates"), enabled: on });
  const catalog = useQuery({ queryKey: ["metric-catalog"], queryFn: () => get<MetricCatalog>("/api/metrics"), enabled: on });
  const fromTemplate = useMutation({
    mutationFn: (template: string) => post<Dashboard>("/api/analytics/dashboards", { template }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["dashboards"] });
      router.push(`/analytics/${d.id}`);
    },
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Dashboards" name="Analytics Studio" canManage={can("modules:manage")}>
        Dashboards and metric history come from the Analytics Studio module. Enable it to start recording device, client and Wi-Fi history on every sync.
      </ModuleOff>
    );
  }

  const rows = list.data?.items;
  const sampled = catalog.data?.items.reduce((n, m) => n + m.series.length, 0) ?? 0;
  const metrics = catalog.data?.items.filter((m) => m.series.length > 0).length ?? 0;
  const columns: Column<DashboardRow>[] = [
    {
      key: "name",
      header: "Dashboard",
      sort: (d) => d.name.toLowerCase(),
      cell: (d) => (
        <span className="flex min-w-0 items-center gap-3 py-1.5">
          <span
            aria-hidden
            className="grid size-8 shrink-0 place-items-center rounded-[3px] border-[1.5px] border-line text-accent"
          >
            <ChartLine className="size-4" />
          </span>
          <span className="flex min-w-0 flex-col">
            <Link href={`/analytics/${d.id}`} className="font-bold hover:underline">{d.name}</Link>
            {d.description && <span className="line-clamp-2 text-sm text-ink-3 md:truncate">{d.description}</span>}
            {/* Phones: the other columns fold in here, so nothing scrolls sideways. */}
            <span className="text-sm text-ink-3 md:hidden">
              {d.widget_count} widget{d.widget_count === 1 ? "" : "s"} · opens at {RANGES.find((x) => x.key === d.default_range)?.label} · updated {ago(d.updated_at)}
            </span>
          </span>
        </span>
      ),
    },
    { key: "w", header: "Widgets", align: "right", sort: (d) => d.widget_count, className: "hidden w-24 md:table-cell", cell: (d) => d.widget_count },
    { key: "range", header: "Opens at", className: "hidden w-28 md:table-cell", cell: (d) => <span className="text-ink-2">{RANGES.find((x) => x.key === d.default_range)?.long}</span> },
    { key: "upd", header: "Updated", sort: (d) => d.updated_at, className: "hidden w-44 md:table-cell", cell: (d) => <span className="text-ink-2">{ago(d.updated_at)}{d.created_by ? ` · ${d.created_by}` : ""}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Dashboards"
        description="Metric history from every sync, arranged the way you want to read it. Charts start from the moment a metrics module was enabled; the controller keeps no history of its own."
        actions={editable && <Button variant="primary" icon={<Plus />} onClick={() => setNaming((v) => !v)} aria-expanded={naming}>New dashboard</Button>}
      />
      {fromTemplate.error && <Notice tone="crit" className="mb-4">{(fromTemplate.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {naming && <NewDashboard onDone={() => setNaming(false)} />}
        {catalog.data && sampled === 0 ? (
          <Notice tone="info" title="No samples yet">
            History builds one sample per sync (every 60 seconds). Charts fill in after the next sync; a full day takes a day.
          </Notice>
        ) : catalog.data ? (
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-ink-2">
            <span aria-hidden className="size-2 rounded-full bg-ok" />
            Recording <b className="text-ink">{sampled}</b> series across <b className="text-ink">{metrics}</b> metrics, one sample per sync.
          </p>
        ) : null}

        <Panel title="Your dashboards" bodyClassName="p-0" actions={rows && rows.length > 0 && <span className="tabular text-sm text-ink-3">{rows.length}</span>}>
          <DataTable
            caption="Dashboards"
            rows={rows}
            columns={columns}
            loading={list.isLoading}
            href={(d) => `/analytics/${d.id}`}
            folds
            empty={
              <EmptyState icon={<ChartLine />} title="No dashboards yet">
                {editable ? "Start from a template below, or build one from scratch with New dashboard." : "Nobody has built a dashboard yet. Someone with the operator role or higher can create one."}
              </EmptyState>
            }
          />
        </Panel>

        <Panel title="Start from a template" bodyClassName="p-0">
          {!templates.data ? (
            <div className="p-4"><Skeleton className="h-24 w-full" /></div>
          ) : (
            <ul>
              {templates.data.items.map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule px-4 py-3 last:border-b-0">
                  <LayoutPreview widgets={t.widgets} className="w-36 shrink-0" />
                  <span className="flex min-w-[12rem] flex-1 flex-col">
                    <span className="font-bold">{t.name}</span>
                    <span className="text-sm text-ink-2">
                      {t.description} <span className="text-ink-3">{t.widgets.length} widgets.</span>
                    </span>
                  </span>
                  {editable && (
                    <Button size="sm" icon={<Plus />} loading={fromTemplate.isPending && fromTemplate.variables === t.id} onClick={() => fromTemplate.mutate(t.id)} aria-label={`Create the ${t.name} dashboard`}>
                      Create
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}

function NewDashboard({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => post<Dashboard>("/api/analytics/dashboards", { name: name.trim() }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["dashboards"] });
      onDone();
      router.push(`/analytics/${d.id}?edit=1`);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message.replace(/^name: /, "") : "Couldn't create the dashboard."),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setError("Give the dashboard a name.");
    setError(null);
    create.mutate();
  };
  return (
    <Panel title="New dashboard">
      <form onSubmit={submit} noValidate className="flex flex-wrap items-start gap-3">
        <Field label="Name" error={error} className="w-full max-w-sm">
          {(p) => <Input {...p} autoFocus value={name} maxLength={128} onChange={(e) => setName(e.target.value)} placeholder="Rack and uplinks" />}
        </Field>
        <div className="flex gap-2 sm:mt-7">
          <Button type="submit" variant="primary" loading={create.isPending}>Create and add widgets</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
