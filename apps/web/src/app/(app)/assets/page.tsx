"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, Package, Plus } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { ASSET_KIND_WORD as KIND_WORD, ASSET_STATUS_WORD as STATUS_WORD, AssetStatusLabel, DaysLabel, DemoLayoutControl, shortDate, toFinding } from "@/components/infra";
import { FindingsPlate } from "@/components/ipam";
import { ButtonLink, buttonClass } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Asset, AssetSummary, CalendarEntry, InfraFinding } from "@/lib/types";


function money(v: string, cur: string) {
  const n = Number(v);
  try {
    return cur && cur !== "—" ? n.toLocaleString(undefined, { style: "currency", currency: cur }) : n.toLocaleString(undefined, { minimumFractionDigits: 2 });
  } catch {
    return `${cur} ${n.toFixed(2)}`;
  }
}

export default function AssetsPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("asset-lifecycle");
  const editable = can("dcim:edit");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const list = useQuery({ queryKey: ["assets", "list"], queryFn: () => get<{ items: Asset[] }>("/api/assets"), enabled: on });
  const summary = useQuery({ queryKey: ["assets", "summary"], queryFn: () => get<AssetSummary>("/api/assets/summary"), enabled: on });
  const findings = useQuery({ queryKey: ["assets", "findings"], queryFn: () => get<{ items: InfraFinding[] }>("/api/assets/findings"), enabled: on });
  const calendar = useQuery({ queryKey: ["assets", "calendar"], queryFn: () => get<{ items: CalendarEntry[] }>("/api/assets/calendar?days=365"), enabled: on });
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return list.data?.items.filter(
      (a) => (!kind || a.kind === kind) && (!status || a.status === status) && (!t || [a.name, a.asset_tag, a.serial, a.vendor, a.model, a.supplier].some((v) => v.toLowerCase().includes(t))),
    );
  }, [list.data, q, kind, status]);

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Assets" name="Asset Lifecycle" canManage={can("modules:manage")}>
        Track hardware, spares, licences and subscriptions with warranty, support and end-of-life dates.
      </ModuleOff>
    );
  }

  const s = summary.data;
  const columns: Column<Asset>[] = [
    {
      key: "name",
      header: "Asset",
      sort: (a) => a.name.toLowerCase(),
      cell: (a) => (
        <span className="flex min-w-0 flex-col">
          <Link href={`/assets/${a.id}`} className="truncate font-bold hover:underline">{a.name}</Link>
          <span className="truncate font-mono text-xs text-ink-3">{[a.asset_tag, a.serial].filter(Boolean).join(" · ") || [a.vendor, a.model].filter(Boolean).join(" ")}</span>
        </span>
      ),
    },
    { key: "kind", header: "Kind", sort: (a) => a.kind, cell: (a) => KIND_WORD[a.kind] },
    { key: "status", header: "Status", sort: (a) => a.status, className: "whitespace-nowrap", cell: (a) => <AssetStatusLabel s={a.status} /> },
    {
      key: "where",
      header: "Linked to",
      cell: (a) =>
        a.device ? (
          <Link href={`/inventory/devices/${a.device_id}`} className="font-semibold text-accent hover:underline">{a.device}</Link>
        ) : (
          <span className="text-ink-2">{a.rack_item ?? a.location ?? <span className="text-ink-3">—</span>}</span>
        ),
    },
    {
      key: "qty",
      header: "Qty",
      align: "right",
      className: "whitespace-nowrap",
      sort: (a) => a.quantity,
      cell: (a) =>
        a.min_quantity != null && a.quantity < a.min_quantity ? (
          <StateLabel tone="warn" className="justify-end">{`${a.quantity} of ${a.min_quantity}`}</StateLabel>
        ) : (
          <span className="tabular">{a.kind === "licence" && a.seats ? `${a.seats} seats` : a.quantity}</span>
        ),
    },
    {
      key: "next",
      header: "Next date",
      sort: (a) => a.next_date?.days ?? null,
      className: "whitespace-nowrap",
      cell: (a) =>
        a.next_date ? (
          <span className="flex flex-col">
            <span className="text-sm text-ink-2">{a.next_date.label} {shortDate(a.next_date.date)}</span>
            <DaysLabel days={a.next_date.days} autoRenew={a.auto_renew} />
          </span>
        ) : (
          <span className="text-ink-3">—</span>
        ),
    },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (a) => (a.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Assets"
        description="Hardware, spares, licences and subscriptions with the dates that matter: warranty, support, renewal and end of life. Warranty dates on device asset records are included."
        meta={
          s && (
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
              {list.data?.items.some((a) => a.is_demo) && <DemoTag />}
              <span><b className="text-ink tabular">{s.active}</b> active of {s.total}</span>
              <span><b className="text-ink tabular">{s.due_within_90_days}</b> due within 90 days</span>
              {Object.entries(s.spend).map(([cur, v]) => (
                <span key={cur}>Purchase value <b className="text-ink tabular">{money(v, cur)}</b></span>
              ))}
            </span>
          )
        }
        actions={
          <>
            {!list.data?.items.length && <DemoLayoutControl hasDemoRows={false} />}
            <a href="/api/assets/export.csv" className={buttonClass("ghost")} download>
              <Download aria-hidden /> Export CSV
            </a>
            {editable && <ButtonLink href="/assets/new" variant="primary" icon={<Plus />}>Add asset</ButtonLink>}
          </>
        }
      />
      {list.error && <Notice tone="crit" className="mb-4">{(list.error as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {findings.data && !!list.data?.items.length && (
          <FindingsPlate
            label="Lifecycle findings"
            findings={findings.data.items.map(toFinding)}
            emptyText="Nothing has lapsed or is running low."
            heading={(n) => `${n} lapsed date${n > 1 ? "s" : ""} or low stock`}
          />
        )}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] 2xl:grid-cols-[minmax(0,1fr)_22rem] 2xl:items-start">
          <Panel bodyClassName="p-0">
            <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
              <Field label="Search" className="w-full sm:w-64">
                {(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, tag, serial, supplier…" />}
              </Field>
              <Field label="Kind" className="w-40">
                {(p) => (
                  <Select {...p} value={kind} onChange={(e) => setKind(e.target.value)}>
                    <option value="">Any kind</option>
                    {Object.entries(KIND_WORD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Status" className="w-40">
                {(p) => (
                  <Select {...p} value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option value="">Any status</option>
                    {Object.entries(STATUS_WORD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                )}
              </Field>
            </div>
            <DataTable
              caption="Assets"
              rows={rows}
              columns={columns}
              loading={list.isLoading}
              href={(a) => `/assets/${a.id}`}
              initialSort={{ key: "next", dir: "asc" }}
              empty={
                <EmptyState icon={<Package />} title={list.data?.items.length ? "No assets match" : "No assets yet"}>
                  {list.data?.items.length ? "Clear the search or filters." : "Add hardware, spares, licences and subscriptions to get a renewal calendar and expiry warnings."}
                </EmptyState>
              }
            />
          </Panel>
          <Calendar entries={calendar.data?.items} loading={calendar.isLoading} />
        </div>
      </div>
    </>
  );
}

/** Lifecycle dates by month, overdue first. Each line names what ends, not just when. */
function Calendar({ entries, loading }: { entries?: CalendarEntry[]; loading: boolean }) {
  const groups = useMemo(() => {
    const out: { key: string; label: string; items: CalendarEntry[] }[] = [];
    for (const e of entries ?? []) {
      const overdue = e.days < 0;
      const key = overdue ? "overdue" : e.date.slice(0, 7);
      const label = overdue ? "Already passed" : new Date(`${e.date}T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
      let g = out.find((x) => x.key === key);
      if (!g) out.push((g = { key, label, items: [] }));
      g.items.push(e);
    }
    return out;
  }, [entries]);
  return (
    <Panel title="Renewal calendar" bodyClassName="p-0" className="2xl:sticky 2xl:top-4">
      {loading ? (
        <div className="p-4"><Skeleton className="h-40" /></div>
      ) : groups.length === 0 ? (
        <p className="px-4 py-3 text-ink-3">Nothing ends in the next 12 months.</p>
      ) : (
        groups.map((g) => (
          <section key={g.key} aria-label={g.label}>
            <h3 className="border-b border-rule bg-surface-2 px-4 py-1.5 text-sm font-semibold text-ink-3">{g.label}</h3>
            <ul>
              {g.items.map((e) => (
                <li key={`${e.source}-${e.id}-${e.field}`} className="border-b border-rule last:border-b-0">
                  <Link href={e.source === "device" ? `/inventory/devices/${e.id}` : `/assets/${e.id}`} className="flex min-h-[var(--row-h)] items-center gap-3 px-4 py-1.5 hover:bg-surface-2">
                    <span className="w-9 shrink-0 text-center font-mono text-xs leading-tight text-ink-2 tabular">
                      {new Date(`${e.date}T00:00:00`).toLocaleDateString(undefined, { day: "2-digit" })}
                      <span className="block text-[10px] uppercase">{new Date(`${e.date}T00:00:00`).toLocaleDateString(undefined, { month: "short" })}</span>
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-semibold">{e.name}</span>
                      <span className="text-sm text-ink-3">{e.label}{e.auto_renew ? " · renews automatically" : ""}{e.source === "device" ? " · device record" : ""}</span>
                    </span>
                    <DaysLabel days={e.days} autoRenew={e.auto_renew} />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </Panel>
  );
}
