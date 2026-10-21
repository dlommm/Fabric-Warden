"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { STATUS_OPTIONS } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, VlanPatch } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { IpStatus, VlanRow } from "@/lib/types";

type Row = VlanRow & { id: string };

function VlanState({ v }: { v: VlanRow }) {
  switch (v.state) {
    case "documented":
      return <StateLabel tone="ok">Documented</StateLabel>;
    case "undocumented":
      return <StateLabel tone="info">Not documented</StateLabel>;
    case "unobserved":
      return <StateLabel tone="warn">Not on controller</StateLabel>;
    case "deprecated_in_use":
      return <StateLabel tone="warn">Deprecated, still in use</StateLabel>;
    case "prefix_only":
      return <StateLabel tone="muted">Only on a prefix</StateLabel>;
    default:
      return <StateLabel tone="muted">{STATUS_OPTIONS.find((o) => o.value === v.state)?.label ?? v.state}</StateLabel>;
  }
}

type Draft = { id?: string; vlan_id: string; name: string; status: IpStatus };

export default function VlansPage() {
  const { can } = useSession();
  const qc = useQueryClient();
  const editable = can("ipam:edit");
  const q = useQuery({ queryKey: ["ipam", "vlans"], queryFn: () => get<{ items: VlanRow[] }>("/api/ipam/vlans") });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState<VlanRow | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/ipam/vlans/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ipam"] }),
  });
  const rows: Row[] | undefined = q.data?.items.map((v) => ({ ...v, id: String(v.vlan_id) }));
  const undocumented = rows?.filter((v) => v.state === "undocumented").length ?? 0;
  const demo = rows?.some((v) => v.record?.is_demo || v.observed_networks.some((n) => n.is_demo));

  const columns: Column<Row>[] = [
    { key: "id", header: "VLAN", align: "right", sort: (v) => v.vlan_id, className: "w-16", cell: (v) => <VlanPatch id={v.vlan_id} /> },
    {
      key: "name",
      header: "Name",
      sort: (v) => (v.record?.name ?? v.observed_networks[0]?.name ?? "").toLowerCase(),
      cell: (v) => (
        <span className="flex flex-col">
          <span className="font-bold">{v.record?.name ?? <span className="font-normal text-ink-3">Unnamed</span>}</span>
          {v.name_mismatch && <span className="text-sm text-warn-text">Controller calls it {v.observed_networks.map((n) => n.name).join(", ")}</span>}
        </span>
      ),
    },
    { key: "state", header: "Status", sort: (v) => v.state, cell: (v) => <VlanState v={v} /> },
    {
      key: "obs",
      header: "On the controller",
      cell: (v) =>
        v.observed_networks.length ? (
          <span className="flex flex-col">
            {v.observed_networks.map((n) => (
              <span key={n.id} className="inline-flex flex-wrap items-baseline gap-x-2">
                <Link href="/inventory/networks" className="font-semibold hover:underline">{n.name}</Link>
                {n.subnet && <span className="font-mono text-xs text-ink-2">{n.subnet}</span>}
                {!n.enabled && <span className="text-sm text-ink-3">disabled</span>}
              </span>
            ))}
          </span>
        ) : (
          <span className="text-ink-3">Not reported</span>
        ),
    },
    {
      key: "prefixes",
      header: "Prefixes",
      cell: (v) =>
        v.prefixes.length ? (
          <span className="flex flex-wrap gap-x-3">
            {v.prefixes.map((p) => (
              <Link key={p.id} href={`/ipam/prefixes/${p.id}`} className="font-mono text-sm font-semibold text-accent hover:underline">{p.cidr}</Link>
            ))}
          </span>
        ) : (
          <span className="text-ink-3">—</span>
        ),
    },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (v) => (v.record?.is_demo || v.observed_networks.some((n) => n.is_demo) ? <DemoTag /> : null) },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (v) =>
        !editable ? null : v.record ? (
          <span className="inline-flex gap-1">
            <Button size="sm" variant="ghost" icon={<Pencil />} aria-label={`Edit VLAN ${v.vlan_id}`} onClick={() => setDraft({ id: v.record!.id, vlan_id: String(v.vlan_id), name: v.record!.name, status: v.record!.status })} />
            <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Delete VLAN ${v.vlan_id} record`} onClick={() => setPending(v)} />
          </span>
        ) : (
          <Button size="sm" onClick={() => setDraft({ vlan_id: String(v.vlan_id), name: v.observed_networks[0]?.name ?? "", status: "active" })}>Document</Button>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="VLANs"
        description="VLAN IDs from your documentation, the controller's networks and your prefixes, in one list. Documentation only."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes simulated networks</span> : undefined}
        actions={editable && <Button variant="primary" icon={<Plus />} onClick={() => setDraft({ vlan_id: "", name: "", status: "planned" })}>Add VLAN</Button>}
      />
      {q.error && <Notice tone="crit" className="mb-4">{(q.error as Error).message}</Notice>}
      {remove.error && <Notice tone="crit" className="mb-4">{(remove.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {draft && <VlanForm key={draft.id ?? draft.vlan_id} draft={draft} onDone={() => setDraft(null)} />}
        {undocumented > 0 && !draft && (
          <Notice tone="info" title={`${undocumented} VLAN${undocumented > 1 ? "s are" : " is"} on the controller but not documented`}>
            Use Document on a row, or adopt observed networks from the <Link href="/ipam" className="font-semibold text-accent hover:underline">IP plan</Link> to document prefixes and VLANs together.
          </Notice>
        )}
        <Panel bodyClassName="p-0">
          <DataTable
            caption="VLANs"
            rows={rows}
            columns={columns}
            loading={q.isLoading}
            initialSort={{ key: "id", dir: "asc" }}
            empty={<EmptyState icon={<Layers />} title="No VLANs yet">VLANs appear once a controller reports tagged networks, or when you document one.</EmptyState>}
          />
        </Panel>
      </div>
      <Confirm
        open={pending !== null}
        onOpenChange={(o) => !o && setPending(null)}
        title={`Delete the record for VLAN ${pending?.vlan_id}?`}
        confirmLabel="Delete record"
        onConfirm={() => pending?.record && remove.mutate(pending.record.id)}
      >
        Your documentation for this VLAN is removed. Networks on the controller are not touched{pending?.observed_networks.length ? " and will show as not documented" : ""}.
      </Confirm>
    </>
  );
}

function VlanForm({ draft, onDone }: { draft: Draft; onDone: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(draft);
  const [errors, setErrors] = useState<{ vlan?: string; name?: string; form?: string }>({});
  const save = useMutation({
    mutationFn: () =>
      form.id
        ? patch(`/api/ipam/vlans/${form.id}`, { name: form.name.trim(), status: form.status })
        : post("/api/ipam/vlans", { vlan_id: Number(form.vlan_id), name: form.name.trim(), status: form.status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ipam"] });
      onDone();
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Couldn't save.";
      if (/vlan/i.test(msg)) setErrors({ vlan: msg });
      else setErrors({ form: msg });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    const n = Number(form.vlan_id);
    if (!form.id && !(n >= 1 && n <= 4094)) next.vlan = "VLAN IDs run from 1 to 4094.";
    if (!form.name.trim()) next.name = "Give the VLAN a name.";
    setErrors(next);
    if (Object.keys(next).length === 0) save.mutate();
  };
  return (
    <Panel title={form.id ? `Edit VLAN ${form.vlan_id}` : "Document a VLAN"}>
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-[minmax(0,7rem)_minmax(0,1fr)_minmax(0,11rem)] md:items-start">
        <Field label="VLAN ID" error={errors.vlan}>
          {(p) => <Input {...p} autoFocus={!form.vlan_id} disabled={!!form.id} inputMode="numeric" className="font-mono" value={form.vlan_id} onChange={(e) => setForm({ ...form, vlan_id: e.target.value.replace(/\D/g, "") })} placeholder="70" />}
        </Field>
        <Field label="Name" error={errors.name}>
          {(p) => <Input {...p} autoFocus={!!form.vlan_id} value={form.name} maxLength={128} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Lab" />}
        </Field>
        <Field label="Status" hint={STATUS_OPTIONS.find((o) => o.value === form.status)?.hint}>
          {(p) => (
            <Select {...p} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as IpStatus })}>
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex flex-wrap items-center gap-2 md:col-span-3">
          <Button type="submit" variant="primary" loading={save.isPending}>{form.id ? "Save" : "Add VLAN"}</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}
