"use client";

import { useIsFetching, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { MetricWidget, RangePicker, ReadingsPanel, entityWord } from "@/components/analytics";
import { SERIES_COLORS } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { del, get, patch } from "@/lib/api";
import { RANGES, isRange } from "@/lib/metrics";
import { useSession } from "@/lib/session";
import type { Dashboard, MetricCatalog, RangeKey, Widget, WidgetKind, WidgetSize } from "@/lib/types";

/** When the widgets last finished loading, from the metric queries themselves; they refresh every minute. */
function Freshness() {
  const fetching = useIsFetching({ queryKey: ["metrics"] });
  const [at, setAt] = useState<number | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    if (fetching === 0) setAt(Date.now());
  }, [fetching]);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 10_000);
    return () => clearInterval(t);
  }, []);
  const secs = at ? Math.round((Date.now() - at) / 1000) : null;
  return (
    <span className="ml-auto inline-flex items-center gap-1.5 text-sm text-ink-2" role="status" aria-live="off">
      <RefreshCw className={fetching ? "size-3.5 animate-spin text-accent" : "size-3.5 text-ink-3"} aria-hidden />
      {fetching ? "Refreshing…" : secs == null ? "Loading…" : `Updated ${secs < 10 ? "just now" : `${secs < 60 ? `${Math.floor(secs / 10) * 10} s` : `${Math.floor(secs / 60)} min`} ago`} · every minute`}
    </span>
  );
}

const KIND_LABEL: Record<WidgetKind, string> = { line: "Line chart", stat: "Reading (one line, full width)", top: "Ranked list" };

/** Runs of consecutive readings become one panel; everything else stays a widget. */
function segments(widgets: Widget[]) {
  const out: { kind: "stat" | "other"; items: { w: Widget; i: number }[] }[] = [];
  widgets.forEach((w, i) => {
    const kind = w.kind === "stat" ? "stat" : "other";
    const last = out[out.length - 1];
    if (last && last.kind === kind && kind === "stat") last.items.push({ w, i });
    else out.push({ kind, items: [{ w, i }] });
  });
  return out;
}
const SIZE_LABEL: Record<WidgetSize, string> = { third: "Third", half: "Half", full: "Full width" };
const GROUP_LABEL: Record<string, string> = {
  site: "Site",
  device: "Devices",
  radio: "Wi-Fi radios",
  network: "Networks",
  controller: "Controllers",
  probe: "WAN probe",
};

