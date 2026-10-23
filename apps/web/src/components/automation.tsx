"use client";

// Automation Studio vocabulary: run and dry-run outcome labels, rule descriptions,
// and the rule editor with its dry run. A rule only proposes changes or notifies.
import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { MANY_TARGET_OPERATIONS } from "@/components/changes";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Notice, Panel } from "@/components/ui/layout-bits";
import { StateLabel, type Tone } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AutomationMeta, AutomationOutcome, AutomationRuleInput, BatchCandidate, ChannelRow, DryRunResult, RuleAction, RuleCondition } from "@/lib/types";

const OUTCOME: Record<AutomationOutcome, [Tone, string]> = {
  proposed: ["info", "Proposed a change"],
  notified: ["ok", "Notified"],
  refused: ["warn", "Refused by the change engine"],
  skipped: ["muted", "Skipped"],
  error: ["crit", "Error"],
};

export function RunOutcome({ outcome }: { outcome: AutomationOutcome }) {
  const [tone, word] = OUTCOME[outcome];
  return <StateLabel tone={tone}>{word}</StateLabel>;
}

const DRY: Record<DryRunResult["items"][number]["outcome"], [Tone, string]> = {
  would_propose: ["info", "Would propose"],
  would_notify: ["info", "Would notify"],
  refused: ["warn", "Would be refused"],
  skipped: ["muted", "Would skip"],
  no_match: ["muted", "No match"],
};

const OP_WORD: Record<RuleCondition["op"], string> = {
  eq: "is",
  ne: "is not",
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  gte: "is at least",
  lte: "is at most",
  exists: "is present",
};

const FIELD_HINTS = ["severity", "entity_type", "entity_id", "site_id", "source", "payload.name", "payload.from", "payload.to", "payload.rule", "payload.summary"];

export function triggerText(r: AutomationRuleInput, meta?: AutomationMeta): string {
  if (r.trigger_kind === "schedule") return `Every ${r.interval_minutes} minutes`;
  const label = meta?.events.find((e) => e.type === r.event_type)?.label ?? r.event_type ?? "";
  const n = r.conditions.length;
  return n ? `${label}, if ${n} condition${n === 1 ? " holds" : "s hold"}` : label;
}

export function actionText(r: AutomationRuleInput): string {
  const a = r.action;
  if (a.kind === "notify") return `Notify ${a.channel_ids.length ? `${a.channel_ids.length} channel${a.channel_ids.length === 1 ? "" : "s"}` : "every enabled channel"}`;
  const op = MANY_TARGET_OPERATIONS.find((o) => o.id === a.operation);
  const what = op?.flag
    ? `${(a.params ?? {})[op.flag.key] ?? true ? op.flag.on : op.flag.off} for`
    : a.operation === "port.power_cycle"
      ? `Power-cycle port ${String((a.params ?? {}).port_idx ?? "")} on`
      : "Restart";
  return `Propose: ${what} ${a.target === "event" ? `the ${targetNoun(a.operation)} the event is about` : "a fixed target"}`;
}

export const EMPTY_RULE: AutomationRuleInput = {
  name: "",
  description: "",
  trigger_kind: "event",
  event_type: "monitoring.alert.firing",
  interval_minutes: 60,
  conditions: [],
  action: { kind: "propose", operation: "device.restart", target: "event", params: {}, reason: "" },
  cooldown_minutes: 60,
};

function targetNoun(operation: string): string {
  if (operation.startsWith("firewall_policy.")) return "firewall policy";
  if (operation.startsWith("network.")) return "network";
  return "device";
}

const ckey = (c: BatchCandidate) => `${c.target_id}:${String(c.params.port_idx ?? "")}`;

