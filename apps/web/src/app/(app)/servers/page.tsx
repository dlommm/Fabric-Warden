"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive, Plus, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useMemo, useState } from "react";
import { FindingsPlate } from "@/components/ipam";
import { CONN_KIND, ConnState, HOST_KIND, HostIcon, HostState, NetworkMatch, PctBar, WORKLOAD_KIND, WorkloadAddresses, WorkloadIcon, WorkloadState } from "@/components/servers";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ConnectionKind, Controller, Finding, HostConnection, ServerHost, ServerWorkload } from "@/lib/types";

interface ServerFinding {
  key: string;
  kind: string;
  severity: Finding["severity"];
  title: string;
  detail?: string;
  host_id?: string;
  connection_id?: string;
}

const toFinding = (f: ServerFinding): Finding => ({ key: f.key, kind: f.kind, severity: f.severity, title: f.title, detail: f.detail, refs: {}, href: f.host_id ? `/servers/hosts/${f.host_id}` : undefined });

const NO_CPU: Partial<Record<ServerHost["kind"], string>> = { docker: "Not reported by Docker Engine", linux: "Needs a second scrape" };
const NO_MEM: Partial<Record<ServerHost["kind"], string>> = { docker: "Not reported by Docker Engine" };

export default function ServersPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("servers");
  const manage = can("servers:manage");
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [confirmDemo, setConfirmDemo] = useState(false);
  const hosts = useQuery({ queryKey: ["servers", "hosts"], queryFn: () => get<{ items: ServerHost[] }>("/api/servers/hosts"), enabled: on, refetchInterval: 60_000 });
  const conns = useQuery({ queryKey: ["servers", "connections"], queryFn: () => get<{ items: HostConnection[] }>("/api/servers/connections"), enabled: on });
  const findings = useQuery({ queryKey: ["servers", "findings"], queryFn: () => get<{ items: ServerFinding[] }>("/api/servers/findings"), enabled: on });
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers"), enabled: on });
  const done = () => qc.invalidateQueries({ queryKey: ["servers"] });
  const demo = useMutation({ mutationFn: () => post("/api/servers/demo"), onSuccess: done });
  const undemo = useMutation({ mutationFn: () => del("/api/servers/demo"), onSuccess: done });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Servers" name="Servers & Virtualization" canManage={can("modules:manage")}>
        Read Proxmox VE, Unraid, Docker hosts and Linux hosts, and see which network client each server and VM is.
      </ModuleOff>
    );
  }
  const items = hosts.data?.items;
  const hasDemo = !!conns.data?.items.some((c) => c.is_demo);
  const demoAvailable = !!controllers.data?.some((c) => c.is_demo);

  const columns: Column<ServerHost>[] = [
    {
      key: "name",
      header: "Host",
      sort: (h) => h.name.toLowerCase(),
      cell: (h) => (
        <span className="flex min-w-0 items-center gap-2">
          <HostIcon kind={h.kind} className="text-ink-3" />
          <span className="flex min-w-0 max-w-[15rem] flex-col">
            <Link href={`/servers/hosts/${h.id}`} className="truncate font-bold hover:underline">{h.name}</Link>
            <span className="truncate text-sm text-ink-3" title={h.os}>{HOST_KIND[h.kind]}</span>
          </span>
        </span>
      ),
    },
    { key: "status", header: "Status", sort: (h) => h.status, className: "whitespace-nowrap", cell: (h) => <HostState h={h} /> },
    { key: "net", header: "On the network", cell: (h) => <NetworkMatch client={h.client} compact expected={!!h.ip_address} /> },
    { key: "cpu", header: "CPU", sort: (h) => h.cpu_pct, cell: (h) => <PctBar size="sm" pct={h.cpu_pct} reason={NO_CPU[h.kind]} /> },
    { key: "mem", header: "Memory", sort: (h) => h.mem_pct, cell: (h) => <PctBar size="sm" pct={h.mem_pct} reason={NO_MEM[h.kind]} /> },
    {
      key: "wl",
      header: "Workloads",
      align: "right",
      sort: (h) => h.workloads.total,
      cell: (h) => (h.workloads.total ? <span className="tabular">{h.workloads.running} <span className="text-ink-3">of {h.workloads.total} running</span></span> : <span className="text-ink-3">—</span>),
    },
    {
      key: "disk",
      header: "Fullest storage",
      sort: (h) => h.storage_worst?.used_pct ?? null,
      cell: (h) => (
        <span className="flex flex-col">
          {h.storage_worst ? (
            <>
              <PctBar size="sm" pct={h.storage_worst.used_pct} />
              <span className="max-w-[8rem] truncate text-xs text-ink-3" title={h.storage_worst.name}>{h.storage_worst.name}</span>
            </>
          ) : (
            <span className="text-ink-3">—</span>
          )}
          {!!h.storage_unhealthy && <StateLabel tone="crit" className="text-sm">{`${h.storage_unhealthy} disk${h.storage_unhealthy > 1 ? "s" : ""} failing`}</StateLabel>}
        </span>
      ),
    },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (h) => (h.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Servers"
        description="Hypervisors, NAS, Docker hosts and Linux hosts, read through their own APIs and matched to the clients the network reports. Read-only: nothing here starts, stops or changes a workload."
        meta={hasDemo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes simulated servers</span> : undefined}
        actions={
          manage && (
            <>
              {hasDemo ? (
                <Button variant="ghost" loading={undemo.isPending} onClick={() => setConfirmDemo(true)}>Remove demo servers</Button>
              ) : (
                demoAvailable && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Load demo servers</Button>
              )}
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add connection</Button>
            </>
          )
        }
      />
      {(hosts.error || demo.error) && <Notice tone="crit" className="mb-4">{((hosts.error || demo.error) as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {adding && <ConnectionForm onDone={() => setAdding(false)} />}
        {findings.data && !!items?.length && (
          <FindingsPlate
            label="Server findings"
            findings={findings.data.items.map(toFinding)}
            emptyText="Every server is up, every autostart workload is running and storage has room."
            heading={(n) => `${n} server problem${n > 1 ? "s" : ""}`}
          />
        )}
        <Panel bodyClassName="p-0">
          <DataTable
            caption="Servers"
            rows={items}
            columns={columns}
            loading={hosts.isLoading}
            href={(h) => `/servers/hosts/${h.id}`}
            initialSort={{ key: "name", dir: "asc" }}
            empty={
              <EmptyState icon={<HardDrive />} title="No servers connected">
                Add a Proxmox VE API token, an Unraid API key, a Docker socket proxy or a node_exporter address. Fabric Warden only reads from them.
              </EmptyState>
            }
          />
        </Panel>
        {!!items?.length && <Workloads hostIps={new Map(items.map((h) => [h.id, h.ip_address]))} />}
        <Connections items={conns.data?.items} loading={conns.isLoading} manage={manage} />
      </div>
      <Confirm open={confirmDemo} onOpenChange={setConfirmDemo} title="Remove the demo servers?" confirmLabel="Remove demo servers" onConfirm={() => undemo.mutate()}>
        The four simulated connections and everything collected from them are deleted. Your own connections stay.
      </Confirm>
    </>
  );
}

function Workloads({ hostIps }: { hostIps: Map<string, string | null> }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const list = useQuery({ queryKey: ["servers", "workloads"], queryFn: () => get<{ items: ServerWorkload[] }>("/api/servers/workloads"), refetchInterval: 60_000 });
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return list.data?.items.filter(
      (w) =>
        !w.template &&
        (!kind || w.kind === kind) &&
        (!t || [w.name, w.image, w.host ?? "", String(w.vmid ?? ""), ...w.ip_addresses, ...w.ports.map((p) => String(p.public ?? p.private ?? ""))].some((v) => v.toLowerCase().includes(t))),
    );
  }, [list.data, q, kind]);
  const columns: Column<ServerWorkload>[] = [
    {
      key: "name",
      header: "Workload",
      sort: (w) => w.name.toLowerCase(),
      cell: (w) => (
        <span className="flex items-center gap-2">
          <WorkloadIcon kind={w.kind} className="text-ink-3" />
          <span className="flex flex-col">
            <span className="font-bold">{w.name}</span>
            <span className="text-sm text-ink-3">{WORKLOAD_KIND[w.kind]}{w.vmid != null ? ` ${w.vmid}` : ""}{w.image ? ` · ${w.image}` : ""}</span>
          </span>
        </span>
      ),
    },
    { key: "host", header: "Host", sort: (w) => w.host ?? "", className: "whitespace-nowrap", cell: (w) => <Link href={`/servers/hosts/${w.host_id}`} className="font-semibold hover:underline">{w.host}</Link> },
    { key: "status", header: "Status", sort: (w) => w.status, className: "whitespace-nowrap", cell: (w) => <WorkloadState w={w} /> },
    { key: "ip", header: "Addresses", cell: (w) => <WorkloadAddresses w={w} hostIp={hostIps.get(w.host_id)} /> },
    {
      key: "net",
      header: "On the network",
      cell: (w) => <NetworkMatch client={w.client} compact expected={w.kind !== "container" && w.ip_addresses.length > 0} emphasis={w.status === "running" && w.kind !== "container"} />,
    },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (w) => (w.is_demo ? <DemoTag /> : null) },
  ];
  return (
    <Panel title="Workloads" bodyClassName="p-0">
      <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
        <Field label="Search" className="w-full sm:w-72">{(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, image, VM ID, address" />}</Field>
        <Field label="Kind" className="w-40">
          {(p) => (
            <Select {...p} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">All kinds</option>
              <option value="vm">VMs</option>
              <option value="lxc">LXC</option>
              <option value="container">Containers</option>
            </Select>
          )}
        </Field>
        <span className="ml-auto self-center text-sm text-ink-3 tabular">{rows ? `${rows.length} shown` : ""}</span>
      </div>
      <DataTable caption="Workloads" rows={rows} columns={columns} loading={list.isLoading} initialSort={{ key: "host", dir: "asc" }} empty={<EmptyState title="No workloads match">Clear the search or kind filter.</EmptyState>} />
    </Panel>
  );
}

function Connections({ items, loading, manage }: { items?: HostConnection[]; loading: boolean; manage: boolean }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<HostConnection | null>(null);
  const sync = useMutation({ mutationFn: (id: string) => post(`/api/servers/connections/${id}/sync`), onSuccess: () => qc.invalidateQueries({ queryKey: ["servers"] }) });
  const remove = useMutation({ mutationFn: (id: string) => del(`/api/servers/connections/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["servers"] }) });
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
                <span className="text-sm text-ink-3">{CONN_KIND[c.kind as ConnectionKind]}{c.version ? ` ${c.version}` : ""}</span>
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2">{c.base_url}{c.token_id ? ` · ${c.token_id}` : ""}</span>
              <span className="flex flex-col md:w-56">
                <ConnState status={c.status} />
                <span className="truncate text-sm text-ink-3" title={c.status_detail}>{c.status_detail || (c.last_sync_at ? `synced ${ago(c.last_sync_at)} · ${c.host_count} host${c.host_count === 1 ? "" : "s"}, ${c.workload_count} workloads` : "never synced")}</span>
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
        Fabric Warden stops reading this host and deletes what it collected from it, along with the stored credential. Nothing changes on the host.
      </Confirm>
    </Panel>
  );
}

const DEFAULT_URL: Record<ConnectionKind, string> = { proxmox: "https://pve.lan:8006", unraid: "https://tower.lan", docker: "http://docker-host.lan:2375", node_exporter: "http://server.lan:9100" };

function ConnectionForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ kind: "proxmox" as ConnectionKind, name: "", base_url: "", token_id: "", secret: "", verify_tls: true, ca_bundle_pem: "" });
  const [result, setResult] = useState<{ ok: boolean; error?: string; hint?: string; version?: string; hosts?: string[]; workloads?: number } | null>(null);
  const body = () => ({ kind: f.kind, base_url: f.base_url.trim(), token_id: f.token_id.trim(), secret: f.secret, verify_tls: f.verify_tls, ca_bundle_pem: f.ca_bundle_pem.trim() || null });
  const errorOf = (e: unknown) => {
    if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "error" in (e.detail as object)) setResult(e.detail as typeof result);
    else setResult({ ok: false, error: e instanceof ApiError ? e.message : "Couldn't connect." });
  };
  const test = useMutation({ mutationFn: () => post<typeof result>("/api/servers/connections/test", body()), onSuccess: setResult, onError: errorOf });
  const save = useMutation({
    mutationFn: () => post("/api/servers/connections", { ...body(), name: f.name.trim() || f.base_url.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["servers"] });
      onDone();
    },
    onError: errorOf,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const secretLabel = f.kind === "proxmox" ? "Token secret" : f.kind === "unraid" ? "API key" : "Bearer token (optional)";
  return (
    <Panel title="Add a connection">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 xl:items-start">
        <Field label="Kind">
          {(p) => (
            <Select {...p} value={f.kind} onChange={(e) => { setF({ ...f, kind: e.target.value as ConnectionKind }); setResult(null); }}>
              {Object.entries(CONN_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Name" hint="Optional; defaults to the URL">{(p) => <Input {...p} value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Proxmox cluster" />}</Field>
        <Field label="URL" hint={f.kind === "docker" ? "A read-only socket proxy, or unix:///var/run/docker.sock" : undefined} className="xl:col-span-2">
          {(p) => <Input {...p} className="font-mono" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder={DEFAULT_URL[f.kind]} />}
        </Field>
        {f.kind === "proxmox" && (
          <Field label="Token ID" hint="USER@REALM!TOKENID, with the PVEAuditor role">
            {(p) => <Input {...p} className="font-mono" value={f.token_id} onChange={(e) => setF({ ...f, token_id: e.target.value })} placeholder="monitor@pve!fabricwarden" />}
          </Field>
        )}
        <Field label={secretLabel} hint="Stored encrypted; never shown again">
          {(p) => <Input {...p} type="password" autoComplete="off" className="font-mono" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />}
        </Field>
        {f.base_url.startsWith("https") && (
          <Field label="TLS" hint={f.verify_tls ? "Paste a CA certificate below for self-signed hosts" : "Only on a network you trust"}>
            {(p) => (
              <Select {...p} value={f.verify_tls ? "verify" : "skip"} onChange={(e) => setF({ ...f, verify_tls: e.target.value === "verify" })}>
                <option value="verify">Verify the certificate</option>
                <option value="skip">Don&apos;t verify</option>
              </Select>
            )}
          </Field>
        )}
        {f.base_url.startsWith("https") && f.verify_tls && (
          <Field label="CA certificate (PEM)" hint="Optional" className="md:col-span-2">
            {(p) => <Textarea {...p} className="min-h-20 font-mono text-xs" value={f.ca_bundle_pem} onChange={(e) => setF({ ...f, ca_bundle_pem: e.target.value })} placeholder="-----BEGIN CERTIFICATE-----" />}
          </Field>
        )}
        {result && (
          <div className="md:col-span-2 xl:col-span-4">
            {result.ok ? (
              <Notice tone="ok" title={`Connected${result.version ? ` · version ${result.version}` : ""}`}>
                Found {result.hosts?.join(", ")} and {result.workloads} workload{result.workloads === 1 ? "" : "s"}.
              </Notice>
            ) : (
              <Notice tone="crit" title={result.error ?? "Couldn't connect"}>{result.hint}</Notice>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-4">
          <Button onClick={() => test.mutate()} loading={test.isPending} disabled={!f.base_url.trim()}>Test</Button>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!f.base_url.trim()}>Save and sync</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
