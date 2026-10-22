"use client";

// Change engine vocabulary: change and risk labels, the propose dialog used by
// every entry point (firewall policy, device, PoE port), and the evidence timeline.
import { useMutation, useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import { type ReactNode, useState } from "react";
import { Button, buttonClass } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/layout-bits";
import { StateLabel, type Tone } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChangeEvidence, ChangeRequest, ChangeStatus } from "@/lib/types";

const STATUS: Record<ChangeStatus, [Tone, string]> = {
  proposed: ["info", "Waiting for approval"],
  approved: ["info", "Approved, not applied"],
  applying: ["warn", "Applying"],
  verifying: ["warn", "Verifying"],
  succeeded: ["ok", "Applied and verified"],
  failed: ["crit", "Failed"],
  reverted: ["warn", "Reverted"],
  conflict: ["warn", "Not sent: target changed"],
  rejected: ["muted", "Rejected"],
  cancelled: ["muted", "Cancelled"],
  expired: ["muted", "Expired"],
};

export function ChangeState({ status, className }: { status: ChangeStatus; className?: string }) {
  const [tone, word] = STATUS[status];
  return <StateLabel tone={tone} className={className}>{word}</StateLabel>;
}

/** Risk is not a state, so it is a word with weight, never a state colour on its own. High risk adds the triangle. */
export function RiskLabel({ risk, protectedPath }: { risk: ChangeRequest["risk"]; protectedPath?: boolean }) {
  if (risk === "high") return <StateLabel tone="warn">{protectedPath ? "High risk · protected path" : "High risk"}</StateLabel>;
  return <span className={clsx("font-semibold", risk === "medium" ? "text-ink" : "text-ink-3")}>{risk === "medium" ? "Medium risk" : "Low risk"}</span>;
}

interface OperationSupport {
  support: Record<string, Record<string, { supported: boolean; min_version: string | null }>>;
  sites: Record<string, string>;
}

/** Whether the controller behind a site offers an operation at its detected version. */
export function useOperationSupport() {
  const q = useQuery({ queryKey: ["changes", "operations"], queryFn: () => get<OperationSupport>("/api/changes/operations"), staleTime: 300_000 });
  return (operation: string, siteId: string | undefined) => {
    const controller = siteId ? q.data?.sites[siteId] : undefined;
    return controller ? q.data?.support[controller]?.[operation] : undefined;
  };
}

/** The open change for a target, if any (the engine allows one at a time). */
export function useOpenChange(targetId: string): ChangeRequest | undefined {
  const pending = useQuery({ queryKey: ["changes", "open"], queryFn: () => get<{ items: ChangeRequest[] }>("/api/changes?view=open") });
  return pending.data?.items.find((c) => c.target.id === targetId);
}

export function OpenChangeLink({ c }: { c: ChangeRequest }) {
  return (
    <Link href={`/changes/${c.id}`} className="inline-flex items-baseline gap-1.5 text-sm font-semibold whitespace-nowrap text-accent hover:underline" title={c.title}>
      <span className="font-mono text-xs">{c.ref}</span> {STATUS[c.status][1].toLowerCase()}
    </Link>
  );
}

export function useCanPropose() {
  const { can, moduleEnabled } = useSession();
  return moduleEnabled("change-engine") && can("changes:propose");
}

export interface ChangeChoice {
  operation: ChangeRequest["operation"];
  label: string;
  params?: Record<string, unknown>;
  /** A value the person supplies, sent as params.value. */
  value?: { label: string; initial?: string; options?: { value: string; label: string }[] };
}

/**
 * Propose a change. The dialog states what will be proposed and asks why; nothing is
 * written here. On success it opens the change, where it is reviewed, approved and applied.
 * With `choices`, the dialog first asks which change to propose.
 */
