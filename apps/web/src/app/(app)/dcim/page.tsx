"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Plus, Rows3, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useMemo, useState } from "react";
import { DemoLayoutControl, toFinding } from "@/components/infra";
import { FindingsPlate, UsageBar } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { InfraFinding, Location, RackSummary } from "@/lib/types";

const LOCATION_KINDS: Location["kind"][] = ["site", "building", "floor", "room", "row", "cage"];

function RackStatus({ s }: { s: RackSummary["status"] }) {
  if (s === "active") return <StateLabel tone="ok">Active</StateLabel>;
  if (s === "planned") return <StateLabel tone="info">Planned</StateLabel>;
  return <StateLabel tone="muted">Retired</StateLabel>;
}

/** Location names with their ancestry, so "Utility closet" reads "Demo Home Lab › Utility closet". */
function pathOf(id: string | null, byId: Map<string, Location>): string {
  const parts: string[] = [];
  let cur = id ? byId.get(id) : undefined;
  while (cur && parts.length < 8) {
    parts.unshift(cur.name);
    cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
  }
  return parts.join(" › ");
}

export default function RacksPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("dcim");
  const editable = can("dcim:edit");
  const racks = useQuery({ queryKey: ["dcim", "racks"], queryFn: () => get<{ items: RackSummary[] }>("/api/dcim/racks"), enabled: on });
  const locations = useQuery({ queryKey: ["dcim", "locations"], queryFn: () => get<{ items: Location[] }>("/api/dcim/locations"), enabled: on });
  const findings = useQuery({ queryKey: ["dcim", "findings"], queryFn: () => get<{ items: InfraFinding[] }>("/api/dcim/findings"), enabled: on });
  const [adding, setAdding] = useState<"rack" | "location" | null>(null);
  const byId = useMemo(() => new Map((locations.data?.items ?? []).map((l) => [l.id, l])), [locations.data]);

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Racks" name="Rack & DCIM Manager" canManage={can("modules:manage")}>
        Document locations, racks and what sits in each unit. Enable Rack & DCIM Manager to start.
      </ModuleOff>
    );
  }

  const rows = racks.data?.items;
  const hasDemo = !!rows?.some((r) => r.is_demo) || !!locations.data?.items.some((l) => l.is_demo);
  const columns: Column<RackSummary>[] = [
    {
      key: "name",
      header: "Rack",
      sort: (r) => r.name.toLowerCase(),
      cell: (r) => (
        <Link href={`/dcim/racks/${r.id}`} className="font-bold hover:underline">
          {r.name}
        </Link>
      ),
    },
    {
      key: "loc",
      header: "Location",
      sort: (r) => pathOf(r.location_id, byId).toLowerCase(),
      cell: (r) => (r.location_id ? <span className="text-ink-2">{pathOf(r.location_id, byId)}</span> : <span className="text-ink-3">Unassigned</span>),
    },
    { key: "u", header: "Height", align: "right", sort: (r) => r.u_height, cell: (r) => <span className="tabular">{r.u_height} U</span> },
    {
      key: "occ",
      header: "Occupied",
      sort: (r) => r.occupancy.utilisation_pct,
      cell: (r) => (
        <span className="flex flex-col">
          <UsageBar used={r.occupancy.used_u} usable={r.u_height} pct={r.occupancy.utilisation_pct} />
          {r.occupancy.planned_u > 0 && <span className="text-sm text-ink-3">{r.occupancy.planned_u} U planned</span>}
        </span>
      ),
    },
    {
      key: "draw",
      header: "Documented draw",
      align: "right",
      sort: (r) => r.occupancy.documented_draw_w,
      cell: (r) => (r.occupancy.documented_draw_w ? <span className="tabular">{r.occupancy.documented_draw_w.toLocaleString()} W</span> : <span className="text-ink-3">—</span>),
    },
    { key: "status", header: "Status", sort: (r) => r.status, cell: (r) => <RackStatus s={r.status} /> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (r) => (r.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Racks"
        description="Where your hardware physically sits: locations, racks and what occupies each unit. This is documentation; nothing here reaches a controller."
        meta={hasDemo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes a demo layout</span> : undefined}
        actions={
          <>
            <DemoLayoutControl hasDemoRows={hasDemo} />
            {editable && (
              <Button icon={<MapPin />} onClick={() => setAdding("location")}>
                Add location
              </Button>
            )}
            {editable && (
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding("rack")}>
                Add rack
              </Button>
            )}
          </>
        }
      />
      {racks.error && <Notice tone="crit" className="mb-4">{(racks.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {adding === "rack" && <RackForm locations={locations.data?.items ?? []} byId={byId} onDone={() => setAdding(null)} />}
        {adding === "location" && <LocationForm locations={locations.data?.items ?? []} byId={byId} onDone={() => setAdding(null)} />}
        {findings.data && rows && rows.length > 0 && (
          <FindingsPlate
            label="Rack findings"
            findings={findings.data.items.map(toFinding)}
            emptyText="Every rack is consistent and every switch and gateway is placed."
            heading={(n) => `${n} rack problem${n > 1 ? "s" : ""} to resolve`}
          />
        )}
        <Panel bodyClassName="p-0">
          <DataTable
            caption="Racks"
            rows={rows}
            columns={columns}
            loading={racks.isLoading}
            href={(r) => `/dcim/racks/${r.id}`}
            initialSort={{ key: "name", dir: "asc" }}
            empty={
              <EmptyState icon={<Rows3 />} title="No racks documented">
                Add a rack, then place devices in it. Rack items can link to devices the controller reports, so the elevation shows their live state.
              </EmptyState>
            }
          />
        </Panel>
        <Locations items={locations.data?.items} loading={locations.isLoading} byId={byId} editable={editable} />
      </div>
    </>
  );
}

function Locations({ items, loading, byId, editable }: { items?: Location[]; loading: boolean; byId: Map<string, Location>; editable: boolean }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Location | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/dcim/locations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dcim"] }),
  });
  const sorted = [...(items ?? [])].sort((a, b) => pathOf(a.id, byId).localeCompare(pathOf(b.id, byId)));
  return (
    <Panel title="Locations" bodyClassName="p-0">
      {remove.error && <Notice tone="crit" className="m-3">{(remove.error as Error).message}</Notice>}
      {loading ? (
        <div className="p-4"><Skeleton className="h-16" /></div>
      ) : sorted.length === 0 ? (
        <p className="px-4 py-3 text-ink-3">No locations yet. Racks can exist without one.</p>
      ) : (
        <ul>
          {sorted.map((l) => {
            const depth = pathOf(l.id, byId).split(" › ").length - 1;
            return (
              <li key={l.id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-[var(--row-pad)] last:border-b-0">
                <span className="flex min-w-0 flex-1 items-center gap-2" style={{ paddingLeft: `${depth * 1.25}rem` }}>
                  {depth > 0 && <span aria-hidden className="-mt-2 h-3 w-2.5 shrink-0 border-b-2 border-l-2 border-rule-strong" />}
                  <span className="truncate font-semibold">{l.name}</span>
                  <span className="text-sm text-ink-3 capitalize">{l.kind}</span>
                </span>
                <span className="text-sm text-ink-2 tabular">{l.rack_count ? `${l.rack_count} rack${l.rack_count > 1 ? "s" : ""}` : ""}</span>
                {l.is_demo && <DemoTag />}
                {editable && (
                  <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Delete location ${l.name}`} onClick={() => setPending(l)} />
                )}
              </li>
            );
          })}
        </ul>
      )}
      <Confirm
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title={`Delete ${pending?.name}?`}
        confirmLabel="Delete location"
        onConfirm={() => pending && remove.mutate(pending.id)}
      >
        Racks in this location stay, with no location. A location that still contains other locations cannot be deleted.
      </Confirm>
    </Panel>
  );
}

function LocationSelect({ id, value, onChange, locations, byId, none }: { id?: string; value: string; onChange: (v: string) => void; locations: Location[]; byId: Map<string, Location>; none: string }) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{none}</option>
      {[...locations]
        .sort((a, b) => pathOf(a.id, byId).localeCompare(pathOf(b.id, byId)))
        .map((l) => (
          <option key={l.id} value={l.id}>
            {pathOf(l.id, byId)}
          </option>
        ))}
    </Select>
  );
}

function RackForm({ locations, byId, onDone }: { locations: Location[]; byId: Map<string, Location>; onDone: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [form, setForm] = useState({ name: "", location_id: "", u_height: "42", depth_mm: "" });
  const [errors, setErrors] = useState<{ name?: string; u?: string; form?: string }>({});
  const save = useMutation({
    mutationFn: () =>
      post<RackSummary>("/api/dcim/racks", {
        name: form.name.trim(),
        location_id: form.location_id || null,
        u_height: Number(form.u_height),
        depth_mm: form.depth_mm ? Number(form.depth_mm) : null,
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      router.push(`/dcim/racks/${r.id}`);
    },
    onError: (e) => setErrors({ form: e instanceof ApiError ? e.message : "Couldn't save." }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!form.name.trim()) next.name = "Name the rack.";
    const u = Number(form.u_height);
    if (!(u >= 1 && u <= 60)) next.u = "Racks run from 1 to 60 U.";
    setErrors(next);
    if (!Object.keys(next).length) save.mutate();
  };
  return (
    <Panel title="Add a rack">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,7rem)_minmax(0,8rem)] md:items-start">
        <Field label="Name" error={errors.name}>
          {(p) => <Input {...p} autoFocus value={form.name} maxLength={128} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Rack A" />}
        </Field>
        <Field label="Location">
          {(p) => <LocationSelect id={p.id} value={form.location_id} onChange={(v) => setForm({ ...form, location_id: v })} locations={locations} byId={byId} none="No location" />}
        </Field>
        <Field label="Height (U)" error={errors.u}>
          {(p) => <Input {...p} inputMode="numeric" className="tabular" value={form.u_height} onChange={(e) => setForm({ ...form, u_height: e.target.value.replace(/\D/g, "") })} />}
        </Field>
        <Field label="Depth (mm)" hint="Optional">
          {(p) => <Input {...p} inputMode="numeric" className="tabular" value={form.depth_mm} onChange={(e) => setForm({ ...form, depth_mm: e.target.value.replace(/\D/g, "") })} placeholder="600" />}
        </Field>
        <div className="flex flex-wrap items-center gap-2 md:col-span-4">
          <Button type="submit" variant="primary" loading={save.isPending}>Add rack</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}

function LocationForm({ locations, byId, onDone }: { locations: Location[]; byId: Map<string, Location>; onDone: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: "", kind: "room" as Location["kind"], parent_id: "" });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => post("/api/dcim/locations", { name: form.name.trim(), kind: form.kind, parent_id: form.parent_id || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  return (
    <Panel title="Add a location">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!form.name.trim()) return setError("Name the location.");
          save.mutate();
        }}
        noValidate
        className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,9rem)_minmax(0,1.4fr)] md:items-start"
      >
        <Field label="Name" error={error}>
          {(p) => <Input {...p} autoFocus value={form.name} maxLength={128} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Utility closet" />}
        </Field>
        <Field label="Kind">
          {(p) => (
            <Select {...p} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Location["kind"] })}>
              {LOCATION_KINDS.map((k) => (
                <option key={k} value={k} className="capitalize">{k[0].toUpperCase() + k.slice(1)}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Inside">
          {(p) => <LocationSelect id={p.id} value={form.parent_id} onChange={(v) => setForm({ ...form, parent_id: v })} locations={locations} byId={byId} none="Top level" />}
        </Field>
        <div className="flex flex-wrap items-center gap-2 md:col-span-3">
          <Button type="submit" variant="primary" loading={save.isPending}>Add location</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
