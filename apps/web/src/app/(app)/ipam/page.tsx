"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Grid3x3, Plus, Import } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useMemo, useState } from "react";
import { FindingsPlate, PrefixStatus, STATUS_OPTIONS, UsageBar } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, VlanPatch } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, get, post } from "@/lib/api";
import { cidrKey } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Finding, IpStatus, PrefixRow } from "@/lib/types";

interface Listing {
  items: PrefixRow[];
  total: number;
  undocumented: { network_id: string; name: string; subnet: string; vlan_id: number | null; is_demo: boolean }[];
}

export default function IpPlanPage() {
  const { can } = useSession();
  const qc = useQueryClient();
  const editable = can("ipam:edit");
  const [adding, setAdding] = useState(false);
  const list = useQuery({ queryKey: ["ipam", "prefixes"], queryFn: () => get<Listing>("/api/ipam/prefixes") });
  const findings = useQuery({ queryKey: ["ipam", "findings"], queryFn: () => get<{ items: Finding[] }>("/api/ipam/findings") });
  const adopt = useMutation({
    mutationFn: () => post<{ created: { id: string }[] }>("/api/ipam/prefixes/adopt", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ipam"] }),
  });

  const rows = list.data?.items;
  const parents = useMemo(() => new Set(rows?.map((p) => p.parent_id).filter(Boolean)), [rows]);
  const undocumented = list.data?.undocumented ?? [];
  const demo = rows?.some((p) => p.is_demo) || undocumented.some((u) => u.is_demo);

  const adoptButton = editable && undocumented.length > 0 && (
    <Button icon={<Import />} loading={adopt.isPending} onClick={() => adopt.mutate()} variant={rows?.length ? "secondary" : "primary"}>
      Adopt {undocumented.length} observed network{undocumented.length > 1 ? "s" : ""}
    </Button>
  );

  const columns: Column<PrefixRow>[] = [
    {
      key: "cidr",
      header: "Prefix",
      sort: (p) => cidrKey(p.cidr),
      cell: (p) => (
        <span className="flex items-center gap-2" style={{ paddingLeft: `${(p.depth ?? 0) * 1.25}rem` }}>
          {(p.depth ?? 0) > 0 && <span aria-hidden className="-mt-2 h-3 w-2.5 shrink-0 border-b-2 border-l-2 border-rule-strong" />}
          <Link href={`/ipam/prefixes/${p.id}`} className="font-mono text-sm font-bold whitespace-nowrap hover:underline">
            {p.cidr}
          </Link>
        </span>
      ),
    },
    { key: "vlan", header: "VLAN", align: "right", sort: (p) => p.vlan_id, className: "w-16", cell: (p) => (p.vlan_id != null ? <VlanPatch id={p.vlan_id} /> : <span className="text-ink-3">—</span>) },
    {
      key: "desc",
      header: "Description",
      sort: (p) => p.description.toLowerCase(),
      cell: (p) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-semibold">{p.description || <span className="font-normal text-ink-3">No description</span>}</span>
          {p.observed && p.observed.name !== p.description && <span className="truncate text-sm text-ink-3">Controller: {p.observed.name}</span>}
        </span>
      ),
    },
    { key: "status", header: "Status", sort: (p) => p.status, cell: (p) => <PrefixStatus p={{ ...p, contains_observed: parents.has(p.id) }} /> },
    { key: "use", header: "Utilisation", sort: (p) => p.utilization_pct, cell: (p) => <UsageBar used={p.used} usable={p.usable} pct={p.utilization_pct} /> },
    { key: "res", header: "Reserved", align: "right", sort: (p) => p.reservations, cell: (p) => <span className="text-ink-2">{p.reservations || "—"}</span> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (p) => (p.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="IP plan"
        description="Prefixes you document, checked against what the controller reports. This is documentation: nothing here changes the controller."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes simulated networks</span> : undefined}
        actions={
          editable && (
            <>
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
                Add prefix
              </Button>
            </>
          )
        }
      />
      {list.error && <Notice tone="crit" className="mb-4">{(list.error as Error).message}</Notice>}
      {adopt.error && <Notice tone="crit" className="mb-4">{(adopt.error as Error).message}</Notice>}

      <div className="flex flex-col gap-[var(--gap)]">
        {adding && <AddPrefix onDone={() => setAdding(false)} />}

        {findings.data ? (
          // With no prefixes yet, "undocumented network" notes only repeat the empty state's call to adopt.
          <FindingsPlate
            findings={rows?.length ? findings.data.items : findings.data.items.filter((f) => f.kind !== "network_undocumented")}
            emptyText="Plan and network agree: every prefix, VLAN and reservation matches what the controller reports."
            action={rows?.length ? adoptButton || undefined : undefined}
          />
        ) : (
          <Skeleton className="h-24 w-full" />
        )}

        <Panel title="Prefixes" bodyClassName="p-0" actions={rows && rows.length > 0 && <span className="tabular text-sm text-ink-3">{rows.length} documented</span>}>
          <DataTable
            caption="Documented prefixes"
            rows={rows}
            columns={columns}
            loading={list.isLoading}
            href={(p) => `/ipam/prefixes/${p.id}`}
            empty={
              <EmptyState icon={<Grid3x3 />} title="No prefixes documented yet" action={adoptButton || undefined}>
                {undocumented.length > 0
                  ? `The controller reports ${undocumented.length} network${undocumented.length > 1 ? "s" : ""}. Adopt them to start the plan from what exists, then add reservations and planned ranges.`
                  : "Add the ranges you use or plan to use. Observed networks appear here once a controller has synced."}
              </EmptyState>
            }
          />
        </Panel>
      </div>
    </>
  );
}

function AddPrefix({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [form, setForm] = useState({ cidr: "", vlan: "", description: "", status: "active" as IpStatus });
  const [errors, setErrors] = useState<{ cidr?: string; vlan?: string; form?: string }>({});
  const create = useMutation({
    mutationFn: () =>
      post<PrefixRow>("/api/ipam/prefixes", {
        cidr: form.cidr.trim(),
        vlan_id: form.vlan ? Number(form.vlan) : null,
        description: form.description.trim(),
        status: form.status,
      }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["ipam"] });
      onDone();
      router.push(`/ipam/prefixes/${p.id}`);
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Couldn't save the prefix.";
      if (/cidr|prefix|documented/i.test(msg)) setErrors({ cidr: msg.replace(/^cidr: (Value error, )?/i, "") });
      else if (/vlan/i.test(msg)) setErrors({ vlan: msg.replace(/^vlan_id: /i, "") });
      else setErrors({ form: msg });
    },
  });
  const validate = () => {
    const next: typeof errors = {};
    if (!/^[0-9a-fA-F:.]+\/\d{1,3}$/.test(form.cidr.trim())) next.cidr = "Enter a prefix such as 10.0.70.0/24 or 2001:db8::/48.";
    if (form.vlan && !(Number(form.vlan) >= 1 && Number(form.vlan) <= 4094)) next.vlan = "VLAN IDs run from 1 to 4094.";
    setErrors(next);
    return Object.keys(next).length === 0;
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (validate()) create.mutate();
  };
  return (
    <Panel title="Add a prefix" id="add-prefix">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-[minmax(0,14rem)_minmax(0,7rem)_minmax(0,1fr)_minmax(0,11rem)] md:items-start">
        <Field label="Prefix" error={errors.cidr} hint="Host bits are cleared for you.">
          {(p) => <Input {...p} autoFocus className="font-mono" value={form.cidr} onChange={(e) => setForm({ ...form, cidr: e.target.value })} onBlur={() => form.cidr && validate()} placeholder="10.0.70.0/24" />}
        </Field>
        <Field label="VLAN" error={errors.vlan}>
          {(p) => <Input {...p} inputMode="numeric" className="font-mono" value={form.vlan} onChange={(e) => setForm({ ...form, vlan: e.target.value.replace(/\D/g, "") })} placeholder="70" />}
        </Field>
        <Field label="Description">
          {(p) => <Input {...p} value={form.description} maxLength={2000} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Lab, cameras, future guest…" />}
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
        <div className={clsx("flex flex-wrap items-center gap-2 md:col-span-4")}>
          <Button type="submit" variant="primary" loading={create.isPending}>Add prefix</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}
