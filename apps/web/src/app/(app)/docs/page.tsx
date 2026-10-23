"use client";

import { useQuery } from "@tanstack/react-query";
import { BookOpen, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { DocTree, NewPageForm, useDocPages } from "@/components/docs";
import { DemoLayoutControl } from "@/components/infra";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel } from "@/components/ui/signs";
import { get } from "@/lib/api";
import { ago } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DocSummary } from "@/lib/types";

export default function DocsIndex() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("docs");
  const editable = can("docs:edit");
  const [creating, setCreating] = useState(false);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const pages = useDocPages();
  const search = useQuery({
    queryKey: ["docs", "search", q.trim(), tag],
    queryFn: () => get<{ items: DocSummary[] }>(`/api/wiki/pages?${new URLSearchParams({ ...(q.trim() ? { q: q.trim() } : {}), ...(tag ? { tag } : {}) })}`),
    enabled: on && (!!q.trim() || !!tag),
  });
  const broken = useQuery({
    queryKey: ["docs", "broken"],
    queryFn: () => get<{ items: { page: DocSummary; raw: string; type: string; key: string }[] }>("/api/wiki/references/check"),
    enabled: on,
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Docs" name="Documentation & Wiki" canManage={can("modules:manage")}>
        Versioned runbooks and architecture pages that reference live inventory.
      </ModuleOff>
    );
  }
  const all = pages.data?.items ?? [];
  const shown = q.trim() || tag ? (search.data?.items ?? []) : [...all].sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  return (
    <>
      <PageHeader
        title="Docs"
        description={
          <>
            Runbooks and architecture notes in Markdown. Write <code className="rounded-[3px] bg-surface-3 px-1 font-mono text-[0.9em]">[[device:Core Aggregation]]</code> to reference live inventory; every save keeps a version.
          </>
        }
        actions={
          <>
            {!all.length && <DemoLayoutControl hasDemoRows={false} />}
            {editable && <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>New page</Button>}
          </>
        }
      />
      {pages.error && <Notice tone="crit" className="mb-4">{(pages.error as Error).message}</Notice>}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] lg:grid-cols-[16rem_minmax(0,1fr)] lg:items-start">
        <DocTree />
        <div className="flex min-w-0 flex-col gap-[var(--gap)]">
          {creating && <NewPageForm onDone={() => setCreating(false)} />}
          {broken.data && broken.data.items.length > 0 && (
            <Notice tone="warn" title={`${broken.data.items.length} reference${broken.data.items.length > 1 ? "s" : ""} no longer resolve`}>
              <ul className="mt-1 flex flex-col gap-0.5">
                {broken.data.items.slice(0, 8).map((b, n) => (
                  <li key={n}>
                    <Link href={`/docs/${b.page.slug}`} className="font-semibold text-ink hover:underline">{b.page.title}</Link>{" "}
                    <code className="font-mono text-xs">{b.raw}</code>
                  </li>
                ))}
              </ul>
            </Notice>
          )}
          <Panel bodyClassName="p-0">
            <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
              <Field label="Search pages" className="w-full sm:w-80">
                {(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title, text or tag" />}
              </Field>
              {(pages.data?.tags.length ?? 0) > 0 && (
                <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by tag">
                  {pages.data!.tags.map((t) => (
                    <button key={t} type="button" aria-pressed={tag === t} onClick={() => setTag(tag === t ? null : t)} className="rounded-[3px] focus-visible:outline-2">
                      <Chip className={tag === t ? "border-accent bg-accent-soft text-ink" : undefined}>{t}</Chip>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {shown.length === 0 ? (
              <EmptyState icon={<BookOpen />} title={all.length ? "No pages match" : "No pages yet"}>
                {all.length ? "Try another word or clear the tag." : "Start with a network overview or a runbook for the thing you fix most often."}
              </EmptyState>
            ) : (
              <ul>
                {shown.map((p) => (
                  <li key={p.id} className="border-b border-rule last:border-b-0">
                    <Link href={`/docs/${p.slug}`} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 hover:bg-surface-2">
                      <span className="min-w-0 flex-[1_1_12rem] truncate font-bold">{p.title}</span>
                      <span className="flex flex-wrap gap-1">{p.tags.map((t) => <Chip key={t}>{t}</Chip>)}</span>
                      <span className="text-sm text-ink-3">v{p.version} · {p.updated_by || "unknown"} · {ago(p.updated_at)}</span>
                      {p.is_demo && <DemoTag />}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          {broken.data && broken.data.items.length === 0 && all.length > 0 && <StateLabel tone="ok" className="text-sm">Every reference in every page resolves to a current object.</StateLabel>}
        </div>
      </div>
    </>
  );
}
