"use client";

// Phase 8 Agent Center panels: schedules (an agent run on an interval as the person who created it) and agent
// notes (memory). Agent-suggested notes wait for a person; until accepted no agent sees them.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, NotebookPen } from "lucide-react";
import { useState } from "react";
import { AiTag, RunLink, RunState, SubjectSelect } from "@/components/ai";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Notice, Panel } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AiAgent, AiNote, AiSchedule } from "@/lib/types";

const INTERVALS: [number, string][] = [
  [60, "Every hour"],
  [360, "Every 6 hours"],
  [720, "Every 12 hours"],
  [1440, "Every day"],
  [10080, "Every week"],
];

function every(min: number): string {
  return INTERVALS.find(([m]) => m === min)?.[1] ?? (min % 1440 === 0 ? `Every ${min / 1440} days` : min % 60 === 0 ? `Every ${min / 60} hours` : `Every ${min} minutes`);
}

function Outcome({ s }: { s: AiSchedule }) {
  if (!s.last_outcome) return <span className="text-ink-3">Not run yet</span>;
  return (
    <span className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-x-2">
        {s.last_outcome === "refused" ? <StateLabel tone="warn">Refused</StateLabel> : <RunState status={s.last_outcome} />}
        {s.last_run_id && s.last_outcome !== "refused" && <RunLink id={s.last_run_id} label="Open run" />}
      </span>
      {s.last_detail && <span className="max-w-[48ch] text-sm text-ink-3">{s.last_detail}</span>}
    </span>
  );
}

