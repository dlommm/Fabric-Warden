"use client";

// AI vocabulary: the AI-generated plate, run states, the autonomy selector, the
// answer panel and the run transcript. AI output is never presented as a fact
// of the network: it is framed, labelled and followed by the evidence it used.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Bot, Square } from "lucide-react";
import Link from "next/link";
import { Markdown } from "@/components/markdown";
import { PctBar } from "@/components/servers";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { Chip, StateLabel, type Tone } from "@/components/ui/signs";
import { get, post } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AiAutonomy, AiProviderKind, AiRun, AiRunStatus, AiRunTrigger, AiSubjectKind, AiTranscriptEntry, AlertItem, ClientRow } from "@/lib/types";

export const PROVIDER_KIND: Record<AiProviderKind, string> = {
  anthropic: "Anthropic Claude",
  openai_compatible: "OpenAI-compatible",
  ollama: "Ollama",
  demo: "Offline demo model",
};

const RUN_STATE: Record<AiRunStatus, [Tone, string]> = {
  running: ["warn", "Running"],
  succeeded: ["ok", "Answered"],
  failed: ["crit", "Failed"],
  budget_exceeded: ["warn", "Stopped: budget reached"],
  stopped: ["warn", "Stopped"],
};

export function RunState({ status, className }: { status: AiRunStatus; className?: string }) {
  const [tone, word] = RUN_STATE[status];
  return <StateLabel tone={tone} className={className}>{word}</StateLabel>;
}

/** The AI plate: an outlined accent plate, never a fill, so it never reads as a state or as demo brown. */
export function AiTag({ className }: { className?: string }) {
  return (
    <span
      className={clsx("inline-flex h-5 shrink-0 items-center gap-1 rounded-[3px] border-2 border-accent bg-surface px-1.5 text-[11px] font-bold tracking-wide whitespace-nowrap text-accent uppercase", className)}
      title="Written by a language model from the tool results below. Check the evidence before acting on it."
    >
      <Bot className="size-3" aria-hidden strokeWidth={2.5} />
      AI-generated
    </span>
  );
}

export const AUTONOMY_WORD: Record<AiAutonomy, string> = { off: "Off", explain: "Explain", propose: "Propose", act_with_approval: "Act after approval" };

const AUTONOMY_HELP: Record<AiAutonomy, string> = {
  off: "The agent cannot run.",
  explain: "Reads Fabric Warden's data and answers. Has no tool that changes anything.",
  propose: "Can also put a change into the Change Approval queue. People approve and apply it.",
  act_with_approval: "Proposes like Propose; when a person approves one of its changes, that approval also applies it, in their name. The agent never approves or applies.",
};

export const TRIGGER_WORD: Record<AiRunTrigger, string> = { manual: "Run by a person", schedule: "Scheduled", ask: "Ask the network" };

/** How a run started, as a chip: the run's own words, never a state colour. */
export function RunTrigger({ trigger }: { trigger: AiRunTrigger }) {
  return trigger === "manual" ? null : <Chip>{TRIGGER_WORD[trigger]}</Chip>;
}

/** Built levels as a radio strip; levels above the agent's ceiling are disabled with the reason. */
export function AutonomyPicker({
  value,
  max,
  onChange,
  disabled,
  id,
}: {
  value: AiAutonomy;
  max: AiAutonomy;
  onChange: (v: AiAutonomy) => void;
  disabled?: boolean;
  id: string;
}) {
  const levels: AiAutonomy[] = ["off", "explain", "propose", "act_with_approval"];
  const allowed = levels.slice(0, levels.indexOf(max) + 1);
  return (
    <div className="flex flex-col gap-1.5">
      <div role="radiogroup" aria-labelledby={`${id}-l`} aria-describedby={`${id}-d`} className="inline-flex flex-wrap self-start rounded-[var(--radius)] border border-rule-strong bg-surface p-0.5">
        {levels.map((l) => {
          const on = l === value;
          const ok = allowed.includes(l) && !disabled;
          return (
            <button
              key={l}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={!ok}
              title={!allowed.includes(l) ? `This agent can be at most “${AUTONOMY_WORD[max]}”` : undefined}
              onClick={() => onChange(l)}
              onKeyDown={(e) => {
                const i = allowed.indexOf(value);
                const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (d && i >= 0) {
                  e.preventDefault();
                  const n = allowed[(i + d + allowed.length) % allowed.length];
                  onChange(n);
                  (e.currentTarget.parentElement?.children[levels.indexOf(n)] as HTMLElement | undefined)?.focus();
                }
              }}
              tabIndex={on ? 0 : -1}
              className={clsx(
                "h-8 rounded-[3px] px-3 text-sm font-semibold transition-colors duration-150 disabled:cursor-not-allowed",
                on ? "sign bg-accent text-accent-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink disabled:text-ink-3 disabled:hover:bg-transparent",
              )}
            >
              {AUTONOMY_WORD[l]}
            </button>
          );
        })}
      </div>
      <p id={`${id}-d`} className="max-w-[48ch] text-sm text-ink-3">
        {AUTONOMY_HELP[value]}
      </p>
    </div>
  );
}

