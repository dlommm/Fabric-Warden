"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ArrowDown, ArrowLeftRight, ArrowUp, Pencil, Plus, Rows3, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { KIND_LABEL, KindIcon, TraceStrip } from "@/components/infra";
import { UsageBar } from "@/components/ipam";
import { ElevationLegend, RackElevation } from "@/components/rack-elevation";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, DeviceState, StateLabel } from "@/components/ui/signs";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DeviceSummary, PanelView, RackDetail, RackItem, RackItemKind, Trace } from "@/lib/types";
import type { ReactNode } from "react";

export default function RackPage() {
  return (
    <Suspense>
      <RackView />
    </Suspense>
  );
}

function RackView() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { can, moduleEnabled } = useSession();
  const editable = can("dcim:edit");
  const q = useQuery({ queryKey: ["dcim", "rack", id], queryFn: () => get<RackDetail>(`/api/dcim/racks/${id}`) });
  const [selected, setSelected] = useState<string | null>(search.get("item"));
  const [mode, setMode] = useState<"view" | "add" | "edit" | "rack">("view");
  const [announce, setAnnounce] = useState("");
  const [moveError, setMoveError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const move = useMutation({
    mutationFn: ({ item, position, face }: { item: RackItem; position: number; face?: "front" | "rear" }) =>
      patch<RackItem>(`/api/dcim/items/${item.id}`, face ? { position, face } : { position }),
    onSuccess: (item) => {
      setMoveError(null);
      setAnnounce(`${item.name} moved to U${item.position}${item.full_depth ? "" : `, ${item.face}`}.`);
      qc.invalidateQueries({ queryKey: ["dcim"] });
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Couldn't move the item.";
      setMoveError(msg);
      setAnnounce(msg);
    },
  });
  const removeRack = useMutation({
    mutationFn: () => del(`/api/dcim/racks/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      router.push("/dcim");
    },
  });

  const rack = q.data;
  const item = rack?.items.find((i) => i.id === selected) ?? null;
  const select = (iid: string) => {
    setSelected(iid);
    setMode("view");
    router.replace(`/dcim/racks/${id}?item=${iid}`, { scroll: false });
  };

  if (q.error) return <Notice tone="crit">{(q.error as Error).message}</Notice>;
  if (!rack) {
    return (
      <>
        <Skeleton className="mb-6 h-8 w-64" />
        <Skeleton className="h-[480px]" />
      </>
    );
  }
  const unplaced = rack.items.filter((i) => rack.unplaced.includes(i.id));
  const occ = rack.occupancy;

  return (
    <>
      <PageHeader
        icon={<Rows3 />}
        title={rack.name}
        description={[rack.location ?? "No location", `${rack.u_height} U`, rack.depth_mm ? `${rack.depth_mm} mm deep` : null, `${rack.width_in}-inch`].filter(Boolean).join(" · ")}
        meta={
          <span className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-ink-2">
            {rack.is_demo && <DemoTag />}
            <span className="inline-flex items-center gap-2">Occupied <UsageBar used={occ.used_u} usable={rack.u_height} pct={occ.utilisation_pct} /></span>
            {occ.planned_u > 0 && <span>{occ.planned_u} U planned</span>}
            <span className="tabular">{occ.documented_draw_w ? `${occ.documented_draw_w.toLocaleString()} W documented draw` : "No draw documented"}</span>
          </span>
        }
        actions={
          editable && (
            <>
              <Button variant="ghost" icon={<Trash2 />} onClick={() => setDeleting(true)}>Delete rack</Button>
              <Button icon={<Pencil />} onClick={() => setMode("rack")}>Edit rack</Button>
              <Button variant="primary" icon={<Plus />} onClick={() => { setMode("add"); setSelected(null); }}>Add item</Button>
            </>
          )
        }
      />
      <p className="sr-only" aria-live="polite">{announce}</p>
      <div className="flex flex-col gap-[var(--gap)]">
        {rack.conflicts.length > 0 && (
          <Notice tone="crit" title={`${rack.conflicts.length} placement conflict${rack.conflicts.length > 1 ? "s" : ""}`}>
            {rack.conflicts.map((c) => c.detail).join(". ")}. Move one of the items to clear it.
          </Notice>
        )}
        {moveError && <Notice tone="warn" title="That move was not made">{moveError}</Notice>}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] xl:grid-cols-[minmax(0,1fr)_24rem] xl:items-start">
          <Panel
            title="Elevation"
            actions={editable && <span className="hidden text-sm text-ink-3 md:inline">Drag an item to move it, or focus it and press Alt + ↑ / ↓</span>}
          >
            {rack.items.length === 0 ? (
              <EmptyState title="Nothing in this rack yet" action={editable && <Button variant="primary" icon={<Plus />} onClick={() => setMode("add")}>Add the first item</Button>}>
                Add devices, patch panels, power and shelves. Link an item to a device the controller reports to see its state here.
              </EmptyState>
            ) : (
              <>
                <RackElevation rack={rack} selected={selected} onSelect={select} editable={editable} onMove={(it, position, face) => move.mutate({ item: it, position, face })} />
                <div className="mt-4 border-t border-rule pt-3">
                  <ElevationLegend />
                </div>
              </>
            )}
          </Panel>
          <div className="flex flex-col gap-[var(--gap)] xl:sticky xl:top-4">
            {mode === "rack" && <RackEditForm rack={rack} onDone={() => setMode("view")} />}
            {(mode === "add" || (mode === "edit" && item)) && (
              <ItemForm key={mode === "edit" ? item!.id : "new"} rack={rack} item={mode === "edit" ? item! : null} onDone={(newId) => { setMode("view"); if (newId) select(newId); }} />
            )}
            {mode === "view" && item && (
              <ItemPanel
                item={item}
                rack={rack}
                editable={editable}
                moving={move.isPending}
                onMove={(position, face) => move.mutate({ item, position, face })}
                onEdit={() => setMode("edit")}
                onRemoved={() => { setSelected(null); router.replace(`/dcim/racks/${id}`, { scroll: false }); }}
                cabling={moduleEnabled("cabling")}
              />
            )}
            {mode === "view" && !item && (
              <Panel title="Selection">
                <p className="text-ink-2">Select an item in the elevation to see its details{editable ? ", move it or edit it" : ""}.</p>
              </Panel>
            )}
            {unplaced.length > 0 && (
              <Panel title={`Not placed (${unplaced.length})`} bodyClassName="p-0">
                <ul>
                  {unplaced.map((u) => (
                    <li key={u.id} className="border-b border-rule last:border-b-0">
                      <button type="button" onClick={() => select(u.id)} className={clsx("flex min-h-[var(--row-h)] w-full items-center gap-2 px-[var(--row-pad)] text-left hover:bg-surface-2", u.id === selected && "bg-surface-2")}>
                        <KindIcon kind={u.kind} className="text-ink-3" />
                        <span className="flex-1 truncate font-semibold">{u.name}</span>
                        <span className="text-sm text-ink-3">{u.kind === "pdu" ? "Zero-U" : "No position"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        </div>
      </div>
      <Confirm open={deleting} onOpenChange={setDeleting} title={`Delete ${rack.name}?`} confirmLabel="Delete rack" onConfirm={() => removeRack.mutate()}>
        The rack and its {rack.items.length} item{rack.items.length === 1 ? "" : "s"} are removed from your documentation, along with cables and power links that end on them. Devices on the controller are not touched.
      </Confirm>
    </>
  );
}

function position(item: RackItem): string {
  if (item.position == null) return item.kind === "pdu" ? "Zero-U, side mounted" : "Not placed";
  const top = item.position + item.height_u - 1;
  return `${item.height_u > 1 ? `U${item.position}–U${top}` : `U${item.position}`} · ${item.face}${item.full_depth ? ", full depth" : ", half depth"}`;
}

function ItemPanel({
  item,
  rack,
  editable,
  moving,
  onMove,
  onEdit,
  onRemoved,
  cabling,
}: {
  item: RackItem;
  rack: RackDetail;
  editable: boolean;
  moving: boolean;
  onMove: (position: number, face?: "front" | "rear") => void;
  onEdit: () => void;
  onRemoved: () => void;
  cabling: boolean;
}) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const remove = useMutation({
    mutationFn: () => del(`/api/dcim/items/${item.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onRemoved();
    },
  });
  const power = item.kind === "ups" || item.kind === "pdu";
  const facts: [string, ReactNode][] = [
    ["Kind", KIND_LABEL[item.kind]],
    ["Position", position(item)],
    ["Status", item.status === "installed" ? <StateLabel tone="ok">Installed</StateLabel> : item.status === "planned" ? <StateLabel tone="info">Planned</StateLabel> : <StateLabel tone="muted">Removed</StateLabel>],
  ];
  if (item.device)
    facts.push([
      "Linked device",
      <span key="d" className="flex flex-col">
        <Link href={`/inventory/devices/${item.device.id}`} className="font-semibold text-accent hover:underline">{item.device.name}</Link>
        <span className="flex flex-wrap items-center gap-x-3">
          {item.device.present ? <DeviceState state={item.device.state} /> : <StateLabel tone="crit">No longer reported</StateLabel>}
          {item.device.ip_address && <span className="font-mono text-xs text-ink-2">{item.device.ip_address}</span>}
        </span>
      </span>,
    ]);
  const hw = [item.manufacturer, item.model || item.device?.model].filter(Boolean).join(" ");
  if (hw) facts.push(["Hardware", hw]);
  if (item.serial) facts.push(["Serial", <span key="s" className="font-mono text-xs">{item.serial}</span>]);
  if (item.kind === "patch_panel") facts.push(["Ports", `${item.port_count}${item.connector ? ` × ${item.connector}` : ""}`]);
  if (item.power_draw_w != null) facts.push(["Typical draw", <span key="w" className="tabular">{item.power_draw_w} W</span>]);
  if (power && item.power_capacity_w) facts.push(["Usable capacity", <span key="c" className="tabular">{item.power_capacity_w.toLocaleString()} W · {item.outlet_count} outlets</span>]);
  if (item.kind === "ups" && item.runtime_full_load_min) facts.push(["Runtime at full load", <span key="r" className="tabular">{item.runtime_full_load_min} min</span>]);
  if (item.notes) facts.push(["Notes", <span key="n" className="whitespace-pre-wrap">{item.notes}</span>]);
  const top = item.position != null ? item.position + item.height_u - 1 : 0;

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <KindIcon kind={item.kind} className="text-ink-3" />
          {item.name}
        </span>
      }
      actions={item.is_demo && <DemoTag />}
    >
      <KeyValue items={facts} />
      {editable && item.position != null && (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-rule pt-3">
          <span className="mr-1 text-sm font-semibold text-ink-2">Move</span>
          <Button size="sm" icon={<ArrowUp />} disabled={moving || top >= rack.u_height} onClick={() => onMove(item.position! + 1)}>Up 1 U</Button>
          <Button size="sm" icon={<ArrowDown />} disabled={moving || item.position <= 1} onClick={() => onMove(item.position! - 1)}>Down 1 U</Button>
          {!item.full_depth && (
            <Button size="sm" icon={<ArrowLeftRight />} disabled={moving} onClick={() => onMove(item.position!, item.face === "front" ? "rear" : "front")}>
              To {item.face === "front" ? "rear" : "front"}
            </Button>
          )}
        </div>
      )}
      {editable && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" icon={<Pencil />} onClick={onEdit}>Edit</Button>
          <Button size="sm" variant="danger" icon={<Trash2 />} onClick={() => setConfirm(true)}>Remove</Button>
        </div>
      )}
      {remove.error && <Notice tone="crit" className="mt-3">{(remove.error as Error).message}</Notice>}
      {item.kind === "patch_panel" && cabling && <PanelPorts itemId={item.id} />}
      <Confirm open={confirm} onOpenChange={setConfirm} title={`Remove ${item.name}?`} confirmLabel="Remove item" onConfirm={() => remove.mutate()}>
        The item leaves this rack&apos;s documentation. Cables and power links that end on it lose that end.{item.device ? " The linked device stays in the inventory." : ""}
      </Confirm>
    </Panel>
  );
}

