"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { FilePlus2, History, Pencil, Trash2, Undo2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { DocTree, NewPageForm, useDocPages } from "@/components/docs";
import { Markdown, RefChip } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag } from "@/components/ui/signs";
import { ApiError, del, get, post, put } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DocPage, DocRef, DocVersion } from "@/lib/types";

type Tab = "read" | "edit" | "history";

export default function DocPageRoute() {
  return (
    <Suspense>
      <DocView />
    </Suspense>
  );
}

function DocView() {
  const { slug } = useParams<{ slug: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const { can } = useSession();
  const editable = can("docs:edit");
  const [tab, setTab] = useState<Tab>(search.get("edit") && editable ? "edit" : "read");
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["docs", "page", slug], queryFn: () => get<DocPage>(`/api/wiki/pages/${slug}`) });
  const page = q.data;

  const go = (t: Tab) => {
    setTab(t);
    router.replace(`/docs/${slug}${t === "edit" ? "?edit=1" : ""}`, { scroll: false });
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] lg:grid-cols-[16rem_minmax(0,1fr)] lg:items-start">
      <DocTree current={slug} />
      <div className="min-w-0">
        {q.error ? (
          <Notice tone="crit">{(q.error as Error).message}</Notice>
        ) : !page ? (
          <>
            <Skeleton className="mb-6 h-8 w-72" />
            <Skeleton className="h-96" />
          </>
        ) : (
          <>
            <PageHeader
              title={page.title}
              meta={
                <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-3">
                  {page.is_demo && <DemoTag />}
                  {page.parent && (
                    <Link href={`/docs/${page.parent.slug}`} className="font-semibold text-accent hover:underline">{page.parent.title}</Link>
                  )}
                  <span>Version {page.version} · {page.updated_by || "unknown"} · {ago(page.updated_at)}</span>
                  {page.tags.map((t) => <Chip key={t}>{t}</Chip>)}
                </span>
              }
              actions={editable && <Button icon={<FilePlus2 />} onClick={() => setCreating(true)}>Sub-page</Button>}
            />
            {creating && <div className="mb-[var(--gap)]"><NewPageForm parentId={page.id} onDone={() => setCreating(false)} /></div>}
            <div role="tablist" aria-label="Page views" className="mb-[var(--gap)] flex gap-5 border-b border-rule">
              {(["read", ...(editable ? ["edit"] : []), "history"] as Tab[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  role="tab"
                  aria-selected={tab === t}
                  onClick={() => go(t)}
                  className={clsx("-mb-px inline-flex h-10 items-center gap-1.5 border-b-2 text-sm font-bold", tab === t ? "border-accent text-ink" : "border-transparent text-ink-2 hover:text-ink")}
                >
                  {t === "edit" ? <Pencil className="size-3.5" aria-hidden /> : t === "history" ? <History className="size-3.5" aria-hidden /> : null}
                  {t === "read" ? "Read" : t === "edit" ? "Edit" : `History (${page.version})`}
                </button>
              ))}
            </div>
            {tab === "read" && <ReadView page={page} />}
            {tab === "edit" && <Editor key={page.version} page={page} onSaved={() => go("read")} />}
            {tab === "history" && <HistoryView page={page} editable={editable} onReverted={() => go("read")} />}
          </>
        )}
      </div>
    </div>
  );
}

/** The page header already carries the title; a body that opens with the same heading would repeat it. */
function withoutTitle(body: string, title: string): string {
  const m = /^#\s+(.*)\n?/.exec(body.trimStart());
  return m && m[1].trim().toLowerCase() === title.trim().toLowerCase() ? body.trimStart().slice(m[0].length) : body;
}

