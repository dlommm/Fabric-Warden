"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { ProposeChange, useCanPropose } from "@/components/changes";
import { FindingsPlate } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, ZonePatch } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Finding, FwMatrixCell, FwPolicy, FwSite } from "@/lib/types";

interface Overview {
  sites: FwSite[];
  findings: (Omit<Finding, "refs"> & { policy_id?: string })[];
  notes: string[];
}

/** Actions are not health, so they never borrow the state colours: block is an ink plate, allow an outlined one. */
function Action({ a }: { a: string | null }) {
  if (!a) return <span className="inline-flex h-6 items-center rounded-[3px] border border-dashed border-ink-3 px-2 text-xs font-semibold text-ink-3">Default</span>;
  const block = a === "BLOCK" || a === "REJECT";
  return (
    <span className={clsx("inline-flex h-6 items-center rounded-[3px] px-2 text-xs font-bold uppercase tracking-wide", block ? "sign bg-ink text-surface" : "border-2 border-ink bg-surface text-ink")}>
      {a.toLowerCase()}
    </span>
  );
}

function Analysis({ p }: { p: FwPolicy }) {
  if (p.relation === "shadowed") return <StateLabel tone="warn">{`Never matches · behind “${p.covered_by}”`}</StateLabel>;
  if (p.relation === "redundant") return <StateLabel tone="info">{`Redundant · “${p.covered_by}” covers it`}</StateLabel>;
  if (p.analysis === "not_determined") return <span className="text-sm text-ink-3" title={`Overlaps ${p.overlaps.join(", ")} in a way the filters cannot settle`}>Overlap not determined</span>;
  if (p.analysis === "unordered") return <span className="text-sm text-ink-3">Position unknown</span>;
  return <span className="text-sm text-ink-3">—</span>;
}