/** Patch panel ports in the bay language: both sides cabled, one side, or free. Selecting a port traces it. */
function PanelPorts({ itemId }: { itemId: string }) {
  const q = useQuery({ queryKey: ["dcim", "panel", itemId], queryFn: () => get<PanelView>(`/api/cabling/panels/${itemId}`) });
  const [port, setPort] = useState<number | null>(null);
  const trace = useQuery({
    queryKey: ["dcim", "trace", itemId, port],
    queryFn: () => get<Trace>(`/api/cabling/trace?item_id=${itemId}&port_idx=${port}&side=front`),
    enabled: port != null,
  });
  if (!q.data) return <Skeleton className="mt-4 h-24" />;
  const p = q.data;
  return (
    <div className="mt-4 border-t border-rule pt-3">
      <p className="mb-2 flex items-baseline justify-between text-sm">
        <span className="font-semibold text-ink-2">Ports</span>
        <span className="text-ink-3 tabular">{p.used} of {p.ports.length} cabled</span>
      </p>
      <ol className="grid grid-cols-8 gap-1" aria-label={`${p.name} ports`}>
        {p.ports.map((row) => {
          const both = row.front && row.rear;
          const one = !both && (row.front || row.rear);
          const label = `Port ${row.port}: ${both ? `front ${row.front!.label} to ${row.front!.far.summary}, rear ${row.rear!.label} to ${row.rear!.far.summary}` : one ? `${row.front ? "front" : "rear"} only, ${(row.front ?? row.rear)!.label}` : "free"}`;
          return (
            <li key={row.port}>
              <button
                type="button"
                onClick={() => setPort(row.port === port ? null : row.port)}
                aria-pressed={row.port === port}
                aria-label={label}
                title={label}
                className={clsx(
                  "flex h-7 w-full items-center justify-center rounded-[3px] font-mono text-xs tabular",
                  both && "bg-ok-soft font-bold text-ok ring-1 ring-ok/60 ring-inset",
                  one && "border-2 border-accent bg-surface font-bold text-ink",
                  !both && !one && "border border-rule-strong bg-surface text-ink-3",
                  row.port === port && "ring-2 ring-ink ring-offset-1 ring-offset-surface",
                )}
              >
                {row.port}
              </button>
            </li>
          );
        })}
      </ol>
      <p className="mt-2 text-xs text-ink-3">Filled: cabled front and rear. Outlined: one side only.</p>
      {port != null && trace.data && <TraceStrip trace={trace.data} />}
    </div>
  );
}

