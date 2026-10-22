"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareText } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AiAnswer, RunLink, RunProgress, RunState } from "@/components/ai";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AiAgentsResponse, AiRun } from "@/lib/types";

const EXAMPLES = [
  "Which devices are offline or need a firmware update?",
  "Which access points carry the most wireless clients?",
  "Which IP prefixes are close to full?",
  "Are any firewall policies shadowed?",
];

export default function AskPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("ai-agents");
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const agents = useQuery({ queryKey: ["ai", "agents"], queryFn: () => get<AiAgentsResponse>("/api/ai/agents"), enabled: on });
  const history = useQuery({
    queryKey: ["ai", "runs", "ask"],
    queryFn: () => get<{ items: AiRun[] }>("/api/ai/runs?agent=ask-network&limit=20"),
    enabled: on,
    refetchInterval: (q) => (q.state.data?.items.some((h) => h.status === "running") ? 2_000 : false),
  });
  const current = useQuery({
    queryKey: ["ai", "runs", runId],
    queryFn: () => get<AiRun>(`/api/ai/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1_500 : false),
  });
  const ask = useMutation({
    mutationFn: () => post<AiRun>("/api/ai/agents/ask-network/runs", { question: question.trim(), background: true }),
    onSuccess: (r) => {
      setRunId(r.id);
      qc.invalidateQueries({ queryKey: ["ai", "runs", "ask"] });
    },
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Ask the network" name="AI Agent Center" canManage={can("modules:manage")}>
        Ask a question in your own words and get an answer from Fabric Warden&apos;s own read-only data. It needs the AI Agent Center and an AI provider.
      </ModuleOff>
    );
  }
  const agent = agents.data?.items.find((a) => a.key === "ask-network");
  const stopped = !!agents.data?.kill_switch.engaged;
  const off = agent && (!agent.config.enabled || agent.config.autonomy === "off");
  const r = current.data;

  return (
    <>
      <PageHeader
        title="Ask the network"
        description="Ask in your own words. The answer comes only from Fabric Warden's read-only tools (inventory, alerts, Wi-Fi radios, IP plan, firewall, compliance, logs, security cases and changes) and only what you may see yourself. It cannot change anything."
      />
      <div className="flex flex-col gap-[var(--gap)]">
        {stopped && <Notice tone="crit" title="All agents are stopped">The AI kill switch is engaged. See the <Link href="/ai/agents" className="font-semibold text-accent hover:underline">Agent Center</Link>.</Notice>}
        {off && <Notice tone="warn" title="Ask the network is switched off">An AI manager can switch it on in the <Link href="/ai/agents" className="font-semibold text-accent hover:underline">Agent Center</Link>.</Notice>}
        <Panel>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (question.trim()) ask.mutate();
            }}
          >
            <Field label="Your question" hint="Ctrl+Enter to ask. Names, notes and log lines the tools return are treated as data, never as instructions.">
              {(p) => (
                <Textarea
                  {...p}
                  className="min-h-24 text-md"
                  maxLength={2000}
                  value={question}
                  placeholder="Which clients moved between access points today?"
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && question.trim()) {
                      e.preventDefault();
                      ask.mutate();
                    }
                  }}
                />
              )}
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" variant="primary" icon={<MessageSquareText />} loading={ask.isPending} disabled={!question.trim() || stopped || !!off || r?.status === "running"}>
                Ask
              </Button>
              <span className="text-sm text-ink-3">Or try:</span>
              {EXAMPLES.map((x) => (
                <button key={x} type="button" className="rounded-[3px] px-1 py-1 text-left text-sm font-semibold text-accent hover:underline focus-visible:outline-2 focus-visible:outline-offset-2" onClick={() => setQuestion(x)}>{x}</button>
              ))}
            </div>
            {ask.error && <Notice tone="crit">{ask.error instanceof ApiError ? ask.error.message : "The question could not be asked."}</Notice>}
          </form>
        </Panel>

        {r && (
          <section aria-label="Answer to your question" className="flex flex-col gap-[var(--gap)]">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h2 className="text-md font-bold">{r.question}</h2>
              <RunLink id={r.id} label={r.ref} />
              <RunState status={r.status} />
              {r.is_demo && <DemoTag />}
            </div>
            <RunProgress run={r} />
            {r.status !== "running" && r.status !== "succeeded" && r.error && <Notice tone={r.status === "failed" ? "crit" : "warn"}>{r.error}</Notice>}
            {r.status === "succeeded" && <AiAnswer run={r} />}
            {!!r.tool_calls?.length && (
              <Panel title="What it read" bodyClassName="p-0">
                <ol aria-label="Tools the agent called">
                  {r.tool_calls.map((c, i) => (
                    <li key={i} className="grid gap-x-3 gap-y-1 border-b border-rule px-4 py-2 last:border-b-0 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
                      <code className="font-mono text-xs font-bold">{c.name}</code>
                      {c.ok ? <span className="text-ink">{c.summary}</span> : <StateLabel tone="crit" className="font-normal">{c.summary ?? "Refused"}</StateLabel>}
                    </li>
                  ))}
                </ol>
                <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">
                  The exact results the model received are in the transcript of <RunLink id={r.id} label={r.ref} />.
                </p>
              </Panel>
            )}
          </section>
        )}

        <Panel title="Earlier questions" bodyClassName="p-0">
          {history.data?.items.length ? (
            <ul aria-label="Earlier questions">
              {history.data.items.map((h) => (
                <li key={h.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-4 py-2 last:border-b-0">
                  <button type="button" className="min-w-0 flex-1 text-left font-semibold text-accent hover:underline" onClick={() => setRunId(h.id)}>
                    {h.question}
                  </button>
                  <RunState status={h.status} />
                  <span className="text-sm text-ink-3">{h.requested_by}</span>
                  <time className="text-sm whitespace-nowrap text-ink-3" dateTime={h.started_at} title={dateTime(h.started_at)}>{ago(h.started_at)}</time>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={<MessageSquareText />} title="No questions yet">Every question is kept as a run, with the tools it read and its answer, in the Agent Center.</EmptyState>
          )}
        </Panel>
      </div>
    </>
  );
}