function Matrix({ site }: { site: FwSite }) {
  const zones = site.zones.filter((z) => site.matrix.some((c) => c.source === z.name || c.destination === z.name));
  const cell = (s: string, d: string): FwMatrixCell | undefined => site.matrix.find((c) => c.source === s && c.destination === d);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] table-fixed border-separate border-spacing-1 text-left">
        <caption className="sr-only">First match-all policy per zone pair, source in rows, destination in columns</caption>
        <thead>
          <tr>
            <th scope="col" className="w-28 px-2 text-xs font-bold tracking-wide text-ink-2 uppercase">From ↓ To →</th>
            {zones.map((z) => (
              <th key={z.id} scope="col" className="px-1"><ZonePatch name={z.name} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {zones.map((src) => (
            <tr key={src.id}>
              <th scope="row" className="pr-2"><ZonePatch name={src.name} /></th>
              {zones.map((dst) => {
                const c = cell(src.name, dst.name);
                return (
                  <td key={dst.id} className={clsx("h-11 rounded-[3px] px-2 align-middle", c ? "border border-rule" : "bg-surface-2")}>
                    {c ? (
                      <span className="flex flex-col items-start gap-0.5" title={c.policy ? `First match-all policy: ${c.policy.name}` : "No match-all policy; the default applies"}>
                        <Action a={c.action} />
                        {(c.filtered_before > 0 || c.system_policies > 0) && (
                          <span className="text-[11px] text-ink-3">{[c.filtered_before ? `${c.filtered_before} filtered first` : null, c.system_policies ? "+ system" : null].filter(Boolean).join(" ")}</span>
                        )}
                      </span>
                    ) : (
                      <span className="sr-only">No match-all policy; the controller default applies</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const columns = (system = false, propose = false): Column<FwPolicy>[] => [
  { key: "idx", header: "#", align: "right", className: "w-20", sort: (p) => p.index, cell: (p) => <span className="font-mono text-xs text-ink-2">{p.index ?? "—"}</span> },
  {
    key: "name",
    header: "Policy",
    sort: (p) => p.name.toLowerCase(),
    cell: (p) => (
      <span className="flex flex-col">
        <span className="font-bold">{p.name}</span>
        <span className="text-sm text-ink-3">{p.filter_summary}{p.ip_scope && p.ip_scope !== "IPV4_AND_IPV6" ? ` · ${p.ip_scope.replace("_", " ")}` : ""}</span>
        {/* Phones: zones, action and analysis fold under the name. */}
        <span className="mt-1 flex flex-wrap items-center gap-1.5 md:hidden">
          <ZonePatch name={p.source_zone} />
          <ArrowRight className="size-3.5 text-ink-3" aria-label="to" />
          <ZonePatch name={p.destination_zone} />
          <Action a={p.action} />
        </span>
        <span className="mt-1 md:hidden"><Analysis p={p} /></span>
        <span className="mt-1 inline-flex items-center gap-2 text-sm md:hidden">
          <span className="text-ink-3">Logs</span> {p.logging ? "Yes" : "No"}
          {propose && <LoggingChange p={p} />}
        </span>
      </span>
    ),
  },
  {
    key: "zones",
    header: "Zones",
    className: "hidden w-64 md:table-cell",
    cell: (p) => (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
        <ZonePatch name={p.source_zone} />
        <ArrowRight className="size-3.5 text-ink-3" aria-label="to" />
        <ZonePatch name={p.destination_zone} />
      </span>
    ),
  },
  { key: "action", header: "Action", className: "hidden w-28 md:table-cell", sort: (p) => p.action, cell: (p) => <Action a={p.action} /> },
  {
    key: "log",
    header: "Logs",
    className: clsx("hidden md:table-cell", propose ? "w-40" : "w-20"),
    cell: (p) => (
      <span className="inline-flex items-center gap-2 whitespace-nowrap">
        {p.logging ? "Yes" : <span className={clsx(p.action !== "ALLOW" && !system ? "font-semibold text-warn-text" : "text-ink-3")}>No</span>}
        {propose && <LoggingChange p={p} />}
      </span>
    ),
  },
  { key: "analysis", header: "Analysis", className: "hidden w-[22rem] md:table-cell", cell: (p) => <Analysis p={p} /> },
];

export default function FirewallPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("firewall");
  const q = useQuery({ queryKey: ["firewall"], queryFn: () => get<Overview>("/api/firewall"), enabled: on, refetchInterval: 60_000 });
  const canPropose = useCanPropose();
  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Firewall" name="Firewall & Policy Manager" canManage={can("modules:manage")}>
        See zone policies in evaluation order, which ones never match, and what each zone pair gets.
      </ModuleOff>
    );
  }
  const d = q.data;
  const demo = d?.sites.some((s) => s.site.is_demo);
  return (
    <>
      <PageHeader
        title="Firewall"
        description="Zone policies in the order the gateway evaluates them, checked for policies that can never match. Nothing here reorders or edits a policy; logging can be switched through a reviewed change."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Simulated policies</span> : undefined}
      />
      {q.error && <Notice tone="crit" className="mb-4">{(q.error as Error).message}</Notice>}
      {!d ? (
        <Skeleton className="h-96" />
      ) : d.sites.length === 0 ? (
        <Panel><EmptyState icon={<ShieldCheck />} title="No policies yet">Policies appear after a controller running UniFi Network 10.1 or later is synced.</EmptyState></Panel>
      ) : (
        <div className="flex flex-col gap-[var(--gap)]">
          <FindingsPlate
            label="Firewall findings"
            findings={d.findings.map((f) => ({ ...f, refs: {} }))}
            emptyText="No shadowed, redundant or permissive policies."
            heading={(n) => `${n} polic${n > 1 ? "ies need" : "y needs"} attention`}
          />
          {d.sites.map((s) => (
            <div key={s.site.id} className="flex flex-col gap-[var(--gap)]">
              {d.sites.length > 1 && <h2 className="text-lg font-bold">{s.site.name}</h2>}
              <Panel title="Zone matrix" actions={<span className="hidden text-sm text-ink-3 md:inline">First match-all policy per zone pair · shaded: none, the controller default applies</span>}>
                <Matrix site={s} />
              </Panel>
              <Panel title={`Policies in order (${s.policies.length})`} bodyClassName="p-0">
                <DataTable folds caption="Policies in evaluation order" rows={s.policies} columns={columns(false, canPropose)} initialSort={{ key: "idx", dir: "asc" }} rowClassName={(p) => (p.relation === "shadowed" ? "bg-warn-soft" : undefined)} />
              </Panel>
              {s.system_policies.length > 0 && (
                <Panel title={`System policies (${s.system_policies.length})`} bodyClassName="p-0">
                  <p className="border-b border-rule px-4 py-2 text-sm text-ink-3">Defined by UniFi. The API gives them no position, so they are left out of ordering analysis.</p>
                  <DataTable folds caption="System policies" rows={s.system_policies} columns={columns(true, canPropose)} />
                </Panel>
              )}
              {s.disabled.length > 0 && (
                <Panel title={`Disabled (${s.disabled.length})`} bodyClassName="p-0">
                  <DataTable folds caption="Disabled policies" rows={s.disabled} columns={columns(true, canPropose)} />
                </Panel>
              )}
            </div>
          ))}
          <ul className="flex flex-col gap-1 text-sm text-ink-3">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
    </>
  );
}

/** Propose switching a policy's logging; the change is reviewed and approved on its own page. */
function LoggingChange({ p }: { p: FwPolicy }) {
  const on = !p.logging;
  return (
    <ProposeChange
      operation="firewall_policy.set_logging"
      targetId={p.id}
      params={{ enabled: on }}
      title={`Propose: turn ${on ? "on" : "off"} logging for “${p.name}”`}
      summary={
        on
          ? "The gateway will write a syslog line each time this policy matches, so its traffic appears under Logs and Connections. Traffic itself is handled exactly as before."
          : "The gateway stops logging this policy's matches. Connections analytics and SOC detections will no longer see them."
      }
      trigger={<Button size="sm" variant="ghost" aria-label={`Turn ${on ? "on" : "off"}… logging for ${p.name}`}>{on ? "Turn on…" : "Turn off…"}</Button>}
    />
  );
}
