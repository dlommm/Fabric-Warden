"use client";

// SNMP port counters vocabulary: target and link state labels, per-minute
// counter cells, the interface table shared by the Port counters page and the
// device page, and the device page's per-port history.
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { MetricWidget, RangePicker } from "@/components/analytics";
import { PctBar } from "@/components/servers";
import { Field, Select } from "@/components/ui/field";
import { EmptyState, Notice } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { clsx } from "clsx";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { ago, bps, speed } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { RangeKey, SnmpInterface, SnmpTarget, Widget } from "@/lib/types";

export function TargetState({ t }: { t: Pick<SnmpTarget, "status" | "stale" | "enabled"> }) {
  if (!t.enabled) return <StateLabel tone="muted">Paused</StateLabel>;
  if (t.status === "ok") return t.stale ? <StateLabel tone="warn">Stale</StateLabel> : <StateLabel tone="ok">Polling</StateLabel>;
  if (t.status === "degraded") return <StateLabel tone="warn">Failing</StateLabel>;
  if (t.status === "unreachable") return <StateLabel tone="crit">No answer</StateLabel>;
  return <StateLabel tone="muted">Not polled</StateLabel>;
}

export function LinkState({ i }: { i: Pick<SnmpInterface, "oper_status" | "admin_up"> }) {
  if (i.admin_up === false) return <StateLabel tone="muted">Disabled</StateLabel>;
  if (i.oper_status === "up") return <StateLabel tone="ok">Up</StateLabel>;
  if (i.oper_status === "down" || i.oper_status === "lowerLayerDown") return <StateLabel tone="muted">Down</StateLabel>;
  return <StateLabel tone="muted">{i.oper_status === "notPresent" ? "Not present" : i.oper_status}</StateLabel>;
}

export function perMinute(v: number): string {
  return String(v >= 10 ? Math.round(v) : Number(v.toFixed(1)));
}

/** A value that exists only from the second poll: a dash, spelled out for screen readers. */
function NotYet() {
  return (
    <span className="text-ink-3" title="Needs a second poll">
      <span aria-hidden>—</span>
      <span className="sr-only">no value until the second poll</span>
    </span>
  );
}

/** A counter delta per minute. Errors are a warning, discards a note; zero recedes; no value yet says why. */
export function PerMinute({ v, kind }: { v: number | null; kind: "errors" | "discards" }) {
  if (v == null) return <NotYet />;
  if (v === 0) return <span className="tabular text-ink-3">0</span>;
  const text = `${perMinute(v)}/min`;
  return kind === "errors" ? <StateLabel tone="warn" className="tabular">{text}</StateLabel> : <StateLabel tone="info" className="tabular">{text}</StateLabel>;
}

function Rate({ v, up }: { v: number | null; up: boolean }) {
  if (!up) return <span className="text-ink-3">—</span>;
  return v == null ? <NotYet /> : <span className="tabular">{bps(v)}</span>;
}

function portName(i: SnmpInterface) {
  return i.alias ? `${i.name} · ${i.alias}` : i.name;
}

export function InterfaceTable({ rows, loading, withDevice, caption, empty }: { rows?: SnmpInterface[]; loading: boolean; withDevice?: boolean; caption: string; empty: React.ReactNode }) {
  const columns: Column<SnmpInterface>[] = [
    {
      key: "name",
      header: "Interface",
      sort: (i) => (withDevice ? `${i.device?.name ?? i.target?.name ?? ""} ${String(i.if_index).padStart(5, "0")}` : i.if_index),
      cell: (i) => (
        <span className="flex min-w-0 flex-col">
          {withDevice && (i.device ? <Link href={`/inventory/devices/${i.device.id}#ports`} className="truncate font-bold hover:underline">{i.device.name}</Link> : <span className="truncate font-bold">{i.target?.name}</span>)}
          <span className={withDevice ? "truncate text-sm text-ink-2" : "truncate font-bold"} title={i.descr !== i.name ? i.descr : undefined}>{portName(i)}</span>
        </span>
      ),
    },
    { key: "link", header: "Link", sort: (i) => i.oper_status, className: "whitespace-nowrap", cell: (i) => <LinkState i={i} /> },
    { key: "speed", header: "Speed", align: "right", sort: (i) => i.speed_mbps, cell: (i) => <span className="tabular">{i.oper_status === "up" ? `${speed(i.speed_mbps)}b/s` : "—"}</span> },
    { key: "rx", header: "Receive", align: "right", sort: (i) => i.rx_bps, cell: (i) => <Rate v={i.rx_bps} up={i.oper_status === "up"} /> },
    { key: "tx", header: "Transmit", align: "right", sort: (i) => i.tx_bps, cell: (i) => <Rate v={i.tx_bps} up={i.oper_status === "up"} /> },
    { key: "util", header: "Utilisation", sort: (i) => i.utilisation_pct, cell: (i) => (i.oper_status === "up" ? <PctBar size="sm" pct={i.utilisation_pct} reason="Needs a second poll" /> : <span className="text-ink-3">—</span>) },
    { key: "err", header: "Errors", align: "right", sort: (i) => i.errors_per_min, cell: (i) => <PerMinute v={i.errors_per_min} kind="errors" /> },
    { key: "disc", header: "Discards", align: "right", sort: (i) => i.discards_per_min, cell: (i) => <PerMinute v={i.discards_per_min} kind="discards" /> },
  ];
  if (withDevice) columns.push({ key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (i) => (i.is_demo ? <DemoTag /> : null) });
  return <DataTable caption={caption} rows={rows} columns={columns} loading={loading} initialSort={{ key: "name", dir: "asc" }} empty={empty} />;
}

