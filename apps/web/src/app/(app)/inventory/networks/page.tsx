"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import Link from "next/link";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, ZonePatch } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import type { NetworkRow } from "@/lib/types";

interface Firewall {
  zones: { id: string; name: string; origin: string | null; network_count: number }[];
  policies: {
    id: string; name: string; enabled: boolean; action: string; index: number | null; origin: string | null;
    logging_enabled: boolean; source_zone: string | null; destination_zone: string | null;
  }[];
}

export default function NetworksPage() {
  const nets = useQuery({ queryKey: ["networks"], queryFn: () => get<{ items: NetworkRow[] }>("/api/inventory/networks") });
  const fw = useQuery({ queryKey: ["firewall"], queryFn: () => get<Firewall>("/api/inventory/firewall") });

  const columns: Column<NetworkRow>[] = [
    { key: "vlan", header: "VLAN", align: "right", sort: (n) => n.vlan_id, cell: (n) => <span className="font-mono font-bold">{n.vlan_id ?? "—"}</span>, className: "w-16" },
    {
      key: "name",
      header: "Name",
      sort: (n) => n.name.toLowerCase(),
      cell: (n) => (
        <span className="font-bold">
          {n.name}
          {n.is_default && <span className="ml-2 text-xs font-semibold text-ink-3">default</span>}
          {!n.enabled && <span className="ml-2 text-xs font-semibold text-crit">disabled</span>}
        </span>
      ),
    },
    { key: "zone", header: "Zone", sort: (n) => n.zone?.name ?? null, cell: (n) => (n.zone ? <ZonePatch name={n.zone.name} /> : <span className="text-ink-3">—</span>) },
    { key: "subnet", header: "Subnet", sort: (n) => n.subnet, cell: (n) => <span className="font-mono text-sm">{n.subnet ?? "—"}</span> },
    {
      key: "dhcp",
      header: "DHCP",
      cell: (n) =>
        n.dhcp_range ? (
          <span className="font-mono text-sm">
            {n.dhcp_range.start} – {n.dhcp_range.stop.split(".").at(-1)}
          </span>
        ) : (
          <span className="text-ink-3">{n.dhcp_mode ? n.dhcp_mode.toLowerCase() : "—"}</span>
        ),
    },
    {
      key: "use",
      header: "Active clients / pool",
      sort: (n) => (n.dhcp_pool_size ? n.active_clients / n.dhcp_pool_size : null),
      cell: (n) => {
        const pct = n.dhcp_pool_size ? n.active_clients / n.dhcp_pool_size : null;
        return (
          <span className="flex items-center gap-3">
            <span className="tabular w-16 text-right">{n.active_clients}{n.dhcp_pool_size ? ` / ${n.dhcp_pool_size}` : ""}</span>
            {pct != null && (
              <span className="h-2 w-24 rounded-[2px] bg-surface-3" aria-hidden>
                <span className={clsx("block h-full rounded-[2px]", pct > 0.8 ? "bg-crit" : pct > 0.6 ? "bg-warn" : "bg-[#00704a]")} style={{ width: `${Math.max(pct * 100, 2)}%` }} />
              </span>
            )}
          </span>
        );
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="Networks"
        description="VLANs and subnets with their firewall zones. Client counts are matched by address; DHCP lease data isn't exposed by the official API."
        meta={nets.data?.items.some((n) => n.provenance.is_demo) ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Simulated controller</span> : undefined}
      />
      {nets.error && <Notice tone="crit" className="mb-4">{(nets.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        <Panel bodyClassName="p-0">
          <DataTable
            caption="Networks"
            rows={nets.data?.items}
            columns={columns}
            loading={nets.isLoading}
            href={(n) => `/inventory/clients?network=${n.id}`}
            initialSort={{ key: "vlan", dir: "asc" }}
            empty={<EmptyState title="No networks reported">Network details need UniFi Network 10.0 or newer. Check the capability list on the controller page.</EmptyState>}
          />
        </Panel>

        <Panel
          title="Firewall zones and policies"
          id="firewall"
          bodyClassName="p-0"
          actions={<span className="text-sm text-ink-3">Read-only inventory · analysis arrives with Firewall & Policy Manager</span>}
        >
          {fw.data && fw.data.zones.length > 0 ? (
            <>
              <div className="flex flex-wrap gap-2 border-b border-rule px-4 py-3">
                {fw.data.zones.map((z) => (
                  <span key={z.id} className="inline-flex items-center gap-1.5">
                    <ZonePatch name={z.name} />
                    <span className="text-sm text-ink-3">{z.network_count} net{z.network_count === 1 ? "" : "s"}</span>
                  </span>
                ))}
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-rule text-xs font-bold tracking-wide text-ink-2 uppercase">
                    <th scope="col" className="h-9 px-4">Policy</th>
                    <th scope="col" className="px-4">From → to</th>
                    <th scope="col" className="px-4">Action</th>
                    <th scope="col" className="px-4">Logging</th>
                    <th scope="col" className="px-4">Origin</th>
                  </tr>
                </thead>
                <tbody>
                  {fw.data.policies.map((p) => (
                    <tr key={p.id} className={clsx("border-b border-rule last:border-b-0", !p.enabled && "text-ink-3")}>
                      <th scope="row" className="h-[var(--row-h)] px-4 font-semibold">
                        {p.name}
                        {!p.enabled && <span className="ml-2 text-xs font-bold">disabled</span>}
                      </th>
                      <td className="px-4">
                        <span className={clsx("inline-flex items-center gap-1.5", !p.enabled && "opacity-60")}>
                          {p.source_zone && <ZonePatch name={p.source_zone} />}
                          <span aria-label="to">→</span>
                          {p.destination_zone && <ZonePatch name={p.destination_zone} />}
                        </span>
                      </td>
                      <td className="px-4">
                        <StateLabel tone={p.action === "ALLOW" ? "ok" : p.action === "BLOCK" ? "crit" : "warn"}>{p.action.toLowerCase()}</StateLabel>
                      </td>
                      <td className="px-4 text-ink-2">{p.logging_enabled ? "on" : "off"}</td>
                      <td className="px-4 text-sm text-ink-3">{p.origin === "SYSTEM_DEFINED" ? "built-in" : "custom"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <EmptyState title="No firewall data">
              Zone-based firewall reads need UniFi Network 10.1 or newer. <Link href="/controllers" className="text-accent hover:underline">Check controller capabilities</Link>.
            </EmptyState>
          )}
        </Panel>
      </div>
    </>
  );
}
