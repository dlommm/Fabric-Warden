"use client";

// Servers & virtualization vocabulary: host and workload state labels, a
// percentage bar in the usage-bar language, the network match line and the
// Client 360 "Server side" lookup.
import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Box, Container, HardDrive, MonitorCog, Server } from "lucide-react";
import Link from "next/link";
import { usageTone } from "@/components/ipam";
import { Chip, StateLabel } from "@/components/ui/signs";
import { get } from "@/lib/api";
import type { ClientRef, ConnectionKind, HostKind, ServerHost, ServerWorkload } from "@/lib/types";

export const HOST_KIND: Record<HostKind, string> = { "proxmox-node": "Proxmox node", unraid: "Unraid", docker: "Docker host", linux: "Linux host" };
export const CONN_KIND: Record<ConnectionKind, string> = { proxmox: "Proxmox VE", unraid: "Unraid", docker: "Docker Engine", node_exporter: "node_exporter" };
export const WORKLOAD_KIND: Record<ServerWorkload["kind"], string> = { vm: "VM", lxc: "LXC", container: "Container" };
const KIND_ICON = { vm: MonitorCog, lxc: Box, container: Container };

export function HostIcon({ kind, className }: { kind: HostKind; className?: string }) {
  const Icon = kind === "unraid" ? HardDrive : kind === "docker" ? Container : Server;
  return <Icon className={clsx("size-4 shrink-0", className)} aria-hidden />;
}

export function WorkloadIcon({ kind, className }: { kind: ServerWorkload["kind"]; className?: string }) {
  const Icon = KIND_ICON[kind];
  return <Icon className={clsx("size-4 shrink-0", className)} aria-hidden />;
}

export function HostState({ h }: { h: Pick<ServerHost, "status"> }) {
  if (h.status === "online") return <StateLabel tone="ok">Online</StateLabel>;
  if (h.status === "offline") return <StateLabel tone="crit">Offline</StateLabel>;
  return <StateLabel tone="muted">Unknown</StateLabel>;
}

export function WorkloadState({ w }: { w: Pick<ServerWorkload, "status" | "autostart" | "raw_status"> }) {
  if (w.status === "running") return <StateLabel tone="ok">Running</StateLabel>;
  if (w.status === "paused") return <StateLabel tone="warn">Paused</StateLabel>;
  // Stopped is only a problem when the workload is meant to come back on its own.
  if (w.status === "stopped") return w.autostart ? <StateLabel tone="warn">Stopped, starts automatically</StateLabel> : <StateLabel tone="muted">Stopped</StateLabel>;
  return <StateLabel tone="muted">{w.raw_status || "Other"}</StateLabel>;
}

export function ConnState({ status }: { status: string }) {
  if (status === "ok") return <StateLabel tone="ok">Connected</StateLabel>;
  if (status === "degraded") return <StateLabel tone="warn">Partial data</StateLabel>;
  if (status === "unreachable") return <StateLabel tone="crit">Unreachable</StateLabel>;
  return <StateLabel tone="muted">Not synced</StateLabel>;
}

/** A percentage in the usage-bar language, without used/usable figures. */
export function PctBar({ pct, label, size, reason }: { pct: number | null | undefined; label?: string; size?: "sm"; reason?: string }) {
  if (pct == null)
    return (
      <span className="text-ink-3" title={reason}>
        —{reason && <span className="sr-only"> {reason}</span>}
      </span>
    );
  const tone = usageTone(pct);
  return (
    <span className="inline-flex items-center gap-2.5 whitespace-nowrap">
      <span className={clsx("h-2 shrink-0 rounded-[2px] bg-surface-3", size === "sm" ? "w-14" : "w-20")} aria-hidden>
        <span className={clsx("block h-full rounded-[2px]", tone === "crit" ? "bg-crit" : tone === "warn" ? "bg-warn ring-1 ring-ink/40" : "bg-[#00704a]")} style={{ width: `${Math.min(100, Math.max(pct, pct > 0 ? 2 : 0))}%` }} />
      </span>
      <span className={clsx("tabular text-sm", tone === "ok" ? "text-ink-2" : tone === "warn" ? "font-bold text-warn-text" : "font-bold text-crit")}>
        {pct.toFixed(pct > 0 && pct < 10 ? 1 : 0)}%
      </span>
      {label && <span className="text-xs text-ink-3 tabular">{label}</span>}
    </span>
  );
}

