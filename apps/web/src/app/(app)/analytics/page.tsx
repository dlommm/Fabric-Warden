"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChartLine, LayoutTemplate, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, get, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { RANGES } from "@/lib/metrics";
import { useSession } from "@/lib/session";
import type { Dashboard, DashboardRow, DashboardTemplate, MetricCatalog } from "@/lib/types";

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
  const columns: Column<DashboardRow>[] = [
    {
      key: "name",
      header: "Dashboard",
      sort: (d) => d.name.toLowerCase(),
      cell: (d) => (
        <span className="flex min-w-0 flex-col py-1">
          <Link href={`/analytics/${d.id}`} className="font-bold hover:underline">{d.name}</Link>
          {d.description && <span className="truncate text-sm text-ink-3">{d.description}</span>}
        </span>
      ),
    },
    { key: "w", header: "Widgets", align: "right", sort: (d) => d.widget_count, className: "w-24", cell: (d) => d.widget_count },
    { key: "range", header: "Opens at", className: "w-28", cell: (d) => <span className="text-ink-2">{RANGES.find((r) => r.key === d.default_range)?.long}</span> },
    { key: "upd", header: "Updated", sort: (d) => d.updated_at, className: "w-36", cell: (d) => <span className="text-ink-2">{ago(d.updated_at)}{d.created_by ? ` · ${d.created_by}` : ""}</span> },
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
        {catalog.data && sampled === 0 && (
          <Notice tone="info" title="No samples yet">
            History builds one sample per sync (every 60 seconds). Charts fill in after the next sync; a full day takes a day.
          </Notice>
        )}
        <Panel title="Your dashboards" bodyClassName="p-0" actions={rows && rows.length > 0 && <span className="tabular text-sm text-ink-3">{rows.length}</span>}>
          <DataTable
            caption="Dashboards"
            rows={rows}
            columns={columns}
            loading={list.isLoading}
            href={(d) => `/analytics/${d.id}`}
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
                  <LayoutTemplate className="size-4 shrink-0 text-ink-3" aria-hidden />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="font-bold">{t.name}</span>
                    <span className="text-sm text-ink-2">{t.description} <span className="text-ink-3">{t.widgets.length} widgets.</span></span>
                  </span>
                  {editable && (
                    <Button size="sm" loading={fromTemplate.isPending && fromTemplate.variables === t.id} onClick={() => fromTemplate.mutate(t.id)}>
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
