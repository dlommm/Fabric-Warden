"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Layers, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { BatchState, ChangeState, EvidenceTimeline, ImpactPanel, RiskLabel, batchProgress } from "@/components/changes";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/field";
import { KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChangeBatch, ChangeSettings } from "@/lib/types";

function BackLink() {
  return (
    <Link href="/changes/batches" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> Bulk operations
    </Link>
  );
}

export default function BatchPage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({
    queryKey: ["batches", id],
    queryFn: () => get<ChangeBatch>(`/api/batches/${id}`),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 5_000 : 60_000),
  });
  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this bulk operation">{(q.error as Error).message}</Notice>
      </>
    );
  }
  const b = q.data;
  if (!b) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-96" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }
  const items = b.items ?? [];
  const reasons = [...new Set(items.filter((i) => i.risk === "high" || i.protected).flatMap((i) => i.risk_reasons.map((r) => `${i.target.label}: ${r}`)))];
  const attention = b.risk === "high" || b.protected;
  return (
    <>
      <BackLink />
      <PageHeader
        icon={<Layers />}
        title={b.title}
        description={b.reason || undefined}
        meta={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="font-mono text-xs font-bold">{b.ref}</span>
            <BatchState status={b.status} />
            <RiskLabel risk={b.risk} protectedPath={b.protected} />
            <span className="text-ink-2">{batchProgress(b)}</span>
            {b.is_demo && <DemoTag />}
          </span>
        }
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-w-0 flex-col gap-[var(--gap)]">
          {attention && (
            <section aria-labelledby="risk-h" className="attention-plate">
              <div className="flex items-baseline gap-3 px-5 pt-4 pb-2">
                <TriangleAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
                <h2 id="risk-h" className="text-lg font-bold">{b.protected ? "Protected path: read this before approving" : "High risk"}</h2>
              </div>
              <ul className="mx-2 mb-2 overflow-hidden rounded-[5px] bg-surface shadow-[0_1px_2px_rgb(4_18_48/0.22)] px-4 py-3 text-ink">
                {reasons.map((r) => <li key={r} className="py-0.5">{r}</li>)}
              </ul>
            </section>
          )}
          <Panel title={`Items, in the order they are applied (${items.length})`} bodyClassName="p-0">
            <ol>
              {items.map((c, i) => (
                <li key={c.id} className="border-b border-rule last:border-b-0">
                  <Link href={`/changes/${c.id}`} className="flex flex-col gap-1 px-4 py-3 hover:bg-surface-2 md:flex-row md:items-center md:gap-5">
                    <span className="flex shrink-0 items-baseline gap-3 md:w-28">
                      <span className="tabular w-5 text-sm font-bold text-ink-3">{i + 1}</span>
                      <span className="font-mono text-xs font-bold">{c.ref}</span>
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-bold">{c.target.label}</span>
                      <span className="truncate text-sm text-ink-3">{c.diff.map((d) => `${d.field}: ${d.before} → ${d.after}`).join(" · ")}</span>
                    </span>
                    <span className="shrink-0 md:w-52"><ChangeState status={c.status} /></span>
                    <span className="whitespace-nowrap shrink-0 md:w-36"><RiskLabel risk={c.risk} protectedPath={c.protected} /></span>
                  </Link>
                </li>
              ))}
            </ol>
          </Panel>
          {b.skipped.length > 0 && (
            <Panel title={`Skipped at proposal (${b.skipped.length})`}>
              <p className="mb-2 text-sm text-ink-3">The change engine refused these targets when the batch was proposed; they are not part of it.</p>
              <ul className="flex flex-col gap-1">
                {b.skipped.map((s) => (
                  <li key={`${s.target_id}:${String(s.params.port_idx ?? "")}`} className="max-w-[70ch]">
                    {s.params.port_idx != null && <span className="font-semibold">Port {String(s.params.port_idx)}: </span>}
                    {s.reason}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          <ImpactPanel url={`/api/twin/batches/${b.id}/impact`} queryKey={["twin", "batch", b.id, b.status]} />
          <Panel title="Batch log">
            <EvidenceTimeline items={b.log} />
            <p className="mt-3 text-sm text-ink-3">Each item keeps its own evidence (every read, write and check) on its change page.</p>
          </Panel>
        </div>
        <div className="flex flex-col gap-[var(--gap)]">
          <Actions b={b} />
          <Panel title="Record">
            <KeyValue
              items={[
                ["Proposed", `${b.proposed_by} · ${dateTime(b.proposed_at)}`],
                ["Approved", b.approved_by ? `${b.approved_by}${b.approved_by === b.proposed_by ? " (own proposal)" : ""} · ${dateTime(b.approved_at)}` : "—"],
                ["Started", b.started_by ? `${b.started_by} · ${dateTime(b.started_at)}` : "—"],
                ["Pause", b.pause_seconds ? `${b.pause_seconds} s between items` : "None"],
                ["Finished", b.finished_at ? dateTime(b.finished_at) : "—"],
              ]}
            />
          </Panel>
        </div>
      </div>
    </>
  );
}

function Actions({ b }: { b: ChangeBatch }) {
  const { can, me } = useSession();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const act = useMutation({
    mutationFn: (what: "approve" | "reject" | "cancel" | "apply" | "stop") =>
      post<ChangeBatch>(`/api/batches/${b.id}/${what}`, what === "apply" ? undefined : { note: note.trim(), acknowledge_protected: ack }),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["batches"] });
      qc.invalidateQueries({ queryKey: ["changes"] });
    },
  });
  const settings = useQuery({ queryKey: ["changes", "settings"], queryFn: () => get<ChangeSettings>("/api/changes/settings/current") });
  const approver = can("changes:approve");
  const mine = me.username === b.proposed_by;
  const needsSecond = !!settings.data?.require_second_person && mine;
  const controllers = new Set((b.items ?? []).map((i) => i.controller?.id));
  const readOnly = (b.items ?? []).some((i) => i.controller?.write_mode !== "changes");

  let body: React.ReactNode;
  if (b.status === "proposed" && approver && needsSecond) {
    body = (
      <>
        <p className="text-ink-2">A second person must approve your own proposal on this installation.</p>
        <Button variant="danger" className="self-start" loading={act.isPending && act.variables === "reject"} onClick={() => act.mutate("reject")}>Reject</Button>
      </>
    );
  } else if (b.status === "proposed") {
    body = approver ? (
      <>
        <p className="text-ink-2">One decision approves every item. Each is still re-read and drift-checked just before it is sent.</p>
        <Field label="Note" hint="Optional; recorded with every item">{(p) => <Textarea {...p} className="min-h-16" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
        {b.protected && (
          <label className="flex items-start gap-2 text-sm font-semibold">
            <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I have read the protected-path warning and accept the impact
          </label>
        )}
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" loading={act.isPending && act.variables === "approve"} disabled={b.protected && !ack} onClick={() => act.mutate("approve")}>Approve all items</Button>
          <Button variant="danger" loading={act.isPending && act.variables === "reject"} onClick={() => act.mutate("reject")}>Reject</Button>
        </div>
        {b.protected && !ack && <p className="text-sm text-ink-3">Tick the acknowledgement above to approve.</p>}
        {mine && <p className="text-sm text-ink-3">This is your own proposal; approving it is recorded as such.</p>}
      </>
    ) : (
      <p className="text-ink-2">Waiting for someone with approval rights.</p>
    );
  } else if (b.status === "approved") {
    body = approver ? (
      <>
        {readOnly ? (
          <Notice tone="info" title={controllers.size > 1 ? "A controller is read-only" : "This controller is read-only"}>
            An owner can allow changes on the <Link href="/changes" className="font-semibold text-accent hover:underline">Changes</Link> page.
          </Notice>
        ) : (
          <p className="text-ink-2">Items are applied one at a time{b.pause_seconds ? `, ${b.pause_seconds} s apart` : ""}. The batch stops at the first item that does not succeed.</p>
        )}
        <Button variant="primary" loading={act.isPending && act.variables === "apply"} disabled={readOnly} onClick={() => act.mutate("apply")}>Start applying</Button>
      </>
    ) : (
      <p className="text-ink-2">Approved; waiting for someone with approval rights to start it.</p>
    );
  } else if (b.status === "running") {
    body = (
      <>
        <p className="text-ink-2">
          {b.next_at ? `Pausing; the next item is sent at ${dateTime(b.next_at)}.` : "Applying and verifying the current item; the next starts when it is verified."}
        </p>
        {approver && (
          <>
            <Field label="Note" hint="Optional; why the rest should not be sent">{(p) => <Textarea {...p} className="min-h-16" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
            <Button variant="danger" className="self-start" loading={act.isPending && act.variables === "stop"} onClick={() => act.mutate("stop")}>Stop after this item</Button>
            <p className="text-sm text-ink-3">The item in progress finishes and is verified; the remaining items are cancelled.</p>
          </>
        )}
      </>
    );
  } else {
    body = <p className="text-ink-2">{b.status === "succeeded" ? "Done. A reversible item can be reverted from its own change page." : b.decided_note || "This bulk operation is closed."}</p>;
  }
  const canCancel = (b.status === "proposed" || b.status === "approved") && (mine || approver);
  return (
    <Panel title="Next step">
      <div className="flex flex-col gap-3">
        {body}
        {canCancel && (
          <Button variant="ghost" className="self-start" loading={act.isPending && act.variables === "cancel"} onClick={() => act.mutate("cancel")}>Cancel this bulk operation</Button>
        )}
        {act.error && <Notice tone="crit">{act.error instanceof ApiError ? act.error.message : "The request failed."}</Notice>}
      </div>
    </Panel>
  );
}
