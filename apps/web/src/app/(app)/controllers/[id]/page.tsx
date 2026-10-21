"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Ban, Check, ChevronLeft, CircleDashed, Lock, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input } from "@/components/ui/field";
import { KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, controllerTone } from "@/components/ui/signs";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ago, dateTime, titleCase } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Capability, Controller } from "@/lib/types";

export default function ControllerPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useSession();
  const q = useQuery({ queryKey: ["controllers", id], queryFn: () => get<Controller>(`/api/controllers/${id}`) });
  const sync = useMutation({
    mutationFn: () => post<{ status: string; error: string | null; summary: { controllers?: { error?: string }[] } }>(`/api/controllers/${id}/sync`),
    onSettled: () => qc.invalidateQueries(),
  });
  const remove = useMutation({ mutationFn: () => del(`/api/controllers/${id}`), onSuccess: () => { qc.invalidateQueries(); router.replace("/controllers"); } });

  const c = q.data;
  if (q.error) return <Notice tone="crit" title="Couldn't load this controller">{(q.error as Error).message}</Notice>;
  if (!c) return <Skeleton className="h-64 w-full" />;

  const caps = Object.entries(c.capabilities.capabilities ?? {});
  const reads = caps.filter(([, v]) => !v.write);
  const writes = caps.filter(([, v]) => v.write);
  const syncErr = sync.data?.summary.controllers?.[0]?.error ?? sync.data?.error;

  return (
    <>
      <Link href="/controllers" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
        <ChevronLeft className="size-4" aria-hidden /> Controllers
      </Link>
      <PageHeader
        title={c.name}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <StateLabel tone={controllerTone(c.status)}>{titleCase(c.status)}</StateLabel>
            <span className="font-mono text-sm">{c.application_version ? `UniFi Network ${c.application_version}` : "Version unknown"}</span>
            {c.is_demo && <DemoTag />}
          </span>
        }
        actions={
          <>
            {can("jobs:run") && (
              <Button icon={<RefreshCw className={clsx(sync.isPending && "animate-spin")} />} onClick={() => sync.mutate()} disabled={sync.isPending}>
                Sync now
              </Button>
            )}
            {can("controllers:manage") && (
              <Confirm
                title={`Remove ${c.name}?`}
                confirmLabel="Remove controller"
                onConfirm={() => remove.mutate()}
                trigger={<Button variant="danger" icon={<Trash2 />}>Remove</Button>}
              >
                Its sites, devices and clients are deleted from Fabric Warden and the stored API key is destroyed. Nothing changes on the controller itself. The audit trail keeps a record.
              </Confirm>
            )}
          </>
        }
      />
      {c.status_detail && <Notice tone={controllerTone(c.status) === "crit" ? "crit" : "warn"} className="mb-4" title="Last sync problem">{c.status_detail}</Notice>}
      {sync.isSuccess && !syncErr && <Notice tone="ok" className="mb-4">Sync completed.</Notice>}
      {syncErr && <Notice tone="crit" className="mb-4" title="Sync failed">{syncErr}</Notice>}

      <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Connection" id="connection">
          <KeyValue
            items={[
              ["Address", <span key="a" className="font-mono">{c.is_demo ? "Built-in simulator (no network access)" : c.base_url}</span>],
              ["Write mode", <span key="w" className="inline-flex items-center gap-1.5 font-semibold"><Lock className="size-4 text-ok" aria-hidden /> Read-only</span>],
              ["TLS", c.is_demo ? "—" : c.verify_tls ? (c.has_ca_bundle ? "Verified against pinned CA" : "Verified (system trust)") : "Not verified"],
              ["API key", c.credential_fingerprint ? <span key="k" className="font-mono text-sm">fingerprint {c.credential_fingerprint}</span> : "—"],
              ["Poll interval", `${c.poll_interval_seconds} s`],
              ["Sites / devices / clients", `${c.site_count} / ${c.device_count} / ${c.client_count}`],
              ["Last sync", c.last_sync_at ? `${dateTime(c.last_sync_at)} (${ago(c.last_sync_at)})` : "Never"],
              ["Capabilities checked", c.capabilities_checked_at ? dateTime(c.capabilities_checked_at) : "Not yet"],
            ]}
          />
          <p className="mt-4 text-sm text-ink-3">
            Writes stay disabled until the Change Approval & Recovery engine ships. Even then, every change will need an explicit, audited approval.
          </p>
        </Panel>
        {can("controllers:manage") && <EditPanel c={c} />}
      </div>

      <Panel
        title="What this controller supports"
        id="capabilities"
        className="mt-[var(--gap)]"
        bodyClassName="p-0"
        actions={c.capabilities.newest_known_spec && <span className="text-sm text-ink-3">Known API specs up to {c.capabilities.newest_known_spec}</span>}
      >
        {c.capabilities.beyond_known_spec && (
          <Notice tone="info" className="m-4">This controller is newer than the newest API spec Fabric Warden knows. Capabilities are probed, not assumed.</Notice>
        )}
        {caps.length === 0 ? (
          <p className="p-4 text-ink-3">Run a sync to detect capabilities.</p>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2">
            <CapList title="Read" items={reads} />
            <CapList title="Write (gated by the change engine)" items={writes} className="border-t border-rule lg:border-t-0 lg:border-l" />
          </div>
        )}
        {c.capabilities.not_exposed && (
          <div className="border-t border-rule px-4 py-4">
            <h3 className="font-bold">Not exposed by the official API at any known version</h3>
            <p className="mt-1 text-sm text-ink-3">These need other collectors (syslog, SNMPv3, restricted SSH) planned for later modules. Fabric Warden won&apos;t pretend to have them.</p>
            <ul className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
              {c.capabilities.not_exposed.map((n) => (
                <li key={n.id} className="flex items-center gap-2 text-base text-ink-2">
                  <Ban className="size-4 shrink-0 text-ink-3" aria-hidden /> {n.title}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Panel>
    </>
  );
}

function CapList({ title, items, className }: { title: string; items: [string, Capability][]; className?: string }) {
  return (
    <div className={className}>
      <h3 className="border-b border-rule px-4 py-2 text-xs font-bold tracking-wide text-ink-2 uppercase">{title}</h3>
      <ul>
        {items.map(([id, cap]) => {
          const ok = cap.status === "supported";
          return (
            <li key={id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-4 py-1.5 last:border-b-0">
              {ok ? <Check className="size-4 shrink-0 text-ok" strokeWidth={2.5} aria-label="Supported" /> : cap.status === "unknown" ? <CircleDashed className="size-4 shrink-0 text-ink-3" aria-label="Unknown" /> : <Ban className="size-4 shrink-0 text-ink-3" aria-label="Not supported" />}
              <span className={clsx("flex-1", !ok && "text-ink-3")}>{cap.title}</span>
              {ok && !cap.write && (
                <span className={clsx("text-xs font-semibold", cap.verified ? "text-ok" : "text-ink-3")}>{cap.verified ? "verified" : "per spec"}</span>
              )}
              {cap.write && ok && <span className="text-xs font-semibold text-ink-3">disabled</span>}
              {!ok && cap.status === "unsupported" && <span className="font-mono text-xs text-ink-3">needs {cap.min_version}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EditPanel({ c }: { c: Controller }) {
  const qc = useQueryClient();
  const [name, setName] = useState(c.name);
  const [interval, setIntervalS] = useState(String(c.poll_interval_seconds));
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "crit"; text: string } | null>(null);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Controller>(`/api/controllers/${c.id}`, body),
    onSuccess: () => { setMsg({ tone: "ok", text: "Saved." }); setKey(""); qc.invalidateQueries({ queryKey: ["controllers"] }); },
    onError: (e) => setMsg({ tone: "crit", text: e instanceof ApiError ? e.message : "Couldn't save." }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = {};
    if (name !== c.name) body.name = name;
    if (Number(interval) !== c.poll_interval_seconds) body.poll_interval_seconds = Number(interval);
    if (key) body.api_key = key;
    save.mutate(body);
  };
  return (
    <Panel title="Settings" id="settings">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Poll interval (seconds)" hint="15 to 3600. Scheduled sync currently runs every 60 s for all controllers.">
          {(p) => <Input {...p} type="number" min={15} max={3600} value={interval} onChange={(e) => setIntervalS(e.target.value)} className="max-w-32" />}
        </Field>
        {!c.is_demo && (
          <Field label="Rotate API key" hint="The new key is tested before it replaces the old one.">
            {(p) => <Input {...p} type="password" autoComplete="off" className="font-mono" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste a new key" />}
          </Field>
        )}
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <div>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={name === c.name && Number(interval) === c.poll_interval_seconds && !key}>
            Save changes
          </Button>
        </div>
      </form>
    </Panel>
  );
}
