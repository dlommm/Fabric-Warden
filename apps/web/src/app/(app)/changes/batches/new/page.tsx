"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { MANY_TARGET_OPERATIONS } from "@/components/changes";
import { Button } from "@/components/ui/button";
import { Field, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import type { BatchCandidate, ChangeBatch } from "@/lib/types";

const PAUSES = [
  [0, "No pause"],
  [30, "30 seconds"],
  [60, "1 minute"],
  [120, "2 minutes"],
  [300, "5 minutes"],
  [900, "15 minutes"],
] as const;
const MAX_ITEMS = 50;

/** Under a group heading that already names the switch, a port reads as "Port 3". */
const shortLabel = (c: BatchCandidate) => (c.label.startsWith(`${c.group} port `) ? `Port ${c.label.slice(c.group.length + 6)}` : c.label);

const key = (c: BatchCandidate) => `${c.target_id}:${String(c.params.port_idx ?? "")}`;

export default function NewBatchPage() {
  const router = useRouter();
  const [operation, setOperation] = useState<string>(MANY_TARGET_OPERATIONS[0].id);
  const [flag, setFlag] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [pause, setPause] = useState(0);
  const [reason, setReason] = useState("");
  const op = MANY_TARGET_OPERATIONS.find((o) => o.id === operation) ?? MANY_TARGET_OPERATIONS[0];
  const cands = useQuery({
    queryKey: ["changes", "candidates", operation],
    queryFn: () => get<{ items: BatchCandidate[] }>(`/api/changes/candidates?operation=${encodeURIComponent(operation)}`),
  });
  const groups = useMemo(() => {
    const out = new Map<string, BatchCandidate[]>();
    for (const c of cands.data?.items ?? []) out.set(c.group, [...(out.get(c.group) ?? []), c]);
    return [...out.entries()];
  }, [cands.data]);
  const byKey = useMemo(() => new Map((cands.data?.items ?? []).map((c) => [key(c), c])), [cands.data]);
  const chosen = [...picked].map((k) => byKey.get(k)).filter((c): c is BatchCandidate => !!c);

  const m = useMutation({
    mutationFn: () =>
      post<ChangeBatch>("/api/batches", {
        operation,
        targets: chosen.map((c) => ({ target_id: c.target_id, params: c.params })),
        params: op.flag ? { [op.flag.key]: flag } : {},
        reason: reason.trim(),
        pause_seconds: pause,
      }),
    onSuccess: (b) => router.push(`/changes/batches/${b.id}`),
  });

  const toggle = (k: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(k);
      else next.delete(k);
      return next;
    });

  return (
    <>
      <Link href="/changes/batches" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
        <ChevronLeft className="size-4" aria-hidden /> Bulk operations
      </Link>
      <PageHeader
        title="New bulk operation"
        description="Proposing reads every target live from the controller and works out each one's risk. Targets the change engine refuses are listed as skipped. Nothing is changed until the batch is approved and applied."
      />
      <form
        className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_22rem]"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Panel title={`Targets${chosen.length ? ` (${chosen.length} chosen)` : ""}`} bodyClassName="p-0">
          {cands.isLoading ? (
            <div className="p-4"><Skeleton className="h-40" /></div>
          ) : cands.error ? (
            <div className="p-4"><Notice tone="crit">{(cands.error as Error).message}</Notice></div>
          ) : !groups.length ? (
            <EmptyState title="No targets in the inventory">Nothing observed on your controllers can take this operation.</EmptyState>
          ) : (
            groups.map(([group, rows]) => {
              const ready = rows.filter((r) => r.ready);
              const all = ready.length > 0 && ready.every((r) => picked.has(key(r)));
              const demoGroup = rows.every((r) => r.is_demo);
              return (
                <fieldset key={group} className="border-b border-rule last:border-b-0">
                  <legend className="sr-only">{group}</legend>
                  <div className="flex min-h-9 items-center justify-between gap-3 bg-surface-2 px-4">
                    <span className="flex items-center gap-3">
                      <span className="text-xs font-bold tracking-wide text-ink-2 uppercase" aria-hidden>{group}</span>
                      {demoGroup && <DemoTag />}
                    </span>
                    {ready.length > 1 && (
                      <button type="button" className="text-sm font-semibold text-accent hover:underline" onClick={() => ready.forEach((r) => toggle(key(r), !all))}>
                        {all ? "Clear" : `Choose all ${ready.length}`}
                      </button>
                    )}
                  </div>
                  <ul className="grid md:grid-cols-2">
                    {rows.map((c) => (
                      <li key={key(c)}>
                        <label className={`flex min-h-[var(--row-h)] items-center gap-3 px-4 py-1.5 ${c.ready ? "cursor-pointer hover:bg-surface-2" : "text-ink-3"}`}>
                          <input
                            type="checkbox"
                            className="size-4 accent-[var(--accent)]"
                            disabled={!c.ready}
                            checked={picked.has(key(c))}
                            onChange={(e) => toggle(key(c), e.target.checked)}
                          />
                          <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate font-semibold">{shortLabel(c)}</span>
                            <span className="truncate text-sm text-ink-3">{c.ready ? c.detail : `${c.detail} · not online, cannot take it now`}</span>
                          </span>
                          {c.is_demo && !demoGroup && <DemoTag />}
                        </label>
                      </li>
                    ))}
                  </ul>
                </fieldset>
              );
            })
          )}
        </Panel>
        <div className="flex flex-col gap-[var(--gap)]">
          <Panel title="Operation">
            <div className="flex flex-col gap-4">
              <Field label="Change">
                {(p) => (
                  <Select
                    {...p}
                    value={operation}
                    onChange={(e) => {
                      setOperation(e.target.value);
                      setPicked(new Set());
                    }}
                  >
                    {MANY_TARGET_OPERATIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </Select>
                )}
              </Field>
              {op.flag && (
                <fieldset className="flex flex-col gap-1">
                  <legend className="mb-1 text-sm font-semibold">Set to</legend>
                  {[true, false].map((v) => (
                    <label key={String(v)} className="flex items-center gap-2">
                      <input type="radio" name="flag" className="size-4 accent-[var(--accent)]" checked={flag === v} onChange={() => setFlag(v)} />
                      {v ? op.flag?.on : op.flag?.off}
                    </label>
                  ))}
                </fieldset>
              )}
              <Field label="Pause between items" hint="Counted from when one item is verified to when the next is sent">
                {(p) => (
                  <Select {...p} value={pause} onChange={(e) => setPause(Number(e.target.value))}>
                    {PAUSES.map(([s, l]) => <option key={s} value={s}>{l}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Why" hint="Recorded with every item and in the audit trail">
                {(p) => <Textarea {...p} className="min-h-20" maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What this fixes or enables" />}
              </Field>
              <p className="text-sm text-ink-3">Items are applied in the order listed and the batch stops at the first one that fails, conflicts or is reverted; the rest are not sent.</p>
              {chosen.length > MAX_ITEMS && <Notice tone="warn">A bulk operation covers at most {MAX_ITEMS} targets.</Notice>}
              {m.error && <Notice tone="crit" title="Couldn't propose this">{m.error instanceof ApiError ? m.error.message : "The request failed."}</Notice>}
              <Button type="submit" variant="primary" loading={m.isPending} disabled={!chosen.length || chosen.length > MAX_ITEMS}>
                {chosen.length ? `Propose for ${chosen.length} target${chosen.length === 1 ? "" : "s"}` : "Choose targets to propose"}
              </Button>
            </div>
          </Panel>
        </div>
      </form>
    </>
  );
}
