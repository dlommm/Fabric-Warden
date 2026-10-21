"use client";

// Wiki chrome shared by the docs index and page views: the page tree and the
// new-page form.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { FileText } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { ApiError, get, post } from "@/lib/api";
import type { DocSummary } from "@/lib/types";

export function useDocPages() {
  return useQuery({ queryKey: ["docs", "pages"], queryFn: () => get<{ items: DocSummary[]; tags: string[] }>("/api/wiki/pages") });
}

export function DocTree({ current }: { current?: string }) {
  const q = useDocPages();
  const pages = q.data?.items ?? [];
  const kids = new Map<string | null, DocSummary[]>();
  for (const p of pages) {
    const parent = p.parent_id && pages.some((x) => x.id === p.parent_id) ? p.parent_id : null;
    kids.set(parent, [...(kids.get(parent) ?? []), p]);
  }
  const render = (parent: string | null, depth: number): ReactNode => {
    const list = kids.get(parent);
    if (!list?.length) return null;
    return (
      <ul className={clsx("flex flex-col", depth > 0 && "ml-3 border-l border-rule pl-2")}>
        {list.map((p) => (
          <li key={p.id}>
            <Link
              href={`/docs/${p.slug}`}
              aria-current={p.slug === current ? "page" : undefined}
              className={clsx(
                "flex min-h-8 items-center gap-2 rounded-[3px] px-2 text-sm",
                p.slug === current ? "sign bg-accent font-bold text-accent-ink" : "text-ink hover:bg-surface-2",
              )}
            >
              <FileText className={clsx("size-3.5 shrink-0", p.slug === current ? "text-accent-ink" : "text-ink-3")} aria-hidden />
              <span className="truncate">{p.title}</span>
            </Link>
            {render(p.id, depth + 1)}
          </li>
        ))}
      </ul>
    );
  };
  return (
    <Panel title="Pages" bodyClassName="p-2" className="lg:sticky lg:top-4">
      {q.isLoading ? <Skeleton className="h-32" /> : pages.length === 0 ? <p className="px-2 py-1 text-sm text-ink-3">No pages yet.</p> : <nav aria-label="Documentation pages">{render(null, 0)}</nav>}
      {pages.some((p) => p.is_demo) && (
        <p className="mt-2 flex items-center gap-2 border-t border-rule px-2 pt-2 text-xs text-ink-3"><DemoTag /> demo pages included</p>
      )}
    </Panel>
  );
}

export function NewPageForm({ onDone, parentId }: { onDone: () => void; parentId?: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const pages = useDocPages();
  const [f, setF] = useState({ title: "", parent_id: parentId ?? "", tags: "" });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      post<DocSummary>("/api/wiki/pages", {
        title: f.title.trim(),
        parent_id: f.parent_id || null,
        tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean),
        body: "",
      }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["docs"] });
      router.push(`/docs/${p.slug}?edit=1`);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't create the page."),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!f.title.trim()) return setError("Give the page a title.");
    save.mutate();
  };
  return (
    <Panel title="New page">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)] md:items-start">
        <Field label="Title" error={error}>{(p) => <Input {...p} autoFocus value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Firewall change runbook" />}</Field>
        <Field label="Inside">
          {(p) => (
            <Select {...p} value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
              <option value="">Top level</option>
              {pages.data?.items.map((pg) => <option key={pg.id} value={pg.id}>{pg.title}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Tags" hint="Comma separated">{(p) => <Input {...p} value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} placeholder="runbook, firewall" />}</Field>
        <div className="flex gap-2 md:col-span-3">
          <Button type="submit" variant="primary" loading={save.isPending}>Create and edit</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
