"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, Router } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { FilterBar, SearchInput, Segmented } from "@/components/filters";
import { Column, DataTable } from "@/components/ui/table";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag, DeviceState, deviceTone } from "@/components/ui/signs";
import { get } from "@/lib/api";
import { ROLE_LABEL, ageLabel, deviceRole, uptime } from "@/lib/format";
import type { DeviceSummary } from "@/lib/types";

type Role = "all" | "gateway" | "switching" | "accessPoint";
type StateFilter = "all" | "attention";

export default function DevicesPage() {
  return (
    <Suspense>
      <Devices />
    </Suspense>
  );
}

function Devices() {
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const role = (params.get("role") as Role) ?? "all";
  const state = (params.get("state") as StateFilter) ?? "all";
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (!v || v === "all") next.delete(k);
    else next.set(k, v);
    router.replace(`/inventory/devices${next.size ? `?${next}` : ""}`, { scroll: false });
  };

  const { data, isLoading, error } = useQuery({ queryKey: ["devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });
  const all = data?.items;
  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return all?.filter(
      (d) =>
        (role === "all" || deviceRole(d.features) === role) &&
        (state === "all" || deviceTone(d.state) !== "ok" || d.firmware_updatable) &&
        (!term || [d.name, d.model, d.mac_address, d.ip_address ?? ""].some((x) => x.toLowerCase().includes(term))),
    );
  }, [all, q, role, state]);
  const count = (r: Role) => all?.filter((d) => r === "all" || deviceRole(d.features) === r).length;
  const demo = all?.some((d) => d.provenance.is_demo);

  const columns: Column<DeviceSummary>[] = [
    {
      key: "name",
      header: "Name",
      sort: (d) => d.name.toLowerCase(),
      cell: (d) => (
        <Link href={`/inventory/devices/${d.id}`} className="font-bold hover:underline">
          {d.name}
        </Link>
      ),
    },
    { key: "state", header: "State", sort: (d) => ["crit", "warn", "muted", "ok"].indexOf(deviceTone(d.state)), cell: (d) => <DeviceState state={d.state} /> },
    { key: "role", header: "Role", sort: (d) => deviceRole(d.features), cell: (d) => <span className="text-ink-2">{ROLE_LABEL[deviceRole(d.features)]}</span> },
    { key: "model", header: "Model", sort: (d) => d.model, cell: (d) => d.model },
    { key: "ip", header: "IP address", sort: (d) => d.ip_address?.split(".").map((p) => p.padStart(3, "0")).join(".") ?? null, cell: (d) => <span className="font-mono text-sm">{d.ip_address ?? "—"}</span> },
    { key: "mac", header: "MAC", cell: (d) => <span className="font-mono text-sm text-ink-2">{d.mac_address}</span> },
    {
      key: "fw",
      header: "Firmware",
      sort: (d) => d.firmware_version,
      cell: (d) => (
        <span className="inline-flex items-center gap-1.5 font-mono text-sm">
          {d.firmware_version ?? "—"}
          {d.firmware_updatable && (
            <span className="inline-flex items-center gap-0.5 font-sans text-xs font-bold text-accent" title="The controller reports a newer firmware">
              <ArrowUpCircle className="size-3.5" aria-hidden /> update
            </span>
          )}
        </span>
      ),
    },
    { key: "uptime", header: "Uptime", align: "right", sort: (d) => d.uptime_sec, cell: (d) => uptime(d.uptime_sec) },
    { key: "cpu", header: "CPU", align: "right", sort: (d) => d.cpu_pct, cell: (d) => (d.cpu_pct != null ? `${Math.round(d.cpu_pct)}%` : "—") },
    { key: "seen", header: "Observed", align: "right", sort: (d) => d.provenance.age_seconds, cell: (d) => <span className="text-ink-3">{ageLabel(d.provenance.age_seconds)} ago</span> },
  ];

  return (
    <>
      <PageHeader
        title="Devices"
        description="Gateways, switches and access points adopted on your controllers."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Simulated controller</span> : undefined}
      />
      {error && <Notice tone="crit" className="mb-4">{(error as Error).message}</Notice>}
      <Panel bodyClassName="p-0">
        <FilterBar>
          <SearchInput value={q} onChange={(v) => setParam("q", v)} placeholder="Name, model, IP or MAC" label="Filter devices" />
          <Segmented
            label="Role"
            value={role}
            onChange={(v) => setParam("role", v)}
            options={[
              { value: "all", label: "All", count: count("all") },
              { value: "gateway", label: "Gateways", count: count("gateway") },
              { value: "switching", label: "Switches", count: count("switching") },
              { value: "accessPoint", label: "APs", count: count("accessPoint") },
            ]}
          />
          <Segmented
            label="State"
            value={state}
            onChange={(v) => setParam("state", v)}
            options={[
              { value: "all", label: "Any state" },
              { value: "attention", label: "Needs attention" },
            ]}
          />
        </FilterBar>
        <DataTable
          caption="Devices"
          rows={rows}
          columns={columns}
          loading={isLoading}
          href={(d) => `/inventory/devices/${d.id}`}
          initialSort={{ key: "state", dir: "asc" }}
          empty={
            all && all.length === 0 ? (
              <EmptyState icon={<Router />} title="No devices yet">Devices appear after the first controller sync.</EmptyState>
            ) : (
              <EmptyState title="No devices match these filters">Clear the search or choose another role.</EmptyState>
            )
          }
        />
      </Panel>
    </>
  );
}