/** Edit a rule and try it against recent events. Saving never enables it; that is a separate, audited step. */
export function RuleEditor({ initial, saving, error, onSave, saveLabel }: { initial: AutomationRuleInput; saving: boolean; error: unknown; onSave: (r: AutomationRuleInput) => void; saveLabel: string }) {
  const { moduleEnabled } = useSession();
  const [r, setR] = useState<AutomationRuleInput>(initial);
  const set = (patch: Partial<AutomationRuleInput>) => setR((prev) => ({ ...prev, ...patch }));
  const setAction = (patch: Partial<RuleAction>) => setR((prev) => ({ ...prev, action: { ...prev.action, ...patch } as RuleAction }));
  const meta = useQuery({ queryKey: ["automation", "meta"], queryFn: () => get<AutomationMeta>("/api/automation/meta"), staleTime: 600_000 });
  const notifyOn = moduleEnabled("notifications");
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => get<{ items: ChannelRow[] }>("/api/notifications/channels"), enabled: notifyOn && r.action.kind === "notify" });
  const a = r.action;
  const op = a.kind === "propose" ? MANY_TARGET_OPERATIONS.find((o) => o.id === a.operation) : undefined;
  const fixed = a.kind === "propose" && a.target === "fixed";
  const cands = useQuery({
    queryKey: ["changes", "candidates", a.kind === "propose" ? a.operation : ""],
    queryFn: () => get<{ items: BatchCandidate[] }>(`/api/changes/candidates?operation=${a.kind === "propose" ? a.operation : ""}`),
    enabled: fixed,
  });
  const notifyOnly = meta.data?.events.find((e) => e.type === r.event_type)?.notify_only;
  const dry = useMutation({ mutationFn: (rule: AutomationRuleInput) => post<DryRunResult>("/api/automation/dry-run", rule) });
  const params = a.kind === "propose" ? (a.params ?? {}) : {};
  const fixedKey = a.kind === "propose" && a.target_id ? `${a.target_id}:${String(params.port_idx ?? "")}` : "";
  const byKey = useMemo(() => new Map((cands.data?.items ?? []).map((c) => [ckey(c), c])), [cands.data]);

  const setCondition = (i: number, patch: Partial<RuleCondition>) => set({ conditions: r.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  return (
    <div className="flex flex-col gap-[var(--gap)]">
      <form
        className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] lg:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(r);
        }}
      >
        <Panel title="When">
          <div className="flex flex-col gap-4">
            <Field label="Name">{(p) => <Input {...p} required maxLength={128} value={r.name} onChange={(e) => set({ name: e.target.value })} placeholder="Restart an AP whose radio alert fires" />}</Field>
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-sm font-semibold">Trigger</legend>
              <label className="flex items-center gap-2">
                <input type="radio" name="trigger" className="size-4 accent-[var(--accent)]" checked={r.trigger_kind === "event"} onChange={() => set({ trigger_kind: "event" })} />
                An event
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="trigger"
                  className="size-4 accent-[var(--accent)]"
                  checked={r.trigger_kind === "schedule"}
                  onChange={() => {
                    set({ trigger_kind: "schedule", conditions: [] });
                    if (a.kind === "propose") setAction({ target: "fixed" });
                  }}
                />
                A schedule
              </label>
            </fieldset>
            {r.trigger_kind === "event" ? (
              <>
                <Field label="Event">
                  {(p) => (
                    <Select {...p} value={r.event_type ?? ""} onChange={(e) => set({ event_type: e.target.value })}>
                      {(meta.data?.events ?? []).map((ev) => <option key={ev.type} value={ev.type}>{ev.label}</option>)}
                    </Select>
                  )}
                </Field>
                <div className="flex flex-col gap-2">
                  <span className="text-sm font-semibold">Only if</span>
                  {r.conditions.length === 0 && <p className="text-sm text-ink-3">Every event of this type, until you add a condition.</p>}
                  {r.conditions.map((c, i) => (
                    <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
                      <Input aria-label="Field" list="rule-fields" value={c.field} onChange={(e) => setCondition(i, { field: e.target.value })} className="font-mono text-sm" />
                      <Select aria-label="Test" value={c.op} onChange={(e) => setCondition(i, { op: e.target.value as RuleCondition["op"] })}>
                        {Object.entries(OP_WORD).map(([k, w]) => <option key={k} value={k}>{w}</option>)}
                      </Select>
                      <Input aria-label="Value" value={c.value ?? ""} disabled={c.op === "exists"} onChange={(e) => setCondition(i, { value: e.target.value })} />
                      <Button size="sm" variant="ghost" aria-label="Remove condition" icon={<X />} onClick={() => set({ conditions: r.conditions.filter((_, j) => j !== i) })} />
                    </div>
                  ))}
                  <datalist id="rule-fields">{FIELD_HINTS.map((f) => <option key={f} value={f} />)}</datalist>
                  <Button size="sm" icon={<Plus />} className="self-start" onClick={() => set({ conditions: [...r.conditions, { field: "severity", op: "gte", value: "warning" }] })}>Add condition</Button>
                </div>
              </>
            ) : (
              <Field label="Every (minutes)" hint={`At least ${meta.data?.min_interval_minutes ?? 15} minutes`}>
                {(p) => <Input {...p} type="number" min={meta.data?.min_interval_minutes ?? 15} value={r.interval_minutes ?? ""} onChange={(e) => set({ interval_minutes: Number(e.target.value) || null })} />}
              </Field>
            )}
            <Field label="Description" hint="Optional">{(p) => <Textarea {...p} className="min-h-16" maxLength={2000} value={r.description} onChange={(e) => set({ description: e.target.value })} />}</Field>
          </div>
        </Panel>
        <Panel title="Then">
          <div className="flex flex-col gap-4">
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-sm font-semibold">Action</legend>
              <label className="flex items-center gap-2">
                <input type="radio" name="action" className="size-4 accent-[var(--accent)]" disabled={notifyOnly} checked={a.kind === "propose"} onChange={() => set({ action: { kind: "propose", operation: "device.restart", params: {}, reason: "", target: r.trigger_kind === "schedule" ? "fixed" : "event" } })} />
                Propose a change
              </label>
              <label className="flex items-center gap-2">
                <input type="radio" name="action" className="size-4 accent-[var(--accent)]" checked={a.kind === "notify"} onChange={() => set({ action: { kind: "notify", channel_ids: [], severity: "warning", title: "" } })} />
                Send a notification
              </label>
            </fieldset>
            {a.kind === "propose" ? (
              <>
                <Field label="Change">
                  {(p) => (
                    <Select {...p} value={a.operation} onChange={(e) => setAction({ operation: e.target.value as typeof a.operation, target_id: undefined, params: {} })}>
                      {MANY_TARGET_OPERATIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                    </Select>
                  )}
                </Field>
                {op?.flag && (
                  <fieldset className="flex flex-col gap-1">
                    <legend className="mb-1 text-sm font-semibold">Set to</legend>
                    {[true, false].map((v) => (
                      <label key={String(v)} className="flex items-center gap-2">
                        <input type="radio" name="flag" className="size-4 accent-[var(--accent)]" checked={(params[op.flag!.key] ?? true) === v} onChange={() => setAction({ params: { ...params, [op.flag!.key]: v } })} />
                        {v ? op.flag?.on : op.flag?.off}
                      </label>
                    ))}
                  </fieldset>
                )}
                <fieldset className="flex flex-col gap-1">
                  <legend className="mb-1 text-sm font-semibold">On</legend>
                  <label className="flex items-center gap-2">
                    <input type="radio" name="target" className="size-4 accent-[var(--accent)]" disabled={r.trigger_kind === "schedule"} checked={a.target === "event"} onChange={() => setAction({ target: "event", target_id: undefined })} />
                    The {targetNoun(a.operation)} the event is about
                  </label>
                  <label className="flex items-center gap-2">
                    <input type="radio" name="target" className="size-4 accent-[var(--accent)]" checked={a.target === "fixed"} onChange={() => setAction({ target: "fixed" })} />
                    A fixed target
                  </label>
                </fieldset>
                {fixed && (
                  <Field label="Target">
                    {(p) => (
                      <Select
                        {...p}
                        value={fixedKey}
                        onChange={(e) => {
                          const c = byKey.get(e.target.value);
                          if (c) setAction({ target_id: c.target_id, params: { ...params, ...c.params } });
                        }}
                      >
                        <option value="" disabled>Choose…</option>
                        {(cands.data?.items ?? []).map((c) => <option key={ckey(c)} value={ckey(c)}>{c.label}</option>)}
                      </Select>
                    )}
                  </Field>
                )}
                {!fixed && a.operation === "port.power_cycle" && (
                  <Field label="Port number" hint="On the device the event is about">
                    {(p) => <Input {...p} type="number" min={1} value={String(params.port_idx ?? "")} onChange={(e) => setAction({ params: { ...params, port_idx: Number(e.target.value) || undefined } })} />}
                  </Field>
                )}
                <Field label="Why" hint="Written on every proposal, followed by the event that triggered it">
                  {(p) => <Textarea {...p} className="min-h-16" maxLength={2000} value={a.reason ?? ""} onChange={(e) => setAction({ reason: e.target.value })} />}
                </Field>
                <p className="text-sm text-ink-3">The proposal waits for someone with approval rights, like any other change. A rule never approves or applies.</p>
              </>
            ) : (
              <>
                {!notifyOn && <Notice tone="warn">The Notifications module is off; runs will be skipped until it is on.</Notice>}
                <fieldset className="flex flex-col gap-1">
                  <legend className="mb-1 text-sm font-semibold">Channels</legend>
                  {(channels.data?.items ?? []).length === 0 && <p className="text-sm text-ink-3">No channels yet; add one under Notifications.</p>}
                  {(channels.data?.items ?? []).map((ch) => (
                    <label key={ch.id} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="size-4 accent-[var(--accent)]"
                        checked={a.channel_ids.includes(ch.id)}
                        onChange={(e) => setAction({ channel_ids: e.target.checked ? [...a.channel_ids, ch.id] : a.channel_ids.filter((x) => x !== ch.id) })}
                      />
                      {ch.name} <span className="text-sm text-ink-3">{ch.kind}{ch.enabled ? "" : ", disabled"}</span>
                    </label>
                  ))}
                  <p className="text-sm text-ink-3">None ticked: every enabled channel at or below the severity.</p>
                </fieldset>
                <Field label="Severity">
                  {(p) => (
                    <Select {...p} value={a.severity} onChange={(e) => setAction({ severity: e.target.value as "info" | "warning" | "critical" })}>
                      <option value="info">Info</option>
                      <option value="warning">Warning</option>
                      <option value="critical">Critical</option>
                    </Select>
                  )}
                </Field>
                <Field label="Title" hint="Defaults to the rule's name">{(p) => <Input {...p} maxLength={128} value={a.title ?? ""} onChange={(e) => setAction({ title: e.target.value })} />}</Field>
              </>
            )}
            <Field label="Quiet period (minutes)" hint="After acting on a target, ignore it for this long. 0 acts every time.">
              {(p) => <Input {...p} type="number" min={0} value={r.cooldown_minutes} onChange={(e) => set({ cooldown_minutes: Math.max(0, Number(e.target.value) || 0) })} />}
            </Field>
          </div>
        </Panel>
        <div className="flex flex-col gap-3 lg:col-span-2">
          {Boolean(error) && <Notice tone="crit" title="Couldn't save this rule">{error instanceof ApiError ? error.message : "The request failed."}</Notice>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" loading={saving} disabled={!r.name.trim()}>{saveLabel}</Button>
            <Button loading={dry.isPending} onClick={() => dry.mutate(r)}>Dry run against the last 7 days</Button>
          </div>
        </div>
      </form>
      {(dry.data || dry.error) && <DryRun result={dry.data} error={dry.error} />}
    </div>
  );
}

