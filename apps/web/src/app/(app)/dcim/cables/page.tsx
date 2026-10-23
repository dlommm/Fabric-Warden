"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Cable, Download, Plus, Printer, Route, Trash2, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useState } from "react";
import { DemoLayoutControl, TraceStrip, toFinding } from "@/components/infra";
import { FindingsPlate } from "@/components/ipam";
import { Button, buttonClass } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { CableEndView, CableRow, DeviceDetail, DeviceSummary, InfraFinding, RackItem, Trace } from "@/lib/types";

const TYPES = ["cat5e", "cat6", "cat6a", "cat8", "om3", "om4", "om5", "os2", "dac", "aoc", "coax", "power", "other"];
const TYPE_HINT: Record<string, string> = { om3: "multimode", om4: "multimode", om5: "multimode", os2: "single-mode", dac: "direct attach copper", aoc: "active optical" };

function CableStatus({ s }: { s: CableRow["status"] }) {
  if (s === "installed") return <StateLabel tone="ok">Installed</StateLabel>;
  if (s === "planned") return <StateLabel tone="info">Planned</StateLabel>;
  return <StateLabel tone="muted">Decommissioned</StateLabel>;
}

function End({ e }: { e: CableEndView | null }) {
  if (!e) return <span className="text-ink-3">—</span>;
  return (
    <span className="flex min-w-0 flex-col">
      <span className="truncate font-semibold">{e.summary}</span>
      <span className="truncate text-sm text-ink-3">
        {e.kind === "external" ? "Outside the racks" : e.rack ?? (e.kind === "device_port" ? (e.port ? `${e.port.connector} · ${e.port.state === "UP" ? "link up" : "link down"}` : "Device port") : "")}
      </span>
    </span>
  );
}

export default function CablesPage() {
  return (
    <Suspense>
      <Cables />
    </Suspense>
  );
}