const KINDS = Object.keys(KIND_LABEL) as RackItemKind[];

function ItemForm({ rack, item, onDone }: { rack: RackDetail; item: RackItem | null; onDone: (newId?: string) => void }) {
  const qc = useQueryClient();
  const devices = useQuery({ queryKey: ["inventory", "devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices") });
  const [f, setF] = useState(() => ({
    name: item?.name ?? "",
    kind: item?.kind ?? ("device" as RackItemKind),
    position: item?.position != null ? String(item.position) : "",
    height_u: String(item?.height_u ?? 1),
    face: item?.face ?? ("front" as "front" | "rear"),
    full_depth: item?.full_depth ?? true,
    status: item?.status ?? ("installed" as RackItem["status"]),
    device_id: item?.device_id ?? "",
    manufacturer: item?.manufacturer ?? "",
    model: item?.model ?? "",
    serial: item?.serial ?? "",
    port_count: String(item?.port_count ?? 24),
    connector: item?.connector ?? "RJ45",
    power_draw_w: item?.power_draw_w != null ? String(item.power_draw_w) : "",
    power_capacity_w: item?.power_capacity_w != null ? String(item.power_capacity_w) : "",
    outlet_count: String(item?.outlet_count ?? 8),
    runtime_full_load_min: item?.runtime_full_load_min != null ? String(item.runtime_full_load_min) : "",
    notes: item?.notes ?? "",
  }));
  const [errors, setErrors] = useState<{ name?: string; position?: string; form?: string }>({});
  // Linking a device fills an empty name with the device's.
  const devName = useMemo(() => devices.data?.items.find((d) => d.id === f.device_id)?.name, [devices.data, f.device_id]);
  useEffect(() => {
    if (devName && !f.name) setF((x) => ({ ...x, name: devName }));
  }, [devName, f.name]);
  const num = (v: string) => (v === "" ? null : Number(v));
  const isPanel = f.kind === "patch_panel";
  const isPower = f.kind === "ups" || f.kind === "pdu";
  const body = () => ({
    name: f.name.trim(),
    kind: f.kind,
    position: num(f.position),
    height_u: Number(f.height_u) || 1,
    face: f.face,
    full_depth: f.full_depth,
    status: f.status,
    device_id: f.device_id || null,
    manufacturer: f.manufacturer.trim(),
    model: f.model.trim(),
    serial: f.serial.trim(),
    port_count: isPanel ? Number(f.port_count) || 0 : 0,
    connector: isPanel ? f.connector : "",
    power_draw_w: num(f.power_draw_w),
    power_capacity_w: isPower ? num(f.power_capacity_w) : null,
    outlet_count: isPower ? Number(f.outlet_count) || 0 : 0,
    runtime_full_load_min: f.kind === "ups" ? num(f.runtime_full_load_min) : null,
    notes: f.notes,
  });
  const save = useMutation({
    mutationFn: () => (item ? patch<RackItem>(`/api/dcim/items/${item.id}`, body()) : post<RackItem>("/api/dcim/items", { rack_id: rack.id, ...body() })),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onDone(saved.id);
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Couldn't save.";
      setErrors(/U\d+|occupied|extend/i.test(msg) ? { position: msg } : { form: msg });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!f.name.trim()) next.name = "Name the item.";
    const pos = num(f.position);
    if (pos != null && !(pos >= 1 && pos <= rack.u_height)) next.position = `Units run from 1 to ${rack.u_height}.`;
    setErrors(next);
    if (!Object.keys(next).length) save.mutate();
  };
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const digits = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value.replace(/[^\d.]/g, "") });

  return (
    <Panel title={item ? `Edit ${item.name}` : `Add to ${rack.name}`}>
      <form onSubmit={submit} noValidate className="grid grid-cols-2 gap-4">
        <Field label="Kind" className="col-span-2 sm:col-span-1">
          {(p) => (
            <Select {...p} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as RackItemKind, full_depth: !["patch_panel", "cable_manager", "blank", "pdu"].includes(e.target.value) })}>
              {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Status" className="col-span-2 sm:col-span-1">
          {(p) => (
            <Select {...p} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as RackItem["status"] })}>
              <option value="installed">Installed</option>
              <option value="planned">Planned</option>
              <option value="removed">Removed</option>
            </Select>
          )}
        </Field>
        {(f.kind === "device" || f.kind === "server") && (
          <Field label="Linked device" hint="Optional. Shows the controller's live state on the plate." className="col-span-2">
            {(p) => (
              <Select {...p} value={f.device_id} onChange={set("device_id")}>
                <option value="">Not linked</option>
                {devices.data?.items.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.model}</option>)}
              </Select>
            )}
          </Field>
        )}
        <Field label="Name" error={errors.name} className="col-span-2">
          {(p) => <Input {...p} value={f.name} maxLength={128} onChange={set("name")} placeholder={isPanel ? "Patch panel B" : "proxmox-03"} />}
        </Field>
        <Field label="Lowest unit" error={errors.position} hint="Empty = not placed (zero-U PDUs, spares)">
          {(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.position} onChange={digits("position")} placeholder="12" />}
        </Field>
        <Field label="Height (U)">
          {(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.height_u} onChange={digits("height_u")} />}
        </Field>
        <Field label="Mounted at">
          {(p) => (
            <Select {...p} value={f.face} onChange={(e) => setF({ ...f, face: e.target.value as "front" | "rear" })}>
              <option value="front">Front</option>
              <option value="rear">Rear</option>
            </Select>
          )}
        </Field>
        <Field label="Depth">
          {(p) => (
            <Select {...p} value={f.full_depth ? "full" : "half"} onChange={(e) => setF({ ...f, full_depth: e.target.value === "full" })}>
              <option value="full">Full depth</option>
              <option value="half">Half depth</option>
            </Select>
          )}
        </Field>
        {isPanel && (
          <>
            <Field label="Ports">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.port_count} onChange={digits("port_count")} />}</Field>
            <Field label="Connector">
              {(p) => (
                <Select {...p} value={f.connector} onChange={set("connector")}>
                  {["RJ45", "LC", "SC", "MPO", "Keystone", "Other"].map((c) => <option key={c}>{c}</option>)}
                </Select>
              )}
            </Field>
          </>
        )}
        {!["patch_panel", "cable_manager", "blank", "shelf"].includes(f.kind) && (
          <Field label={isPower ? "Own draw (W)" : "Typical draw (W)"} hint={isPower ? "Losses and electronics" : "Used for power headroom"}>
            {(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.power_draw_w} onChange={digits("power_draw_w")} />}
          </Field>
        )}
        {isPower && (
          <>
            <Field label="Usable capacity (W)">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.power_capacity_w} onChange={digits("power_capacity_w")} />}</Field>
            <Field label="Outlets">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.outlet_count} onChange={digits("outlet_count")} />}</Field>
          </>
        )}
        {f.kind === "ups" && (
          <Field label="Runtime at full load (min)" hint="From the datasheet">
            {(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.runtime_full_load_min} onChange={digits("runtime_full_load_min")} />}
          </Field>
        )}
        <Field label="Manufacturer">{(p) => <Input {...p} value={f.manufacturer} maxLength={64} onChange={set("manufacturer")} />}</Field>
        <Field label="Model">{(p) => <Input {...p} value={f.model} maxLength={64} onChange={set("model")} />}</Field>
        <Field label="Serial" className="col-span-2">{(p) => <Input {...p} className="font-mono" value={f.serial} maxLength={64} onChange={set("serial")} />}</Field>
        <Field label="Notes" className="col-span-2">{(p) => <Textarea {...p} value={f.notes} maxLength={4000} onChange={set("notes")} />}</Field>
        <div className="col-span-2 flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={save.isPending}>{item ? "Save" : "Add item"}</Button>
          <Button variant="ghost" onClick={() => onDone()}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}