export function money(v: number): string {
  if (v === 0) return "$0";
  return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

export function tokens(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 10_000 ? `${Math.round(n / 1000)}k` : n.toLocaleString();
}

/** This month's spend against a limit, in the usage-bar language; without a limit, the figure alone. */
export function BudgetLine({ used, limit, format }: { used: number; limit: number | null; format: (n: number) => string }) {
  if (limit == null) return <span className="tabular text-ink-2">{format(used)} <span className="text-ink-3">· no limit</span></span>;
  const pct = limit > 0 ? (100 * used) / limit : 100;
  return <PctBar size="sm" pct={pct} label={`${format(used)} of ${format(limit)}`} />;
}

/** The model's answer, framed and labelled as AI-generated, with what produced it. */
export function AiAnswer({ run }: { run: AiRun }) {
  return (
    <section aria-labelledby={`ans-${run.id}`} className="rounded-[var(--radius)] border border-accent/40 bg-surface">
      <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b border-accent/30 bg-accent-soft px-4 py-2">
        <h2 id={`ans-${run.id}`} className="text-md font-bold">Answer</h2>
        <AiTag />
        <span className="text-sm text-ink-2">
          <span className="font-mono text-xs">{run.model}</span> via {run.provider_name}
        </span>
      </div>
      <div className="p-4">
        {run.output ? (
          <div className="max-w-[72ch]">
            <Markdown body={run.output} refs={[]} />
          </div>
        ) : (
          <p className="text-ink-3">No answer: {run.error || "the run ended before the model answered."}</p>
        )}
        <p className="mt-4 border-t border-rule pt-3 text-sm text-ink-3">
          Written by a language model from the tool results listed below. It can be wrong; the tool results are the evidence.
        </p>
      </div>
    </section>
  );
}

const ENTRY_WORD: Record<AiTranscriptEntry["kind"], string> = {
  system: "System prompt",
  user: "Request",
  model: "Model",
  tool: "Tool",
  finish: "Finished",
};

function pretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/** Everything sent to and received from the model, in order: the run's evidence. */
export function Transcript({ items }: { items: AiTranscriptEntry[] }) {
  return (
    <ol className="relative ml-1.5 border-l-2 border-rule-strong">
      {items.map((e, i) => {
        const bad = (e.kind === "tool" && e.ok === false) || (e.kind === "finish" && e.status !== "succeeded");
        return (
          <li key={i} className="relative pb-4 pl-5 last:pb-0">
            <span aria-hidden className={clsx("absolute top-1.5 -left-[5px] size-2 rounded-[1px]", bad ? "bg-crit" : e.kind === "finish" ? "bg-ok" : e.kind === "model" ? "bg-accent" : "bg-ink")} />
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="text-xs font-bold tracking-wide text-ink-2 uppercase">
                {ENTRY_WORD[e.kind]}
                {e.turn ? ` · turn ${e.turn}` : ""}
              </span>
              <time className="font-mono text-xs text-ink-3" dateTime={e.at}>{dateTime(e.at)}</time>
              {e.kind === "model" && <span className="tabular text-xs text-ink-3">{e.input_tokens} in · {e.output_tokens} out</span>}
            </div>
            {e.kind === "system" && (
              <details className="mt-1">
                <summary className="cursor-pointer text-sm font-semibold text-accent">Show the instructions the agent was given</summary>
                <pre className="mt-1 max-h-72 overflow-auto rounded-[3px] bg-surface-2 p-2 font-mono text-xs whitespace-pre-wrap text-ink-2">{e.text}</pre>
              </details>
            )}
            {e.kind === "user" && <p className="mt-0.5 font-mono text-xs whitespace-pre-wrap text-ink">{e.text}</p>}
            {e.kind === "model" && (
              <div className="mt-0.5">
                {e.text && <p className="max-w-[72ch] whitespace-pre-wrap text-ink-2">{e.text}</p>}
                {!!e.tool_calls?.length && (
                  <p className="text-ink">
                    Asked for{" "}
                    {e.tool_calls.map((c, j) => (
                      <span key={c.id}>
                        {j > 0 && ", "}
                        <code className="font-mono text-xs font-bold">{c.name}</code>
                      </span>
                    ))}
                  </p>
                )}
              </div>
            )}
            {e.kind === "tool" && (
              <div className="mt-0.5">
                <p className={clsx(bad ? "font-semibold text-crit" : "text-ink")}>
                  <code className="font-mono text-xs font-bold">{e.name}</code>
                  <span className="font-mono text-xs break-all text-ink-3"> {JSON.stringify(e.arguments ?? {})}</span>
                  {bad ? " · refused or failed" : ""}
                </p>
                <details className="mt-1">
                  <summary className="cursor-pointer text-sm font-semibold text-accent">Result returned to the model</summary>
                  <pre className="mt-1 max-h-72 overflow-auto rounded-[3px] bg-surface-2 p-2 font-mono text-xs text-ink-2">{pretty(e.result ?? "")}</pre>
                </details>
              </div>
            )}
            {e.kind === "finish" && <p className={clsx("mt-0.5", bad ? "font-semibold text-crit" : "text-ink")}>{e.error || "The model gave its final answer."}</p>}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * While a run is in progress: what it is doing now, read from its growing transcript, and a stop button for the person
 * who started it or an AI manager. The page polls the run; the stop takes effect before the next model call.
 */
export function RunProgress({ run }: { run: AiRun }) {
  const { can, me } = useSession();
  const qc = useQueryClient();
  const stop = useMutation({
    mutationFn: () => post<AiRun>(`/api/ai/runs/${run.id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai"] }),
  });
  if (run.status !== "running") return null;
  const t = run.transcript ?? [];
  const last = t[t.length - 1];
  const turns = t.filter((e) => e.kind === "model").length;
  const calls = t.filter((e) => e.kind === "tool").length;
  const doing =
    run.cancel_requested || stop.isSuccess
      ? "Stopping before the next model call…"
      : !last || last.kind === "user" || last.kind === "system"
        ? "Waiting for the model's first turn…"
        : last.kind === "tool"
          ? `Read ${last.name}; waiting for the model…`
          : `Model turn ${turns}: calling ${last.tool_calls?.map((c) => c.name).join(", ") || "tools"}…`;
  const mayStop = run.requested_by === me.username || can("ai:manage");
  return (
    <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[var(--radius)] border border-rule bg-surface px-4 py-3">
      <span aria-hidden className="size-2 animate-pulse rounded-[1px] bg-accent motion-reduce:animate-none" />
      <span className="font-semibold">{doing}</span>
      <span className="tabular text-sm text-ink-3">
        {turns} model turn{turns === 1 ? "" : "s"} · {calls} tool call{calls === 1 ? "" : "s"} · {(run.input_tokens + run.output_tokens).toLocaleString()} tokens
      </span>
      {mayStop && (
        <Button size="sm" variant="danger" className="ml-auto" icon={<Square />} loading={stop.isPending} disabled={run.cancel_requested || stop.isSuccess} onClick={() => stop.mutate()}>
          Stop this run
        </Button>
      )}
      {stop.error && <p className="w-full text-sm font-semibold text-crit">{(stop.error as Error).message}</p>}
    </div>
  );
}

/** Subject picker for an agent's subject field; each kind lists what Fabric Warden already knows. */
export function SubjectSelect({ kind, value, onChange, id, required }: { kind: AiSubjectKind; value: string; onChange: (v: string) => void; id: string; required: boolean }) {
  const alerts = useQuery({ queryKey: ["monitoring", "alerts"], queryFn: () => get<{ items: AlertItem[] }>("/api/monitoring/alerts"), enabled: kind === "alert" });
  const clients = useQuery({ queryKey: ["inventory", "clients"], queryFn: () => get<{ items: ClientRow[] }>("/api/inventory/clients"), enabled: kind === "client" });
  const named = useQuery({
    queryKey: ["ai", "subjects", kind],
    queryFn: () => get<{ items: { id: string; name: string; is_demo: boolean }[] }>(`/api/ai/subjects/${kind === "case" ? "cases" : kind === "change" ? "changes" : "sites"}`),
    enabled: kind === "site" || kind === "case" || kind === "change",
  });
  const options: { id: string; label: string }[] =
    kind === "alert"
      ? (alerts.data?.items ?? []).map((x) => ({ id: x.id, label: `${x.rule ?? "Alert"} · ${x.label} (${x.state})` }))
      : kind === "client"
        ? (clients.data?.items ?? []).map((x) => ({ id: x.id, label: `${x.alias || x.name}${x.ip_address ? ` · ${x.ip_address}` : ""}` }))
        : (named.data?.items ?? []).map((x) => ({ id: x.id, label: x.name }));
  const loading = alerts.isLoading || clients.isLoading || named.isLoading;
  const none = kind === "alert" ? "No active alerts" : kind === "case" ? "No security cases" : kind === "change" ? "No changes" : "Nothing to choose";
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={loading}>
      <option value="">{loading ? "Loading…" : required ? (options.length ? "Choose…" : none) : "All sites"}</option>
      {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </Select>
  );
}

export function RunLink({ id, label }: { id: string; label: string }) {
  return (
    <Link href={`/ai/runs/${id}`} className="font-mono text-xs font-bold text-accent hover:underline">
      {label}
    </Link>
  );
}