/** The SNMP side of a device: per-port counters keyed by front-panel port number, for the port strip. */
export function useDeviceSnmp(deviceId: string) {
  const { moduleEnabled } = useSession();
  const on = moduleEnabled("snmp");
  const q = useQuery({
    queryKey: ["snmp", "device", deviceId],
    queryFn: () => get<{ target: SnmpTarget | null; interfaces: SnmpInterface[] }>(`/api/snmp/devices/${deviceId}`),
    enabled: on,
    refetchInterval: 60_000,
  });
  const byPort = useMemo(() => new Map((q.data?.interfaces ?? []).filter((i) => i.port_idx != null).map((i) => [i.port_idx as number, i])), [q.data]);
  return { on, target: q.data?.target ?? null, interfaces: q.data?.interfaces, byPort, loading: q.isLoading, error: q.error as Error | null };
}

/** Counters and history for one device's ports, inside its Ports panel. */
export function DevicePortCounters({ target, interfaces }: { target: SnmpTarget; interfaces: SnmpInterface[] }) {
  const physical = interfaces.filter((i) => i.physical);
  const busiest = [...physical].filter((i) => i.oper_status === "up").sort((a, b) => Math.max(b.rx_bps ?? 0, b.tx_bps ?? 0) - Math.max(a.rx_bps ?? 0, a.tx_bps ?? 0))[0];
  const [chosen, setChosen] = useState<string | null>(null);
  const [range, setRange] = useState<RangeKey>("6h");
  const sel = physical.find((i) => i.id === chosen) ?? busiest;
  const w = (id: string, metric: string, title: string): Widget => ({ id, metric, title, kind: "line", entity_ids: sel ? [sel.id] : [], size: "half", limit: 1 });
  return (
    <div className="mt-5 flex flex-col gap-4 border-t border-rule pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="font-bold">Counters</h3>
        <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-ink-3">
          {target.is_demo && <DemoTag />}
          <span>
            Read over SNMP {target.profile?.version} from <span className="font-mono text-xs">{target.host}</span> ·{" "}
            <span className={clsx(target.stale && "font-semibold text-warn-text")}>{target.last_poll_at ? `polled ${ago(target.last_poll_at)}${target.stale ? " (stale)" : ""}` : "not polled yet"}</span>
            {!target.enabled && " · polling paused"} · <Link href="/snmp" className="font-semibold text-accent hover:underline">Port counters</Link>
          </span>
        </p>
      </div>
      {(target.status === "degraded" || target.status === "unreachable") && (
        <Notice tone={target.status === "unreachable" ? "crit" : "warn"} title={target.status_detail || "Polling fails"}>{target.hint}</Notice>
      )}
      <div className="-mx-4 border-y border-rule">
        <InterfaceTable
          caption={`Port counters for ${target.name}`}
          rows={physical}
          loading={false}
          empty={
            interfaces.length === 0 ? (
              <EmptyState title="Not polled yet">The first poll runs within a minute; rates follow after the second.</EmptyState>
            ) : (
              <EmptyState title="No physical interfaces">The agent reported only virtual interfaces.</EmptyState>
            )
          }
        />
      </div>
      {sel && (
        <section aria-label="Port history" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <Field label="History for" className="w-56">
              {(p) => (
                <Select {...p} value={sel.id} onChange={(e) => setChosen(e.target.value)}>
                  {physical.map((i) => <option key={i.id} value={i.id}>{portName(i)}{i.oper_status !== "up" ? " (down)" : ""}</option>)}
                </Select>
              )}
            </Field>
            <RangePicker value={range} onChange={setRange} />
          </div>
          <div className="grid grid-cols-1 gap-[var(--gap)] md:grid-cols-6">
            <MetricWidget key={`rx-${sel.id}`} widget={w("rx", "port.rx_bps", "Receive")} range={range} label={`${sel.name} receive`} height={160} />
            <MetricWidget key={`tx-${sel.id}`} widget={w("tx", "port.tx_bps", "Transmit")} range={range} label={`${sel.name} transmit`} height={160} />
          </div>
        </section>
      )}
      <p className="text-sm text-ink-3">
        Ports are matched to interfaces by name (&ldquo;Port 7&rdquo;), which is inferred, not published by Ubiquiti. Optical power and FEC are not read: UniFi publishes no MIB for them.
      </p>
    </div>
  );
}
