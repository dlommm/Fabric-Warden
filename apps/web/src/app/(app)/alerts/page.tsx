"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { BellOff, CircleCheck, Clock, OctagonAlert, Pencil, Plus, Siren, Trash2, TriangleAlert, Wrench } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, type ReactNode, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel, type Tone } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ago, dateTime, duration } from "@/lib/format";
import { conditionLabel, entityHref, formatValue, holdLabel } from "@/lib/metrics";
import { useSession } from "@/lib/session";
import type { AlertCounts, AlertItem, AlertRuleRow, ChannelRow, MaintenanceWindowRow, MetricCatalog, Severity } from "@/lib/types";

const TABS = [
  { id: "active", label: "Active" },
  { id: "history", label: "History" },
  { id: "rules", label: "Rules" },
  { id: "maintenance", label: "Maintenance" },
] as const;
type Tab = (typeof TABS)[number]["id"];

const SEVERITY_TONE: Record<Severity, Tone> = { critical: "crit", warning: "warn", info: "info" };
const SEVERITY_WORD: Record<Severity, string> = { critical: "Critical", warning: "Warning", info: "Info" };
const RESOLUTION: Record<string, string> = { recovered: "Recovered", no_data: "No data", rule_changed: "Rule changed" };

function SeverityLabel({ s, children }: { s: Severity; children?: ReactNode }) {
  return <StateLabel tone={SEVERITY_TONE[s]}>{children ?? SEVERITY_WORD[s]}</StateLabel>;
}

