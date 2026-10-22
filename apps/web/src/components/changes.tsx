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
import { Notice, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, StateLabel, type Tone, VlanPatch, ZonePatch } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { BatchStatus, ChangeBatch, ChangeEvidence, ChangeRequest, ChangeStatus, ImpactPreview } from "@/lib/types";

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
  batch: "Bulk",
  propose: "Proposed",
  start: "Started",
  item: "Item",
  stopped: "Stopped",
};

const BATCH_STATUS: Record<BatchStatus, [Tone, string]> = {
  proposed: ["info", "Waiting for approval"],
  approved: ["info", "Approved, not started"],
  running: ["warn", "Running"],
  succeeded: ["ok", "All items applied and verified"],
  stopped: ["crit", "Stopped"],
  rejected: ["muted", "Rejected"],
  cancelled: ["muted", "Cancelled"],
  expired: ["muted", "Expired"],
};

export function BatchState({ status, className }: { status: BatchStatus; className?: string }) {
  const [tone, word] = BATCH_STATUS[status];
  return <StateLabel tone={tone} className={className}>{word}</StateLabel>;
}

/** Operations offered over many targets, in the words used for picking one. */
export const MANY_TARGET_OPERATIONS: { id: ChangeRequest["operation"]; label: string; flag?: { key: "enabled" | "value"; on: string; off: string } }[] = [
  { id: "port.power_cycle", label: "Power-cycle PoE ports" },
  { id: "device.restart", label: "Restart devices" },
  { id: "firewall_policy.set_logging", label: "Firewall policy logging", flag: { key: "enabled", on: "Turn logging on", off: "Turn logging off" } },
  { id: "firewall_policy.set_enabled", label: "Enable or disable firewall policies", flag: { key: "enabled", on: "Enable", off: "Disable" } },
  { id: "network.set_internet_access", label: "Network internet access", flag: { key: "value", on: "Allow internet access", off: "Block internet access" } },
  { id: "network.set_isolation", label: "Network isolation", flag: { key: "value", on: "Turn isolation on", off: "Turn isolation off" } },
];

/** How far a batch has got, in words: "2 of 5 applied · 1 failed". */
export function batchProgress(b: ChangeBatch): string {
  const total = Object.values(b.counts).reduce((a, n) => a + (n ?? 0), 0);
  const parts = [`${b.counts.succeeded ?? 0} of ${total} applied`];
  const bad = (b.counts.failed ?? 0) + (b.counts.conflict ?? 0) + (b.counts.reverted ?? 0);
  if (bad) parts.push(`${bad} did not succeed`);
  if (b.counts.verifying || b.counts.applying) parts.push("one verifying");
  if (b.counts.cancelled && b.status === "stopped") parts.push(`${b.counts.cancelled} not applied`);
  return parts.join(" · ");
}

const RELATION_WORD: Record<ImpactPreview["devices"][number]["relation"], string> = {
  target: "The target",
  downstream: "Behind it",
  possibly: "Possibly",
};

/**
 * What a change (or a bulk operation) would touch, from the observed inventory graph:
 * devices on the uplink tree, clients, networks, zones and policies. Read-only; says what it cannot know.
 */
