"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Workflow } from "lucide-react";
import Link from "next/link";
import { ChangeRefLink, RunOutcome, actionText, triggerText } from "@/components/automation";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AutomationMeta, AutomationRule, AutomationRun } from "@/lib/types";

export default function AutomationPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("automation");
  const rules = useQuery({ queryKey: ["automation", "rules"], queryFn: () => get<{ items: AutomationRule[] }>("/api/automation/rules"), enabled: on, refetchInterval: 30_000 });
  const runs = useQuery({ queryKey: ["automation", "runs"], queryFn: () => get<{ items: AutomationRun[] }>("/api/automation/runs?limit=50"), enabled: on, refetchInterval: 30_000 });
  const meta = useQuery({ queryKey: ["automation", "meta"], queryFn: () => get<AutomationMeta>("/api/automation/meta"), enabled: on, staleTime: 600_000 });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Automation" name="Automation Studio" canManage={can("modules:manage")}>
        Rules that react to events or run on a schedule. A rule can propose a change, which still waits for a person to approve and apply it, or send a notification.
      </ModuleOff>
    );
  }
  const names = new Map((rules.data?.items ?? []).map((r) => [r.id, r.name]));
  const ruleColumns: Column<AutomationRule>[] = [
    {
      key: "name",
      header: "Rule",
      sort: (r) => r.name,
      cell: (r) => (
        <span className="flex min-w-0 flex-col">
          <Link href={`/automation/${r.id}`} className="truncate font-bold hover:underline">{r.name}</Link>
          <span className="truncate text-sm text-ink-3">{triggerText(r, meta.data)}</span>
        </span>
      ),
    },
    { key: "action", header: "Then", cell: (r) => <span className="text-sm text-ink-2">{actionText(r)}</span> },
    { key: "state", header: "State", sort: (r) => (r.enabled ? 0 : 1), className: "whitespace-nowrap", cell: (r) => (r.enabled ? <StateLabel tone="ok">On</StateLabel> : <StateLabel tone="muted">Off</StateLabel>) },
    {
      key: "runs",
      header: "Runs",
      align: "right",
      sort: (r) => Object.values(r.stats).reduce((a, n) => a + (n ?? 0), 0),
      cell: (r) => (
        <span className="tabular text-sm text-ink-2">
          {(r.stats.proposed ?? 0) + (r.stats.notified ?? 0)} acted · {(r.stats.skipped ?? 0) + (r.stats.refused ?? 0) + (r.stats.error ?? 0)} not
        </span>
      ),
    },
    { key: "last", header: "Last fired", align: "right", sort: (r) => r.last_run_at ?? "", cell: (r) => <span className="tabular text-sm text-ink-3" title={r.last_run_at ? dateTime(r.last_run_at) : undefined}>{r.last_run_at ? ago(r.last_run_at) : "—"}</span> },
  ];
  const runColumns: Column<AutomationRun>[] = [
    { key: "at", header: "When", sort: (r) => r.at, className: "whitespace-nowrap", cell: (r) => <time className="font-mono text-xs text-ink-3" dateTime={r.at}>{dateTime(r.at)}</time> },
    { key: "rule", header: "Rule", cell: (r) => <Link href={`/automation/${r.rule_id}`} className="font-semibold hover:underline">{names.get(r.rule_id) ?? "Deleted rule"}</Link> },
    { key: "outcome", header: "Outcome", sort: (r) => r.outcome, className: "whitespace-nowrap", cell: (r) => <RunOutcome outcome={r.outcome} /> },
    {
      key: "detail",
      header: "Detail",
      cell: (r) => (
        <span className="text-sm text-ink-2">
          {r.change_id && r.change_ref && <><ChangeRefLink id={r.change_id} refText={r.change_ref} /> </>}
          {r.detail}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Automation"
        description="Rules turn an event or a schedule into a change proposal or a notification. A rule never approves or applies a change: what it proposes waits on the Changes page like any other."
        actions={can("changes:propose") ? <ButtonLink href="/automation/new" variant="primary" icon={<Plus />}>New rule</ButtonLink> : undefined}
      />
      {rules.error && <Notice tone="crit" className="mb-4">{(rules.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        <Panel title="Rules" bodyClassName="p-0">
          <DataTable
            caption="Automation rules"
            rows={rules.data?.items}
            columns={ruleColumns}
            loading={rules.isLoading}
            href={(r) => `/automation/${r.id}`}
            initialSort={{ key: "name", dir: "asc" }}
            empty={
              <EmptyState icon={<Workflow />} title="No rules yet">
                For example: when an alert fires on an access point, propose restarting it. Try a rule with a dry run before turning it on.
              </EmptyState>
            }
          />
        </Panel>
        <Panel title="Recent runs" bodyClassName="p-0">
          <DataTable caption="Recent automation runs" rows={runs.data?.items} columns={runColumns} loading={runs.isLoading} initialSort={{ key: "at", dir: "desc" }} empty={<EmptyState title="No runs yet">Rules run every 30 seconds against new events.</EmptyState>} />
        </Panel>
      </div>
    </>
  );
}
