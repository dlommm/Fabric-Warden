"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { BatchState, ChangeState, EvidenceTimeline, ImpactPanel, RiskLabel } from "@/components/changes";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/field";
import { KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChangeRequest, ChangeSettings } from "@/lib/types";

const TARGET_HREF: Record<string, (c: ChangeRequest) => string> = {
  firewall_policy: () => "/firewall",
  device: (c) => `/inventory/devices/${c.target.id}`,
  port: (c) => `/inventory/devices/${c.target.id}#ports`,
};

function BackLink() {
  return (
    <Link href="/changes" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> Changes
    </Link>
  );
}

export default function ChangePage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({
    queryKey: ["changes", id],
    queryFn: () => get<ChangeRequest>(`/api/changes/${id}`),
    refetchInterval: (query) => (["applying", "verifying"].includes(query.state.data?.status ?? "") ? 5_000 : 60_000),
  });
  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this change">{(q.error as Error).message}</Notice>
      </>
    );
  }
  const c = q.data;
  if (!c) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-96" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }
  const attention = c.risk === "high" || c.protected;
  return (
    <>
      <BackLink />
      <PageHeader
        title={c.title}
        description={c.reason || undefined}
        meta={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="font-mono text-xs font-bold">{c.ref}</span>
            <ChangeState status={c.status} />
            <RiskLabel risk={c.risk} protectedPath={c.protected} />
            {c.is_demo && <DemoTag />}
          </span>
        }
      />
      <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-w-0 flex-col gap-[var(--gap)]">
          {attention && (
            <section aria-labelledby="risk-h" className="sign rounded-[var(--radius)] bg-warn text-warn-ink" style={{ outlineColor: "#14171a" }}>
              <div className="flex items-baseline gap-3 px-5 pt-4 pb-2">
                <TriangleAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
                <h2 id="risk-h" className="text-lg font-bold">{c.protected ? "Protected path: read this before approving" : "High risk"}</h2>
              </div>
              <ul className="mx-2 mb-2 rounded-[3px] bg-surface px-4 py-3 text-ink">
                {c.risk_reasons.map((r) => <li key={r} className="py-0.5">{r}</li>)}
              </ul>
            </section>
          )}
          <Panel title="What changes">
            <table className="w-full text-left">
              <caption className="sr-only">Before and after</caption>
              <thead>
                <tr className="text-xs font-bold tracking-wide text-ink-2 uppercase">
                  <th className="pb-2 font-bold">Field</th>
                  <th className="pb-2 font-bold">Now</th>
                  <th className="pb-2 font-bold">After</th>
                </tr>
              </thead>
              <tbody>
                {c.diff.map((d) => (
                  <tr key={d.field} className="border-t border-rule">
                    <td className="py-2 pr-4 font-semibold">{d.field}</td>
                    {d.unchanged ? (
                      <>
                        <td className="py-2 pr-4 text-ink-3">{d.before}</td>
                        <td className="py-2 text-ink-2">{d.after}</td>
                      </>
                    ) : (
                      <>
                        <td className="py-2 pr-4 text-crit line-through decoration-crit/60">{d.before}</td>
                        <td className="py-2 font-bold">{d.after}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-sm text-ink-3">
              On <Link href={TARGET_HREF[c.target.type]?.(c) ?? "#"} className="font-semibold text-accent hover:underline">{c.target.label}</Link> via {c.controller?.name ?? "the controller"}. “Now” was read live from the controller when this was proposed, {ago(c.proposed_at)}; it is read again just before applying.
            </p>
          </Panel>
          <ImpactPanel url={`/api/twin/changes/${c.id}/impact`} queryKey={["twin", "change", c.id, c.status]} />
          <Panel title="Impact and recovery">
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {c.impact.map((i) => <li key={i}>{i}</li>)}
              {!attention && c.risk_reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
            <p className="mt-3 max-w-[70ch] text-ink-2"><span className="font-semibold text-ink">Recovery.</span> {c.recovery}</p>
          </Panel>
          <Panel title="Evidence">
            <EvidenceTimeline items={c.evidence} />
          </Panel>
        </div>
        <div className="flex flex-col gap-[var(--gap)]">
          {c.batch && ["proposed", "approved", "applying", "verifying"].includes(c.status) ? (
            <Panel title="Next step">
              <div className="flex flex-col gap-2">
                <p className="text-ink-2">
                  Item {(c.batch_position ?? 0) + 1} of <Link href={`/changes/batches/${c.batch.id}`} className="font-semibold text-accent hover:underline"><span className="font-mono text-xs">{c.batch.ref}</span></Link>. It is approved, applied and cancelled with its bulk operation.
                </p>
                <BatchState status={c.batch.status} />
              </div>
            </Panel>
          ) : (
            <Actions c={c} />
          )}
          <Panel title="Record">
            <KeyValue
              items={[
                ["Proposed", `${c.proposed_by} · ${dateTime(c.proposed_at)}`],
                ["Approved", c.approved_by ? `${c.approved_by}${c.approved_by === c.proposed_by ? " (own proposal)" : ""} · ${dateTime(c.approved_at)}` : "—"],
                ["Applied", c.applied_by ? `${c.applied_by} · ${dateTime(c.applied_at)}` : "—"],
                ["Finished", c.finished_at ? dateTime(c.finished_at) : "—"],
                ["Reversible", c.reversible ? "Yes, by a new change" : "No"],
                ...(c.status === "proposed" || c.status === "approved" ? [["Open until", dateTime(c.expires_at)] as [string, string]] : []),
              ]}
            />
            {c.batch && (
              <p className="mt-3 text-sm">
                Part of <Link href={`/changes/batches/${c.batch.id}`} className="font-semibold text-accent hover:underline"><span className="font-mono text-xs">{c.batch.ref}</span> {c.batch.title}</Link>
              </p>
            )}
            {c.inverse_of_id && <p className="mt-3 text-sm"><Link href={`/changes/${c.inverse_of_id}`} className="font-semibold text-accent hover:underline">The change this reverts</Link></p>}
          </Panel>
        </div>
      </div>
    </>
  );
}

function Actions({ c }: { c: ChangeRequest }) {
  const { can, me } = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);
  const done = () => qc.invalidateQueries({ queryKey: ["changes"] });
  const act = useMutation({
    mutationFn: (what: "approve" | "reject" | "cancel" | "apply") => post<ChangeRequest>(`/api/changes/${c.id}/${what}`, what === "apply" ? undefined : { note: note.trim(), acknowledge_protected: ack }),
    onSuccess: () => {
      setNote("");
      done();
    },
  });
  const revert = useMutation({
    mutationFn: () => post<ChangeRequest>(`/api/changes/${c.id}/revert`, { note: note.trim() }),
    onSuccess: (n) => {
      done();
      router.push(`/changes/${n.id}`);
    },
  });
  const approver = can("changes:approve");
  const proposer = can("changes:propose");
  const mine = me.username === c.proposed_by;
  const readOnly = c.controller?.write_mode !== "changes";
  const settings = useQuery({ queryKey: ["changes", "settings"], queryFn: () => get<ChangeSettings>("/api/changes/settings/current") });
  const needsSecond = !!settings.data?.require_second_person && mine;
  const expired = (c.status === "proposed" || c.status === "approved") && new Date(c.expires_at) < new Date();
  const error = act.error || revert.error;

  let body: React.ReactNode = null;
  if (expired) {
    body = <p className="text-ink-2">The 24-hour window closed at {dateTime(c.expires_at)}; propose the change again.</p>;
  } else if (c.status === "proposed" && approver && needsSecond) {
    body = (
      <>
        <p className="text-ink-2">A second person must approve your own proposal on this installation.</p>
        <Button variant="danger" className="self-start" loading={act.isPending && act.variables === "reject"} onClick={() => act.mutate("reject")}>Reject</Button>
      </>
    );
  } else if (c.status === "proposed") {
    body = approver ? (
      <>
        <Field label="Note" hint="Optional; recorded with the decision">{(p) => <Textarea {...p} className="min-h-16" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
        {c.protected && (
          <label id={`ack-${c.id}`} className="flex items-start gap-2 text-sm font-semibold">
            <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I have read the protected-path warning and accept the impact
          </label>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            loading={act.isPending && act.variables === "approve"}
            disabled={c.protected && !ack}
            aria-describedby={c.protected && !ack ? `ack-hint-${c.id}` : undefined}
            onClick={() => act.mutate("approve")}
          >
            Approve
          </Button>
          <Button variant="danger" loading={act.isPending && act.variables === "reject"} onClick={() => act.mutate("reject")}>Reject</Button>
        </div>
        {c.protected && !ack && <p id={`ack-hint-${c.id}`} className="text-sm text-ink-3">Tick the acknowledgement above to approve.</p>}
        {mine && <p className="text-sm text-ink-3">This is your own proposal; approving it is recorded as such.</p>}
        {readOnly && <p className="text-sm text-ink-3">This controller is read-only: approving records the decision; the change can only be applied after an owner allows changes.</p>}
      </>
    ) : (
      <p className="text-ink-2">Waiting for someone with approval rights.</p>
    );
  } else if (c.status === "approved") {
    body = approver ? (
      <>
        {readOnly ? (
          <Notice tone="info" title="This controller is read-only">An owner can allow changes on it on the <Link href="/changes" className="font-semibold text-accent hover:underline">Changes</Link> page.</Notice>
        ) : (
          <p className="text-ink-2">Applying re-reads the target first and sends nothing if it changed since the proposal.</p>
        )}
        <Button variant="primary" loading={act.isPending && act.variables === "apply"} disabled={readOnly} onClick={() => act.mutate("apply")}>Apply now</Button>
      </>
    ) : (
      <p className="text-ink-2">Approved; waiting for someone with approval rights to apply it.</p>
    );
  } else if (c.status === "applying" || c.status === "verifying") {
    body = (
      <p className="text-ink-2">
        Sent. Fabric Warden is re-reading the controller until the expected state appears{c.verify_deadline ? `, until ${dateTime(c.verify_deadline)}` : ""}.
      </p>
    );
  } else if (c.status === "succeeded" && c.reversible && proposer) {
    body = (
      <>
        <p className="text-ink-2">Reverting proposes the opposite change, which is reviewed and approved like any other.</p>
        <Button loading={revert.isPending} onClick={() => revert.mutate()}>Propose a revert</Button>
      </>
    );
  } else {
    body = <p className="text-ink-2">{c.decided_note || (c.status === "succeeded" ? "Done." : "This change is closed.")}</p>;
  }
  const canCancel = (c.status === "proposed" || c.status === "approved") && (mine || approver);
  return (
    <Panel title="Next step">
      <div className="flex flex-col gap-3">
        {body}
        {canCancel && (
          <Button variant="ghost" className="self-start" loading={act.isPending && act.variables === "cancel"} onClick={() => act.mutate("cancel")}>Cancel this change</Button>
        )}
        {error && <Notice tone="crit">{error instanceof ApiError ? error.message : "The request failed."}</Notice>}
      </div>
    </Panel>
  );
}
