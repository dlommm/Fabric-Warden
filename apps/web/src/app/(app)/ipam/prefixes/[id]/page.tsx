"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, ChevronLeft, ChevronRight, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useEffect, useState } from "react";
import { AddressMap, CELL_LABEL, FindingsPlate, PrefixStatus, ReservationState, STATUS_OPTIONS, UsageBar, cellKind } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, type Tone, VlanPatch } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ipKey } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AddressCell, Finding, Holder, IpStatus, PrefixDetail, Reservation } from "@/lib/types";

export default function PrefixPage() {
  return (
    <Suspense>
      <Prefix />
    </Suspense>
  );
}

const KIND_TONE: Record<ReturnType<typeof cellKind>, Tone> = {
  observed: "ok",
  reserved: "info",
  dhcp: "muted",
  free: "muted",
  gateway: "info",
  network: "muted",
  broadcast: "muted",
  conflict: "crit",
};

const KIND_WORD: Record<Reservation["kind"], string> = { static: "Static", dhcp: "DHCP", infrastructure: "Infrastructure" };

type Draft = { id?: string; address: string; hostname: string; mac_address: string; kind: Reservation["kind"]; description: string };

function Prefix() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useSession();
  const editable = can("ipam:edit");
  const q = useQuery({ queryKey: ["ipam", "prefix", id], queryFn: () => get<PrefixDetail>(`/api/ipam/prefixes/${id}`) });
  const findings = useQuery({ queryKey: ["ipam", "findings"], queryFn: () => get<{ items: Finding[] }>("/api/ipam/findings") });
  const [selected, setSelected] = useState<string | null>(params.get("address"));
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState(false);
  const remove = useMutation({
    mutationFn: () => del(`/api/ipam/prefixes/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ipam"] });
      router.push("/ipam");
    },
  });

  const p = q.data;
  useEffect(() => {
    if (p && !selected) setSelected(p.next_free ?? p.address_map?.[1]?.address ?? null);
  }, [p, selected]);

  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this prefix">{(q.error as Error).message}</Notice>
      </>
    );
  }
  if (!p) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-72" />
        <Skeleton className="h-96 w-full" />
      </>
    );
  }

  const mine = (findings.data?.items ?? []).filter((f) => f.refs.prefix_id === p.id);
  const cell = p.address_map?.find((c) => c.address === selected) ?? null;
  const startReservation = (address: string) => {
    setDraft({ address, hostname: "", mac_address: "", kind: "static", description: "" });
    requestAnimationFrame(() => document.getElementById("reservation-form")?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };
  const editReservation = (r: Reservation) => {
    setDraft({ id: r.id, address: r.address, hostname: r.hostname, mac_address: r.mac_address ?? "", kind: r.kind, description: r.description });
    requestAnimationFrame(() => document.getElementById("reservation-form")?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };

  return (
    <>
      <BackLink />
      <PageHeader
        title={p.cidr}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <PrefixStatus p={{ ...p, contains_observed: p.children.length > 0 }} />
            {p.vlan_id != null && <span className="inline-flex items-center gap-1.5"><VlanPatch id={p.vlan_id} /> <span className="text-ink-3">VLAN</span></span>}
            {p.description && <span>{p.description}</span>}
          </span>
        }
        meta={
          <span className="inline-flex flex-wrap items-center gap-2 text-sm text-ink-3">
            {p.is_demo && <DemoTag />}
            {p.parents.length > 0 && (
              <nav aria-label="Containing prefixes" className="inline-flex flex-wrap items-center gap-1">
                {p.parents.map((x) => (
                  <span key={x.id} className="inline-flex items-center gap-1">
                    <Link href={`/ipam/prefixes/${x.id}`} className="font-mono text-xs font-semibold text-accent hover:underline">{x.cidr}</Link>
                    <ChevronRight className="size-3.5" aria-hidden />
                  </span>
                ))}
                <span className="font-mono text-xs text-ink-2">{p.cidr}</span>
              </nav>
            )}
          </span>
        }
        actions={
          editable && (
            <>
              <Button icon={<Pencil />} onClick={() => setEditing((v) => !v)} aria-expanded={editing}>Edit</Button>
              <Confirm
                trigger={<Button variant="danger" icon={<Trash2 />}>Delete</Button>}
                title={`Delete ${p.cidr}?`}
                confirmLabel="Delete prefix"
                onConfirm={() => remove.mutate()}
              >
                The prefix leaves your IP plan{p.reservations > 0 ? ` along with its ${p.reservations} reservation${p.reservations > 1 ? "s" : ""}` : ""}. The controller is not touched; an observed network will show as undocumented again.
              </Confirm>
            </>
          )
        }
      />
      {remove.error && <Notice tone="crit" className="mb-4">{(remove.error as Error).message}</Notice>}

      <div className="flex flex-col gap-[var(--gap)]">
        {editing && <EditPrefix p={p} onDone={() => setEditing(false)} />}
        {mine.length > 0 && <FindingsPlate findings={mine} emptyText="" />}

        <Panel title="Summary" id="summary">
          <KeyValue
            className="lg:grid-cols-[minmax(8rem,auto)_1fr_minmax(8rem,auto)_1fr]"
            items={[
              ["Utilisation", <UsageBar key="u" used={p.used} usable={p.usable} pct={p.utilization_pct} />],
              ["Next free", p.next_free ? <button key="n" type="button" className="font-mono font-semibold text-accent hover:underline" onClick={() => setSelected(p.next_free)}>{p.next_free}</button> : <span className="text-ink-3">{p.address_map ? "None outside the DHCP pool" : "—"}</span>],
              ["Observed as", p.observed ? <Link key="o" href="/inventory/networks" className="font-semibold hover:underline">{p.observed.name}</Link> : <span className="text-ink-3">Not reported by any controller</span>],
              ["Gateway", <span key="g" className="font-mono">{p.observed?.gateway ?? "—"}</span>],
              ["DHCP pool", p.observed?.dhcp_range ? <span key="d" className="font-mono">{p.observed.dhcp_range.start} – {p.observed.dhcp_range.stop} <span className="font-sans text-ink-3">({p.observed.dhcp_pool_size} addresses)</span></span> : "—"],
              ["Size", <span key="s" className="tabular">{p.size.toLocaleString()} addresses, {p.usable.toLocaleString()} usable</span>],
            ]}
          />
        </Panel>

        {p.address_map ? (
          <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-[auto_minmax(18rem,1fr)]">
            <Panel title="Address map" id="map" actions={<span className="text-sm text-ink-3">Arrow keys move between addresses</span>}>
              <AddressMap cells={p.address_map} selected={selected} onSelect={setSelected} label={`Addresses in ${p.cidr}`} />
              {cell && (
                // Below xl the full readout sits after the map; this strip keeps the focused bay's state in view.
                <div className="sticky bottom-0 -mx-4 mt-4 -mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-rule bg-surface px-4 py-2.5 xl:hidden" aria-hidden>
                  <span className="font-mono font-bold">{cell.address}</span>
                  <StateLabel tone={KIND_TONE[cellKind(cell)]}>{CELL_LABEL[cellKind(cell)]}</StateLabel>
                  {cell.holders?.[0] && <span className="truncate text-sm text-ink-2">{cell.holders.map((h) => h.name).join(", ")}</span>}
                  <a href="#readout" className="ml-auto text-sm font-semibold text-accent hover:underline">Details</a>
                </div>
              )}
            </Panel>
            <Readout cell={cell} editable={editable} onReserve={startReservation} onEdit={(rid) => {
              const r = p.reservation_items.find((x) => x.id === rid);
              if (r) editReservation(r);
            }} />
          </div>
        ) : (
          <Notice tone="info" title="No address map for a prefix this large">
            The map draws prefixes of up to 1,024 addresses. Document smaller subnets inside it to map them; reservations below still work.
          </Notice>
        )}

        {draft && <ReservationForm key={draft.id ?? draft.address} prefixId={p.id} draft={draft} onDone={(addr) => { setDraft(null); if (addr) setSelected(addr); }} />}

        <Reservations p={p} editable={editable} onEdit={editReservation} onSelect={setSelected} onAdd={() => startReservation(selected && cell && !cell.reservation ? selected : p.next_free ?? "")} />

        {p.children.length > 0 && (
          <Panel title={`Contains ${p.children.length} prefix${p.children.length > 1 ? "es" : ""}`} bodyClassName="p-0">
            <ul>
              {p.children.map((c) => (
                <li key={c.id} className="border-b border-rule last:border-b-0">
                  <Link href={`/ipam/prefixes/${c.id}`} className="flex min-h-[var(--row-h)] items-center gap-3 px-4 hover:bg-surface-2">
                    <span className="font-mono text-sm font-bold">{c.cidr}</span>
                    <span className="text-sm text-ink-3">{STATUS_OPTIONS.find((o) => o.value === c.status)?.label}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>
    </>
  );
}

function BackLink() {
  return (
    <Link href="/ipam" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> IP plan
    </Link>
  );
}

function holderHref(h: Holder) {
  return h.kind === "device" ? `/inventory/devices/${h.id}` : `/inventory/clients/${h.id}`;
}

function Readout({ cell, editable, onReserve, onEdit }: { cell: AddressCell | null; editable: boolean; onReserve: (a: string) => void; onEdit: (id: string) => void }) {
  if (!cell) {
    return (
      <Panel title="Address">
        <p className="text-ink-3">Select an address on the map.</p>
      </Panel>
    );
  }
  const kind = cellKind(cell);
  const reservable = editable && !cell.reservation && !["network", "broadcast", "gateway"].includes(cell.state);
  return (
    <Panel title="Selected address" id="readout" className="scroll-mt-4 xl:sticky xl:top-4 xl:self-start">
      <div aria-live="polite" className="flex flex-col gap-4">
        <div>
          <p className="font-mono text-xl font-bold">{cell.address}</p>
          <StateLabel tone={KIND_TONE[kind]} className="mt-1">{CELL_LABEL[kind]}</StateLabel>
          {cell.in_dhcp_pool && kind !== "dhcp" && <p className="mt-1 text-sm text-ink-3">Inside the DHCP pool.</p>}
        </div>
        {cell.holders && cell.holders.length > 0 && (
          <div>
            <p className="text-xs font-bold tracking-wide text-ink-2 uppercase">Reported by the controller</p>
            <ul className="mt-1.5 flex flex-col gap-1">
              {cell.holders.map((h) => (
                <li key={h.id} className="flex flex-wrap items-baseline gap-x-2">
                  <Link href={holderHref(h)} className="font-bold hover:underline">{h.name}</Link>
                  <span className="text-sm text-ink-3">{h.kind}</span>
                  {h.mac && <span className="font-mono text-xs text-ink-2">{h.mac}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {cell.reservation && (
          <div>
            <p className="text-xs font-bold tracking-wide text-ink-2 uppercase">Reservation</p>
            <p className="mt-1.5 font-bold">{cell.reservation.hostname || "Unnamed"} <span className="font-normal text-ink-3">{KIND_WORD[cell.reservation.kind as Reservation["kind"]] ?? cell.reservation.kind}</span></p>
            {cell.reservation.mac && <p className="font-mono text-xs text-ink-2">{cell.reservation.mac}</p>}
            {cell.conflict && (
              <p className="mt-2 text-sm text-ink-2">The device at this address doesn&apos;t match the reserved MAC. Check which one is configured statically.</p>
            )}
            {editable && <Button size="sm" className="mt-2" icon={<Pencil />} onClick={() => onEdit(cell.reservation!.id)}>Edit reservation</Button>}
          </div>
        )}
        {reservable && (
          <Button variant="primary" icon={<BookmarkPlus />} onClick={() => onReserve(cell.address)} className="self-start">
            Reserve {cell.address}
          </Button>
        )}
      </div>
    </Panel>
  );
}

function Reservations({
  p,
  editable,
  onEdit,
  onSelect,
  onAdd,
}: {
  p: PrefixDetail;
  editable: boolean;
  onEdit: (r: Reservation) => void;
  onSelect: (a: string) => void;
  onAdd: () => void;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Reservation | null>(null);
  const remove = useMutation({
    mutationFn: (r: Reservation) => del(`/api/ipam/reservations/${r.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ipam"] }),
  });
  const columns: Column<Reservation>[] = [
    {
      key: "addr",
      header: "Address",
      sort: (r) => ipKey(r.address),
      cell: (r) => (
        <button type="button" onClick={() => onSelect(r.address)} className="font-mono text-sm font-bold hover:underline">
          {r.address}
        </button>
      ),
    },
    { key: "host", header: "Hostname", sort: (r) => r.hostname.toLowerCase(), cell: (r) => <span className="font-semibold">{r.hostname || <span className="font-normal text-ink-3">—</span>}</span> },
    { key: "mac", header: "MAC", cell: (r) => <span className="font-mono text-sm text-ink-2">{r.mac_address ?? "—"}</span> },
    { key: "kind", header: "Kind", sort: (r) => r.kind, cell: (r) => <span className="text-ink-2">{KIND_WORD[r.kind]}</span> },
    { key: "state", header: "Observed", sort: (r) => r.state, cell: (r) => <ReservationState r={r} /> },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (r) =>
        editable ? (
          <span className="inline-flex gap-1">
            <Button size="sm" variant="ghost" icon={<Pencil />} onClick={() => onEdit(r)} aria-label={`Edit reservation ${r.address}`} />
            <Button size="sm" variant="ghost" icon={<Trash2 />} onClick={() => setPending(r)} aria-label={`Delete reservation ${r.address}`} />
          </span>
        ) : null,
    },
  ];
  return (
    <Panel
      title={`Reservations (${p.reservation_items.length})`}
      id="reservations"
      bodyClassName="p-0"
      actions={editable && <Button size="sm" icon={<BookmarkPlus />} onClick={onAdd}>Add reservation</Button>}
    >
      {remove.error && <Notice tone="crit" className="m-4">{(remove.error as Error).message}</Notice>}
      <DataTable
        caption={`Reservations in ${p.cidr}`}
        rows={p.reservation_items}
        columns={columns}
        initialSort={{ key: "addr", dir: "asc" }}
        empty={
          <EmptyState title="No reservations">
            Reserve addresses for servers, cameras and infrastructure so drift shows up here. Reservations are documentation; they don&apos;t create fixed IPs on the controller.
          </EmptyState>
        }
      />
      <Confirm
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title={`Delete the reservation for ${pending?.address}?`}
        confirmLabel="Delete reservation"
        onConfirm={() => pending && remove.mutate(pending)}
      >
        {pending?.hostname ? `${pending.hostname} will no longer be documented at this address.` : "The address returns to free in your plan."} The controller is not touched.
      </Confirm>
    </Panel>
  );
}

