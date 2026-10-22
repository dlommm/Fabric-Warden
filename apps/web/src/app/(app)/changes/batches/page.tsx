"use client";

import { useQuery } from "@tanstack/react-query";
import { Layers, Plus } from "lucide-react";
import Link from "next/link";
import { BatchState, RiskLabel, batchProgress } from "@/components/changes";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChangeBatch } from "@/lib/types";

export default function BatchesPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("bulk-ops");
  const open = useQuery({ queryKey: ["batches", "open"], queryFn: () => get<{ items: ChangeBatch[] }>("/api/batches?view=open"), enabled: on, refetchInterval: 10_000 });
  const history = useQuery({ queryKey: ["batches", "history"], queryFn: () => get<{ items: ChangeBatch[] }>("/api/batches?view=history"), enabled: on, refetchInterval: 60_000 });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Bulk operations" name="Bulk Operations" canManage={can("modules:manage")}>
        Apply one change to many targets, such as power-cycling several PoE ports or restarting several access points. A bulk operation is approved once and applied one item at a time; it stops at the first item that does not succeed.
      </ModuleOff>
    );
  }
  const items = open.data?.items;
  const columns: Column<ChangeBatch>[] = [
    { key: "ref", header: "Batch", sort: (b) => b.number, className: "w-24", cell: (b) => <span className="font-mono text-xs font-bold">{b.ref}</span> },
    {
      key: "title",
      header: "What",
      sort: (b) => b.title,
      cell: (b) => (
        <span className="flex min-w-0 flex-col">
          <Link href={`/changes/batches/${b.id}`} className="truncate font-bold hover:underline">{b.title}</Link>
          <span className="truncate text-sm text-ink-3">{batchProgress(b)}</span>
        </span>
      ),
    },
    { key: "status", header: "Result", sort: (b) => b.status, className: "whitespace-nowrap", cell: (b) => <BatchState status={b.status} /> },
    { key: "risk", header: "Risk", sort: (b) => ({ high: 0, medium: 1, low: 2 })[b.risk], className: "whitespace-nowrap", cell: (b) => <RiskLabel risk={b.risk} protectedPath={b.protected} /> },
    { key: "who", header: "Proposed / started", cell: (b) => <span className="text-sm text-ink-2">{b.proposed_by}{b.started_by ? ` / ${b.started_by}` : ""}</span> },
    { key: "when", header: "Finished", sort: (b) => b.finished_at ?? "", align: "right", cell: (b) => <span className="tabular text-sm text-ink-3" title={b.finished_at ? dateTime(b.finished_at) : undefined}>{b.finished_at ? ago(b.finished_at) : "—"}</span> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (b) => (b.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Bulk operations"
        description="One change over many targets. Each target becomes its own change, read live, drift-checked, sent and verified like any other; the batch is approved once, applies them one at a time and stops at the first that does not succeed."
        actions={can("changes:propose") ? <ButtonLink href="/changes/batches/new" variant="primary" icon={<Plus />}>New bulk operation</ButtonLink> : undefined}
      />
      {open.error && <Notice tone="crit" className="mb-4">{(open.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        <Panel title={`Open${items?.length ? ` (${items.length})` : ""}`} bodyClassName="p-0">
          {open.isLoading ? (
            <div className="p-4"><Skeleton className="h-16" /></div>
          ) : !items?.length ? (
            <EmptyState icon={<Layers />} title="Nothing waiting or running">
              Start one with New bulk operation: pick the operation, tick the targets, say why.
            </EmptyState>
          ) : (
            <ul>
              {items.map((b) => (
                <li key={b.id} className="border-b border-rule last:border-b-0">
                  <Link href={`/changes/batches/${b.id}`} className="flex flex-col gap-1 px-4 py-3 hover:bg-surface-2 md:flex-row md:items-center md:gap-5">
                    <span className="font-mono text-xs font-bold md:w-20">{b.ref}</span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-bold">{b.title}</span>
                      <span className="truncate text-sm text-ink-3">{batchProgress(b)} · proposed by {b.proposed_by} {ago(b.proposed_at)}</span>
                    </span>
                    <span className="md:w-64"><BatchState status={b.status} /></span>
                    <span className="whitespace-nowrap md:w-56"><RiskLabel risk={b.risk} protectedPath={b.protected} /></span>
                    {b.is_demo && <DemoTag className="self-start md:self-center" />}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="History" bodyClassName="p-0">
          <DataTable caption="Bulk operation history" rows={history.data?.items} columns={columns} loading={history.isLoading} href={(b) => `/changes/batches/${b.id}`} initialSort={{ key: "ref", dir: "desc" }} empty={<EmptyState title="No finished bulk operations yet" />} />
        </Panel>
      </div>
    </>
  );
}
