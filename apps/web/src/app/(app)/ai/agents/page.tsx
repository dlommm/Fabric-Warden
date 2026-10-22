"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, OctagonAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AUTONOMY_WORD, AutonomyPicker, money, RunLink, RunState, RunTrigger, SubjectSelect, tokens } from "@/components/ai";
import { NotesPanel, SchedulesPanel } from "@/components/ai-schedules";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, get, post, put } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AiAgent, AiAgentsResponse, AiProvider, AiRun } from "@/lib/types";

export default function AgentCenterPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("ai-agents");
  const manage = can("ai:manage");
  const qc = useQueryClient();
  const [confirmStop, setConfirmStop] = useState(false);
  const agents = useQuery({ queryKey: ["ai", "agents"], queryFn: () => get<AiAgentsResponse>("/api/ai/agents"), enabled: on });
  const providers = useQuery({ queryKey: ["ai", "providers"], queryFn: () => get<{ items: AiProvider[] }>("/api/ai/providers"), enabled: on });
  const runs = useQuery({ queryKey: ["ai", "runs"], queryFn: () => get<{ items: AiRun[]; total: number }>("/api/ai/runs?limit=100"), enabled: on, refetchInterval: 30_000 });
  const kill = useMutation({
    mutationFn: (engaged: boolean) => post("/api/ai/kill-switch", { engaged, reason: engaged ? "Stopped from the Agent Center" : "" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai"] }),
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Agent Center" name="AI Agent Center" canManage={can("modules:manage")}>
        Optional agents that explain alerts, slow clients, Wi-Fi, capacity, compliance, security cases, changes and firewall findings from Fabric Warden&apos;s own data. They need an AI provider.
      </ModuleOff>
    );
  }
  const ks = agents.data?.kill_switch;
  const usable = providers.data?.items.filter((p) => p.enabled) ?? [];

  const runColumns: Column<AiRun>[] = [
    { key: "ref", header: "Run", className: "whitespace-nowrap", sort: (r) => r.number, cell: (r) => <RunLink id={r.id} label={r.ref} /> },
    {
      key: "agent",
      header: "Agent",
      sort: (r) => r.agent_name,
      cell: (r) => (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-semibold">{r.agent_name}</span>
          <RunTrigger trigger={r.trigger} />
        </span>
      ),
    },
    { key: "status", header: "Result", sort: (r) => r.status, className: "whitespace-nowrap", cell: (r) => <RunState status={r.status} /> },
    {
      key: "prop",
      header: "Proposed",
      cell: (r) =>
        r.proposals.length ? (
          <span className="flex flex-wrap gap-x-2">
            {r.proposals.map((p) => <Link key={p.id} href={`/changes/${p.id}`} className="font-mono text-xs font-bold text-accent hover:underline">{p.ref}</Link>)}
          </span>
        ) : <span className="text-ink-3">—</span>,
    },
    { key: "by", header: "Asked by", sort: (r) => r.requested_by, cell: (r) => r.requested_by },
    { key: "model", header: "Model", cell: (r) => <span className="font-mono text-xs whitespace-nowrap">{r.model}</span> },
    { key: "tok", header: "Tokens", align: "right", sort: (r) => r.input_tokens + r.output_tokens, cell: (r) => tokens(r.input_tokens + r.output_tokens) },
    { key: "cost", header: "Cost", align: "right", sort: (r) => r.cost_usd, cell: (r) => money(r.cost_usd) },
    { key: "at", header: "Started", sort: (r) => r.started_at, cell: (r) => <span className="text-sm whitespace-nowrap text-ink-2" title={dateTime(r.started_at)}>{ago(r.started_at)}</span> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (r) => (r.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Agent Center"
        description="Agents that answer from Fabric Warden's own inventory, metrics, logs, IP plan, compliance and firewall data, using only the read-only tools listed for each and only what you may see yourself. At most they propose a change; people approve it, and only a person's approval can apply it."
        actions={
          ks && !ks.engaged && can("ai:use") && (
            <Button variant="danger" icon={<OctagonAlert />} onClick={() => setConfirmStop(true)}>Stop all agents</Button>
          )
        }
      />
      <div className="flex flex-col gap-[var(--gap)]">
        {(agents.error || kill.error) && <Notice tone="crit">{((agents.error || kill.error) as Error).message}</Notice>}
        {ks?.engaged && (
          <Notice tone="crit" title="All agents are stopped">
            <p>
              The kill switch was engaged by {ks.by ?? "someone"}{ks.at ? ` ${ago(ks.at)}` : ""}{ks.reason ? `: ${ks.reason}` : ""}. Running agents stop before their next model call.{" "}
              {!manage && "Someone with AI management rights can release it."}
            </p>
            {manage && <Button className="mt-3" loading={kill.isPending} onClick={() => kill.mutate(false)}>Let agents run again</Button>}
          </Notice>
        )}
        {providers.data && usable.length === 0 && (
          <Notice tone="info" title="No AI provider is set up" action={<Link href="/ai/providers" className="font-semibold text-accent hover:underline">Set up a provider</Link>}>
            Agents need a model: Ollama or LM Studio on your network, Anthropic Claude, or the offline demo model.
          </Notice>
        )}
        <Panel title="Agents" bodyClassName="p-0">
          {agents.data ? (
            <ul>
              {agents.data.items.map((a) => (
                <AgentRow key={a.key} a={a} providers={providers.data?.items ?? []} stopped={!!ks?.engaged} />
              ))}
            </ul>
          ) : (
            <p className="p-4 text-ink-3">Loading agents…</p>
          )}
          <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">
            Autonomy built so far: Off, Explain, Propose and Act after approval (only the firewall review can reach it, and only a person&apos;s approval applies its change). “Autonomous within guardrails” is planned and cannot be chosen.
          </p>
        </Panel>
        {agents.data && <SchedulesPanel agents={agents.data.items} />}
        {agents.data && <NotesPanel agents={agents.data.items} />}
        <Panel title={runs.data ? `Runs (${runs.data.total})` : "Runs"} bodyClassName="p-0">
          <DataTable
            caption="Agent runs"
            rows={runs.data?.items}
            loading={runs.isLoading}
            columns={runColumns}
            href={(r) => `/ai/runs/${r.id}`}
            empty={<EmptyState icon={<Bot />} title="No runs yet">Run an agent above. Every run keeps its instructions, tool calls and answer here.</EmptyState>}
          />
        </Panel>
      </div>
      <Confirm open={confirmStop} onOpenChange={setConfirmStop} title="Stop every agent?" confirmLabel="Stop all agents" onConfirm={() => kill.mutate(true)}>
        No agent can start, and running agents stop before their next model call. Only someone with AI management rights can let them run again.
      </Confirm>
    </>
  );
}

function AgentRow({ a, providers, stopped }: { a: AiAgent; providers: AiProvider[]; stopped: boolean }) {
  const { can } = useSession();
  const manage = can("ai:manage");
  const qc = useQueryClient();
  const [open, setOpen] = useState<"run" | "settings" | null>(null);
  const configure = useMutation({
    mutationFn: (body: Record<string, unknown>) => put(`/api/ai/agents/${a.key}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai", "agents"] }),
  });
  const c = a.config;
  const off = !c.enabled || c.autonomy === "off";
  const blocked = a.missing_modules.length > 0;
  const reads = a.tools.filter((t) => !t.proposes && !t.stores);
  return (
    <li className="border-b border-rule last:border-b-0">
      <div className="grid gap-4 px-4 py-4 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start lg:gap-8">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h3 className="text-md font-bold">{a.name}</h3>
            {off ? <StateLabel tone="muted">Switched off</StateLabel> : <StateLabel tone="ok">Available</StateLabel>}
            {a.last_run && (
              <span className="text-sm text-ink-3">
                Last run <RunLink id={a.last_run.id} label={a.last_run.ref} /> {ago(a.last_run.started_at)}
              </span>
            )}
          </div>
          <p className="mt-1 max-w-[70ch] text-ink-2">{a.summary}</p>
          <p className="mt-2 text-sm text-ink-3">
            Reads with{" "}
            {reads.map((t, i) => (
              <span key={t.name}>
                {i > 0 && ", "}
                <code className="font-mono text-xs text-ink-2" title={t.description}>{t.name}</code>
              </span>
            ))}
            {a.allowed_operations.length > 0 && (
              <>
                . At Propose or above it may also propose <code className="font-mono text-xs text-ink-2">{a.allowed_operations.join(", ")}</code>
              </>
            )}
            .
          </p>
          {blocked && <p className="mt-2 text-sm font-semibold text-warn-text">Needs the {a.missing_modules.join(", ")} module{a.missing_modules.length > 1 ? "s" : ""} to run.</p>}
        </div>
        <div className="flex flex-col gap-3">
          {manage ? (
            <div className="flex flex-col gap-1">
              <span id={`aut-${a.key}-l`} className="text-sm font-semibold">Autonomy</span>
              <AutonomyPicker id={`aut-${a.key}`} value={c.autonomy} max={a.max_autonomy} disabled={configure.isPending} onChange={(v) => configure.mutate({ autonomy: v })} />
            </div>
          ) : (
            <span className="text-sm text-ink-2">Autonomy: <span className="font-semibold text-ink">{AUTONOMY_WORD[c.autonomy]}</span></span>
          )}
          <div className="flex flex-wrap gap-2">
            <Button aria-expanded={open === "run"} disabled={off || stopped || blocked} onClick={() => setOpen(open === "run" ? null : "run")}>Run…</Button>
            {manage && (
              <>
                <Button variant="ghost" aria-expanded={open === "settings"} onClick={() => setOpen(open === "settings" ? null : "settings")}>Settings</Button>
                <Button variant="ghost" loading={configure.isPending && "enabled" in (configure.variables ?? {})} onClick={() => configure.mutate({ enabled: !c.enabled })}>
                  {c.enabled ? "Switch off" : "Switch on"}
                </Button>
              </>
            )}
          </div>
          {configure.error && <p className="text-sm font-semibold text-crit">{(configure.error as Error).message}</p>}
        </div>
      </div>
      {open === "run" && <RunForm a={a} onClose={() => setOpen(null)} />}
      {open === "settings" && <AgentSettings a={a} providers={providers} onSave={(b) => configure.mutateAsync(b).then(() => setOpen(null))} saving={configure.isPending} />}
    </li>
  );
}

function RunForm({ a, onClose }: { a: AiAgent; onClose: () => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [subject, setSubject] = useState<Record<string, string>>({});
  const [question, setQuestion] = useState("");
  const run = useMutation({
    mutationFn: () => post<AiRun>(`/api/ai/agents/${a.key}/runs`, { subject, question: question.trim(), background: true }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["ai"] });
      router.push(`/ai/runs/${r.id}`);
    },
  });
  const missing = a.subject.some((s) => s.required && !subject[s.key]) || (a.question_required && !question.trim());
  return (
    <form
      className="mx-4 mb-4 grid gap-4 rounded-[var(--radius)] border border-rule bg-surface-2 p-4 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]"
      onSubmit={(e) => {
        e.preventDefault();
        run.mutate();
      }}
    >
      {a.subject.map((s) => (
        <Field key={s.key} label={s.label} hint={s.required ? undefined : "Optional"}>
          {(p) => <SubjectSelect kind={s.kind} id={p.id} required={s.required} value={subject[s.key] ?? ""} onChange={(v) => setSubject({ ...subject, [s.key]: v })} />}
        </Field>
      ))}
      <Field label="Question" hint={a.question_required ? "Required: ask in your own words" : "Optional; the agent's own question is used when empty"}>
        {(p) => <Textarea {...p} className="min-h-16" maxLength={2000} value={question} placeholder={a.default_question} onChange={(e) => setQuestion(e.target.value)} />}
      </Field>
      {run.error && <Notice tone="crit" className="md:col-span-2">{run.error instanceof ApiError ? run.error.message : "The run could not start."}</Notice>}
      <div className="flex flex-wrap items-center gap-2 md:col-span-2">
        <Button type="submit" variant="primary" loading={run.isPending} disabled={missing}>Run agent</Button>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <span className="text-sm text-ink-3">
          {`Runs at “${AUTONOMY_WORD[a.config.autonomy]}”: ${
            a.config.autonomy === "act_with_approval"
              ? "it may propose a change; approving it also applies it."
              : a.config.autonomy === "propose"
                ? "it may propose a change for approval."
                : "it cannot change anything."
          } The run page follows it live.`}
        </span>
      </div>
    </form>
  );
}

function AgentSettings({ a, providers, onSave, saving }: { a: AiAgent; providers: AiProvider[]; onSave: (b: Record<string, unknown>) => Promise<unknown>; saving: boolean }) {
  const c = a.config;
  const [f, setF] = useState({ provider_id: c.provider_id ?? "", model: c.model, tok: String(c.max_tokens_per_run), calls: String(c.max_tool_calls), turns: String(c.max_turns) });
  const provider = providers.find((p) => p.id === f.provider_id);
  return (
    <form
      className="mx-4 mb-4 grid gap-4 rounded-[var(--radius)] border border-rule bg-surface-2 p-4 md:grid-cols-2 xl:grid-cols-5"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          ...(f.provider_id ? { provider_id: f.provider_id } : { clear_provider: true }),
          model: f.model,
          max_tokens_per_run: Number(f.tok),
          max_tool_calls: Number(f.calls),
          max_turns: Number(f.turns),
        });
      }}
    >
      <Field label="Provider" hint="Automatic picks the first enabled provider, real before demo">
        {(p) => (
          <Select {...p} value={f.provider_id} onChange={(e) => setF({ ...f, provider_id: e.target.value, model: "" })}>
            <option value="">Automatic</option>
            {providers.map((x) => <option key={x.id} value={x.id}>{x.name}{x.enabled ? "" : " (disabled)"}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Model" hint="Empty uses the provider's default">
        {(p) => (
          <Select {...p} value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} disabled={!provider}>
            <option value="">Provider default</option>
            {provider?.models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Tokens per run" hint="The run stops when reached">
        {(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.tok} onChange={(e) => setF({ ...f, tok: e.target.value.replace(/[^0-9]/g, "") })} />}
      </Field>
      <Field label="Tool calls per run">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.calls} onChange={(e) => setF({ ...f, calls: e.target.value.replace(/[^0-9]/g, "") })} />}</Field>
      <Field label="Model turns per run">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.turns} onChange={(e) => setF({ ...f, turns: e.target.value.replace(/[^0-9]/g, "") })} />}</Field>
      <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-5">
        <Button type="submit" variant="primary" loading={saving}>Save agent settings</Button>
        {c.updated_by && <span className="text-sm text-ink-3">Last changed by {c.updated_by}{c.updated_at ? ` ${ago(c.updated_at)}` : ""}</span>}
      </div>
    </form>
  );
}