function ReservationForm({ prefixId, draft, onDone }: { prefixId: string; draft: Draft; onDone: (address?: string) => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(draft);
  const [errors, setErrors] = useState<{ address?: string; mac?: string; form?: string }>({});
  const save = useMutation({
    mutationFn: () => {
      const body = { address: form.address.trim(), hostname: form.hostname.trim(), mac_address: form.mac_address.trim() || null, kind: form.kind, description: form.description };
      return form.id ? patch<Reservation>(`/api/ipam/reservations/${form.id}`, body) : post<Reservation>(`/api/ipam/prefixes/${prefixId}/reservations`, body);
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["ipam"] });
      onDone(r.address);
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Couldn't save the reservation.";
      if (/mac/i.test(msg)) setErrors({ mac: msg.replace(/^mac_address: (Value error, )?/i, "") });
      else if (/address|reserved|outside|broadcast/i.test(msg)) setErrors({ address: msg });
      else setErrors({ form: msg });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!form.address.trim()) next.address = "Enter the address to reserve.";
    if (form.mac_address.trim() && !/^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(form.mac_address.trim())) next.mac = "Use the form aa:bb:cc:dd:ee:ff.";
    setErrors(next);
    if (Object.keys(next).length === 0) save.mutate();
  };
  return (
    <Panel title={form.id ? `Edit reservation ${draft.address}` : "Reserve an address"} id="reservation-form">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-2 xl:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_minmax(0,13rem)_minmax(0,10rem)]">
        <Field label="Address" error={errors.address}>
          {(p) => <Input {...p} autoFocus={!form.address} className="font-mono" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} placeholder="10.0.30.10" />}
        </Field>
        <Field label="Hostname">
          {(p) => <Input {...p} autoFocus={!!form.address} value={form.hostname} maxLength={128} onChange={(e) => setForm({ ...form, hostname: e.target.value })} placeholder="nas-01" />}
        </Field>
        <Field label="MAC address" error={errors.mac} hint="Optional. Used to spot conflicts.">
          {(p) => <Input {...p} className="font-mono" value={form.mac_address} onChange={(e) => setForm({ ...form, mac_address: e.target.value })} placeholder="aa:bb:cc:dd:ee:ff" />}
        </Field>
        <Field label="Kind">
          {(p) => (
            <Select {...p} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Reservation["kind"] })}>
              <option value="static">Static</option>
              <option value="dhcp">DHCP reservation</option>
              <option value="infrastructure">Infrastructure</option>
            </Select>
          )}
        </Field>
        <Field label="Notes" className="md:col-span-2 xl:col-span-4">
          {(p) => <Textarea {...p} className="min-h-16" value={form.description} maxLength={2000} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="What it is and who owns it" />}
        </Field>
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-4">
          <Button type="submit" variant="primary" loading={save.isPending}>{form.id ? "Save reservation" : "Reserve address"}</Button>
          <Button variant="ghost" onClick={() => onDone()}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}

