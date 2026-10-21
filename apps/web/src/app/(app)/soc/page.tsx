"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { FolderOpen, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Select, Textarea } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel, type Tone } from "@/components/ui/signs";
import { get, patch, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

type Sev = "low" | "medium" | "high" | "critical";
interface Entity { type: string; id: string | null; label: string | null }
interface Signal { key: string; kind: string; severity: Sev; title: string; detail: string; source: string; entities: Entity[]; seen_at: string | null; is_demo: boolean; case: { id: string; status: string; title: string } | null }
interface Case { id: string; title: string; severity: Sev; status: "open" | "investigating" | "resolved" | "false_positive"; assignee: string | null; summary: string; entities: Entity[]; signal_key: string | null; timeline: { at: string; author: string; kind: string; text: string }[]; created_at: string; updated_at: string; resolved_at: string | null; is_demo: boolean }

const SEV_TONE: Record<Sev, Tone> = { critical: "crit", high: "crit", medium: "warn", low: "info" };
const STATUS_WORD = { open: "Open", investigating: "Investigating", resolved: "Resolved", false_positive: "False positive" };

function entityHref(e: Entity): string | undefined {
  if (e.type === "ip" && e.id) return `/investigate?q=${e.id}`;
  if (e.type === "device" && e.id) return `/inventory/devices/${e.id}`;
  if (e.type === "client" && e.id) return `/inventory/clients/${e.id}`;
  if (e.type === "policy") return "/firewall";
  if (e.type === "control") return "/compliance";
  return undefined;
}

function Entities({ list }: { list: Entity[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {list.map((e, i) => {
        const href = entityHref(e);
        const body = <Chip className={href ? "hover:border-ink-3" : undefined}><span className="mr-1 text-ink-3">{e.type}</span>{e.label ?? e.id}</Chip>;
        return href ? <Link key={i} href={href}>{body}</Link> : <span key={i}>{body}</span>;
      })}
    </span>
  );
}

export default function SocPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("soc");
  const manage = can("soc:manage");
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["soc"], queryFn: () => get<{ signals: Signal[]; cases: Case[]; counts: { signals: number; open_cases: number } }>("/api/soc"), enabled: on, refetchInterval: 30_000 });
  const openCase = useMutation({
    mutationFn: (s: Signal) => post<Case>("/api/soc/cases", { title: s.title, severity: s.severity, summary: s.detail, entities: s.entities, signal_key: s.key }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["soc"] });
      setSelected(c.id);
    },
  });
  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="SOC" name="Security Operations Center" canManage={can("modules:manage")}>
        One queue of security signals from firewall analysis, compliance, alerts and logs, and cases to work them.
      </ModuleOff>
    );
  }
  const d = q.data;
  const fresh = d?.signals.filter((s) => !s.case) ?? [];
  const handled = d?.signals.filter((s) => s.case) ?? [];
  const current = d?.cases.find((c) => c.id === selected) ?? null;
  return (
    <>
      <PageHeader
        title="SOC"
        description="Security signals gathered from firewall analysis, compliance, alerts and logs, worked as cases. Signals are computed live; a case records what you decided and why."
        meta={d && <span className="flex flex-wrap gap-x-4 text-sm"><StateLabel tone={fresh.length ? "warn" : "muted"}>{`${fresh.length} signal${fresh.length === 1 ? "" : "s"} without a case`}</StateLabel><StateLabel tone={d.counts.open_cases ? "info" : "muted"}>{`${d.counts.open_cases} open case${d.counts.open_cases === 1 ? "" : "s"}`}</StateLabel></span>}
      />
      {(q.error || openCase.error) && <Notice tone="crit" className="mb-4">{((q.error || openCase.error) as Error).message}</Notice>}
      {!d ? (
        <Skeleton className="h-96" />
      ) : (
        <div className="grid gap-[var(--gap)] 2xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] 2xl:items-start">
          <div className="flex flex-col gap-[var(--gap)]">
            <Panel title="Signals without a case" bodyClassName="p-0">
              {fresh.length === 0 ? (
                <EmptyState icon={<ShieldCheck />} title="Nothing waiting">Every signal has a case, or nothing needs attention.</EmptyState>
              ) : (
                <ul>
                  {fresh.map((s) => (
                    <li key={s.key} className={clsx("flex flex-col gap-2 border-b border-rule px-4 py-3 last:border-b-0 md:flex-row md:items-start md:gap-4", (s.severity === "high" || s.severity === "critical") && "bg-warn-soft")}>
                      <span className="md:w-24 md:shrink-0"><StateLabel tone={SEV_TONE[s.severity]} className="capitalize">{s.severity}</StateLabel></span>
                      <span className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="font-bold">{s.title}</span>
                        {s.detail && <span className="break-words text-sm text-ink-2">{s.detail}</span>}
                        <span className="flex flex-wrap items-center gap-2 text-xs text-ink-3"><span>{s.source}</span>{s.seen_at && <span>· {ago(s.seen_at)}</span>}<Entities list={s.entities} />{s.is_demo && <DemoTag />}</span>
                      </span>
                      {manage && <Button size="sm" icon={<FolderOpen />} loading={openCase.isPending && openCase.variables?.key === s.key} onClick={() => openCase.mutate(s)}>Open case</Button>}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title={`Cases (${d.cases.length})`} bodyClassName="p-0">
              {d.cases.length === 0 ? (
                <p className="px-4 py-3 text-sm text-ink-3">No cases yet. Open one from a signal.</p>
              ) : (
                <ul>
                  {d.cases.map((c) => (
                    <li key={c.id} className="border-b border-rule last:border-b-0">
                      <button type="button" onClick={() => setSelected(c.id === selected ? null : c.id)} aria-pressed={c.id === selected} className={clsx("flex w-full flex-col gap-1 px-4 py-2.5 text-left hover:bg-surface-2 md:flex-row md:items-center md:gap-4", c.id === selected && "bg-accent-soft")}>
                        <span className="md:w-24 md:shrink-0"><StateLabel tone={SEV_TONE[c.severity]} className="capitalize">{c.severity}</StateLabel></span>
                        <span className="min-w-0 flex-1 truncate font-bold">{c.title}</span>
                        <StateLabel tone={c.status === "resolved" ? "ok" : c.status === "false_positive" ? "muted" : c.status === "investigating" ? "info" : "warn"}>{STATUS_WORD[c.status]}</StateLabel>
                        <span className="text-sm text-ink-3">{c.assignee ?? "unassigned"} · {ago(c.updated_at)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            {handled.length > 0 && <p className="text-sm text-ink-3">{handled.length} more signal{handled.length > 1 ? "s are" : " is"} already tied to a case.</p>}
          </div>
          {current && <CasePanel key={current.id} c={current} manage={manage} />}
        </div>
      )}
    </>
  );
}

function CasePanel({ c, manage }: { c: Case; manage: boolean }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Case>(`/api/soc/cases/${c.id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["soc"] });
      setNote("");
    },
  });
  return (
    <Panel title={c.title} actions={c.is_demo && <DemoTag />} className="2xl:sticky 2xl:top-4">
      <div className="flex flex-col gap-4">
        {c.summary && <p className="text-sm text-ink-2">{c.summary}</p>}
        {c.entities.length > 0 && <Entities list={c.entities} />}
        {manage && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Status">
              {(p) => (
                <Select {...p} value={c.status} onChange={(e) => update.mutate({ status: e.target.value })}>
                  {Object.entries(STATUS_WORD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Severity">
              {(p) => (
                <Select {...p} value={c.severity} onChange={(e) => update.mutate({ severity: e.target.value })}>
                  {["low", "medium", "high", "critical"].map((s) => <option key={s} value={s} className="capitalize">{s[0].toUpperCase() + s.slice(1)}</option>)}
                </Select>
              )}
            </Field>
          </div>
        )}
        <ol className="flex flex-col gap-0 border-l-2 border-rule-strong pl-4" aria-label="Case timeline">
          {c.timeline.map((t, i) => (
            <li key={i} className="relative pb-3 last:pb-0">
              <span aria-hidden className="absolute top-1.5 -left-[21px] size-2.5 rounded-[2px] bg-ink" />
              <p className="text-xs text-ink-3">{dateTime(t.at)} · {t.author}</p>
              <p className={clsx("text-sm", t.kind === "note" ? "text-ink" : "font-semibold text-ink-2")}>{t.text}</p>
            </li>
          ))}
        </ol>
        {manage && (
          <form onSubmit={(e) => { e.preventDefault(); if (note.trim()) update.mutate({ note: note.trim() }); }} className="flex flex-col gap-2">
            <Field label="Add a note">{(p) => <Textarea {...p} className="min-h-16" value={note} maxLength={5000} onChange={(e) => setNote(e.target.value)} placeholder="What you checked, what you found, what you did" />}</Field>
            <span><Button type="submit" size="sm" variant="primary" loading={update.isPending} disabled={!note.trim()}>Add note</Button></span>
          </form>
        )}
        {update.error && <Notice tone="crit">{(update.error as Error).message}</Notice>}
      </div>
    </Panel>
  );
}