const MATCH_WORD = { ip: "by IP", mac: "by MAC", hostname: "by name" };

/** Which network client this host or workload is, and how we know. */
/** `expected`: the record has an address, so no match means the network has not seen it (not "nothing to match"). */
export function NetworkMatch({ client, compact, expected, emphasis }: { client: ClientRef | null; compact?: boolean; expected?: boolean; emphasis?: boolean }) {
  if (!client) {
    if (compact && !expected) return <span className="text-sm text-ink-3">—</span>;
    return <span className={clsx("text-sm", emphasis ? "font-semibold text-warn-text" : "text-ink-3")}>{compact ? "Not seen" : "Not seen by the network"}</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <Link href={`/inventory/clients/${client.id}`} className="font-semibold text-accent hover:underline">
        {client.name}
      </Link>
      {!compact && client.ip_address && <span className="font-mono text-xs text-ink-2">{client.ip_address}</span>}
      {client.match && <Chip className="h-5 px-1.5 text-[11px]">{MATCH_WORD[client.match]}</Chip>}
      {!client.present && <span className="text-sm text-crit">not connected now</span>}
    </span>
  );
}

interface RunsOn {
  host: { id: string; name: string; kind: HostKind; status: string; match: string } | null;
  workload: { id: string; name: string; kind: ServerWorkload["kind"]; status: string; vmid: number | null; match: string; host: { id: string; name: string; kind: HostKind } | null } | null;
}

/** Client 360: the server-side identity of a network client, if Servers knows it. */
export function ServerSide({ clientId }: { clientId: string }) {
  const q = useQuery({ queryKey: ["servers", "runs-on", clientId], queryFn: () => get<RunsOn>(`/api/servers/clients/${clientId}`) });
  if (q.isError) return <span className="text-ink-3">Couldn&apos;t load the server side</span>;
  if (!q.data) return <span className="text-ink-3">Loading…</span>;
  const { host, workload } = q.data;
  if (workload)
    return (
      <span className="inline-flex flex-wrap items-center gap-x-1.5">
        <WorkloadIcon kind={workload.kind} className="text-ink-3" />
        <span className="font-semibold">{workload.name}</span>
        <span className="text-ink-2">
          ({WORKLOAD_KIND[workload.kind]}
          {workload.vmid != null ? ` ${workload.vmid}` : ""})
        </span>
        on
        {workload.host ? (
          <Link href={`/servers/hosts/${workload.host.id}`} className="font-semibold text-accent hover:underline">
            {workload.host.name}
          </Link>
        ) : (
          "an unknown host"
        )}
      </span>
    );
  if (host)
    return (
      <Link href={`/servers/hosts/${host.id}`} className="inline-flex items-center gap-1.5 font-semibold text-accent hover:underline">
        <HostIcon kind={host.kind} />
        {HOST_KIND[host.kind]} {host.name}
      </Link>
    );
  return <span className="text-ink-3">Not a known server or VM</span>;
}

/** A workload's reachable address: its own IPs, else its published ports on the host's address. Never a bare port. */
export function WorkloadAddresses({ w, hostIp }: { w: Pick<ServerWorkload, "ip_addresses" | "ports">; hostIp?: string | null }) {
  if (w.ip_addresses.length) return <span className="font-mono text-xs">{w.ip_addresses.join(", ")}</span>;
  const published = w.ports.filter((p) => p.public);
  if (published.length) return <span className="font-mono text-xs text-ink-2">{published.map((p) => `${hostIp ?? "host"}:${p.public}`).join(", ")}</span>;
  return <span className="text-sm text-ink-3">{w.ports.length ? "No published ports" : "—"}</span>;
}
