"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Download } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button, buttonClass } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { FindingsPlate } from "@/components/ipam";
import { ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { ApiError, del, get, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ComplianceControl } from "@/lib/types";

function Status({ c }: { c: ComplianceControl }) {
  if (c.status === "pass") return <StateLabel tone="ok">Pass</StateLabel>;
  if (c.status === "fail") return <StateLabel tone="crit">Fail</StateLabel>;
  if (c.status === "waived") return <StateLabel tone="warn">Waived</StateLabel>;
  return <StateLabel tone="muted">Unknown</StateLabel>;
}

export default function CompliancePage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("compliance");
  const manage = can("compliance:manage");
  const q = useQuery({ queryKey: ["compliance"], queryFn: () => get<{ items: ComplianceControl[]; tally: Record<string, number> }>("/api/compliance"), enabled: on });
  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Compliance" name="Policy & Compliance" canManage={can("modules:manage")}>
        Check MFA, segmentation, firewall hygiene, firmware and drift against built-in controls, with evidence.
      </ModuleOff>
    );
  }
  const items = q.data?.items ?? [];
  const t = q.data?.tally;
  // Categories with failures come first, so attention is at the top of the page.
  const fails = (g: string) => items.filter((c) => c.category === g && c.status === "fail").length;
  const groups = [...new Set(items.map((c) => c.category))].sort((a, b) => fails(b) - fails(a));
  const failing = items.filter((c) => c.status === "fail");
  return (
    <>
      <PageHeader
        title="Compliance"
        description="Built-in controls checked against what Fabric Warden knows right now. Each answer comes with the evidence it used; unknown means the data to decide is not available, never a guess."
        meta={
          t && (
            <span className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <StateLabel tone={t.fail ? "crit" : "muted"}>{`${t.fail} failing`}</StateLabel>
              <StateLabel tone={t.waived ? "warn" : "muted"}>{`${t.waived} waived`}</StateLabel>
              <StateLabel tone={t.pass ? "ok" : "muted"}>{`${t.pass} passing`}</StateLabel>
              <StateLabel tone="muted">{`${t.unknown} unknown`}</StateLabel>
            </span>
          )
        }
        actions={
          <a href="/api/compliance/evidence.csv" className={buttonClass("secondary")} download>
            <Download aria-hidden /> Evidence CSV
          </a>
        }
      />
      {q.error && <Notice tone="crit" className="mb-4">{(q.error as Error).message}</Notice>}
      {!q.data ? (
        <Skeleton className="h-96" />
      ) : (
        <div className="flex flex-col gap-[var(--gap)]">
          <FindingsPlate
            label="Failing controls"
            findings={failing.map((c) => ({ key: c.id, kind: c.category, severity: "warning", title: c.title, detail: c.detail, refs: {}, href: `#control-${c.id}` }))}
            emptyText="No control is failing."
            heading={(n) => `${n} control${n > 1 ? "s" : ""} failing`}
          />
          {groups.map((g) => (
            <Panel key={g} title={g} bodyClassName="p-0">
              <ul>
                {items
                  .filter((c) => c.category === g)
                  .sort((a, b) => ["fail", "waived", "unknown", "pass"].indexOf(a.status) - ["fail", "waived", "unknown", "pass"].indexOf(b.status))
                  .map((c) => (
                    <ControlRow key={c.id} c={c} manage={manage} />
                  ))}
              </ul>
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}

function ControlRow({ c, manage }: { c: ComplianceControl; manage: boolean }) {
  const [open, setOpen] = useState(c.status === "fail");
  const [waiving, setWaiving] = useState(false);
  const qc = useQueryClient();
  const unwaive = useMutation({ mutationFn: () => del(`/api/compliance/${c.id}/waive`), onSuccess: () => qc.invalidateQueries({ queryKey: ["compliance"] }) });
  return (
    <li id={`control-${c.id}`} className="scroll-mt-4 border-b border-rule last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex min-h-[var(--row-h)] w-full flex-col gap-1 px-4 py-2.5 text-left hover:bg-surface-2 md:flex-row md:items-center md:gap-4">
        <span className="md:w-28 md:shrink-0"><Status c={c} /></span>
        <span className="min-w-0 flex-1">
          <span className="block font-bold">{c.title}</span>
          {c.detail !== c.title && <span className="block text-sm text-ink-2">{c.detail}</span>}
        </span>
        {c.failing_since && c.status !== "pass" && <span className="text-sm text-ink-3">failing since {ago(c.failing_since)}</span>}
        <ChevronDown className={`size-4 shrink-0 text-ink-3 transition-transform duration-150 ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && (
        <div className="flex flex-col gap-3 px-4 pb-4 md:pl-36">
          <p className="max-w-[70ch] text-sm text-ink-2">{c.rationale}</p>
          {c.evidence.length > 0 && (
            <table className="w-full max-w-3xl table-fixed border-collapse text-left text-sm">
              <colgroup>
                <col className="w-[42%]" />
                <col />
              </colgroup>
              <caption className="sr-only">Evidence for {c.title}</caption>
              <thead>
                <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                  <th scope="col" className="h-8 pr-4">Looked at</th>
                  <th scope="col" className="h-8">Found</th>
                </tr>
              </thead>
              <tbody>
                {c.evidence.map((e, i) => (
                  <tr key={i} className="border-b border-rule last:border-b-0">
                    <td className="py-1.5 pr-4 font-semibold">{e.object}</td>
                    <td className="py-1.5 text-ink-2">{e.fact}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {c.waiver && (
            <Notice tone="warn" title={`Waived by ${c.waiver.by} until ${new Date(c.waiver.until).toLocaleDateString()}`} action={manage && <Button size="sm" variant="ghost" loading={unwaive.isPending} onClick={() => unwaive.mutate()}>Remove waiver</Button>}>
              {c.waiver.reason}
            </Notice>
          )}
          {manage && c.raw_status === "fail" && !c.waiver && (waiving ? <WaiveForm c={c} onDone={() => setWaiving(false)} /> : <span><Button size="sm" onClick={() => setWaiving(true)}>Waive…</Button></span>)}
        </div>
      )}
    </li>
  );
}

function WaiveForm({ c, onDone }: { c: ComplianceControl; onDone: () => void }) {
  const qc = useQueryClient();
  const in30 = new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10);
  const [f, setF] = useState({ reason: "", until: in30 });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => post(`/api/compliance/${c.id}/waive`, { reason: f.reason.trim(), until: new Date(`${f.until}T23:59:59`).toISOString() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compliance"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (f.reason.trim().length < 5) return setError("Say why this control does not apply.");
    save.mutate();
  };
  return (
    <form onSubmit={submit} noValidate className="grid max-w-3xl gap-3 rounded-[var(--radius)] border border-rule p-3 md:grid-cols-[minmax(0,1fr)_10rem]">
      <Field label="Why it does not apply" error={error}>{(p) => <Textarea {...p} className="min-h-16" value={f.reason} maxLength={1000} onChange={(e) => setF({ ...f, reason: e.target.value })} />}</Field>
      <Field label="Waive until" hint="The waiver lapses after this date">{(p) => <Input {...p} type="date" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} />}</Field>
      <div className="flex gap-2 md:col-span-2">
        <Button type="submit" variant="primary" size="sm" loading={save.isPending}>Waive control</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </form>
  );
}