export default function DashboardPage() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useSession();
  const editable = can("analytics:edit");
  const q = useQuery({ queryKey: ["dashboard", id], queryFn: () => get<Dashboard>(`/api/analytics/dashboards/${id}`) });
  const catalog = useQuery({ queryKey: ["metric-catalog"], queryFn: () => get<MetricCatalog>("/api/metrics"), refetchInterval: 60_000 });
  const [draft, setDraft] = useState<Dashboard | null>(null);
  const [editing, setEditing] = useState(Boolean(search.get("edit")) && editable);
  const [adding, setAdding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const d = q.data;
  const current = editing ? (draft ?? d) : d;
  const range: RangeKey = isRange(search.get("range")) ? (search.get("range") as RangeKey) : (d?.default_range ?? "24h");
  const setRange = (r: RangeKey) => {
    const p = new URLSearchParams(search);
    p.set("range", r);
    router.replace(`?${p}`, { scroll: false });
  };

  const save = useMutation({
    mutationFn: (x: Dashboard) => patch<Dashboard>(`/api/analytics/dashboards/${id}`, { name: x.name, description: x.description, widgets: x.widgets, default_range: x.default_range }),
    onSuccess: (x) => {
      qc.setQueryData(["dashboard", id], x);
      qc.invalidateQueries({ queryKey: ["dashboards"] });
      setEditing(false);
      setDraft(null);
      setAdding(false);
    },
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/analytics/dashboards/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dashboards"] });
      router.push("/analytics");
    },
  });

  const labels = useMemo(() => new Map(catalog.data?.items.map((m) => [m.id, m.label])), [catalog.data]);
  const demo = catalog.data?.items.some((m) => m.series.some((s) => s.is_demo));

  if (q.error) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <Notice tone="crit">{(q.error as Error).message}</Notice>
      </>
    );
  }
  if (!d || !current) return <Skeleton className="h-96 w-full" />;

  const edit = (fn: (x: Dashboard) => Dashboard) => setDraft(fn(draft ?? d));
  const move = (i: number, by: -1 | 1) =>
    edit((x) => {
      const w = [...x.widgets];
      [w[i], w[i + by]] = [w[i + by], w[i]];
      return { ...x, widgets: w };
    });

  const controlsFor = (w: Widget, i: number, reading: boolean) => {
    const name = w.title || labels.get(w.metric);
    return (
      <span className="flex items-center gap-0.5">
        <Button size="sm" variant="ghost" icon={<ArrowUp />} aria-label={`Move ${name} earlier`} disabled={i === 0} onClick={() => move(i, -1)} />
        <Button size="sm" variant="ghost" icon={<ArrowDown />} aria-label={`Move ${name} later`} disabled={i === current.widgets.length - 1} onClick={() => move(i, 1)} />
        {!reading && (
          <Select
            aria-label="Width"
            className="h-8 w-28 text-sm"
            value={w.size}
            onChange={(e) => edit((x) => ({ ...x, widgets: x.widgets.map((y) => (y.id === w.id ? { ...y, size: e.target.value as WidgetSize } : y)) }))}
          >
            {Object.entries(SIZE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        )}
        <Button size="sm" variant="ghost" icon={<X />} aria-label={`Remove ${name}`} onClick={() => edit((x) => ({ ...x, widgets: x.widgets.filter((y) => y.id !== w.id) }))} />
      </span>
    );
  };

  return (
    <>
      <PageHeader
        title={current.name}
        description={current.description || undefined}
        meta={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-3">
            <Link href="/analytics" className="font-semibold text-accent hover:underline">All dashboards</Link>
            {demo && (
              <span className="inline-flex items-center gap-2 text-ink-2">
                <DemoTag /> Includes simulated history
              </span>
            )}
          </span>
        }
        actions={
          editable &&
          (editing ? (
            <>
              <Button variant="danger" icon={<Trash2 />} onClick={() => setConfirmDelete(true)}>Delete</Button>
              <Button variant="ghost" onClick={() => { setEditing(false); setDraft(null); setAdding(false); }}>Cancel</Button>
              <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(draft ?? d)}>Save dashboard</Button>
            </>
          ) : (
            <Button icon={<Pencil />} onClick={() => { setDraft(d); setEditing(true); }}>Edit</Button>
          ))
        }
      />
      {save.error && <Notice tone="crit" className="mb-4">{(save.error as Error).message}</Notice>}

      <div className="plate mb-[var(--gap)] flex flex-wrap items-center gap-x-5 gap-y-2 px-3 py-2.5">
        <RangePicker value={range} onChange={setRange} />
        <span className="text-sm text-ink-3">Ranges over 2 days use 5-minute or hourly averages with peaks kept.</span>
        <Freshness />
      </div>

      {editing && (
        <Panel title="Dashboard settings" className="mb-[var(--gap)]">
          <div className="grid gap-4 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_minmax(0,11rem)]">
            <Field label="Name">{(p) => <Input {...p} value={current.name} maxLength={128} onChange={(e) => edit((x) => ({ ...x, name: e.target.value }))} />}</Field>
            <Field label="Description">{(p) => <Input {...p} value={current.description} maxLength={2000} onChange={(e) => edit((x) => ({ ...x, description: e.target.value }))} placeholder="What this dashboard is for" />}</Field>
            <Field label="Opens at">
              {(p) => (
                <Select {...p} value={current.default_range} onChange={(e) => edit((x) => ({ ...x, default_range: e.target.value as RangeKey }))}>
                  {RANGES.map((r) => <option key={r.key} value={r.key}>{r.long}</option>)}
                </Select>
              )}
            </Field>
          </div>
        </Panel>
      )}

      {current.widgets.length === 0 && !adding ? (
        <Panel>
          <EmptyState
            title="This dashboard is empty"
            action={editable ? <Button variant="primary" icon={<Plus />} onClick={() => { if (!editing) { setDraft(d); setEditing(true); } setAdding(true); }}>Add a widget</Button> : undefined}
          >
            Widgets chart one metric each: a line over time, the current reading, or a ranked list of the highest values.
          </EmptyState>
        </Panel>
      ) : (
        <div className="grid grid-cols-1 gap-[var(--gap)] md:grid-cols-6">
          {segments(current.widgets).map((seg) =>
            seg.kind === "stat" ? (
              <ReadingsPanel
                key={seg.items[0].w.id}
                widgets={seg.items.map((x) => x.w)}
                range={range}
                labelOf={(w) => labels.get(w.metric) ?? w.metric}
                controlsOf={(w) => editing && controlsFor(w, current.widgets.indexOf(w), true)}
              />
            ) : (
              seg.items.map(({ w, i }) => (
                <MetricWidget key={w.id} widget={w} range={range} label={labels.get(w.metric) ?? w.metric} controls={editing && controlsFor(w, i, false)} />
              ))
            ),
          )}
          {editing && !adding && (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="flex min-h-32 items-center justify-center gap-2 rounded-[var(--r-plate)] border border-dashed border-rule-strong font-semibold text-ink-2 transition-colors duration-[var(--dur)] hover:border-accent hover:bg-accent-soft/40 hover:text-ink md:col-span-3 xl:col-span-2"
            >
              <Plus className="size-4" aria-hidden /> Add widget
            </button>
          )}
        </div>
      )}
      {editing && adding && catalog.data && (
        <WidgetForm
          catalog={catalog.data}
          onCancel={() => setAdding(false)}
          onAdd={(w) => {
            edit((x) => ({ ...x, widgets: [...x.widgets, w] }));
            setAdding(false);
          }}
        />
      )}
      <Confirm
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete “${d.name}”?`}
        confirmLabel="Delete dashboard"
        onConfirm={() => remove.mutate()}
      >
        The dashboard and its layout are removed for everyone. Metric history is not affected.
      </Confirm>
    </>
  );
}

function WidgetForm({ catalog, onAdd, onCancel }: { catalog: MetricCatalog; onAdd: (w: Widget) => void; onCancel: () => void }) {
  const [metric, setMetric] = useState(catalog.items.find((m) => m.series.length)?.id ?? catalog.items[0].id);
  const [kind, setKind] = useState<WidgetKind>("line");
  const [size, setSize] = useState<WidgetSize>("half");
  const [title, setTitle] = useState("");
  const [pick, setPick] = useState<"top" | "chosen">("top");
  const [chosen, setChosen] = useState<string[]>([]);
  const [limit, setLimit] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const def = catalog.items.find((m) => m.id === metric)!;
  const cap = kind === "line" ? SERIES_COLORS : 20;
  const groups = Object.entries(Object.groupBy(catalog.items, (m) => m.entity_type));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (pick === "chosen" && chosen.length === 0) return setError(`Choose at least one ${entityWord(def.entity_type, 1)}.`);
    if (pick === "chosen" && chosen.length > cap) return setError(`A line chart shows at most ${cap} lines; choose fewer or use a ranked list.`);
    onAdd({ id: Math.random().toString(16).slice(2, 10), title: title.trim(), kind, metric, size, limit: Math.min(limit, cap), entity_ids: pick === "chosen" ? chosen : [] });
  };

  return (
    <Panel title="Add a widget" className="mt-[var(--gap)]">
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <Field label="Metric" hint={def.description}>
            {(p) => (
              <Select {...p} value={metric} onChange={(e) => { setMetric(e.target.value); setChosen([]); setError(null); }}>
                {groups.map(([type, items]) => (
                  <optgroup key={type} label={GROUP_LABEL[type] ?? type}>
                    {items!.map((m) => (
                      <option key={m.id} value={m.id}>{m.label}{m.series.length ? "" : " (no data yet)"}</option>
                    ))}
                  </optgroup>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Show as">
            {(p) => (
              <Select {...p} value={kind} onChange={(e) => setKind(e.target.value as WidgetKind)}>
                {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Width">
            {(p) => (
              <Select {...p} value={size} onChange={(e) => setSize(e.target.value as WidgetSize)}>
                {Object.entries(SIZE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            )}
          </Field>
        </div>
        <Field label="Title" hint={`Leave blank to use “${def.label}”.`} className="max-w-md">
          {(p) => <Input {...p} value={title} maxLength={128} onChange={(e) => setTitle(e.target.value)} />}
        </Field>
        {def.series.length > 1 && (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1.5 text-sm font-semibold">Which {entityWord(def.entity_type, 2)}</legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="pick" checked={pick === "top"} onChange={() => setPick("top")} className="size-4 accent-[var(--accent)]" />
              <span>
                The {def.higher_is_worse ? "highest" : "lowest"}
                <input
                  aria-label="How many"
                  type="number"
                  min={1}
                  max={cap}
                  value={limit}
                  onChange={(e) => setLimit(Math.max(1, Math.min(cap, Number(e.target.value) || 1)))}
                  className="mx-2 h-8 w-16 rounded-[var(--radius)] border border-rule-strong bg-surface px-2 text-base tabular focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:outline-none"
                />
                at each refresh
              </span>
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="pick" checked={pick === "chosen"} onChange={() => setPick("chosen")} className="size-4 accent-[var(--accent)]" />
              <span>Only the ones I choose</span>
            </label>
            {pick === "chosen" && (
              <ul className="ml-6 grid max-h-56 gap-x-6 overflow-auto rounded-[3px] border border-rule p-2 sm:grid-cols-2 lg:grid-cols-3">
                {def.series.map((s) => (
                  <li key={s.id}>
                    <label className="flex h-8 items-center gap-2">
                      <input
                        type="checkbox"
                        className="size-4 accent-[var(--accent)]"
                        checked={chosen.includes(s.entity_id)}
                        onChange={(e) => setChosen((c) => (e.target.checked ? [...c, s.entity_id] : c.filter((x) => x !== s.entity_id)))}
                      />
                      <span className="truncate">{s.label}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary">Add to dashboard</Button>
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          {error && <span role="alert" className="text-sm font-semibold text-crit">{error}</span>}
          <span className="text-sm text-ink-3">Widgets are saved with the dashboard.</span>
        </div>
      </form>
    </Panel>
  );
}