function EditPrefix({ p, onDone }: { p: PrefixDetail; onDone: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ description: p.description, vlan: p.vlan_id?.toString() ?? "", status: p.status });
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => patch(`/api/ipam/prefixes/${p.id}`, { description: form.description, vlan_id: form.vlan ? Number(form.vlan) : null, status: form.status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ipam"] });
      onDone();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const vlanBad = form.vlan !== "" && !(Number(form.vlan) >= 1 && Number(form.vlan) <= 4094);
  return (
    <Panel title={`Edit ${p.cidr}`}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!vlanBad) save.mutate();
        }}
        noValidate
        className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,7rem)_minmax(0,11rem)] md:items-start"
      >
        <Field label="Description">
          {(f) => <Input {...f} value={form.description} maxLength={2000} onChange={(e) => setForm({ ...form, description: e.target.value })} />}
        </Field>
        <Field label="VLAN" error={vlanBad ? "1 to 4094" : null}>
          {(f) => <Input {...f} className="font-mono" inputMode="numeric" value={form.vlan} onChange={(e) => setForm({ ...form, vlan: e.target.value.replace(/\D/g, "") })} />}
        </Field>
        <Field label="Status" hint={STATUS_OPTIONS.find((o) => o.value === form.status)?.hint}>
          {(f) => (
            <Select {...f} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as IpStatus })}>
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex flex-wrap items-center gap-2 md:col-span-3">
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {err && <span role="alert" className="text-sm font-semibold text-crit">{err}</span>}
        </div>
        <p className="text-sm text-ink-3 md:col-span-3">The prefix itself can&apos;t be changed; delete it and add the new range instead.</p>
      </form>
    </Panel>
  );
}
