"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, HardDrive } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";
import { type Hop, RouteTrace, pathHops } from "@/components/route-trace";
import { CONN_KIND, ConnState, HOST_KIND, HostIcon, HostState, NetworkMatch, PctBar, WORKLOAD_KIND, WorkloadAddresses, WorkloadIcon, WorkloadState } from "@/components/servers";
import { KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, ProvenanceLine, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { bytes, duration } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ClientDetail, ServerHostDetail, ServerStorage, ServerWorkload } from "@/lib/types";

const STORAGE_KIND: Record<ServerStorage["kind"], string> = { datastore: "Datastore", array: "Array", disk: "Disk", pool: "Pool", filesystem: "Filesystem" };

function DiskState({ s }: { s: ServerStorage }) {
  if (s.healthy === false) return <StateLabel tone="crit">{s.status.replace(/^DISK_/, "").toLowerCase().replace(/_/g, " ") || "Unhealthy"}</StateLabel>;
  if (s.temperature_c != null && s.temperature_c >= 50) return <StateLabel tone="warn">{`Hot · ${s.temperature_c} °C`}</StateLabel>;
  if (s.healthy) return <StateLabel tone="ok">{s.kind === "disk" ? "OK" : s.kind === "array" ? "Started" : "Available"}</StateLabel>;
  return <span className="text-sm text-ink-2">{s.status || "—"}</span>;
}

