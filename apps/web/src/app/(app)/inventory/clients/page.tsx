"use client";

import { useQuery } from "@tanstack/react-query";
import { Laptop } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { FilterBar, SearchInput, Segmented } from "@/components/filters";
import { Select } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { ago } from "@/lib/format";
import type { ClientRow, NetworkRow } from "@/lib/types";

type Kind = "all" | "WIRED" | "WIRELESS" | "VPN";

export default function ClientsPage() {
  return (
    <Suspense>
      <Clients />
    </Suspense>
  );
}

function ipKey(ip: string | null) {
  return ip ? ip.split(".").map((p) => p.padStart(3, "0")).join(".") : null;
}

function Clients() {
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = (params.get("type") as Kind) ?? "all";
  const network = params.get("network") ?? "";
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (!v || v === "all") next.delete(k);
    else next.set(k, v);
    router.replace(`/inventory/clients${next.size ? `?${next}` : ""}`, { scroll: false });
  };
  const { data, isLoading, error } = useQuery({ queryKey: ["clients", ""], queryFn: () => get<{ items: ClientRow[] }>("/api/inventory/clients") });
  const nets = useQuery({ queryKey: ["networks"], queryFn: () => get<{ items: NetworkRow[] }>("/api/inventory/networks") });
  const all = data?.items;
  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return all?.filter(
      (c) =>
        (kind === "all" || c.type === kind) &&
        (!network || c.network?.id === network) &&
        (!term || [c.name, c.ip_address ?? "", c.mac_address ?? ""].some((x) => x.toLowerCase().includes(term))),
    );
  }, [all, q, kind, network]);
  const count = (k: Kind) => all?.filter((c) => k === "all" || c.type === k).length;

  const columns: Column<ClientRow>[] = [
    { key: "name", header: "Name", sort: (c) => c.name.toLowerCase(), cell: (c) => <span className="font-bold">{c.name}</span> },
    { key: "type", header: "Connection", sort: (c) => c.type, cell: (c) => <span className="text-ink-2">{c.type === "WIRELESS" ? "Wi-Fi" : c.type === "WIRED" ? "Wired" : c.type}</span> },
    { key: "ip", header: "IP address", sort: (c) => ipKey(c.ip_address), cell: (c) => <span className="font-mono text-sm">{c.ip_address ?? "—"}</span> },
    { key: "mac", header: "MAC", cell: (c) => <span className="font-mono text-sm text-ink-2">{c.mac_address ?? "—"}</span> },
    {
      key: "net",
      header: "Network",
      sort: (c) => c.network?.vlan_id ?? null,
      cell: (c) => (c.network ? <span>{c.network.name} <span className="text-ink-3">VLAN {c.network.vlan_id}</span></span> : <span className="text-ink-3">—</span>),
    },
    {
      key: "uplink",
      header: "Connected via",
      sort: (c) => c.uplink?.name ?? null,
      cell: (c) => (c.uplink ? <Link href={`/inventory/devices/${c.uplink.id}`} className="hover:underline">{c.uplink.name}</Link> : <span className="text-ink-3">—</span>),
    },
    { key: "access", header: "Access", cell: (c) => <span className={c.access_type === "GUEST" ? "font-semibold text-warn-text" : "text-ink-2"}>{c.access_type === "GUEST" ? "Guest" : "Default"}</span> },
    { key: "since", header: "Connected", align: "right", sort: (c) => c.connected_at, cell: (c) => <span className="text-ink-2">{ago(c.connected_at)}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Clients"
        description="Devices currently connected, as reported by the controller. History arrives with Client Intelligence."
        meta={all?.some((c) => c.provenance.is_demo) ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Simulated controller</span> : undefined}
      />
      {error && <Notice tone="crit" className="mb-4">{(error as Error).message}</Notice>}
      <Panel bodyClassName="p-0">
        <FilterBar>
          <SearchInput value={q} onChange={(v) => setParam("q", v)} placeholder="Name, IP or MAC" label="Filter clients" />
          <Segmented
            label="Connection"
            value={kind}
            onChange={(v) => setParam("type", v)}
            options={[
              { value: "all", label: "All", count: count("all") },
              { value: "WIRED", label: "Wired", count: count("WIRED") },
              { value: "WIRELESS", label: "Wi-Fi", count: count("WIRELESS") },
              { value: "VPN", label: "VPN", count: count("VPN") },
            ]}
          />
          <Select aria-label="Network" value={network} onChange={(e) => setParam("network", e.target.value)} className="w-auto min-w-44">
            <option value="">All networks</option>
            {nets.data?.items.map((n) => (
              <option key={n.id} value={n.id}>{n.name}{n.vlan_id != null ? ` (VLAN ${n.vlan_id})` : ""}</option>
            ))}
          </Select>
        </FilterBar>
        <DataTable
          caption="Clients"
          rows={rows}
          columns={columns}
          loading={isLoading}
          initialSort={{ key: "name", dir: "asc" }}
          empty={
            all && all.length === 0 ? (
              <EmptyState icon={<Laptop />} title="No clients connected">Clients appear here after a sync while they are online.</EmptyState>
            ) : (
              <EmptyState title="No clients match these filters" />
            )
          }
        />
      </Panel>
    </>
  );
}