function Cables() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("cabling");
  const editable = can("dcim:edit");
  const params = useSearchParams();
  const router = useRouter();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState("");
  const [adding, setAdding] = useState(false);
  const [labels, setLabels] = useState(false);
  const traceId = params.get("trace");
  const list = useQuery({ queryKey: ["dcim", "cables"], queryFn: () => get<{ items: CableRow[] }>("/api/cabling/cables"), enabled: on });
  const findings = useQuery({ queryKey: ["dcim", "cable-findings"], queryFn: () => get<{ items: InfraFinding[] }>("/api/cabling/findings"), enabled: on });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Cabling" name="Cabling & Fiber Documentation" canManage={can("modules:manage")}>
        Document cables between device ports, patch panels and outlets, then trace any path end to end.
      </ModuleOff>
    );
  }

  const all = list.data?.items;
  const term = q.trim().toLowerCase();
  const rows = all?.filter(
    (c) =>
      (!status || c.status === status) &&
      (!term || [c.label, c.description, c.a?.summary, c.b?.summary, c.type].some((v) => v?.toLowerCase().includes(term))),
  );
  const tracing = all?.find((c) => c.id === traceId) ?? null;
  const openTrace = (id: string | null) => router.replace(id ? `/dcim/cables?trace=${id}` : "/dcim/cables", { scroll: false });

  const columns: Column<CableRow>[] = [
    { key: "label", header: "Label", sort: (c) => c.label, cell: (c) => <span className="font-mono text-sm font-bold">{c.label}</span> },
    {
      key: "type",
      header: "Type",
      sort: (c) => c.type,
      cell: (c) => (
        <span className="flex flex-col whitespace-nowrap" title={TYPE_HINT[c.type] ?? c.medium}>
          <span className="font-semibold uppercase">{c.type}</span>
          <span className="text-sm text-ink-3">{c.color || c.medium}</span>
        </span>
      ),
    },
    { key: "a", header: "A end", sort: (c) => c.a?.summary ?? "", cell: (c) => <End e={c.a} /> },
    { key: "b", header: "B end", sort: (c) => c.b?.summary ?? "", cell: (c) => <End e={c.b} /> },
    { key: "len", header: "Length", align: "right", sort: (c) => c.length_m, cell: (c) => (c.length_m ? `${c.length_m} m` : <span className="text-ink-3">—</span>) },
    { key: "status", header: "Status", sort: (c) => c.status, className: "whitespace-nowrap", cell: (c) => <CableStatus s={c.status} /> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (c) => (c.is_demo ? <DemoTag /> : null) },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (c) => (
        <Button size="sm" variant={traceId === c.id ? "secondary" : "ghost"} className={traceId === c.id ? "border-accent bg-accent-soft" : undefined} icon={<Route />} onClick={() => openTrace(traceId === c.id ? null : c.id)} aria-pressed={traceId === c.id}>
          Trace
        </Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Cabling"
        description="Cables between device ports, patch panels and outlets. Patch panel ports pass straight through, so any path can be traced end to end."
        meta={all?.some((c) => c.is_demo) ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes demo cabling</span> : undefined}
        actions={
          <>
            {!all?.length && <DemoLayoutControl hasDemoRows={false} />}
            <a href="/api/cabling/schedule.csv" className={buttonClass("ghost")} download>
              <Download aria-hidden /> Cable schedule
            </a>
            <Button icon={<Printer />} onClick={() => setLabels((v) => !v)} aria-pressed={labels}>Labels</Button>
            {editable && <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add cable</Button>}
          </>
        }
      />
      {list.error && <Notice tone="crit" className="mb-4">{(list.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {adding && <CableForm onDone={() => setAdding(false)} />}
        {labels && <LabelSheet onClose={() => setLabels(false)} />}
        <Suggestions editable={editable} />
        {findings.data && !!all?.length && (
          <FindingsPlate
            label="Cabling findings"
            findings={findings.data.items.map(toFinding)}
            emptyText="Documented cabling agrees with what the controller reports."
            heading={(n) => `${n} cabling difference${n > 1 ? "s" : ""} from the network`}
          />
        )}
        <div className={clsx("grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)]", tracing && "2xl:grid-cols-[minmax(0,1fr)_22rem] 2xl:items-start")}>
          <Panel bodyClassName="p-0" className="order-2 2xl:order-1">
            <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
              <Field label="Search" className="w-full sm:w-72">
                {(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Label, device, panel…" />}
              </Field>
              <Field label="Status" className="w-44">
                {(p) => (
                  <Select {...p} value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option value="">Any status</option>
                    <option value="installed">Installed</option>
                    <option value="planned">Planned</option>
                    <option value="decommissioned">Decommissioned</option>
                  </Select>
                )}
              </Field>
              <span className="ml-auto self-center text-sm text-ink-3 tabular">{rows ? `${rows.length} of ${all?.length ?? 0}` : ""}</span>
            </div>
            <DataTable
              caption="Cables"
              rows={rows}
              columns={columns}
              loading={list.isLoading}
              initialSort={{ key: "label", dir: "asc" }}
              rowClassName={(c) => (c.id === traceId ? "bg-accent-soft" : undefined)}
              empty={
                <EmptyState icon={<Cable />} title={all?.length ? "No cables match" : "No cables documented"}>
                  {all?.length ? "Clear the search or status filter." : "Add a cable between two ports. Patch panels come from racks; device ports come from the controller."}
                </EmptyState>
              }
            />
          </Panel>
          {tracing && <TracePanel cable={tracing} onClose={() => openTrace(null)} editable={editable} />}
        </div>
      </div>
    </>
  );
}

interface Suggestion {
  key: string;
  title: string;
  device: { id: string; name: string; model: string };
  uplink: { id: string; name: string; model: string };
  is_demo: boolean;
}

/**
 * Cables the observed uplinks imply but nobody has documented. The controller names the uplink device but
 * not the port on either side, so an accepted suggestion becomes a planned cable with both ports to confirm.
 */
function Suggestions({ editable }: { editable: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dcim", "cable-suggestions"], queryFn: () => get<{ items: Suggestion[] }>("/api/cabling/suggestions") });
  const accept = useMutation({
    mutationFn: (keys: string[]) => post<{ items: CableRow[] }>("/api/cabling/suggestions/accept", { keys }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["dcim"] }),
  });
  const items = q.data?.items ?? [];
  if (!items.length) return null;
  return (
    <Panel
      title={`Suggested from observed uplinks (${items.length})`}
      bodyClassName="p-0"
      actions={
        editable && items.length > 1 ? (
          <Button size="sm" loading={accept.isPending && accept.variables?.length === items.length} onClick={() => accept.mutate(items.map((i) => i.key))}>
            Document all as planned
          </Button>
        ) : undefined
      }
    >
      <p className="border-b border-rule px-4 py-2.5 text-sm text-ink-2">
        Each device reports which device it uplinks to, but not the port on either side. Documenting one adds a planned cable with the next label; confirm its ports when you trace it.
      </p>
      {accept.error && <Notice tone="crit" className="m-3">{(accept.error as Error).message}</Notice>}
      <ul>
        {items.map((s) => (
          <li key={s.key} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-4 py-2.5 last:border-b-0">
            <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2">
              <span className="font-semibold">{s.device.name}</span>
              <span className="text-sm text-ink-3">{s.device.model}</span>
              <span aria-hidden className="text-ink-3">→</span>
              <span className="font-semibold">{s.uplink.name}</span>
              <span className="text-sm text-ink-3">{s.uplink.model}</span>
              <span className="sr-only">uplink, no cable documented</span>
              {s.is_demo && <DemoTag />}
            </span>
            {editable && (
              <Button size="sm" variant="ghost" icon={<Plus />} loading={accept.isPending && accept.variables?.length === 1 && accept.variables[0] === s.key} onClick={() => accept.mutate([s.key])}>
                Document as planned cable
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** Confirm the ports of a planned cable accepted from an observed uplink, then it traces like any other. */
function ConfirmPorts({ cable }: { cable: CableRow }) {
  const qc = useQueryClient();
  const ends = (["a", "b"] as const).filter((n) => cable[n]?.kind === "device_port" && cable[n]?.port_idx == null);
  const ports = useQuery({
    queryKey: ["dcim", "confirm-ports", cable.id],
    queryFn: () => Promise.all(ends.map((n) => get<DeviceDetail>(`/api/inventory/devices/${cable[n]!.device_id}`))),
  });
  const [picked, setPicked] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () =>
      patch(`/api/cabling/cables/${cable.id}`, Object.fromEntries(ends.map((n) => [n, { kind: "device_port", device_id: cable[n]!.device_id, port_idx: Number(picked[n]) }]))),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dcim"] }),
  });
  return (
    <form
      className="mt-2 flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-sm text-ink-2">The controller reports which device this uplinks to, not the ports. Pick the port on each end to trace it.</p>
      {ends.map((n, i) => (
        <Field key={n} label={`${n.toUpperCase()} end: ${cable[n]!.name}`}>
          {(p) => (
            <Select {...p} value={picked[n] ?? ""} disabled={!ports.data} onChange={(e) => setPicked({ ...picked, [n]: e.target.value })}>
              <option value="">Choose a port</option>
              {ports.data?.[i].ports.map((pt) => (
                <option key={pt.idx} value={pt.idx}>
                  Port {pt.idx} · {pt.connector} · {pt.state === "UP" ? "link up" : "link down"}
                </option>
              ))}
            </Select>
          )}
        </Field>
      ))}
      {save.error && <Notice tone="crit">{(save.error as Error).message}</Notice>}
      <span>
        <Button type="submit" size="sm" variant="primary" loading={save.isPending} disabled={ends.some((n) => !picked[n])}>
          Confirm ports
        </Button>
      </span>
    </form>
  );
}

function TracePanel({ cable, onClose, editable }: { cable: CableRow; onClose: () => void; editable: boolean }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const a = cable.a;
  const unconfirmed = [cable.a, cable.b].some((e) => e?.kind === "device_port" && e.port_idx == null);
  const query = unconfirmed
    ? null
    : a?.kind === "device_port"
      ? `device_id=${a.device_id}&port_idx=${a.port_idx}`
      : a?.kind === "panel_port"
        ? `item_id=${a.item_id}&port_idx=${a.port_idx}&side=${a.side}`
        : a?.kind === "item"
          ? `item_id=${a.item_id}`
          : null;
  const trace = useQuery({ queryKey: ["dcim", "trace", cable.id], queryFn: () => get<Trace>(`/api/cabling/trace?${query}`), enabled: !!query });
  const remove = useMutation({
    mutationFn: () => del(`/api/cabling/cables/${cable.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onClose();
    },
  });
  return (
    <Panel
      title={<span className="font-mono">{cable.label}</span>}
      actions={<Button size="sm" variant="ghost" icon={<X />} aria-label="Close trace" onClick={onClose} />}
      className="order-1 2xl:sticky 2xl:top-4 2xl:order-2"
    >
      <p className="text-sm text-ink-2">
        <span className="font-semibold uppercase">{cable.type}</span>
        {cable.length_m ? ` · ${cable.length_m} m` : ""}
        {cable.color ? ` · ${cable.color}` : ""}
      </p>
      {cable.description && <p className="mt-1 text-sm text-ink-2">{cable.description}</p>}
      <p className="mt-4 text-sm font-semibold text-ink-2">Full path</p>
      {unconfirmed ? (
        editable ? (
          <ConfirmPorts cable={cable} />
        ) : (
          <p className="mt-2 text-sm text-ink-3">This planned cable&apos;s ports aren&apos;t confirmed yet, so it can&apos;t be traced.</p>
        )
      ) : !query ? (
        <p className="mt-2 text-sm text-ink-3">This cable starts outside the racks; trace it from its other end.</p>
      ) : trace.data ? (
        <TraceStrip trace={trace.data} />
      ) : (
        <Skeleton className="mt-3 h-24" />
      )}
      {editable && (
        <div className="mt-4 border-t border-rule pt-3">
          <Button size="sm" variant="danger" icon={<Trash2 />} onClick={() => setConfirm(true)}>Delete cable</Button>
        </div>
      )}
      <Confirm open={confirm} onOpenChange={setConfirm} title={`Delete ${cable.label}?`} confirmLabel="Delete cable" onConfirm={() => remove.mutate()}>
        The cable is removed from your documentation. To keep its history, set it to Decommissioned instead.
      </Confirm>
    </Panel>
  );
}

function LabelSheet({ onClose }: { onClose: () => void }) {
  const q = useQuery({ queryKey: ["dcim", "labels"], queryFn: () => get<{ items: { cable_id: string; label: string; lines: string[] }[] }>("/api/cabling/labels") });
  return (
    <Panel
      title="Cable labels"
      actions={
        <>
          <Button size="sm" icon={<Printer />} onClick={() => window.print()}>Print</Button>
          <Button size="sm" variant="ghost" icon={<X />} aria-label="Close labels" onClick={onClose} />
        </>
      }
    >
      <p className="mb-3 text-sm text-ink-2">One label per cable: print two and fix one at each end. Planned and decommissioned cables are left out.</p>
      {!q.data ? (
        <Skeleton className="h-24" />
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {q.data.items.map((l) => (
            <li key={l.cable_id} className="rounded-[3px] border-2 border-ink bg-surface px-3 py-2 font-mono text-xs leading-5 break-inside-avoid">
              <p className="font-bold">{l.lines[0]}</p>
              <p className="truncate">{l.lines[1]}</p>
              <p className="truncate">{l.lines[2]}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

type EndDraft = { kind: CableEndView["kind"]; device_id: string; item_id: string; port_idx: string; side: "front" | "rear"; text: string };
const emptyEnd = (): EndDraft => ({ kind: "device_port", device_id: "", item_id: "", port_idx: "", side: "rear", text: "" });

function EndPicker({ legend, value, onChange, devices, items }: { legend: string; value: EndDraft; onChange: (v: EndDraft) => void; devices: DeviceSummary[]; items: (RackItem & { rack: string | null })[] }) {
  const detail = useQuery({
    queryKey: ["device", value.device_id],
    queryFn: () => get<DeviceDetail>(`/api/inventory/devices/${value.device_id}`),
    enabled: value.kind === "device_port" && !!value.device_id,
  });
  const panels = items.filter((i) => i.kind === "patch_panel");
  const panel = panels.find((p) => p.id === value.item_id);
  return (
    <fieldset className="flex min-w-0 flex-col gap-3 rounded-[var(--radius)] border border-rule p-3">
      <legend className="px-1 text-sm font-semibold text-ink-3">{legend}</legend>
      <Field label="Ends on">
        {(p) => (
          <Select {...p} value={value.kind} onChange={(e) => onChange({ ...emptyEnd(), kind: e.target.value as EndDraft["kind"] })}>
            <option value="device_port">A device port</option>
            <option value="panel_port">A patch panel port</option>
            <option value="item">A rack item (server, NVR…)</option>
            <option value="external">Something else (wall jack, ISP)</option>
          </Select>
        )}
      </Field>
      {value.kind === "device_port" && (
        <div className="grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
          <Field label="Device">
            {(p) => (
              <Select {...p} value={value.device_id} onChange={(e) => onChange({ ...value, device_id: e.target.value, port_idx: "" })}>
                <option value="">Choose…</option>
                {devices.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Port">
            {(p) => (
              <Select {...p} value={value.port_idx} disabled={!detail.data} onChange={(e) => onChange({ ...value, port_idx: e.target.value })}>
                <option value="">—</option>
                {detail.data?.ports.map((pt) => (
                  <option key={pt.idx} value={pt.idx}>{pt.idx} · {pt.connector}{pt.state === "UP" ? "" : " (down)"}</option>
                ))}
              </Select>
            )}
          </Field>
        </div>
      )}
      {value.kind === "panel_port" && (
        <div className="grid grid-cols-[minmax(0,1fr)_5rem_6rem] gap-3">
          <Field label="Panel">
            {(p) => (
              <Select {...p} value={value.item_id} onChange={(e) => onChange({ ...value, item_id: e.target.value, port_idx: "" })}>
                <option value="">Choose…</option>
                {panels.map((i) => <option key={i.id} value={i.id}>{i.name}{i.rack ? ` · ${i.rack}` : ""}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Port">
            {(p) => (
              <Select {...p} value={value.port_idx} disabled={!panel} onChange={(e) => onChange({ ...value, port_idx: e.target.value })}>
                <option value="">—</option>
                {Array.from({ length: panel?.port_count ?? 0 }, (_, n) => <option key={n + 1} value={n + 1}>{n + 1}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Side">
            {(p) => (
              <Select {...p} value={value.side} onChange={(e) => onChange({ ...value, side: e.target.value as "front" | "rear" })}>
                <option value="front">Front</option>
                <option value="rear">Rear</option>
              </Select>
            )}
          </Field>
        </div>
      )}
      {value.kind === "item" && (
        <Field label="Rack item">
          {(p) => (
            <Select {...p} value={value.item_id} onChange={(e) => onChange({ ...value, item_id: e.target.value })}>
              <option value="">Choose…</option>
              {items.filter((i) => i.kind !== "patch_panel").map((i) => <option key={i.id} value={i.id}>{i.name}{i.rack ? ` · ${i.rack}` : ""}</option>)}
            </Select>
          )}
        </Field>
      )}
      {value.kind === "external" && (
        <Field label="Description">
          {(p) => <Input {...p} value={value.text} maxLength={128} onChange={(e) => onChange({ ...value, text: e.target.value })} placeholder="Office wall jack O-04" />}
        </Field>
      )}
    </fieldset>
  );
}

function endBody(e: EndDraft) {
  return {
    kind: e.kind,
    device_id: e.kind === "device_port" ? e.device_id || null : null,
    item_id: e.kind === "panel_port" || e.kind === "item" ? e.item_id || null : null,
    port_idx: e.kind === "device_port" || e.kind === "panel_port" ? (e.port_idx ? Number(e.port_idx) : null) : null,
    side: e.kind === "panel_port" ? e.side : null,
    text: e.text.trim(),
  };
}

function endReady(e: EndDraft): boolean {
  if (e.kind === "device_port") return !!e.device_id && !!e.port_idx;
  if (e.kind === "panel_port") return !!e.item_id && !!e.port_idx;
  if (e.kind === "item") return !!e.item_id;
  return !!e.text.trim();
}

function CableForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const devices = useQuery({ queryKey: ["inventory", "devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });
  const items = useQuery({ queryKey: ["dcim", "items"], queryFn: () => get<{ items: (RackItem & { rack: string | null })[] }>("/api/dcim/items") });
  const [f, setF] = useState({ label: "", type: "cat6a", color: "", length_m: "", status: "installed", description: "" });
  const [a, setA] = useState<EndDraft>(emptyEnd);
  const [b, setB] = useState<EndDraft>(() => ({ ...emptyEnd(), kind: "panel_port" }));
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      post("/api/cabling/cables", {
        label: f.label.trim(),
        type: f.type,
        color: f.color.trim(),
        length_m: f.length_m ? Number(f.length_m) : null,
        status: f.status,
        description: f.description,
        a: endBody(a),
        b: endBody(b),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!endReady(a) || !endReady(b)) return setError("Choose both ends completely.");
    setError(null);
    save.mutate();
  };
  const devs = devices.data?.items ?? [];
  const its = items.data?.items ?? [];
  return (
    <Panel title="Add a cable">
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,9rem)_minmax(0,9rem)_minmax(0,8rem)_minmax(0,7rem)_minmax(0,9rem)_minmax(0,1fr)]">
          <Field label="Label" hint="Empty: next free C-number">
            {(p) => <Input {...p} className="font-mono" value={f.label} maxLength={64} onChange={(e) => setF({ ...f, label: e.target.value })} placeholder="C-0042" />}
          </Field>
          <Field label="Type">
            {(p) => (
              <Select {...p} value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
                {TYPES.map((t) => <option key={t} value={t}>{t.toUpperCase()}{TYPE_HINT[t] ? ` (${TYPE_HINT[t]})` : ""}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Colour">{(p) => <Input {...p} value={f.color} maxLength={32} onChange={(e) => setF({ ...f, color: e.target.value })} placeholder="blue" />}</Field>
          <Field label="Length (m)">{(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.length_m} onChange={(e) => setF({ ...f, length_m: e.target.value.replace(/[^\d.]/g, "") })} />}</Field>
          <Field label="Status">
            {(p) => (
              <Select {...p} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
                <option value="installed">Installed</option>
                <option value="planned">Planned</option>
                <option value="decommissioned">Decommissioned</option>
              </Select>
            )}
          </Field>
          <Field label="Description">{(p) => <Input {...p} value={f.description} maxLength={2000} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <EndPicker legend="A end" value={a} onChange={setA} devices={devs} items={its} />
          <EndPicker legend="B end" value={b} onChange={setB} devices={devs} items={its} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={save.isPending}>Add cable</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {error && <span role="alert" className="text-sm font-semibold text-crit">{error}</span>}
        </div>
      </form>
    </Panel>
  );
}