export default function AlertsPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("monitoring");
  const search = useSearchParams();
  const router = useRouter();
  const tab: Tab = (TABS.find((t) => t.id === search.get("tab"))?.id ?? "active") as Tab;
  const counts = useQuery({
    queryKey: ["alerts", "active"],
    queryFn: () => get<{ items: AlertItem[]; counts: AlertCounts }>("/api/monitoring/alerts"),
    enabled: on,
    refetchInterval: 30_000,
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Alerts" name="Monitoring & Alerts" canManage={can("modules:manage")}>
        Alert rules watch the metric history recorded on every sync: devices offline, controllers unreachable, high CPU, Wi-Fi retries and probe loss. Enable Monitoring & Alerts to turn them on.
      </ModuleOff>
    );
  }

  const select = (t: Tab) => router.replace(t === "active" ? "/alerts" : `/alerts?tab=${t}`, { scroll: false });
  const c = counts.data?.counts;

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Rules check the newest sample of every matching series after each sync. A breach waits out its hold time as pending, then fires; it resolves when the value recovers or the data stops."
      />
      <div role="tablist" aria-label="Alerts" className="mb-[var(--gap)] flex gap-1 overflow-x-auto border-b border-rule">
        {TABS.map((t) => {
          const active = t.id === tab;
          const badge = t.id === "active" && c ? c.firing : 0;
          return (
            <button
              key={t.id}
              role="tab"
              type="button"
              id={`tab-${t.id}`}
              aria-selected={active}
              aria-controls={`panel-${t.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => select(t.id)}
              onKeyDown={(e) => {
                const i = TABS.findIndex((x) => x.id === tab);
                const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!d) return;
                e.preventDefault();
                const next = TABS[(i + d + TABS.length) % TABS.length].id;
                select(next);
                document.getElementById(`tab-${next}`)?.focus();
              }}
              className={clsx(
                "-mb-px inline-flex h-10 items-center gap-2 border-b-2 px-3 font-semibold whitespace-nowrap transition-colors duration-150",
                active ? "border-accent text-ink" : "border-transparent text-ink-2 hover:text-ink",
              )}
            >
              {t.label}
              {badge > 0 && <span className="tabular inline-flex h-5 min-w-5 items-center justify-center rounded-[3px] bg-crit px-1.5 font-mono text-xs font-semibold text-ground">{badge}</span>}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "active" && <ActiveTab data={counts.data} loading={counts.isLoading} error={counts.error as Error | null} />}
        {tab === "history" && <HistoryTab />}
        {tab === "rules" && <RulesTab />}
        {tab === "maintenance" && <MaintenanceTab />}
      </div>
    </>
  );
}

function EntityLink({ a }: { a: Pick<AlertItem, "entity_type" | "entity_id" | "label"> }) {
  const href = entityHref(a.entity_type, a.entity_id);
  return href ? <Link href={href} className="font-bold hover:underline">{a.label}</Link> : <span className="font-bold">{a.label}</span>;
}

function ActiveTab({ data, loading, error }: { data?: { items: AlertItem[]; counts: AlertCounts }; loading: boolean; error: Error | null }) {
  const { can, moduleEnabled } = useSession();
  const qc = useQueryClient();
  const ack = useMutation({
    mutationFn: (id: string) => post(`/api/monitoring/alerts/${id}/ack`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
  if (error) return <Notice tone="crit">{error.message}</Notice>;
  if (loading || !data) return <Skeleton className="h-40 w-full" />;
  const firing = data.items.filter((a) => a.state === "firing");
  const pending = data.items.filter((a) => a.state === "pending");
  const loud = firing.filter((a) => !a.silenced);

  return (
    <div className="flex flex-col gap-[var(--gap)]">
      {!moduleEnabled("notifications") && (
        <Notice tone="info" title="Alerts are recorded but not sent anywhere">
          Notification Channels is switched off, so firing alerts only appear here and on the Overview.
        </Notice>
      )}
      {firing.length === 0 ? (
        <section aria-label="Firing alerts" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[var(--radius)] border border-ok/40 bg-ok-soft px-5 py-4">
          <CircleCheck className="size-5 text-ok" aria-hidden />
          <p className="font-bold">No alerts firing.</p>
          <p className="text-ink-2">{pending.length ? `${pending.length} breach${pending.length > 1 ? "es are" : " is"} waiting out the hold time below.` : "Every rule is within its threshold."}</p>
        </section>
      ) : (
        <section aria-labelledby="firing-h" className="attention-plate" data-tone={firing.some((a) => a.severity === "critical") ? "crit" : undefined}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 pt-4 pb-2">
            {firing.some((a) => a.severity === "critical") ? (
              <OctagonAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
            ) : (
              <TriangleAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
            )}
            <h2 id="firing-h" className="text-lg font-bold">
              {firing.length} alert{firing.length > 1 ? "s" : ""} firing
            </h2>
            {data.counts.unacknowledged < firing.length && <span className="font-semibold">{firing.length - data.counts.unacknowledged} acknowledged</span>}
            {loud.length < firing.length && <span className="font-semibold">{firing.length - loud.length} silenced by maintenance</span>}
          </div>
          <ul className="mx-2 mb-2 overflow-hidden rounded-[3px] border border-rule bg-surface text-ink">
            {firing.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-3 py-2.5 last:border-b-0">
                <span className="sm:w-24"><SeverityLabel s={a.severity} /></span>
                <span className="flex min-w-0 flex-[1_1_14rem] flex-col">
                  <span className="flex flex-wrap items-center gap-x-2">
                    <EntityLink a={a} />
                    <span className="text-ink-2">{a.rule}</span>
                    {a.is_demo && <DemoTag />}
                  </span>
                  <span className="text-sm text-ink-2">
                    {a.unit === "bool" ? "Reported down" : `${formatValue(a.unit, a.value)}, threshold ${a.comparator === "gt" ? "above" : "below"} ${formatValue(a.unit, a.threshold)}`} · fired {ago(a.fired_at)}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  {a.silenced && <Chip><BellOff className="mr-1 size-3.5" aria-hidden />Silenced</Chip>}
                  {a.acknowledged_at ? (
                    <span className="text-sm text-ink-3">Acknowledged by {a.acknowledged_by} {ago(a.acknowledged_at)}</span>
                  ) : (
                    can("monitoring:manage") && (
                      <Button size="sm" loading={ack.isPending && ack.variables === a.id} onClick={() => ack.mutate(a.id)}>Acknowledge</Button>
                    )
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {ack.error && <Notice tone="crit">{(ack.error as Error).message}</Notice>}
      <Panel title="Pending" actions={<span className="text-sm text-ink-3">Breaching, waiting out the hold time</span>} bodyClassName="p-0">
        {pending.length === 0 ? (
          <p className="px-4 py-4 text-ink-3">Nothing pending.</p>
        ) : (
          <ul>
            {pending.map((a) => (
              <li key={a.id} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-4 py-2 last:border-b-0">
                <StateLabel tone="muted"><span className="text-ink-2">Pending</span></StateLabel>
                <span className="min-w-0 flex-[1_1_14rem]"><EntityLink a={a} /> <span className="text-ink-2">{a.rule}: {a.unit === "bool" ? "reported down" : formatValue(a.unit, a.value)}</span></span>
                <span className="inline-flex items-center gap-1.5 text-sm text-ink-3"><Clock className="size-3.5" aria-hidden />since {ago(a.pending_since)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function HistoryTab() {
  const q = useQuery({ queryKey: ["alerts", "resolved"], queryFn: () => get<{ items: AlertItem[] }>("/api/monitoring/alerts?state=resolved") });
  const columns: Column<AlertItem>[] = [
    { key: "sev", header: "Severity", sort: (a) => a.severity, className: "w-28", cell: (a) => <SeverityLabel s={a.severity} /> },
    { key: "what", header: "Alert", sort: (a) => a.label.toLowerCase(), cell: (a) => <span className="flex flex-col py-1"><EntityLink a={a} /><span className="text-sm text-ink-2">{a.rule}</span></span> },
    { key: "fired", header: "Fired", sort: (a) => a.fired_at, cell: (a) => <span className="whitespace-nowrap text-ink-2">{dateTime(a.fired_at)}</span> },
    {
      key: "dur",
      header: "Lasted",
      align: "right",
      sort: (a) => (a.fired_at && a.resolved_at ? new Date(a.resolved_at).getTime() - new Date(a.fired_at).getTime() : null),
      cell: (a) => (a.fired_at && a.resolved_at ? duration((new Date(a.resolved_at).getTime() - new Date(a.fired_at).getTime()) / 1000) : "—"),
    },
    { key: "res", header: "Ended by", sort: (a) => a.resolution, cell: (a) => <span className="text-ink-2">{RESOLUTION[a.resolution ?? ""] ?? "—"}</span> },
    { key: "ack", header: "Acknowledged", cell: (a) => <span className="text-ink-3">{a.acknowledged_by ?? "—"}</span> },
  ];
  return (
    <Panel bodyClassName="p-0">
      {q.error && <Notice tone="crit">{(q.error as Error).message}</Notice>}
      <DataTable
        caption="Resolved alerts"
        rows={q.data?.items}
        columns={columns}
        loading={q.isLoading}
        empty={<EmptyState icon={<Siren />} title="No resolved alerts yet">Alerts that fire and then recover are kept here for a year.</EmptyState>}
      />
    </Panel>
  );
}

// -- rules ------------------------------------------------------------------------------

type RuleDraft = {
  id?: string;
  name: string;
  description: string;
  metric: string;
  comparator: "gt" | "lt";
  threshold: string;
  for_seconds: number;
  severity: Severity;
  enabled: boolean;
  entity_ids: string[];
  channel_ids: string[];
  builtin?: boolean;
};

const GROUP_LABEL: Record<string, string> = { site: "Site", device: "Devices", radio: "Wi-Fi radios", network: "Networks", controller: "Controllers", probe: "WAN probe" };
const HOLDS = [0, 60, 120, 300, 600, 900, 1800, 3600];

function RulesTab() {
  const { can, moduleEnabled } = useSession();
  const editable = can("monitoring:manage");
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: () => get<{ items: AlertRuleRow[] }>("/api/monitoring/rules") });
  const catalog = useQuery({ queryKey: ["metric-catalog"], queryFn: () => get<MetricCatalog>("/api/metrics") });
  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => get<{ items: ChannelRow[] }>("/api/notifications/channels"),
    enabled: moduleEnabled("notifications"),
  });
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [pending, setPending] = useState<AlertRuleRow | null>(null);
  const toggle = useMutation({
    mutationFn: (r: AlertRuleRow) => patch(`/api/monitoring/rules/${r.id}`, { enabled: !r.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-rules"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/monitoring/rules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-rules"] }),
  });
  const channelName = new Map(channels.data?.items.map((c) => [c.id, c.name]));

  const columns: Column<AlertRuleRow>[] = [
    {
      key: "name",
      header: "Rule",
      sort: (r) => r.name.toLowerCase(),
      className: "min-w-60",
      cell: (r) => (
        <span className={clsx("flex flex-col py-1", !r.enabled && "opacity-60")}>
          <span className="flex flex-wrap items-center gap-2 font-bold">
            {r.name}
            {r.builtin && <Chip>Built-in</Chip>}
          </span>
          <span className="text-sm text-ink-2">{conditionLabel(r)}</span>
        </span>
      ),
    },
    { key: "sev", header: "Severity", sort: (r) => r.severity, cell: (r) => <SeverityLabel s={r.severity} /> },
    {
      key: "scope",
      header: "Applies to",
      cell: (r) => <span className="text-ink-2">{r.entity_ids.length ? `${r.entity_ids.length} chosen` : `Every ${r.entity_type === "probe" ? "probe target" : (r.entity_type ?? "series")}`}</span>,
    },
    {
      key: "notify",
      header: "Notifies",
      cell: (r) =>
        !moduleEnabled("notifications") ? (
          <span className="text-ink-3">Channels off</span>
        ) : r.channel_ids.length ? (
          <span className="text-ink-2">{r.channel_ids.map((id) => channelName.get(id) ?? "removed channel").join(", ")}</span>
        ) : (
          <span className="text-ink-2">All channels</span>
        ),
    },
    {
      key: "active",
      header: "Active",
      align: "right",
      sort: (r) => r.active_alerts,
      cell: (r) => (r.active_alerts ? <span className="font-bold text-warn-text">{r.active_alerts}</span> : <span className="text-ink-3">—</span>),
    },
    {
      key: "on",
      header: "Enabled",
      cell: (r) => (
        <label className="inline-flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            role="switch"
            className="peer sr-only"
            checked={r.enabled}
            disabled={!editable || toggle.isPending}
            onChange={() => toggle.mutate(r)}
            aria-label={`${r.enabled ? "Disable" : "Enable"} ${r.name}`}
          />
          <span aria-hidden className="relative h-5 w-9 rounded-full bg-rule-strong transition-colors duration-150 peer-checked:bg-accent peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--focus)] after:absolute after:top-0.5 after:left-0.5 after:size-4 after:rounded-full after:bg-surface after:transition-transform after:duration-150 peer-checked:after:translate-x-4" />
          <span className="text-sm text-ink-2">{r.enabled ? "On" : "Off"}</span>
        </label>
      ),
    },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (r) =>
        editable && (
          <span className="inline-flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              icon={<Pencil />}
              aria-label={`Edit ${r.name}`}
              onClick={() =>
                setDraft({ ...r, threshold: String(r.threshold), builtin: r.builtin })
              }
            />
            <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Delete ${r.name}`} onClick={() => setPending(r)} />
          </span>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-[var(--gap)]">
      {editable && !draft && (
        <div>
          <Button
            variant="primary"
            icon={<Plus />}
            onClick={() => setDraft({ name: "", description: "", metric: "device.cpu_pct", comparator: "gt", threshold: "", for_seconds: 300, severity: "warning", enabled: true, entity_ids: [], channel_ids: [] })}
          >
            Add rule
          </Button>
        </div>
      )}
      {draft && catalog.data && (
        <RuleForm key={draft.id ?? "new"} draft={draft} catalog={catalog.data} channels={moduleEnabled("notifications") ? channels.data?.items ?? [] : null} onDone={() => setDraft(null)} />
      )}
      {(toggle.error || remove.error) && <Notice tone="crit">{((toggle.error || remove.error) as Error).message}</Notice>}
      <Panel bodyClassName="p-0">
        <DataTable caption="Alert rules" rows={rules.data?.items} columns={columns} loading={rules.isLoading} empty={<EmptyState title="No rules">Add a rule to start watching a metric.</EmptyState>} />
      </Panel>
      <Confirm open={pending !== null} onOpenChange={(o) => !o && setPending(null)} title={`Delete “${pending?.name}”?`} confirmLabel="Delete rule" onConfirm={() => pending && remove.mutate(pending.id)}>
        Its open alerts close and its history is removed.{pending?.builtin ? " Built-in rules are not recreated once deleted; disable it instead if you may want it back." : ""}
      </Confirm>
    </div>
  );
}

function RuleForm({ draft, catalog, channels, onDone }: { draft: RuleDraft; catalog: MetricCatalog; channels: ChannelRow[] | null; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState(draft);
  const [errors, setErrors] = useState<{ name?: string; threshold?: string; form?: string }>({});
  const def = catalog.items.find((m) => m.id === f.metric)!;
  const bool = def.unit === "bool";
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name.trim(),
        description: f.description.trim(),
        comparator: bool ? "lt" : f.comparator,
        threshold: bool ? 1 : Number(f.threshold),
        for_seconds: f.for_seconds,
        severity: f.severity,
        enabled: f.enabled,
        entity_ids: f.entity_ids,
        channel_ids: f.channel_ids,
      };
      return f.id ? patch(`/api/monitoring/rules/${f.id}`, body) : post("/api/monitoring/rules", { ...body, metric: f.metric });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alert-rules"] });
      onDone();
    },
    onError: (e) => setErrors({ form: e instanceof ApiError ? e.message : "Couldn't save the rule." }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!f.name.trim()) next.name = "Name the rule.";
    if (!bool && (f.threshold.trim() === "" || Number.isNaN(Number(f.threshold)))) next.threshold = "Enter a number.";
    setErrors(next);
    if (Object.keys(next).length === 0) save.mutate();
  };
  const unitHint: Record<string, string> = { pct: "Percent, 0 to 100.", ms: "Milliseconds.", bps: "Bits per second.", count: "A count.", load: "Load average." };
  const groups = Object.entries(Object.groupBy(catalog.items, (m) => m.entity_type));

  return (
    <Panel title={f.id ? `Edit “${draft.name}”` : "New alert rule"}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Field label="Name" error={errors.name}>{(p) => <Input {...p} autoFocus={!f.id} value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Rack switch running hot" />}</Field>
          <Field label="Metric" hint={f.id ? "The metric of an existing rule can't change; create a new rule instead." : def.description}>
            {(p) => (
              <Select {...p} disabled={!!f.id} value={f.metric} onChange={(e) => setF({ ...f, metric: e.target.value, entity_ids: [] })}>
                {groups.map(([type, items]) => (
                  <optgroup key={type} label={GROUP_LABEL[type] ?? type}>
                    {items!.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </optgroup>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-4">
          {bool ? (
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <span className="text-sm font-semibold">Condition</span>
              <p className="flex h-9 items-center text-ink-2">Fires when {def.label.toLowerCase()} reports down.</p>
            </div>
          ) : (
            <>
              <Field label="When the value is">
                {(p) => (
                  <Select {...p} value={f.comparator} onChange={(e) => setF({ ...f, comparator: e.target.value as "gt" | "lt" })}>
                    <option value="gt">Above</option>
                    <option value="lt">Below</option>
                  </Select>
                )}
              </Field>
              <Field label="Threshold" error={errors.threshold} hint={unitHint[def.unit]}>
                {(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} />}
              </Field>
            </>
          )}
          <Field label="For at least">
            {(p) => (
              <Select {...p} value={f.for_seconds} onChange={(e) => setF({ ...f, for_seconds: Number(e.target.value) })}>
                {HOLDS.map((s) => <option key={s} value={s}>{s === 0 ? "Fire immediately" : holdLabel(s).replace("for ", "")}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Severity">
            {(p) => (
              <Select {...p} value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value as Severity })}>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </Select>
            )}
          </Field>
        </div>
        <ScopePicker
          legend={`Applies to`}
          everyLabel={`Every ${def.entity_type === "probe" ? "probe target" : def.entity_type}, including ones added later`}
          options={def.series.map((s) => ({ id: s.entity_id, label: s.label }))}
          value={f.entity_ids}
          onChange={(ids) => setF({ ...f, entity_ids: ids })}
        />
        {channels && (
          <ScopePicker
            legend="Notifies"
            everyLabel="Every enabled channel at or above its minimum severity"
            options={channels.map((c) => ({ id: c.id, label: `${c.name} (${c.kind})` }))}
            value={f.channel_ids}
            onChange={(ids) => setF({ ...f, channel_ids: ids })}
            emptyText="No channels yet. Add one under Notifications."
          />
        )}
        <Field label="Description" className="max-w-2xl">{(p) => <Textarea {...p} className="min-h-16" value={f.description} maxLength={2000} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={save.isPending}>{f.id ? "Save rule" : "Add rule"}</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}

function ScopePicker({
  legend,
  everyLabel,
  options,
  value,
  onChange,
  emptyText,
}: {
  legend: string;
  everyLabel: string;
  options: { id: string; label: string }[];
  value: string[];
  onChange: (ids: string[]) => void;
  emptyText?: string;
}) {
  const [chosen, setChosen] = useState(value.length > 0);
  const name = useMemo(() => `scope-${legend.replace(/\W/g, "")}`, [legend]);
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1.5 text-sm font-semibold">{legend}</legend>
      <label className="flex items-center gap-2">
        <input type="radio" name={name} checked={!chosen} onChange={() => { setChosen(false); onChange([]); }} className="size-4 accent-[var(--accent)]" />
        {everyLabel}
      </label>
      <label className="flex items-center gap-2">
        <input type="radio" name={name} checked={chosen} disabled={options.length === 0} onChange={() => setChosen(true)} className="size-4 accent-[var(--accent)]" />
        Only the ones I choose
        {options.length === 0 && emptyText && <span className="text-sm text-ink-3">{emptyText}</span>}
      </label>
      {chosen && (
        <ul className="ml-6 grid max-h-56 gap-x-6 overflow-auto rounded-[3px] border border-rule p-2 sm:grid-cols-2 lg:grid-cols-3">
          {options.map((o) => (
            <li key={o.id}>
              <label className="flex h-8 items-center gap-2">
                <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.includes(o.id)} onChange={(e) => onChange(e.target.checked ? [...value, o.id] : value.filter((x) => x !== o.id))} />
                <span className="truncate">{o.label}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

// -- maintenance -------------------------------------------------------------------------

const DURATIONS = [30, 60, 120, 240, 480, 1440];

function MaintenanceTab() {
  const { can } = useSession();
  const editable = can("monitoring:manage");
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["maintenance"], queryFn: () => get<{ items: MaintenanceWindowRow[] }>("/api/monitoring/maintenance"), refetchInterval: 60_000 });
  const catalog = useQuery({ queryKey: ["metric-catalog"], queryFn: () => get<MetricCatalog>("/api/metrics") });
  const [adding, setAdding] = useState(false);
  const end = useMutation({
    mutationFn: (id: string) => post(`/api/monitoring/maintenance/${id}/end`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance"] }),
  });
  const devices = catalog.data?.items.find((m) => m.id === "device.online")?.series ?? [];
  const names = new Map(devices.map((s) => [s.entity_id, s.label]));
  const stateTone: Record<MaintenanceWindowRow["state"], Tone> = { active: "warn", scheduled: "info", ended: "muted" };

  return (
    <div className="flex flex-col gap-[var(--gap)]">
      <p className="max-w-[70ch] text-ink-2">
        During a maintenance window, alerts on the covered devices still fire and are recorded, but they are marked silenced and notify nobody.
      </p>
      {editable && !adding && (
        <div>
          <Button variant="primary" icon={<Wrench />} onClick={() => setAdding(true)}>Start maintenance</Button>
        </div>
      )}
      {adding && <WindowForm devices={devices.map((s) => ({ id: s.entity_id, label: s.label }))} onDone={() => setAdding(false)} />}
      {end.error && <Notice tone="crit">{(end.error as Error).message}</Notice>}
      <Panel title="Windows" bodyClassName="p-0" actions={<span className="text-sm text-ink-3">Last 30 days</span>}>
        {!list.data ? (
          <div className="p-4"><Skeleton className="h-20 w-full" /></div>
        ) : list.data.items.length === 0 ? (
          <EmptyState title="No maintenance windows">Start one before planned work so the expected alerts don’t page anyone.</EmptyState>
        ) : (
          <ul>
            {list.data.items.map((w) => (
              <li key={w.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-4 py-3 last:border-b-0">
                <span className="w-24"><StateLabel tone={stateTone[w.state]}>{w.state === "active" ? "Active" : w.state === "scheduled" ? "Scheduled" : "Ended"}</StateLabel></span>
                <span className="flex min-w-0 flex-[1_1_14rem] flex-col">
                  <span className="font-bold">{w.name}</span>
                  <span className="text-sm text-ink-2">
                    {dateTime(w.starts_at)} to {dateTime(w.ends_at)} · {w.entity_ids.length ? w.entity_ids.map((id) => names.get(id) ?? "removed device").join(", ") : "everything"}
                    {w.created_by && ` · by ${w.created_by}`}
                  </span>
                  {w.reason && <span className="text-sm text-ink-3">{w.reason}</span>}
                </span>
                {editable && w.state !== "ended" && (
                  <Button size="sm" loading={end.isPending && end.variables === w.id} onClick={() => end.mutate(w.id)}>
                    {w.state === "active" ? "End now" : "Cancel"}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function WindowForm({ devices, onDone }: { devices: { id: string; label: string }[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", duration: 60, reason: "", entity_ids: [] as string[] });
  const [errors, setErrors] = useState<{ name?: string; form?: string }>({});
  const create = useMutation({
    mutationFn: () => post("/api/monitoring/maintenance", { name: f.name.trim(), duration_minutes: f.duration, reason: f.reason.trim(), entity_ids: f.entity_ids }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance"] });
      onDone();
    },
    onError: (e) => setErrors({ form: e instanceof ApiError ? e.message : "Couldn't start the window." }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!f.name.trim()) return setErrors({ name: "Name the work, for example “Core switch firmware”." });
    setErrors({});
    create.mutate();
  };
  return (
    <Panel title="Start maintenance">
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,12rem)]">
          <Field label="What's happening" error={errors.name}>{(p) => <Input {...p} autoFocus value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
          <Field label="Starting now, for">
            {(p) => (
              <Select {...p} value={f.duration} onChange={(e) => setF({ ...f, duration: Number(e.target.value) })}>
                {DURATIONS.map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutes` : m === 60 ? "1 hour" : m === 1440 ? "24 hours" : `${m / 60} hours`}</option>)}
              </Select>
            )}
          </Field>
        </div>
        <ScopePicker legend="Covers" everyLabel="Everything (all alerts are silenced)" options={devices} value={f.entity_ids} onChange={(ids) => setF({ ...f, entity_ids: ids })} />
        <Field label="Reason" hint="Optional. Shown with the window." className="max-w-2xl">{(p) => <Textarea {...p} className="min-h-16" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />}</Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={create.isPending}>Start window</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}