function RackEditForm({ rack, onDone }: { rack: RackDetail; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: rack.name, u_height: String(rack.u_height), depth_mm: rack.depth_mm ? String(rack.depth_mm) : "", status: rack.status, descending: rack.descending_units, description: rack.description });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      patch(`/api/dcim/racks/${rack.id}`, {
        name: f.name.trim(),
        u_height: Number(f.u_height),
        depth_mm: f.depth_mm ? Number(f.depth_mm) : null,
        status: f.status,
        descending_units: f.descending,
        description: f.description,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  return (
    <Panel title={`Edit ${rack.name}`}>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} noValidate className="grid grid-cols-2 gap-4">
        <Field label="Name" className="col-span-2">{(p) => <Input {...p} value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
        <Field label="Height (U)" error={error}>{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.u_height} onChange={(e) => setF({ ...f, u_height: e.target.value.replace(/\D/g, "") })} />}</Field>
        <Field label="Depth (mm)">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.depth_mm} onChange={(e) => setF({ ...f, depth_mm: e.target.value.replace(/\D/g, "") })} />}</Field>
        <Field label="Status">
          {(p) => (
            <Select {...p} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as RackDetail["status"] })}>
              <option value="active">Active</option>
              <option value="planned">Planned</option>
              <option value="retired">Retired</option>
            </Select>
          )}
        </Field>
        <Field label="Unit numbering">
          {(p) => (
            <Select {...p} value={f.descending ? "desc" : "asc"} onChange={(e) => setF({ ...f, descending: e.target.value === "desc" })}>
              <option value="asc">U1 at the bottom</option>
              <option value="desc">U1 at the top</option>
            </Select>
          )}
        </Field>
        <Field label="Description" className="col-span-2">{(p) => <Textarea {...p} value={f.description} maxLength={2000} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
        <div className="col-span-2 flex gap-2">
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
