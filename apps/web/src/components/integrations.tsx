"use client";

/** Shared pieces for the ecosystem integrations (UniFi Protect, Home Assistant): connections, the add form, findings. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import { ConnState, NetworkMatch } from "@/components/servers";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { ApiError, del, get, post } from "@/lib/api";
import { ago } from "@/lib/format";
import type { Finding, IntegrationConnection, IntegrationFinding } from "@/lib/types";

export interface IntegrationSpec {
  /** API prefix, e.g. /api/protect */
  api: string;
  /** TanStack Query key root */
  qk: string;
  product: string;
  urlPlaceholder: string;
  urlHint: string;
  secretLabel: string;
  secretHint: string;
  /** What a successful test found, in words. */
  found: (r: TestResult) => string;
  /** What deleting a connection removes, in words. */
  deleteText: string;
}

export interface TestResult {
  ok: boolean;
  error?: string;
  hint?: string;
  version?: string;
  cameras?: number;
  entities?: number;
  states_seen?: number;
  notes?: string[];
}

export const toFinding = (f: IntegrationFinding, i: number, href?: (f: IntegrationFinding) => string | undefined): Finding => ({
  key: `${f.kind}:${f.target.id}:${i}`,
  kind: f.kind,
  severity: f.severity,
  title: f.title,
  detail: f.detail,
  refs: {},
  href: href?.(f),
});

/** The network client a camera or tracker was matched to (only present clients are matched). */
export function MatchedClient({ client, expected = true }: { client: { id: string; name: string; ip_address: string | null; match: "ip" | "mac" | "hostname" | null } | null; expected?: boolean }) {
  return <NetworkMatch client={client ? { ...client, mac_address: null, present: true } : null} compact expected={expected} />;
}

export function useIntegration(spec: IntegrationSpec, enabled: boolean) {
  const qc = useQueryClient();
  const conns = useQuery({ queryKey: [spec.qk, "connections"], queryFn: () => get<{ items: IntegrationConnection[] }>(`${spec.api}/connections`), enabled });
  const done = () => qc.invalidateQueries({ queryKey: [spec.qk] });
  const demo = useMutation({ mutationFn: () => post(`${spec.api}/demo`), onSuccess: done });
  const undemo = useMutation({ mutationFn: () => del(`${spec.api}/demo`), onSuccess: done });
  return { conns, demo, undemo, hasDemo: !!conns.data?.items.some((c) => c.is_demo) };
}