function ReadView({ page }: { page: DocPage }) {
  const refs = page.references;
  const unique = useMemo(() => {
    const seen = new Map<string, DocRef>();
    refs.forEach((r) => seen.set(`${r.type}:${r.key}`, r));
    return [...seen.values()];
  }, [refs]);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_17rem] xl:items-start">
      <Panel>
        {page.body.trim() ? <Markdown body={withoutTitle(page.body, page.title)} refs={refs} /> : <p className="text-ink-3">This page is empty.</p>}
      </Panel>
      <div className="flex flex-col gap-[var(--gap)]">
        <Panel title={`References (${unique.length})`} bodyClassName="p-0">
          {unique.length === 0 ? (
            <p className="px-4 py-3 text-sm text-ink-3">None. Write [[device:Name]] to link live inventory.</p>
          ) : (
            <ul>
              {unique.map((r) => (
                <li key={`${r.type}:${r.key}`} className="flex flex-col gap-0.5 border-b border-rule px-4 py-2 last:border-b-0">
                  <span><RefChip r={r} /></span>
                  <span className="truncate pl-1 text-xs text-ink-3">{r.type}{r.resolved && r.detail ? ` · ${r.detail}` : r.resolved ? "" : " · not found"}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        {(page.children.length > 0 || page.backlinks.length > 0) && (
          <Panel title="Linked pages" bodyClassName="p-0">
            {page.children.length > 0 && (
              <>
                <p className="border-b border-rule bg-surface-2 px-4 py-1.5 text-sm font-semibold text-ink-3">Sub-pages</p>
                <ul>{page.children.map((c) => <li key={c.id} className="border-b border-rule last:border-b-0"><Link href={`/docs/${c.slug}`} className="block px-4 py-2 font-semibold hover:bg-surface-2">{c.title}</Link></li>)}</ul>
              </>
            )}
            {page.backlinks.length > 0 && (
              <>
                <p className="border-y border-rule bg-surface-2 px-4 py-1.5 text-sm font-semibold text-ink-3">Mentioned by</p>
                <ul>{page.backlinks.map((c) => <li key={c.id} className="border-b border-rule last:border-b-0"><Link href={`/docs/${c.slug}`} className="block px-4 py-2 font-semibold hover:bg-surface-2">{c.title}</Link></li>)}</ul>
              </>
            )}
          </Panel>
        )}
      </div>
    </div>
  );
}

function Editor({ page, onSaved }: { page: DocPage; onSaved: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const pages = useDocPages();
  const [f, setF] = useState({ title: page.title, body: page.body, tags: page.tags.join(", "), parent_id: page.parent_id ?? "", message: "" });
  const [preview, setPreview] = useState(true);
  const [conflict, setConflict] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const dirty = f.title !== page.title || f.body !== page.body || f.tags !== page.tags.join(", ") || (f.parent_id || null) !== page.parent_id;
  // Resolve references in the draft after a pause in typing.
  const [debounced, setDebounced] = useState(f.body);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(f.body), 400);
    return () => clearTimeout(t);
  }, [f.body]);
  const refs = useQuery({
    queryKey: ["docs", "preview", debounced],
    queryFn: () => post<{ references: DocRef[] }>("/api/wiki/references", { body: debounced }),
    enabled: preview,
    placeholderData: (prev) => prev,
  });
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = useMutation({
    mutationFn: () =>
      put(`/api/wiki/pages/${page.slug}`, {
        base_version: page.version,
        title: f.title.trim(),
        body: f.body,
        tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean),
        parent_id: f.parent_id || null,
        message: f.message.trim(),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docs"] });
      onSaved();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && /saved by/i.test(e.message)) setConflict(e.message);
      else setError(e instanceof ApiError ? e.message : "Couldn't save.");
    },
  });
  const remove = useMutation({
    mutationFn: () => del(`/api/wiki/pages/${page.slug}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docs"] });
      router.push("/docs");
    },
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!f.title.trim()) return setError("The page needs a title.");
        setError(null);
        save.mutate();
      }}
      noValidate
      className="flex flex-col gap-[var(--gap)]"
    >
      {conflict && (
        <Notice tone="crit" title="Someone else saved this page" action={<Button size="sm" onClick={() => { qc.invalidateQueries({ queryKey: ["docs", "page", page.slug] }); setConflict(null); }}>Load their version</Button>}>
          {conflict} Copy your text first: loading their version replaces this draft.
        </Notice>
      )}
      <Panel>
        <div className="grid gap-4 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <Field label="Title" error={error}>{(p) => <Input {...p} value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} />}</Field>
          <Field label="Inside">
            {(p) => (
              <Select {...p} value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
                <option value="">Top level</option>
                {pages.data?.items.filter((x) => x.id !== page.id).map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Tags" hint="Comma separated">{(p) => <Input {...p} value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} />}</Field>
        </div>
      </Panel>
      <div className={clsx("grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)]", preview && "xl:grid-cols-2")}>
        <Panel
          title="Markdown"
          actions={<Button size="sm" variant="ghost" aria-pressed={preview} onClick={() => setPreview((v) => !v)}>{preview ? "Hide preview" : "Show preview"}</Button>}
          bodyClassName="p-0"
        >
          <label htmlFor="doc-body" className="sr-only">Page text in Markdown</label>
          <Textarea
            id="doc-body"
            value={f.body}
            onChange={(e) => setF({ ...f, body: e.target.value })}
            spellCheck
            className="min-h-[28rem] rounded-none border-0 font-mono text-sm leading-6 focus-visible:ring-0"
          />
          <p className="border-t border-rule px-4 py-2 text-xs text-ink-3">
            References: <code className="font-mono">[[device:Name]]</code> <code className="font-mono">[[client:host]]</code> <code className="font-mono">[[prefix:10.0.30.0/24]]</code> <code className="font-mono">[[rack:Rack A]]</code> <code className="font-mono">[[cable:C-0004]]</code> <code className="font-mono">[[page:slug|text]]</code>
          </p>
        </Panel>
        {preview && (
          <Panel title="Preview">
            {f.body.trim() ? <Markdown body={withoutTitle(f.body, f.title)} refs={refs.data?.references ?? []} /> : <p className="text-ink-3">Nothing to preview.</p>}
          </Panel>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="What changed" hint="Optional; shown in history" className="w-full sm:w-96">
          {(p) => <Input {...p} value={f.message} maxLength={200} onChange={(e) => setF({ ...f, message: e.target.value })} placeholder="Add shutdown order" />}
        </Field>
        <Button type="submit" variant="primary" loading={save.isPending} disabled={!dirty}>Save version {page.version + 1}</Button>
        <Button variant="ghost" onClick={onSaved}>{dirty ? "Discard changes" : "Done"}</Button>
        <Button variant="danger" icon={<Trash2 />} className="ml-auto" onClick={() => setDeleting(true)}>Delete page</Button>
      </div>
      <Confirm open={deleting} onOpenChange={setDeleting} title={`Delete ${page.title}?`} confirmLabel="Delete page" onConfirm={() => remove.mutate()}>
        The page and all {page.version} version{page.version > 1 ? "s" : ""} are deleted. Sub-pages move to the top level.
      </Confirm>
    </form>
  );
}

function HistoryView({ page, editable, onReverted }: { page: DocPage; editable: boolean; onReverted: () => void }) {
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["docs", "versions", page.slug], queryFn: () => get<{ items: DocVersion[] }>(`/api/wiki/pages/${page.slug}/versions`) });
  const [pair, setPair] = useState<[number, number]>([Math.max(1, page.version - 1), page.version]);
  const [reverting, setReverting] = useState<number | null>(null);
  const diff = useQuery({
    queryKey: ["docs", "diff", page.slug, pair],
    queryFn: () => get<{ diff: string[]; added: number; removed: number; title_changed: boolean }>(`/api/wiki/pages/${page.slug}/diff?from=${pair[0]}&to=${pair[1]}`),
    enabled: pair[0] !== pair[1],
  });
  const revert = useMutation({
    mutationFn: (v: number) => post(`/api/wiki/pages/${page.slug}/revert`, { version: v, base_version: page.version }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docs"] });
      onReverted();
    },
  });
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] xl:grid-cols-[22rem_minmax(0,1fr)] xl:items-start">
      <Panel title="Versions" bodyClassName="p-0">
        {revert.error && <Notice tone="crit" className="m-3">{(revert.error as Error).message}</Notice>}
        {!versions.data ? (
          <div className="p-4"><Skeleton className="h-40" /></div>
        ) : (
          <table className="w-full border-collapse text-left">
            <caption className="sr-only">Versions; choose two to compare</caption>
            <thead>
              <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                <th scope="col" className="h-9 w-10 px-2 text-center">From</th>
                <th scope="col" className="w-10 px-2 text-center">To</th>
                <th scope="col" className="px-2">Version</th>
                <th scope="col" className="px-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {versions.data.items.map((v) => (
                <tr key={v.version} className="border-b border-rule last:border-b-0">
                  <td className="px-2 text-center"><input type="radio" name="from" aria-label={`Compare from version ${v.version}`} checked={pair[0] === v.version} onChange={() => setPair([v.version, pair[1]])} className="accent-[var(--accent)]" /></td>
                  <td className="px-2 text-center"><input type="radio" name="to" aria-label={`Compare to version ${v.version}`} checked={pair[1] === v.version} onChange={() => setPair([pair[0], v.version])} className="accent-[var(--accent)]" /></td>
                  <td className="px-2 py-2">
                    <span className="block font-bold">v{v.version}{v.version === page.version ? <span className="ml-2 text-sm font-semibold text-ok">current</span> : null}</span>
                    <span className="block text-sm text-ink-2">{v.message || <span className="text-ink-3">No message</span>}</span>
                    <span className="block text-xs text-ink-3">{v.author || "unknown"} · {dateTime(v.created_at)}</span>
                  </td>
                  <td className="px-2 text-right">
                    {editable && v.version !== page.version && (
                      <Button size="sm" variant="ghost" icon={<Undo2 />} aria-label={`Restore version ${v.version}`} onClick={() => setReverting(v.version)} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <Panel
        title={`Changes from v${pair[0]} to v${pair[1]}`}
        actions={diff.data && <span className="text-sm text-ink-2 tabular"><b className="text-ok">+{diff.data.added}</b> <b className="text-crit">−{diff.data.removed}</b></span>}
        bodyClassName="p-0"
      >
        {pair[0] === pair[1] ? (
          <p className="px-4 py-3 text-ink-3">Choose two different versions.</p>
        ) : !diff.data ? (
          <div className="p-4"><Skeleton className="h-40" /></div>
        ) : diff.data.diff.length === 0 ? (
          <p className="px-4 py-3 text-ink-3">{diff.data.title_changed ? "Only the title changed." : "The text is identical."}</p>
        ) : (
          <pre className="overflow-x-auto py-2 font-mono text-xs leading-5" aria-label="Line differences; plus is added, minus is removed">
            {diff.data.diff.slice(2).map((line, n) => {
              const add = line.startsWith("+");
              const rem = line.startsWith("-");
              const hunk = line.startsWith("@@");
              return (
                <div key={n} className={clsx("px-4 whitespace-pre-wrap", add && "bg-ok-soft", rem && "bg-crit-soft", hunk && "bg-surface-2 text-ink-3")}>
                  <span aria-hidden className={clsx("inline-block w-4 font-bold", add && "text-ok", rem && "text-crit")}>{add ? "+" : rem ? "−" : " "}</span>
                  {hunk ? line : line.slice(1)}
                </div>
              );
            })}
          </pre>
        )}
      </Panel>
      <Confirm
        open={reverting !== null}
        onOpenChange={(o) => !o && setReverting(null)}
        title={`Restore version ${reverting}?`}
        confirmLabel="Restore as new version"
        destructive={false}
        onConfirm={() => reverting && revert.mutate(reverting)}
      >
        Its text is saved as version {page.version + 1}. Nothing in the history is deleted.
      </Confirm>
    </div>
  );
}