export function SchedulesPanel({ agents }: { agents: AiAgent[] }) {
  const { can } = useSession();
  const manage = can("ai:manage");
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const q = useQuery({ queryKey: ["ai", "schedules"], queryFn: () => get<{ items: AiSchedule[] }>("/api/ai/schedules"), refetchInterval: 60_000 });
  const done = () => qc.invalidateQueries({ queryKey: ["ai"] });
  const act = useMutation({
    mutationFn: ({ s, what }: { s: AiSchedule; what: "run" | "toggle" | "delete" }) =>
      what === "run" ? post(`/api/ai/schedules/${s.id}/run`) : what === "toggle" ? patch(`/api/ai/schedules/${s.id}`, { enabled: !s.enabled }) : del(`/api/ai/schedules/${s.id}`),
    onSuccess: done,
  });
  const items = q.data?.items ?? [];
  return (
    <Panel
      title={`Schedules${items.length ? ` (${items.length})` : ""}`}
      actions={manage && !adding ? <Button size="sm" onClick={() => setAdding(true)}>New schedule…</Button> : undefined}
      bodyClassName="p-0"
    >
      {adding && <ScheduleForm agents={agents} onDone={() => setAdding(false)} />}
      {q.error && <Notice tone="crit" className="m-4">{(q.error as Error).message}</Notice>}
      {items.length ? (
        <ul aria-label="Agent schedules">
          {items.map((s) => (
            <li key={s.id} className="grid gap-x-6 gap-y-2 border-b border-rule px-4 py-3 last:border-b-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,18rem)_auto] lg:items-start">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-bold">{s.name}</span>
                  {s.enabled ? <StateLabel tone="ok">On</StateLabel> : <StateLabel tone="muted">Paused</StateLabel>}
                </div>
                <p className="text-sm text-ink-2">
                  {s.agent_name} · {every(s.interval_minutes)} · at most {s.max_runs_per_day} a day · runs as <span className="font-semibold">{s.run_as}</span>
                </p>
                <p className="text-sm text-ink-3">
                  {s.last_run_at ? `Last ran ${ago(s.last_run_at)}` : "Runs at the next scheduler pass"}
                  {s.next_run_at && <> · next <time dateTime={s.next_run_at} title={dateTime(s.next_run_at)}>{dateTime(s.next_run_at)}</time></>}
                </p>
              </div>
              <Outcome s={s} />
              {manage && (
                <div className="flex flex-wrap gap-2 lg:justify-end">
                  <Button size="sm" loading={act.isPending && act.variables?.s.id === s.id && act.variables.what === "run"} onClick={() => act.mutate({ s, what: "run" })}>Run now</Button>
                  <Button size="sm" variant="ghost" onClick={() => act.mutate({ s, what: "toggle" })}>{s.enabled ? "Pause" : "Resume"}</Button>
                  <Button size="sm" variant="ghost" onClick={() => act.mutate({ s, what: "delete" })}>Delete</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        !adding && (
          <EmptyState icon={<CalendarClock />} title="No schedules">
            Run an agent on an interval, for example a nightly firewall review. A schedule runs as the person who created it, with the same budgets and kill switches as a run you start.
          </EmptyState>
        )
      )}
      {act.error && <p className="border-t border-rule px-4 py-2 text-sm font-semibold text-crit">{(act.error as Error).message}</p>}
      <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">
        Scheduled runs are capped per day as well as by each agent&apos;s token, tool-call and turn limits and the provider&apos;s monthly budget. A refused run is recorded here and in the audit trail.
      </p>
    </Panel>
  );
}

function ScheduleForm({ agents, onDone }: { agents: AiAgent[]; onDone: () => void }) {
  const qc = useQueryClient();
  const usable = agents.filter((a) => a.missing_modules.length === 0);
  const [key, setKey] = useState(usable[0]?.key ?? "");
  const agent = agents.find((a) => a.key === key);
  const [name, setName] = useState("");
  const [subject, setSubject] = useState<Record<string, string>>({});
  const [question, setQuestion] = useState("");
  const [intervalMin, setIntervalMin] = useState(1440);
  const [cap, setCap] = useState("4");
  const save = useMutation({
    mutationFn: () => post("/api/ai/schedules", { agent_key: key, name: name.trim() || agent?.name, subject, question: question.trim(), interval_minutes: intervalMin, max_runs_per_day: Number(cap) || 1 }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai", "schedules"] });
      onDone();
    },
  });
  const missing = !agent || agent.subject.some((s) => s.required && !subject[s.key]) || (agent.question_required && !question.trim());
  return (
    <form
      className="m-4 grid gap-4 rounded-[var(--radius)] border border-rule bg-surface-2 p-4 md:grid-cols-2 xl:grid-cols-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label="Agent">
        {(p) => (
          <Select {...p} value={key} onChange={(e) => { setKey(e.target.value); setSubject({}); }}>
            {usable.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Name" hint="Shown in this list and the audit trail">
        {(p) => <Input {...p} maxLength={128} value={name} placeholder={agent?.name} onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="How often">
        {(p) => (
          <Select {...p} value={intervalMin} onChange={(e) => setIntervalMin(Number(e.target.value))}>
            {INTERVALS.map(([m, l]) => <option key={m} value={m}>{l}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Runs per day, at most">
        {(p) => <Input {...p} inputMode="numeric" className="tabular" value={cap} onChange={(e) => setCap(e.target.value.replace(/[^0-9]/g, "").slice(0, 2))} />}
      </Field>
      {agent?.subject.map((s) => (
        <Field key={s.key} label={s.label} hint={s.required ? undefined : "Optional"}>
          {(p) => <SubjectSelect kind={s.kind} id={p.id} required={s.required} value={subject[s.key] ?? ""} onChange={(v) => setSubject({ ...subject, [s.key]: v })} />}
        </Field>
      ))}
      <Field label="Question" hint={agent?.question_required ? "Required for this agent" : "Optional; the agent's own question is used when empty"} className="md:col-span-2">
        {(p) => <Textarea {...p} className="min-h-16" maxLength={2000} value={question} placeholder={agent?.default_question} onChange={(e) => setQuestion(e.target.value)} />}
      </Field>
      {save.error && <Notice tone="crit" className="md:col-span-2 xl:col-span-4">{save.error instanceof ApiError ? save.error.message : "The schedule could not be saved."}</Notice>}
      <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-4">
        <Button type="submit" variant="primary" loading={save.isPending} disabled={missing}>Save schedule</Button>
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <span className="text-sm text-ink-3">It runs as you, with your permissions at the time of each run, at the agent&apos;s current autonomy.</span>
      </div>
    </form>
  );
}

export function NotesPanel({ agents }: { agents: AiAgent[] }) {
  const { can } = useSession();
  const manage = can("ai:manage");
  const qc = useQueryClient();
  const [agentKey, setAgentKey] = useState("*");
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["ai", "notes"], queryFn: () => get<{ items: AiNote[] }>("/api/ai/notes") });
  const name = (k: string) => (k === "*" ? "Every agent" : (agents.find((a) => a.key === k)?.name ?? k));
  const done = () => qc.invalidateQueries({ queryKey: ["ai", "notes"] });
  const add = useMutation({ mutationFn: () => post("/api/ai/notes", { agent_key: agentKey, text: text.trim() }), onSuccess: () => { setText(""); done(); } });
  const act = useMutation({
    mutationFn: ({ n, what }: { n: AiNote; what: "accept" | "reject" | "delete" }) => (what === "delete" ? del(`/api/ai/notes/${n.id}`) : post(`/api/ai/notes/${n.id}/decide`, { accept: what === "accept" })),
    onSuccess: done,
  });
  const items = q.data?.items ?? [];
  const pending = items.filter((n) => n.status === "pending");
  const active = items.filter((n) => n.status === "active");
  const row = (n: AiNote) => (
    <li key={n.id} className="grid gap-x-6 gap-y-2 border-b border-rule px-4 py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
      <div className="min-w-0">
        <p className="max-w-[70ch] text-ink">{n.text}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-3">
          <span>{name(n.agent_key)}</span>
          {n.source === "agent" ? (
            <>
              <AiTag />
              <span>suggested in {n.source_run_id ? <RunLink id={n.source_run_id} label="its run" /> : "a deleted run"} for {n.created_by}</span>
            </>
          ) : (
            <span>written by {n.created_by}</span>
          )}
          <span>{ago(n.created_at)}</span>
          {n.decided_by && <span>accepted by {n.decided_by}</span>}
        </p>
      </div>
      {manage && (
        <div className="flex flex-wrap gap-2 md:justify-end">
          {n.status === "pending" ? (
            <>
              <Button size="sm" onClick={() => act.mutate({ n, what: "accept" })}>Accept</Button>
              <Button size="sm" variant="ghost" onClick={() => act.mutate({ n, what: "reject" })}>Reject</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => act.mutate({ n, what: "delete" })}>Delete</Button>
          )}
        </div>
      )}
    </li>
  );
  return (
    <Panel title="Agent notes" bodyClassName="p-0">
      <p className="max-w-[70ch] px-4 pt-3 text-sm text-ink-2">
        Short background facts every run of an agent is given, such as what a zone is for. They are passed as data and never override an agent&apos;s rules. Notes an agent suggests wait here until someone with AI management rights accepts them; until then no agent sees them.
      </p>
      {pending.length > 0 && (
        <>
          <h3 className="mt-3 border-y border-rule bg-surface-2 px-4 py-1.5 text-sm font-semibold text-ink-3">Waiting for review ({pending.length})</h3>
          <ul aria-label="Notes waiting for review">{pending.map(row)}</ul>
        </>
      )}
      <h3 className="mt-3 border-y border-rule bg-surface-2 px-4 py-1.5 text-sm font-semibold text-ink-3">In use ({active.length})</h3>
      {active.length ? (
        <ul aria-label="Notes in use">{active.map(row)}</ul>
      ) : (
        <EmptyState icon={<NotebookPen />} title="No notes in use">Agents start every run with only their own instructions.</EmptyState>
      )}
      {manage && (
        <form
          className="grid gap-3 border-t border-rule p-4 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto] md:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <Field label="For">
            {(p) => (
              <Select {...p} value={agentKey} onChange={(e) => setAgentKey(e.target.value)}>
                <option value="*">Every agent</option>
                {agents.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Note" hint="At most 500 characters; never a password or key">
            {(p) => <Input {...p} maxLength={500} value={text} placeholder="The Lab zone is a test bench; its policies are deliberately open." onChange={(e) => setText(e.target.value)} />}
          </Field>
          <Button type="submit" loading={add.isPending} disabled={!text.trim()} className="md:mb-6">Add note</Button>
        </form>
      )}
      {(add.error || act.error) && <p className="px-4 pb-3 text-sm font-semibold text-crit">{((add.error || act.error) as Error).message}</p>}
    </Panel>
  );
}
