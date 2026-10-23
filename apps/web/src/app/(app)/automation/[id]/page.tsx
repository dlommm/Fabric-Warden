"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Trash2, Workflow } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChangeRefLink, RuleEditor, RunOutcome, actionText, triggerText } from "@/components/automation";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { del, get, put } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AutomationMeta, AutomationRule, AutomationRuleInput, AutomationRun } from "@/lib/types";

function BackLink() {
  return (
    <Link href="/automation" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> Automation
    </Link>
  );
}

export default function RulePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useSession();
  const editor = can("changes:propose");
  const q = useQuery({ queryKey: ["automation", "rule", id], queryFn: () => get<AutomationRule>(`/api/automation/rules/${id}`), refetchInterval: 30_000 });
  const meta = useQuery({ queryKey: ["automation", "meta"], queryFn: () => get<AutomationMeta>("/api/automation/meta"), staleTime: 600_000 });
  const done = () => qc.invalidateQueries({ queryKey: ["automation"] });
  const save = useMutation({ mutationFn: (r: AutomationRuleInput) => put<AutomationRule>(`/api/automation/rules/${id}`, r), onSuccess: done });
  const toggle = useMutation({ mutationFn: (enabled: boolean) => put<AutomationRule>(`/api/automation/rules/${id}/enabled`, { enabled }), onSuccess: done });
  const remove = useMutation({
    mutationFn: () => del(`/api/automation/rules/${id}`),
    onSuccess: () => {
      done();
      router.push("/automation");
    },
  });

  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this rule">{(q.error as Error).message}</Notice>
      </>
    );
  }
  const r = q.data;
  if (!r) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-96" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }
  const columns: Column<AutomationRun>[] = [
    { key: "at", header: "When", sort: (x) => x.at, className: "whitespace-nowrap", cell: (x) => <time className="font-mono text-xs text-ink-3" dateTime={x.at}>{dateTime(x.at)}</time> },
    { key: "trigger", header: "Trigger", cell: (x) => <span className="text-sm text-ink-2">{x.trigger === "schedule" ? "Schedule" : (x.event_type ?? "Event")}</span> },
    { key: "outcome", header: "Outcome", sort: (x) => x.outcome, className: "whitespace-nowrap", cell: (x) => <RunOutcome outcome={x.outcome} /> },
    {
      key: "detail",
      header: "Detail",
      cell: (x) => (
        <span className="text-sm text-ink-2">
          {x.change_id && x.change_ref && <><ChangeRefLink id={x.change_id} refText={x.change_ref} /> </>}
          {x.detail}
        </span>
      ),
    },
  ];
  return (
    <>
      <BackLink />
      <PageHeader
        icon={<Workflow />}
        title={r.name}
        description={r.description || `${triggerText(r, meta.data)}. ${actionText(r)}.`}
        meta={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            {r.enabled ? <StateLabel tone="ok">On</StateLabel> : <StateLabel tone="muted">Off</StateLabel>}
            <span className="text-ink-3">created by {r.created_by} · {dateTime(r.created_at)}</span>
          </span>
        }
        actions={
          editor ? (
            <>
              <Button loading={toggle.isPending} onClick={() => toggle.mutate(!r.enabled)}>
                {r.enabled ? "Turn off" : "Turn on"}
              </Button>
              <Confirm
                trigger={<Button variant="danger" icon={<Trash2 />}>Delete</Button>}
                title={`Delete “${r.name}”?`}
                confirmLabel="Delete rule"
                onConfirm={() => remove.mutate()}
              >
                The rule and its run history are removed. Changes it proposed stay on the Changes page.
              </Confirm>
            </>
          ) : undefined
        }
      />
      {(toggle.error || remove.error) && <Notice tone="crit" className="mb-4">{((toggle.error || remove.error) as Error).message}</Notice>}
      {!r.enabled && <Notice tone="info" className="mb-4">Turned off. When turned on, it acts only on events from that moment; it never replays older ones.</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {editor && <RuleEditor key={r.updated_at} initial={r} saving={save.isPending} error={save.error} onSave={(x) => save.mutate(x)} saveLabel="Save changes" />}
        <Panel title="Run history" bodyClassName="p-0">
          <DataTable caption="Run history" rows={r.runs} columns={columns} initialSort={{ key: "at", dir: "desc" }} empty={<EmptyState title="No runs yet">{r.enabled ? "Waiting for a matching event." : "Turn the rule on to start."}</EmptyState>} />
        </Panel>
      </div>
    </>
  );
}
