"use client";

// Phase 4 vocabulary: rack item kinds, findings mapping, load bars, lifecycle
// dates and the demo-layout control shared by racks, cabling, power and assets.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Archive, BatteryCharging, EthernetPort, PanelTop, Plug, Router, Rows3, Server, Square, SquareDashed } from "lucide-react";
import { useState } from "react";
import { UsageBar, usageTone } from "@/components/ipam";
import { type Hop, RouteTrace } from "@/components/route-trace";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Notice } from "@/components/ui/layout-bits";
import { StateLabel, type Tone } from "@/components/ui/signs";
import { del, get, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { AssetKind, AssetStatus, Controller, Finding, InfraFinding, RackItemKind, Trace } from "@/lib/types";

export const KIND_LABEL: Record<RackItemKind, string> = {
  device: "Network device",
  server: "Server",
  patch_panel: "Patch panel",
  pdu: "PDU",
  ups: "UPS",
  shelf: "Shelf",
  cable_manager: "Cable manager",
  blank: "Blank panel",
  other: "Other",
};

export const KIND_ICON: Record<RackItemKind, typeof Server> = {
  device: Router,
  server: Server,
  patch_panel: EthernetPort,
  pdu: Plug,
  ups: BatteryCharging,
  shelf: Archive,
  cable_manager: Rows3,
  blank: Square,
  other: PanelTop,
};

export function KindIcon({ kind, className }: { kind: RackItemKind; className?: string }) {
  const Icon = KIND_ICON[kind] ?? SquareDashed;
  return <Icon className={clsx("size-4 shrink-0", className)} aria-hidden />;
}

/** Phase 4 findings in the shape the shared findings plate reads, linked to where they can be fixed. */
export function toFinding(f: InfraFinding): Finding {
  const href = f.rack_id
    ? `/dcim/racks/${f.rack_id}${f.item_id ? `?item=${f.item_id}` : ""}`
    : f.device_id
      ? `/inventory/devices/${f.device_id}`
      : f.cable_id
        ? `/dcim/cables?trace=${f.cable_id}`
        : f.asset_id
          ? `/assets/${f.asset_id}`
          : undefined;
  return { key: f.key, kind: f.kind, severity: f.severity, title: f.title, detail: f.detail, refs: {}, href };
}

/** Watts against capacity in the usage-bar language; no capacity means no bar, only the figure. */
export function LoadBar({ load, capacity, pct }: { load: number; capacity: number | null; pct: number | null }) {
  if (!capacity || pct == null) {
    return (
      <span className="tabular text-sm text-ink-2">
        {Math.round(load).toLocaleString()} W <span className="text-ink-3">· no capacity documented</span>
      </span>
    );
  }
  return <UsageBar used={Math.round(load)} usable={Math.round(capacity)} pct={pct} />;
}

export function pctTone(pct: number | null | undefined): Tone {
  return pct == null ? "muted" : usageTone(pct);
}

/** Days until a lifecycle date, as a state label: overdue is a warning, soon is info. */
export function DaysLabel({ days, autoRenew }: { days: number; autoRenew?: boolean }) {
  if (days < 0) return <StateLabel tone={autoRenew ? "info" : "warn"}>{`${-days} day${days === -1 ? "" : "s"} ago`}</StateLabel>;
  if (days === 0) return <StateLabel tone="warn">Today</StateLabel>;
  if (days <= 90) return <StateLabel tone={autoRenew ? "muted" : "info"}>{`In ${days} day${days === 1 ? "" : "s"}`}</StateLabel>;
  return <span className="text-sm text-ink-2 tabular">In {days} days</span>;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Load or remove the labelled demo rack layout. Only offered while a demo
 * controller exists, and removal leaves user-entered rows alone.
 */
export function DemoLayoutControl({ hasDemoRows }: { hasDemoRows: boolean }) {
  const { can } = useSession();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers") });
  const demo = controllers.data?.some((c) => c.is_demo) ?? false;
  const done = () => qc.invalidateQueries();
  const seed = useMutation({ mutationFn: () => post("/api/dcim/demo"), onSuccess: done });
  const remove = useMutation({ mutationFn: () => del("/api/dcim/demo"), onSuccess: done });
  if (!can("dcim:edit") || (!demo && !hasDemoRows)) return null;
  return (
    <>
      {hasDemoRows ? (
        <Button variant="ghost" loading={remove.isPending} onClick={() => setConfirm(true)}>
          Remove demo layout
        </Button>
      ) : (
        <Button loading={seed.isPending} onClick={() => seed.mutate()}>
          Load demo layout
        </Button>
      )}
      {(seed.error || remove.error) && (
        <Notice tone="crit" className="basis-full">
          {((seed.error || remove.error) as Error).message}
        </Notice>
      )}
      <Confirm
        open={confirm}
        onOpenChange={setConfirm}
        title="Remove the demo layout?"
        confirmLabel="Remove demo rows"
        onConfirm={() => remove.mutate()}
      >
        Every rack, cable, power link, asset and page marked as demo data is deleted. Anything you entered yourself stays.
      </Confirm>
    </>
  );
}

/**
 * A cable path as a Route Sign: the two end ports are destinations, patch panel
 * pass-throughs are round hops on the line, and each hop names the cable that
 * reaches it. A path that stops at a panel ends on a yellow hop.
 */
export function TraceStrip({ trace, from }: { trace: Trace; from?: "a" | "b" }) {
  if (trace.hops.length === 0) return <p className="mt-3 text-sm text-ink-3">No cable on this port.</p>;
  const stops = [trace.hops[0].from, ...trace.hops.map((h) => h.to)];
  const last = stops.length - 1;
  const hops: Hop[] = stops.map((s, i) => {
    const via = i > 0 ? trace.hops[i - 1].cable : null;
    const end = i === 0 || i === last;
    return {
      key: `${i}-${s.summary}`,
      label: s.summary,
      sub: (
        <>
          {via && <span className="font-mono font-bold">{via.label} {via.type.toUpperCase()}{via.status !== "installed" ? ` · ${via.status}` : ""}</span>}
          {via && s.rack ? " · " : ""}
          {s.rack}
        </>
      ),
      href: s.kind === "device_port" && s.device_id ? `/inventory/devices/${s.device_id}` : undefined,
      tone: i === last && !trace.complete ? "warn" : end ? "ok" : "muted",
      current: (from === "a" && i === 0) || (from === "b" && i === last),
    };
  });
  return (
    <div className="mt-3">
      <RouteTrace
        vertical
        hops={hops}
        label="Cable path"
        footer={trace.complete ? `${trace.hops.length} cable${trace.hops.length > 1 ? "s" : ""}, end to end.` : "The path stops at a patch panel with nothing on the other side."}
      />
    </div>
  );
}

export const ASSET_KIND_WORD: Record<AssetKind, string> = { hardware: "Hardware", spare: "Spare", licence: "Licence", subscription: "Subscription", consumable: "Consumable" };
export const ASSET_STATUS_WORD: Record<AssetStatus, string> = { ordered: "Ordered", in_service: "In service", spare: "Spare", in_repair: "In repair", retired: "Retired", disposed: "Disposed" };

export function AssetStatusLabel({ s }: { s: AssetStatus }) {
  const tone: Tone = s === "in_service" ? "ok" : s === "in_repair" ? "warn" : s === "ordered" ? "info" : "muted";
  return <StateLabel tone={tone}>{ASSET_STATUS_WORD[s]}</StateLabel>;
}