export function ImpactPanel({ url, queryKey }: { url: string; queryKey: unknown[] }) {
  const { moduleEnabled } = useSession();
  const on = moduleEnabled("digital-twin");
  const q = useQuery({ queryKey, queryFn: () => get<ImpactPreview>(url), enabled: on, staleTime: 30_000 });
  if (!on) return null;
  const imp = q.data;
  return (
    <Panel
      id="impact"
      title="What it touches"
      actions={imp?.traffic_change === false ? <StateLabel tone="muted">No traffic change</StateLabel> : undefined}
    >
      {q.error ? (
        <Notice tone="crit">{(q.error as Error).message}</Notice>
      ) : !imp ? (
        <Skeleton className="h-24" />
      ) : (
        <div className="flex flex-col gap-4">
          <ul className="flex flex-col gap-1">
            {imp.summary.map((s) => <li key={s} className="max-w-[70ch] font-semibold">{s}</li>)}
          </ul>
          {imp.notes.length > 0 && (
            <Notice tone="info">
              {imp.notes.map((n) => <p key={n} className="max-w-[70ch]">{n}</p>)}
            </Notice>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            {imp.devices.length > 0 && (
              <ImpactList title={`Devices (${imp.devices.length})`}>
                {imp.devices.map((d) => (
                  <li key={d.id} className="flex items-baseline justify-between gap-3 py-1">
                    <Link href={`/inventory/devices/${d.id}`} className="min-w-0 truncate font-semibold hover:underline" style={{ paddingLeft: `${Math.min(d.depth, 4) * 12}px` }}>
                      {d.name}
                    </Link>
                    {d.relation === "possibly" ? (
                      <Chip dashed>Possibly</Chip>
                    ) : (
                      <span className="text-sm whitespace-nowrap text-ink-3">{RELATION_WORD[d.relation]}{d.relation === "downstream" && d.via ? ` · via ${d.via}` : ""}</span>
                    )}
                  </li>
                ))}
              </ImpactList>
            )}
            {(imp.clients.count > 0 || imp.clients.possible > 0) && (
              <ImpactList
                title={`Clients (${imp.clients.count}${imp.clients.possible ? `, ${imp.clients.possible} possibly` : ""})`}
                footer={imp.clients.count + imp.clients.possible > imp.clients.sample.length ? `First ${imp.clients.sample.length} shown` : undefined}
              >
                {imp.clients.sample.map((c) => (
                  <li key={c.id} className="flex items-baseline justify-between gap-3 py-1">
                    <Link href={`/inventory/clients/${c.id}`} className="min-w-0 truncate hover:underline">
                      {c.name} {c.ip && <span className="font-mono text-xs text-ink-3">{c.ip}</span>}
                    </Link>
                    {c.certain ? <span className="text-sm whitespace-nowrap text-ink-3">{c.via}</span> : <Chip dashed>Possibly</Chip>}
                  </li>
                ))}
              </ImpactList>
            )}
            {imp.networks.length > 0 && (
              <ImpactList title={`Networks (${imp.networks.length})`}>
                {imp.networks.map((n) => (
                  <li key={n.id} className="flex items-center justify-between gap-3 py-1">
                    <span className="flex min-w-0 items-center gap-2">
                      {n.vlan_id != null && <VlanPatch id={n.vlan_id} />}
                      <span className="truncate font-semibold">{n.name}</span>
                    </span>
                    <span className="text-sm whitespace-nowrap text-ink-3">
                      {n.relation}
                      {n.clients != null ? ` · ${n.clients} client${n.clients === 1 ? "" : "s"}` : ""}
                    </span>
                  </li>
                ))}
              </ImpactList>
            )}
            {(imp.zones.length > 0 || imp.policies.length > 0) && (
              <ImpactList title={`Zones and policies (${imp.zones.length + imp.policies.length})`}>
                {imp.zones.map((z) => (
                  <li key={z.id} className="flex items-center justify-between gap-3 py-1">
                    <ZonePatch name={z.name} />
                    <span className="text-sm text-ink-3">{z.relation}</span>
                  </li>
                ))}
                {imp.policies.map((p) => (
                  <li key={p.id} className="flex items-baseline justify-between gap-3 py-1">
                    <span className="min-w-0 truncate">
                      <span className="font-semibold">{p.name}</span> <span className="text-sm text-ink-3">{p.action.toLowerCase()}{p.enabled ? "" : ", disabled"}</span>
                    </span>
                    <span className="text-sm whitespace-nowrap text-ink-3">{p.relation}</span>
                  </li>
                ))}
              </ImpactList>
            )}
          </div>
          <p className="text-sm text-ink-3">
            {imp.basis.text}
            {imp.basis.observed_at ? ` Last sync ${ago(imp.basis.observed_at)}.` : ""}
          </p>
        </div>
      )}
    </Panel>
  );
}

function ImpactList({ title, children, footer }: { title: string; children: ReactNode; footer?: string }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1 border-b border-rule pb-1 text-xs font-bold tracking-wide text-ink-2 uppercase">{title}</h3>
      <ul className="max-h-72 overflow-auto">{children}</ul>
      {footer && <p className="mt-1 text-sm text-ink-3">{footer}</p>}
    </div>
  );
}

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
