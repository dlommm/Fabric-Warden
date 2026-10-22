"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitPullRequest, Lock, LockOpen } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ChangeState, RiskLabel } from "@/components/changes";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get, put } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChangeRequest, ChangeSettings } from "@/lib/types";

export default function ChangesPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("change-engine");
  const open = useQuery({ queryKey: ["changes", "open"], queryFn: () => get<{ items: ChangeRequest[] }>("/api/changes?view=open"), enabled: on, refetchInterval: 15_000 });
  const history = useQuery({ queryKey: ["changes", "history"], queryFn: () => get<{ items: ChangeRequest[] }>("/api/changes?view=history"), enabled: on, refetchInterval: 60_000 });
  const settings = useQuery({ queryKey: ["changes", "settings"], queryFn: () => get<ChangeSettings>("/api/changes/settings/current"), enabled: on });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Changes" name="Change Approval & Recovery" canManage={can("modules:manage")}>
        Make reviewed changes to your controller: each one is proposed with the live state and its risk, approved, applied only after a fresh read, and verified afterwards.
      </ModuleOff>
    );
  }
  const items = open.data?.items;
  const columns: Column<ChangeRequest>[] = [
    { key: "ref", header: "Change", sort: (c) => c.number, className: "w-24", cell: (c) => <span className="font-mono text-xs font-bold">{c.ref}</span> },
    {
      key: "title",
      header: "What",
      sort: (c) => c.title,
      cell: (c) => (
        <span className="flex min-w-0 flex-col">
          <Link href={`/changes/${c.id}`} className="truncate font-bold hover:underline">{c.title}</Link>
          <span className="truncate text-sm text-ink-3">{c.controller?.name ?? "—"}{c.reason ? ` · ${c.reason}` : ""}</span>
        </span>
      ),
    },
    { key: "status", header: "Result", sort: (c) => c.status, className: "whitespace-nowrap", cell: (c) => <ChangeState status={c.status} /> },
    { key: "risk", header: "Risk", sort: (c) => ({ high: 0, medium: 1, low: 2 })[c.risk], className: "whitespace-nowrap", cell: (c) => <RiskLabel risk={c.risk} protectedPath={c.protected} /> },
    { key: "who", header: "Proposed / applied", cell: (c) => <span className="text-sm text-ink-2">{c.proposed_by}{c.applied_by ? ` / ${c.applied_by}` : ""}</span> },
    { key: "when", header: "Finished", sort: (c) => c.finished_at ?? "", align: "right", cell: (c) => <span className="tabular text-sm text-ink-3" title={c.finished_at ? dateTime(c.finished_at) : undefined}>{c.finished_at ? ago(c.finished_at) : "—"}</span> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (c) => (c.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Changes"
        description="Every write Fabric Warden makes to a controller is a change here: proposed with the live state, a diff and its risk, approved, applied only after a fresh re-read, then verified by reading the controller again."
      />
      {open.error && <Notice tone="crit" className="mb-4">{(open.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        <Panel title={`Open${items?.length ? ` (${items.length})` : ""}`} bodyClassName="p-0">
          {open.isLoading ? (
            <div className="p-4"><Skeleton className="h-16" /></div>
          ) : !items?.length ? (
            <EmptyState icon={<GitPullRequest />} title="Nothing waiting">
              Propose a change from a firewall policy (logging), a device (restart) or a PoE port (power-cycle).
            </EmptyState>
          ) : (
            <ul>
              {items.map((c) => (
                <li key={c.id} className="border-b border-rule last:border-b-0">
                  <Link href={`/changes/${c.id}`} className="flex flex-col gap-1 px-4 py-3 hover:bg-surface-2 md:flex-row md:items-center md:gap-5">
                    <span className="font-mono text-xs font-bold md:w-20">{c.ref}</span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-bold">{c.title}</span>
                      <span className="truncate text-sm text-ink-3">proposed by {c.proposed_by} {ago(c.proposed_at)}{c.reason ? ` · ${c.reason}` : ""}</span>
                    </span>
                    <span className="md:w-56"><ChangeState status={c.status} /></span>
                    <span className="whitespace-nowrap md:w-56"><RiskLabel risk={c.risk} protectedPath={c.protected} /></span>
                    {c.is_demo && <DemoTag className="self-start md:self-center" />}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <WriteModes settings={settings.data} />
        <Panel title="History" bodyClassName="p-0">
          <DataTable caption="Change history" rows={history.data?.items} columns={columns} loading={history.isLoading} href={(c) => `/changes/${c.id}`} initialSort={{ key: "ref", dir: "desc" }} empty={<EmptyState title="No finished changes yet" />} />
        </Panel>
      </div>
    </>
  );
}

function WriteModes({ settings }: { settings?: ChangeSettings }) {
  const { can } = useSession();
  const owner = can("system:manage");
  const qc = useQueryClient();
  const [pending, setPending] = useState<ChangeSettings["controllers"][number] | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["changes"] });
  const mode = useMutation({ mutationFn: ({ id, write_mode }: { id: string; write_mode: string }) => put(`/api/changes/controllers/${id}/write-mode`, { write_mode }), onSuccess: done });
  const second = useMutation({ mutationFn: (v: boolean) => put("/api/changes/settings/current", { require_second_person: v }), onSuccess: done });
  return (
    <Panel title="Where changes are allowed" bodyClassName="p-0">
      {!settings ? (
        <div className="p-4"><Skeleton className="h-12" /></div>
      ) : (
        <>
          <ul>
            {settings.controllers.map((c) => (
              <li key={c.id} className="flex flex-col gap-2 border-b border-rule px-4 py-3 md:flex-row md:items-center md:gap-5">
                <span className="flex min-w-0 flex-col md:w-72">
                  <span className="truncate font-bold">{c.name}</span>
                  <span className="text-sm text-ink-3">Network {c.application_version ?? "version unknown"}</span>
                </span>
                <span className="flex-1">
                  {c.write_mode === "changes" ? (
                    <StateLabel tone="warn">Approved changes can be applied</StateLabel>
                  ) : (
                    <StateLabel tone="muted">Read-only</StateLabel>
                  )}
                </span>
                {c.is_demo && <DemoTag className="self-start md:self-center" />}
                {owner &&
                  (c.write_mode === "changes" ? (
                    <Button size="sm" icon={<Lock />} loading={mode.isPending && mode.variables?.id === c.id} onClick={() => mode.mutate({ id: c.id, write_mode: "read_only" })}>Make read-only</Button>
                  ) : (
                    <Button size="sm" icon={<LockOpen />} onClick={() => setPending(c)}>Allow changes</Button>
                  ))}
              </li>
            ))}
          </ul>
          <div className="flex flex-col gap-2 px-4 py-3 md:flex-row md:items-center md:justify-between">
            <span>
              <span className="font-semibold">Approval</span>{" "}
              <span className="text-ink-2">{settings.require_second_person ? "A second person must approve every change." : "Whoever has approval rights may approve their own proposal; it is recorded as such."}</span>
            </span>
            {owner && (
              <Button size="sm" loading={second.isPending} onClick={() => second.mutate(!settings.require_second_person)}>
                {settings.require_second_person ? "Allow self-approval" : "Require a second person"}
              </Button>
            )}
          </div>
          {!owner && <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">Only an owner can allow changes on a controller.</p>}
        </>
      )}
      {(mode.error || second.error) && <div className="border-t border-rule p-4"><Notice tone="crit">{((mode.error || second.error) as Error).message}</Notice></div>}
      <Confirm
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title={`Allow changes on ${pending?.name}?`}
        confirmLabel="Allow changes"
        destructive={false}
        onConfirm={() => pending && mode.mutate({ id: pending.id, write_mode: "changes" })}
      >
        Approved changes can then be applied to this controller. Each one is still proposed, reviewed and approved first, re-read just before sending and verified afterwards. You can make the controller read-only again at any time.
      </Confirm>
    </Panel>
  );
}
