"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { FilterBar, Segmented } from "@/components/filters";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { get } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { AuditRecord } from "@/lib/types";

type Outcome = "all" | "success" | "denied" | "failure";
const ACTIONS = [
  ["", "All actions"],
  ["auth.", "Sign-in & MFA"],
  ["user.", "Users"],
  ["token.", "API tokens"],
  ["controller.", "Controllers"],
  ["module.", "Modules"],
  ["inventory.", "Inventory edits"],
  ["job.", "Jobs"],
  ["setup.", "Setup"],
] as const;

interface Page {
  chain_intact: boolean;
  first_bad_seq: number | null;
  records: AuditRecord[];
}

export default function AuditPage() {
  const [outcome, setOutcome] = useState<Outcome>("all");
  const [action, setAction] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["audit", outcome, action],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ limit: "100" });
      if (pageParam) p.set("before_seq", String(pageParam));
      if (outcome !== "all") p.set("outcome", outcome);
      if (action) p.set("action", action);
      return get<Page>(`/api/audit?${p}`);
    },
    getNextPageParam: (last) => (last.records.length === 100 ? last.records.at(-1)!.seq : undefined),
  });
  const first = q.data?.pages[0];
  const records = q.data?.pages.flatMap((p) => p.records) ?? [];

  return (
    <>
      <PageHeader
        title="Audit trail"
        description="Every sign-in, permission change, controller and module action, in order. Records are hash-chained so edits or deletions are detectable."
        meta={
          first &&
          (first.chain_intact ? (
            <StateLabel tone="ok">Chain verified intact</StateLabel>
          ) : (
            <StateLabel tone="crit">Chain broken at record {first.first_bad_seq}</StateLabel>
          ))
        }
      />
      {first && !first.chain_intact && (
        <Notice tone="crit" className="mb-4" title="The audit chain doesn't verify">
          A record at or after #{first.first_bad_seq} was changed or removed outside Fabric Warden. Treat entries from that point as untrusted and check database access.
        </Notice>
      )}
      <Panel bodyClassName="p-0">
        <FilterBar>
          <Segmented
            label="Outcome"
            value={outcome}
            onChange={setOutcome}
            options={[
              { value: "all", label: "All" },
              { value: "success", label: "Succeeded" },
              { value: "denied", label: "Denied" },
              { value: "failure", label: "Failed" },
            ]}
          />
          <Select aria-label="Action type" value={action} onChange={(e) => setAction(e.target.value)} className="w-auto min-w-44">
            {ACTIONS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </Select>
        </FilterBar>
        {q.isLoading && <div className="p-4"><Skeleton className="h-48 w-full" /></div>}
        {q.error && <Notice tone="crit" className="m-4">{(q.error as Error).message}</Notice>}
        {!q.isLoading && records.length === 0 && <EmptyState title="No matching records" />}
        {records.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <caption className="sr-only">Audit records</caption>
              <thead>
                <tr className="border-b border-rule-strong text-xs font-bold tracking-wide text-ink-2 uppercase">
                  <th scope="col" className="h-9 w-8 px-2"><span className="sr-only">Expand</span></th>
                  <th scope="col" className="px-[var(--row-pad)]">#</th>
                  <th scope="col" className="px-[var(--row-pad)]">When</th>
                  <th scope="col" className="px-[var(--row-pad)]">Who</th>
                  <th scope="col" className="px-[var(--row-pad)]">Action</th>
                  <th scope="col" className="px-[var(--row-pad)]">Target</th>
                  <th scope="col" className="px-[var(--row-pad)]">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <Fragment key={r.seq}>
                    <tr className={clsx("border-b border-rule", r.outcome !== "success" && "bg-crit-soft/40")}>
                      <td className="px-2">
                        <button type="button" onClick={() => setOpen(open === r.seq ? null : r.seq)} className="rounded p-1 text-ink-3 hover:bg-surface-3 hover:text-ink" aria-expanded={open === r.seq} aria-label={`Details for record ${r.seq}`}>
                          {open === r.seq ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                        </button>
                      </td>
                      <td className="tabular h-[var(--row-h)] px-[var(--row-pad)] text-sm text-ink-3">{r.seq}</td>
                      <td className="px-[var(--row-pad)] whitespace-nowrap text-ink-2">{dateTime(r.occurred_at)}</td>
                      <td className="px-[var(--row-pad)]">
                        <span className="font-semibold">{r.actor_name}</span> <span className="text-xs text-ink-3">{r.actor_type}</span>
                      </td>
                      <td className="px-[var(--row-pad)] font-mono text-sm">{r.action}</td>
                      <td className="px-[var(--row-pad)] text-sm text-ink-2">
                        {r.target_type ? (
                          <span className="inline-flex max-w-[18rem] items-baseline gap-1.5" title={r.target_id ?? undefined}>
                            <span className="shrink-0">{r.target_type}</span>
                            {r.target_id && <span className="truncate font-mono text-xs text-ink-3">{r.target_id}</span>}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-[var(--row-pad)]">
                        <StateLabel tone={r.outcome === "success" ? "ok" : r.outcome === "denied" ? "warn" : "crit"}>{r.outcome}</StateLabel>
                      </td>
                    </tr>
                    {open === r.seq && (
                      <tr className="border-b border-rule bg-surface-2">
                        <td />
                        <td colSpan={6} className="px-[var(--row-pad)] py-3">
                          <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-1 text-sm">
                            <dt className="text-ink-3">Source IP</dt><dd className="font-mono">{r.ip_address ?? "—"}</dd>
                            <dt className="text-ink-3">Target ID</dt><dd className="font-mono break-all">{r.target_id ?? "—"}</dd>
                            <dt className="text-ink-3">Detail</dt><dd><pre className="font-mono text-xs whitespace-pre-wrap">{JSON.stringify(r.detail, null, 2)}</pre></dd>
                            <dt className="text-ink-3">Record hash</dt><dd className="font-mono text-xs break-all text-ink-3">{r.hash}</dd>
                          </dl>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {q.hasNextPage && (
          <div className="border-t border-rule p-3 text-center">
            <Button onClick={() => q.fetchNextPage()} loading={q.isFetchingNextPage}>Load older records</Button>
          </div>
        )}
      </Panel>
    </>
  );
}