export function ProposeChange({
  operation,
  targetId,
  siteId,
  params = {},
  title,
  summary,
  trigger,
  choices,
}: {
  choices?: ChangeChoice[];
  operation: ChangeRequest["operation"];
  targetId: string;
  /** The target's site; when its controller lacks the operation, say so instead of offering it. */
  siteId?: string;
  params?: Record<string, unknown>;
  title: string;
  summary: ReactNode;
  trigger: ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // One open change per target: show that one instead of offering a proposal the engine would refuse.
  const existing = useOpenChange(targetId);
  const support = useOperationSupport()(choices?.[0]?.operation ?? operation, siteId);
  const [reason, setReason] = useState("");
  const [pick, setPick] = useState(0);
  const choice = choices?.[pick];
  const [value, setValue] = useState(choice?.value?.initial ?? "");
  const m = useMutation({
    mutationFn: () =>
      post<ChangeRequest>("/api/changes", {
        operation: choice?.operation ?? operation,
        target_id: targetId,
        params: choice ? { ...choice.params, ...(choice.value ? { value } : {}) } : params,
        reason: reason.trim(),
      }),
    onSuccess: (cr) => {
      setOpen(false);
      router.push(`/changes/${cr.id}`);
    },
  });
  if (support && !support.supported) {
    return <span className="text-sm text-ink-3" title="The controller's Network version does not offer this through the official API">Needs Network {support.min_version}</span>;
  }
  if (existing) return <OpenChangeLink c={existing} />;
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) m.reset(); }}>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(480px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-[6px] border border-rule-strong bg-surface p-5 shadow-[0_16px_48px_rgb(0_0_0/0.22)]">
          <Dialog.Title className="text-lg font-bold">{title}</Dialog.Title>
          <Dialog.Description asChild>
            <div className="mt-2 text-ink-2">
              {summary}
              <p className="mt-2 text-sm text-ink-3">Proposing reads the live state from the controller and works out the risk. Nothing is changed until the proposal is approved and applied.</p>
            </div>
          </Dialog.Description>
          <form
            className="mt-4 flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              m.mutate();
            }}
          >
            {choices && (
              <fieldset className="flex flex-col gap-1">
                <legend className="mb-1 text-sm font-semibold">Change</legend>
                {choices.map((c, i) => (
                  <label key={c.label} className="flex items-center gap-2">
                    <input type="radio" name="change-choice" className="size-4 accent-[var(--accent)]" checked={pick === i} onChange={() => { setPick(i); setValue(c.value?.initial ?? ""); }} />
                    {c.label}
                  </label>
                ))}
              </fieldset>
            )}
            {choice?.value && (
              <Field label={choice.value.label}>
                {(p) =>
                  choice.value?.options ? (
                    <Select {...p} value={value} onChange={(e) => setValue(e.target.value)}>
                      <option value="" disabled>Choose…</option>
                      {choice.value.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </Select>
                  ) : (
                    <Input {...p} value={value} maxLength={64} onChange={(e) => setValue(e.target.value)} />
                  )
                }
              </Field>
            )}
            <Field label="Why" hint="Recorded with the change and in the audit trail">
              {(p) => <Textarea {...p} className="min-h-20" maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What this fixes or enables" />}
            </Field>
            {m.error && <Notice tone="crit" title="Couldn't propose this change">{m.error instanceof ApiError ? m.error.message : "The request failed."}</Notice>}
            <div className="flex justify-end gap-2">
              <Dialog.Close className={buttonClass("secondary")}>Cancel</Dialog.Close>
              <Button type="submit" variant="primary" loading={m.isPending} disabled={!!choice?.value && !value.trim()}>Propose change</Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const KIND_WORD: Record<string, string> = {
  read: "Read",
  approve: "Approved",
  write: "Sent",
  verify: "Checked",
  revert: "Revert",
  succeeded: "Succeeded",
  failed: "Failed",
  reverted: "Reverted",
  conflict: "Conflict",
  rejected: "Rejected",
  cancelled: "Cancelled",
  expired: "Expired",
};

/** The change's evidence: every read, write and check, oldest first, on the case-timeline rule. */
export function EvidenceTimeline({ items }: { items: ChangeEvidence[] }) {
  return (
    <ol className="relative ml-1.5 border-l-2 border-rule-strong">
      {items.map((e, i) => {
        const bad = ["failed", "conflict"].includes(e.kind);
        const data = e.data && Object.keys(e.data).length ? e.data : null;
        return (
          <li key={i} className="relative pb-4 pl-5 last:pb-0">
            <span aria-hidden className={clsx("absolute top-1.5 -left-[5px] size-2 rounded-[1px]", bad ? "bg-crit" : e.kind === "succeeded" ? "bg-ok" : "bg-ink")} />
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="text-xs font-bold tracking-wide text-ink-2 uppercase">{KIND_WORD[e.kind] ?? e.kind}</span>
              <time className="font-mono text-xs text-ink-3" dateTime={e.at}>{dateTime(e.at)}</time>
            </div>
            <p className={clsx("mt-0.5", bad ? "font-semibold text-crit" : "text-ink")}>{e.text}</p>
            {data && (
              <details className="mt-1">
                <summary className="cursor-pointer text-sm font-semibold text-accent">Data</summary>
                <pre className="mt-1 max-h-60 overflow-auto rounded-[3px] bg-surface-2 p-2 font-mono text-xs text-ink-2">{JSON.stringify(data, null, 2)}</pre>
              </details>
            )}
          </li>
        );
      })}
    </ol>
  );
}