export function IntegrationConnections({ spec, items, loading, manage, summary }: { spec: IntegrationSpec; items?: IntegrationConnection[]; loading: boolean; manage: boolean; summary?: (c: IntegrationConnection) => ReactNode }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<IntegrationConnection | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: [spec.qk] });
  const sync = useMutation({ mutationFn: (id: string) => post(`${spec.api}/connections/${id}/sync`), onSuccess: done });
  const remove = useMutation({ mutationFn: (id: string) => del(`${spec.api}/connections/${id}`), onSuccess: done });
  return (
    <Panel title="Connections" bodyClassName="p-0">
      {loading ? (
        <div className="p-4"><Skeleton className="h-16" /></div>
      ) : !items?.length ? (
        <p className="px-4 py-3 text-ink-3">No connections yet.</p>
      ) : (
        <ul>
          {items.map((c) => (
            <li key={c.id} className="flex flex-col gap-2 border-b border-rule px-4 py-3 last:border-b-0 md:flex-row md:items-center md:gap-5">
              <span className="flex min-w-0 flex-col md:w-64 md:shrink-0">
                <span className="truncate font-bold">{c.name}</span>
                <span className="text-sm text-ink-3">{spec.product}{c.version ? <span className="font-mono text-xs"> {c.version}</span> : ""}</span>
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2">
                {c.base_url}
                {c.credential_fingerprint && <span className="text-ink-3"> · key {c.credential_fingerprint}</span>}
              </span>
              <span className="flex flex-col md:w-60">
                <ConnState status={c.status} />
                <span className="truncate text-sm text-ink-3" title={c.status_detail}>
                  {c.status_detail || (c.last_sync_at ? <>synced {ago(c.last_sync_at)}{summary ? <> · {summary(c)}</> : null}</> : "never synced")}
                </span>
              </span>
              {c.is_demo && <DemoTag className="self-start md:self-center" />}
              {manage && (
                <span className="flex gap-1">
                  <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={sync.isPending && sync.variables === c.id} onClick={() => sync.mutate(c.id)}>Sync</Button>
                  {!c.is_demo && <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Delete connection ${c.name}`} onClick={() => setPending(c)} />}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <Confirm open={pending !== null} onOpenChange={(o) => !o && setPending(null)} title={`Delete ${pending?.name}?`} confirmLabel="Delete connection" onConfirm={() => pending && remove.mutate(pending.id)}>
        {spec.deleteText}
      </Confirm>
    </Panel>
  );
}

export function IntegrationConnectionForm({ spec, onDone }: { spec: IntegrationSpec; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", base_url: "", secret: "", verify_tls: true, ca_bundle_pem: "" });
  const [result, setResult] = useState<TestResult | null>(null);
  const body = () => ({ base_url: f.base_url.trim(), secret: f.secret, verify_tls: f.verify_tls, ca_bundle_pem: f.ca_bundle_pem.trim() || null });
  const errorOf = (e: unknown) => {
    if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "error" in (e.detail as object)) setResult(e.detail as TestResult);
    else setResult({ ok: false, error: e instanceof ApiError ? e.message : "Couldn't connect." });
  };
  const test = useMutation({ mutationFn: () => post<TestResult>(`${spec.api}/connections/test`, body()), onSuccess: setResult, onError: errorOf });
  const save = useMutation({
    mutationFn: () => post(`${spec.api}/connections`, { ...body(), name: f.name.trim() || f.base_url.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [spec.qk] });
      onDone();
    },
    onError: errorOf,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const ready = f.base_url.trim().length > 0 && f.secret.length > 0;
  const https = f.base_url.startsWith("https");
  return (
    <Panel title={`Add a ${spec.product} connection`}>
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 xl:items-start">
        <Field label="Name" hint="Optional; defaults to the URL">{(p) => <Input {...p} value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
        <Field label="URL" hint={spec.urlHint} className="xl:col-span-2">
          {(p) => <Input {...p} className="font-mono" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder={spec.urlPlaceholder} />}
        </Field>
        <Field label={spec.secretLabel} hint={spec.secretHint}>
          {(p) => <Input {...p} type="password" autoComplete="off" className="font-mono" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />}
        </Field>
        {https && (
          <Field label="TLS" hint={f.verify_tls ? "Paste a CA certificate for a self-signed console" : "Only on a network you trust"}>
            {(p) => (
              <Select {...p} value={f.verify_tls ? "verify" : "skip"} onChange={(e) => setF({ ...f, verify_tls: e.target.value === "verify" })}>
                <option value="verify">Verify the certificate</option>
                <option value="skip">Don&apos;t verify</option>
              </Select>
            )}
          </Field>
        )}
        {https && f.verify_tls && (
          <Field label="CA certificate (PEM)" hint="Optional" className="md:col-span-2">
            {(p) => <Textarea {...p} className="min-h-20 font-mono text-xs" value={f.ca_bundle_pem} onChange={(e) => setF({ ...f, ca_bundle_pem: e.target.value })} placeholder="-----BEGIN CERTIFICATE-----" />}
          </Field>
        )}
        {result && (
          <div className="md:col-span-2 xl:col-span-4">
            {result.ok ? (
              <Notice tone="ok" title={`Connected${result.version ? ` · version ${result.version}` : ""}`}>{spec.found(result)}</Notice>
            ) : (
              <Notice tone="crit" title={result.error ?? "Couldn't connect"}>{result.hint}</Notice>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-4">
          <Button onClick={() => test.mutate()} loading={test.isPending} disabled={!ready}>Test</Button>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!ready}>Save and sync</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