export default function HostPage() {
  const { id } = useParams<{ id: string }>();
  const { moduleEnabled } = useSession();
  const q = useQuery({ queryKey: ["servers", "host", id], queryFn: () => get<ServerHostDetail>(`/api/servers/hosts/${id}`), refetchInterval: 60_000 });
  const clientId = q.data?.client?.id;
  // The route comes from Client Intelligence: the uplink path of the client this host is.
  const client = useQuery({
    queryKey: ["client", clientId],
    queryFn: () => get<ClientDetail>(`/api/client-intel/clients/${clientId}`),
    enabled: !!clientId && moduleEnabled("client-intel"),
  });
  if (q.error) return <Notice tone="crit">{(q.error as Error).message}</Notice>;
  const h = q.data;
  if (!h) {
    return (
      <>
        <Skeleton className="mb-6 h-8 w-64" />
        <Skeleton className="h-96" />
      </>
    );
  }
  const facts: [string, ReactNode][] = [
    ["On the network", <NetworkMatch key="n" client={h.client} />],
    ["Address", <span key="a" className="font-mono text-sm">{h.ip_address ?? "—"}</span>],
    ["MAC", h.mac_addresses.length ? <span key="m" className="font-mono text-xs">{h.mac_addresses.join(", ")}</span> : <span className="text-ink-3">Not reported</span>],
    ["CPU", <span key="c" className="inline-flex flex-wrap items-center gap-3"><PctBar pct={h.cpu_pct} />{h.cpu_count && <span className="text-sm text-ink-3">{h.cpu_count} threads</span>}{h.cpu_pct == null && h.kind === "linux" && <span className="text-sm text-ink-3">needs a second scrape</span>}</span>],
    ["Memory", h.mem_total ? <PctBar key="m" pct={h.mem_pct} label={`${bytes(h.mem_used)} of ${bytes(h.mem_total)}`} /> : "—"],
    ["Load (1 min)", h.load_1m != null ? <span key="l" className="tabular">{h.load_1m.toFixed(2)}</span> : "—"],
    ["Uptime", h.uptime_s != null ? duration(h.uptime_s) : "—"],
    ["Version", h.version || "—"],
  ];
  if (h.extra.cluster) facts.push(["Cluster", h.extra.cluster]);
  if (h.extra.array_state) facts.push(["Array", h.extra.array_state === "STARTED" ? <StateLabel key="ar" tone="ok">Started</StateLabel> : <StateLabel key="ar" tone="crit">{h.extra.array_state.toLowerCase().replace(/_/g, " ")}</StateLabel>]);
  const workloads = h.workload_items;
  const storage = h.storage_items;

  const wcols: Column<ServerWorkload>[] = [
    {
      key: "name",
      header: "Workload",
      sort: (w) => w.name.toLowerCase(),
      cell: (w) => (
        <span className="flex items-center gap-2">
          <WorkloadIcon kind={w.kind} className="text-ink-3" />
          <span className="flex min-w-0 flex-col">
            <span className="truncate font-bold">{w.name}{w.template && <span className="ml-2 text-sm font-normal text-ink-3">template</span>}</span>
            <span className="truncate text-sm text-ink-3">{WORKLOAD_KIND[w.kind]}{w.vmid != null ? ` ${w.vmid}` : ""}{w.image ? ` · ${w.image}` : ""}{w.tags.length ? ` · ${w.tags.join(", ")}` : ""}</span>
          </span>
        </span>
      ),
    },
    { key: "status", header: "Status", sort: (w) => w.status, className: "whitespace-nowrap", cell: (w) => <WorkloadState w={w} /> },
    { key: "cpu", header: "CPU", sort: (w) => w.cpu_pct, cell: (w) => (w.kind === "container" ? <span className="text-ink-3">—</span> : <PctBar pct={w.cpu_pct} label={w.cpu_count ? `${w.cpu_count} vCPU` : undefined} />) },
    { key: "mem", header: "Memory", sort: (w) => w.mem_pct, cell: (w) => (w.mem_max && w.status === "running" ? <PctBar pct={w.mem_pct ?? 0} label={bytes(w.mem_max)} /> : w.mem_max ? <span className="text-sm text-ink-3 tabular">{bytes(w.mem_max)} allocated</span> : <span className="text-ink-3">—</span>) },
    {
      key: "addr",
      header: "Addresses",
      cell: (w) => <WorkloadAddresses w={w} hostIp={h.ip_address} />,
    },
    {
      key: "net",
      header: "On the network",
      cell: (w) => <NetworkMatch client={w.client} compact expected={w.kind !== "container" && w.ip_addresses.length > 0} emphasis={w.status === "running" && w.kind !== "container"} />,
    },
    { key: "auto", header: "Autostart", sort: (w) => (w.autostart ? 1 : 0), cell: (w) => (w.autostart == null ? <span className="text-ink-3">Unknown</span> : w.autostart ? "Yes" : "No") },
  ];
  const scols: Column<ServerStorage>[] = [
    {
      key: "name",
      header: "Storage",
      sort: (s) => s.name,
      cell: (s) => (
        <span className="flex flex-col">
          <span className="font-bold">{s.name}</span>
          <span className="text-sm text-ink-3">{STORAGE_KIND[s.kind]}{s.extra.role ? ` · ${s.extra.role}` : ""}{s.extra.plugin ? ` · ${s.extra.plugin}` : ""}{s.shared ? " · shared" : ""}{s.extra.device ? ` · ${s.extra.device}` : ""}</span>
        </span>
      ),
    },
    {
      key: "use",
      header: "Used",
      sort: (s) => s.used_pct,
      cell: (s) =>
        s.used_pct != null ? (
          <PctBar pct={s.used_pct} label={`${bytes(s.used_bytes)} of ${bytes(s.total_bytes)}`} />
        ) : s.total_bytes ? (
          <span className="text-sm text-ink-3 tabular">{bytes(s.total_bytes)} size · usage not reported</span>
        ) : (
          <span className="text-ink-3">—</span>
        ),
    },
    { key: "state", header: "State", sort: (s) => (s.healthy === false ? 0 : 1), className: "whitespace-nowrap", cell: (s) => <DiskState s={s} /> },
    { key: "temp", header: "Temperature", align: "right", sort: (s) => s.temperature_c, cell: (s) => (s.temperature_c != null ? <span className="tabular">{s.temperature_c} °C</span> : <span className="text-ink-3">—</span>) },
  ];

  return (
    <>
      <Link href="/servers" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
        <ChevronLeft className="size-4" aria-hidden /> Servers
      </Link>
      <PageHeader
        icon={<HardDrive />}
        title={h.name}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3">
            <HostState h={h} />
            <span className="inline-flex items-center gap-1.5"><HostIcon kind={h.kind} className="text-ink-3" />{HOST_KIND[h.kind]}</span>
            {h.os && <span>{h.os}</span>}
          </span>
        }
        meta={<ProvenanceLine p={h.provenance} />}
      />
      <div className="flex flex-col gap-[var(--gap)]">
        {client.data && (
          <Panel title="Route from the internet">
            <RouteTrace hops={hostRoute(client.data, h)} label={`Path from the internet to ${h.name}`} footer={`${h.name} is the network client ${client.data.alias || client.data.name}, attached to ${client.data.path[0]?.name ?? "an unreported device"}.`} />
          </Panel>
        )}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Panel title="Resources and identity">
            <KeyValue items={facts} />
          </Panel>
          <div className="flex flex-col gap-[var(--gap)]">
            {h.connection && (
              <Panel title="Read through">
                <KeyValue
                  items={[
                    ["Connection", h.connection.name],
                    ["API", CONN_KIND[h.connection.kind]],
                    ["State", <ConnState key="s" status={h.connection.status} />],
                  ]}
                />
                <p className="mt-3 text-sm text-ink-3">Read-only. Power and configuration actions stay in {CONN_KIND[h.connection.kind]}.</p>
              </Panel>
            )}
            {(h.extra.ups ?? []).map((u) => (
              <Panel key={u.name} title={`UPS · ${u.model || u.name}`}>
                <KeyValue
                  items={[
                    ["Status", u.status === "Online" ? <StateLabel key="s" tone="ok">On mains</StateLabel> : <StateLabel key="s" tone="warn">{u.status}</StateLabel>],
                    ["Battery", <span key="b" className="tabular">{u.charge_pct ?? "—"}%{u.battery_health && u.battery_health !== "Good" ? ` · ${u.battery_health}` : ""}</span>],
                    ["Runtime", u.runtime_s != null ? duration(u.runtime_s) : "—"],
                    ["Load", u.load_pct != null ? <PctBar key="l" pct={u.load_pct} /> : "—"],
                  ]}
                />
              </Panel>
            ))}
          </div>
        </div>
        {workloads.length > 0 && (
          <Panel title={`Workloads (${workloads.filter((w) => w.status === "running").length} of ${workloads.filter((w) => !w.template).length} running)`} bodyClassName="p-0">
            <DataTable caption="Workloads" rows={workloads} columns={wcols} initialSort={{ key: "name", dir: "asc" }} />
          </Panel>
        )}
        {storage.length > 0 && (
          <Panel title="Storage" bodyClassName="p-0">
            <DataTable caption="Storage" rows={storage} columns={scols} />
          </Panel>
        )}
        {h.is_demo && <p className="flex items-center gap-2 text-sm text-ink-3"><DemoTag /> Served by the built-in simulator, not a real host.</p>}
      </div>
    </>
  );
}

function hostRoute(c: ClientDetail, h: ServerHostDetail): Hop[] {
  return [...pathHops(c.path), { key: h.id, label: h.name, sub: h.ip_address ?? HOST_KIND[h.kind], tone: h.status === "online" ? "ok" : h.status === "offline" ? "crit" : "muted", current: true }];
}
