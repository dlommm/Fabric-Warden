"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AiAnswer, AiTag, AUTONOMY_WORD, money, RunProgress, RunState, RunTrigger, TRIGGER_WORD, Transcript } from "@/components/ai";
import { RiskLabel } from "@/components/changes";
import { KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { get } from "@/lib/api";
import { dateTime, duration } from "@/lib/format";
import type { AiRun } from "@/lib/types";

const SUBJECT_HREF: Record<string, (v: string) => string> = {
  client_id: (v) => `/inventory/clients/${v}`,
  alert_id: () => "/alerts",
  site_id: () => "/firewall",
  case_id: () => "/soc",
  change_id: (v) => `/changes/${v}`,
};

function BackLink() {
  return (
    <Link href="/ai/agents" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> Agent Center
    </Link>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({
    queryKey: ["ai", "runs", id],
    queryFn: () => get<AiRun>(`/api/ai/runs/${id}`),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1_500 : false),
  });
  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this run">{(q.error as Error).message}</Notice>
      </>
    );
  }
  const r = q.data;
  if (!r) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-96" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }
  const took = r.finished_at ? Math.max(0, (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000) : null;
  const subject = Object.entries(r.subject);
  return (
    <>
      <BackLink />
      <PageHeader
        title={`${r.agent_name}`}
        description={r.question}
        meta={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="font-mono text-xs font-bold">{r.ref}</span>
            <RunState status={r.status} />
            <RunTrigger trigger={r.trigger} />
            <AiTag />
            {r.is_demo && <DemoTag />}
          </span>
        }
      />
      <div className="grid gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-w-0 flex-col gap-[var(--gap)]">
          <RunProgress run={r} />
          {r.status !== "succeeded" && r.error && (
            <Notice tone={r.status === "failed" ? "crit" : "warn"} title={r.status === "failed" ? "The run failed" : "The run was stopped"}>
              {r.error}
            </Notice>
          )}
          {(r.output || r.status === "succeeded") && <AiAnswer run={r} />}
          {r.proposals.length > 0 && (
            <Panel title="Proposed for approval">
              <ul className="flex flex-col gap-2">
                {r.proposals.map((p) => (
                  <li key={p.id} className="flex flex-wrap items-baseline gap-x-3">
                    <Link href={`/changes/${p.id}`} className="font-mono text-xs font-bold text-accent hover:underline">{p.ref}</Link>
                    <span className="font-semibold">{p.title}</span>
                    <RiskLabel risk={p.risk} />
                  </li>
                ))}
              </ul>
              <p className="mt-3 max-w-[70ch] text-sm text-ink-3">
                {r.proposals.some((p) => p.apply_on_approval)
                  ? "The agent only proposed these, at “act after approval”. Each waits in the Change Approval queue; the person who approves one also applies it, in their name, after the usual re-read. The agent can do neither."
                  : "The agent only proposed these. Each waits in the Change Approval queue; nothing is sent to the controller until a person approves and applies it."}
              </p>
            </Panel>
          )}
          <Panel title={`Tool calls (${r.tool_calls?.length ?? 0})`} bodyClassName="p-0">
            {r.tool_calls?.length ? (
              <ol aria-label="Tool calls made by the agent">
                <li aria-hidden className="hidden h-9 grid-cols-[3.5rem_minmax(0,16rem)_minmax(0,1fr)] items-center border-b border-rule-strong px-[var(--row-pad)] text-xs font-bold tracking-wide text-ink-2 uppercase md:grid">
                  <span>Turn</span>
                  <span>Tool</span>
                  <span>Result</span>
                </li>
                {r.tool_calls.map((c, i) => (
                  <li key={i} className="grid gap-x-3 gap-y-1 border-b border-rule px-[var(--row-pad)] py-2 last:border-b-0 md:grid-cols-[3.5rem_minmax(0,16rem)_minmax(0,1fr)]">
                    <span className="tabular text-sm text-ink-3"><span className="md:sr-only">Turn </span>{c.turn}</span>
                    <span className="min-w-0">
                      <code className="font-mono text-xs font-bold">{c.name}</code>
                      <span className="block truncate font-mono text-xs text-ink-3" title={JSON.stringify(c.arguments)}>{JSON.stringify(c.arguments)}</span>
                    </span>
                    <span className="min-w-0">
                      {c.ok ? <span className="text-ink">{c.summary}</span> : <StateLabel tone="crit" className="font-normal">{c.summary ?? "Refused"}</StateLabel>}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="p-4 text-ink-3">The agent called no tools.</p>
            )}
          </Panel>
          <Panel title="Transcript">
            <p className="mb-4 max-w-[70ch] text-sm text-ink-3">Everything sent to and received from the model, in order. Tool results are shown exactly as the model received them.</p>
            <Transcript items={r.transcript ?? []} />
          </Panel>
        </div>
        <div className="flex flex-col gap-[var(--gap)]">
          <Panel title="Record">
            <KeyValue
              items={[
                ["Asked by", r.requested_by],
                ["Started from", TRIGGER_WORD[r.trigger]],
                ["Autonomy", AUTONOMY_WORD[r.autonomy]],
                ["Provider", r.provider_name || "—"],
                ["Model", <span key="m" className="font-mono text-xs">{r.model}</span>],
                ...subject.map(([k, v]) => [
                  k.replace(/_id$/, "").replace(/^./, (s) => s.toUpperCase()),
                  SUBJECT_HREF[k] ? <Link key={k} href={SUBJECT_HREF[k](v)} className="font-mono text-xs text-accent hover:underline">{v.slice(0, 8)}</Link> : <span key={k} className="font-mono text-xs">{v}</span>,
                ] as [string, React.ReactNode]),
                ["Started", dateTime(r.started_at)],
                ["Took", took == null ? "Still running" : took < 60 ? `${took.toFixed(1)} s` : duration(Math.round(took))],
                ["Tokens", <span key="t" className="tabular">{r.input_tokens.toLocaleString()} in · {r.output_tokens.toLocaleString()} out</span>],
                ["Cost", <span key="c" className="tabular">{money(r.cost_usd)}</span>],
              ]}
            />
            <p className="mt-3 text-sm text-ink-3">The start and finish of this run are in the audit trail.</p>
          </Panel>
        </div>
      </div>
    </>
  );
}