export function DryRun({ result, error }: { result?: DryRunResult; error?: unknown }) {
  return (
    <Panel id="dry-run" title="Dry run" actions={result ? <span className="text-sm text-ink-2">{result.evaluated ? `${result.matched} of ${result.evaluated} events matched` : "One scheduled run"}</span> : undefined} bodyClassName="p-0">
      {error ? (
        <div className="p-4"><Notice tone="crit">{error instanceof ApiError ? error.message : "The dry run failed."}</Notice></div>
      ) : !result?.items.length ? (
        <EmptyState title="No events of this type in the last 7 days">Nothing would have happened. The rule acts only on events after it is enabled.</EmptyState>
      ) : (
        <>
          <p className="border-b border-rule px-4 py-2 text-sm text-ink-3">Nothing was stored, proposed or sent. A real proposal also reads the live state, which may refuse it.</p>
          <ul>
            {result.items.map((it, i) => {
              const [tone, word] = DRY[it.outcome];
              return (
                <li key={it.event?.id ?? i} className="flex flex-col gap-1 border-b border-rule px-4 py-2 last:border-b-0 md:flex-row md:items-baseline md:gap-5">
                  <time className="font-mono text-xs text-ink-3 md:w-40">{it.event ? dateTime(it.event.occurred_at) : "Now"}</time>
                  <span className="min-w-0 flex-1 truncate">{it.event?.summary ?? "Scheduled run"}</span>
                  <span className="md:w-44"><StateLabel tone={tone}>{word}</StateLabel></span>
                  <span className="min-w-0 text-sm text-ink-2 md:w-[28rem]">{it.detail}</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Panel>
  );
}

export function ChangeRefLink({ id, refText }: { id: string; refText: string }) {
  return <Link href={`/changes/${id}`} className="font-mono text-xs font-bold text-accent hover:underline">{refText}</Link>;
}
