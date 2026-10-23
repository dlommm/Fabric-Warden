"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Plug, PlugZap, Plus, Unplug, Zap } from "lucide-react";
import Link from "next/link";
import { type FormEvent, type ReactNode, useState } from "react";
import { DemoLayoutControl, KIND_LABEL, KindIcon, LoadBar, pctTone, toFinding } from "@/components/infra";
import { FindingsPlate } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel } from "@/components/ui/signs";
import { ApiError, del, get, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { InfraFinding, PoeSwitch, PowerLoad, PowerNode, PowerTree, RackItem } from "@/lib/types";

interface Chains {
  trees: PowerTree[];
  unpowered: { item_id: string; name: string; kind: RackItem["kind"]; rack: string | null }[];
}

const w = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString()} W`);

function Pct({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <span className="text-ink-3">—</span>;
  const tone = pctTone(pct);
  const text = `${pct.toFixed(pct > 0 && pct < 10 ? 1 : 0)}%`;
  return tone === "ok" ? <span className="tabular text-ink-2">{text}</span> : <StateLabel tone={tone}>{text}</StateLabel>;
}

/** One line per source: what it carries now, if the other feed fails, and with planned items added. */
function LoadLine({ l }: { l: PowerLoad }) {
  const bits: ReactNode[] = [
    <span key="n">Now <b className="text-ink tabular">{w(l.load_w)}</b></span>,
    <span key="f">on failover <b className="text-ink tabular">{w(l.failover_load_w)}</b></span>,
  ];
  if (l.projected_failover_w > l.failover_load_w) bits.push(<span key="p">with planned items <b className="text-ink tabular">{w(l.projected_failover_w)}</b></span>);
  if (l.headroom_w != null) bits.push(<span key="h">headroom <b className={clsx("tabular", l.headroom_w < 0 ? "text-crit" : "text-ink")}>{w(l.headroom_w)}</b></span>);
  if (l.runtime_estimate_min != null) bits.push(<span key="r">runtime estimate <b className="text-ink tabular">{l.runtime_estimate_min} min</b></span>);
  return (
    <p className="flex flex-wrap gap-x-2 text-sm text-ink-2">
      {bits.map((b, i) => (
        <span key={i} className="inline-flex gap-2">
          {i > 0 && <span aria-hidden>·</span>}
          {b}
        </span>
      ))}
    </p>
  );
}

function flatten(nodes: PowerNode[], depth = 0, out: { n: PowerNode; depth: number; last: boolean }[] = []) {
  nodes.forEach((n, i) => {
    out.push({ n, depth, last: i === nodes.length - 1 });
    if (n.children?.length) flatten(n.children, depth + 1, out);
  });
  return out;
}

export default function PowerPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("power");
  const editable = can("dcim:edit");
  const qc = useQueryClient();
  const [adding, setAdding] = useState<"feed" | "link" | null>(null);
  const chains = useQuery({ queryKey: ["dcim", "power"], queryFn: () => get<Chains>("/api/power/chains"), enabled: on });
  const findings = useQuery({ queryKey: ["dcim", "power-findings"], queryFn: () => get<{ items: InfraFinding[] }>("/api/power/findings"), enabled: on });
  const poe = useQuery({ queryKey: ["dcim", "poe"], queryFn: () => get<{ items: PoeSwitch[]; note: string }>("/api/power/poe"), enabled: on });
  const unlink = useMutation({ mutationFn: (id: string) => del(`/api/power/links/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["dcim"] }) });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Power" name="Power & Environment" canManage={can("modules:manage")}>
        Document feeds, UPSes and PDUs and see load, failover headroom and runtime estimates.
      </ModuleOff>
    );
  }
  const trees = chains.data?.trees ?? [];
  const demo = trees.some((t) => t.is_demo);

  return (
    <>
      <PageHeader
        title="Power"
        description="Feeds, UPSes and PDUs and what each one carries. Loads are your documented typical draw, not measurements; failover assumes the other supply of a redundant load has failed."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes demo power chains</span> : undefined}
        actions={
          <>
            {!trees.length && <DemoLayoutControl hasDemoRows={false} />}
            {editable && <Button icon={<Zap />} onClick={() => setAdding("feed")}>Add feed</Button>}
            {editable && <Button variant="primary" icon={<Plus />} onClick={() => setAdding("link")}>Connect power</Button>}
          </>
        }
      />
      {(chains.error || unlink.error) && <Notice tone="crit" className="mb-4">{((chains.error || unlink.error) as Error).message}</Notice>}
      <div className="flex min-w-0 flex-col gap-[var(--gap)]">
        {adding === "feed" && <FeedForm onDone={() => setAdding(null)} />}
        {adding === "link" && <LinkForm trees={trees} onDone={() => setAdding(null)} />}
        {findings.data && trees.length > 0 && (
          <FindingsPlate
            label="Power findings"
            findings={findings.data.items.map(toFinding)}
            emptyText="Every source has headroom and every redundant load reaches two feeds."
            heading={(n) => `${n} power risk${n > 1 ? "s" : ""}`}
          />
        )}
        {chains.isLoading && <Skeleton className="h-48" />}
        {!chains.isLoading && trees.length === 0 && (
          <Panel>
            <EmptyState icon={<PlugZap />} title="No power documented">
              Add a feed (a circuit), give your UPS and PDU rack items a capacity, then connect each load to the outlet that powers it.
            </EmptyState>
          </Panel>
        )}
        {trees.map((t) => (
          <Panel key={t.id} bodyClassName="p-0" className="min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-rule px-4 py-2.5">
              <h2 className="flex min-w-0 flex-wrap items-baseline gap-x-3 text-md font-bold">
                <span className="inline-flex items-center gap-2">
                  {t.kind === "feed" ? <Zap className="size-4 text-ink-3" aria-hidden /> : <KindIcon kind={t.kind} className="text-ink-3" />}
                  {t.name}
                </span>
                <span className="text-sm font-normal text-ink-3">
                  {t.kind === "feed" ? `${t.voltage_v} V · ${t.amperage_a} A · ${t.max_utilisation_pct}% continuous` : `${KIND_LABEL[t.kind]} with no documented feed`}
                </span>
              </h2>
              <span className="flex flex-wrap items-center gap-2">
                {t.is_demo && <DemoTag />}
                <LoadBar load={t.failover_load_w} capacity={t.capacity_w} pct={t.failover_utilisation_pct} />
              </span>
            </div>
            <div className="border-b border-rule px-4 py-2.5">
              <LoadLine l={t} />
            </div>
            {t.children.length === 0 ? (
              <p className="px-4 py-3 text-ink-3">Nothing connected yet.</p>
            ) : (
              <div className="relative overflow-x-auto">
                <table className="w-full border-collapse text-left">
                  <caption className="sr-only">Loads on {t.name}</caption>
                  <thead>
                    <tr className="border-b border-rule-strong">
                      {["Connected", "Outlet", "Draw", "Load", "On failover", ""].map((h, i) => (
                        <th key={i} scope="col" className={clsx("h-9 px-[var(--row-pad)] text-sm font-semibold whitespace-nowrap text-ink-3", i >= 1 && i <= 2 && "text-right", i >= 2 && i <= 4 && "hidden md:table-cell")}>
                          {h || <span className="sr-only">Actions</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {flatten(t.children).map(({ n, depth }) => {
                      const source = n.kind === "ups" || n.kind === "pdu";
                      return (
                        <tr key={n.link_id} className="border-b border-rule last:border-b-0">
                          <td className="h-[var(--row-h)] px-[var(--row-pad)] py-1.5">
                            <span className="flex items-center gap-2" style={{ paddingLeft: `${depth * 1.5}rem` }}>
                              {depth > 0 && <span aria-hidden className="-mt-2 h-3 w-2.5 shrink-0 border-b-2 border-l-2 border-rule-strong" />}
                              <KindIcon kind={n.kind} className="text-ink-3" />
                              <span className={clsx("truncate", source ? "font-bold" : "font-semibold")}>{n.name}</span>
                              {n.inputs > 1 && <span className="text-sm text-ink-3">input {n.input} of {n.inputs}</span>}
                              {n.status === "planned" && <StateLabel tone="info" className="text-sm">Planned</StateLabel>}
                            </span>
                            {/* Phones: the columns hidden below md read as one line under the name. */}
                            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-2 md:hidden" style={{ paddingLeft: `${depth * 1.5 + 1.5}rem` }}>
                              {n.draw_w != null && <span className="tabular">{w(n.draw_w)}</span>}
                              {source && <LoadBar load={n.load_w ?? 0} capacity={n.capacity_w ?? null} pct={n.utilisation_pct ?? null} />}
                              {source && n.failover_utilisation_pct != null && <span className="inline-flex gap-1">failover <Pct pct={n.failover_utilisation_pct} /></span>}
                              {n.runtime_estimate_min != null && <span className="tabular">{n.runtime_estimate_min} min runtime</span>}
                            </span>
                          </td>
                          <td className="px-[var(--row-pad)] text-right font-mono text-xs text-ink-2 tabular">{n.outlet ?? "—"}</td>
                          <td className="hidden px-[var(--row-pad)] text-right tabular md:table-cell">{n.draw_w != null ? w(n.draw_w) : <span className="text-ink-3">—</span>}</td>
                          <td className="hidden px-[var(--row-pad)] md:table-cell">
                            {source ? <LoadBar load={n.load_w ?? 0} capacity={n.capacity_w ?? null} pct={n.utilisation_pct ?? null} /> : <span className="text-sm text-ink-3">{n.rack ?? ""}</span>}
                          </td>
                          <td className="hidden px-[var(--row-pad)] md:table-cell">
                            {source ? (
                              <span className="flex flex-col">
                                <Pct pct={n.failover_utilisation_pct} />
                                {n.runtime_estimate_min != null && <span className="text-sm text-ink-3 tabular">{n.runtime_estimate_min} min runtime</span>}
                              </span>
                            ) : null}
                          </td>
                          <td className="px-[var(--row-pad)] text-right">
                            {editable && <Button size="sm" variant="ghost" icon={<Unplug />} aria-label={`Disconnect ${n.name} from this outlet`} onClick={() => unlink.mutate(n.link_id)} />}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        ))}
        {chains.data && chains.data.unpowered.length > 0 && (
          <Panel title={`Installed but not connected (${chains.data.unpowered.length})`} bodyClassName="p-0">
            <ul>
              {chains.data.unpowered.map((u) => (
                <li key={u.item_id} className="flex min-h-[var(--row-h)] items-center gap-2 border-b border-rule px-[var(--row-pad)] last:border-b-0">
                  <KindIcon kind={u.kind} className="text-ink-3" />
                  <span className="font-semibold">{u.name}</span>
                  <span className="text-sm text-ink-3">{u.rack}</span>
                </li>
              ))}
            </ul>
          </Panel>
        )}
        <PoePanel data={poe.data} loading={poe.isLoading} />
      </div>
    </>
  );
}

function PoePanel({ data, loading }: { data?: { items: PoeSwitch[]; note: string }; loading: boolean }) {
  return (
    <Panel title="PoE delivery" bodyClassName="p-0">
      {data && <p className="border-b border-rule px-4 py-2.5 text-sm text-ink-2">{data.note}</p>}
      {loading ? (
        <div className="p-4"><Skeleton className="h-20" /></div>
      ) : !data?.items.length ? (
        <p className="px-4 py-3 text-ink-3">No PoE ports reported.</p>
      ) : (
        <ul>
          {data.items.map((s) => (
            <li key={s.device_id} className="flex flex-col gap-2 border-b border-rule px-4 py-3 last:border-b-0 md:flex-row md:items-center md:gap-6">
              <span className="flex min-w-0 flex-col md:w-56 md:shrink-0">
                <Link href={`/inventory/devices/${s.device_id}`} className="truncate font-bold hover:underline">{s.name}</Link>
                <span className="truncate text-sm text-ink-3">{s.model}</span>
              </span>
              <ol className="flex flex-wrap gap-1" aria-label={`${s.name} PoE ports`}>
                {s.ports.map((p) => {
                  const up = p.state === "UP";
                  return (
                    <li
                      key={p.idx}
                      title={`Port ${p.idx}: ${up ? "delivering power" : "not delivering"}${p.standard ? ` · ${p.standard}${p.type ? ` type ${p.type}` : ""}` : ""}`}
                      className={clsx(
                        "flex h-6 min-w-6 items-center justify-center rounded-[3px] px-1 font-mono text-[11px] tabular",
                        up ? "bg-ok-soft font-bold text-ok ring-1 ring-ok/60 ring-inset" : "border border-rule-strong bg-surface text-ink-3",
                      )}
                    >
                      <span className="sr-only">Port </span>
                      {p.idx}
                      <span className="sr-only">{up ? " delivering power" : " idle"}</span>
                    </li>
                  );
                })}
              </ol>
              <span className="text-sm text-ink-2 md:ml-auto md:text-right">
                <b className="text-ink tabular">{s.active_ports}</b> of {s.poe_ports} delivering · at most <b className="text-ink tabular">{s.upper_bound_w} W</b>
              </span>
              {s.is_demo && <DemoTag className="self-start md:self-center" />}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function FeedForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", voltage_v: "230", amperage_a: "16", max_utilisation_pct: "80" });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => post("/api/power/feeds", { name: f.name.trim(), voltage_v: Number(f.voltage_v), amperage_a: Number(f.amperage_a), max_utilisation_pct: Number(f.max_utilisation_pct) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const cap = Number(f.voltage_v) * Number(f.amperage_a) * (Number(f.max_utilisation_pct) / 100);
  const digits = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value.replace(/\D/g, "") });
  return (
    <Panel title="Add a feed">
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (!f.name.trim()) return setError("Name the circuit.");
          save.mutate();
        }}
        noValidate
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_7rem_7rem_9rem]"
      >
        <Field label="Name" error={error}>{(p) => <Input {...p} autoFocus value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Circuit 12 (closet)" />}</Field>
        <Field label="Voltage (V)">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.voltage_v} onChange={digits("voltage_v")} />}</Field>
        <Field label="Breaker (A)">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.amperage_a} onChange={digits("amperage_a")} />}</Field>
        <Field label="Continuous limit (%)" hint="80% is the usual breaker rule">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.max_utilisation_pct} onChange={digits("max_utilisation_pct")} />}</Field>
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2 lg:col-span-4">
          <Button type="submit" variant="primary" loading={save.isPending}>Add feed</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {cap > 0 && <span className="text-sm text-ink-2">Usable capacity <b className="text-ink tabular">{Math.round(cap).toLocaleString()} W</b></span>}
        </div>
      </form>
    </Panel>
  );
}

function LinkForm({ trees, onDone }: { trees: PowerTree[]; onDone: () => void }) {
  const qc = useQueryClient();
  const items = useQuery({ queryKey: ["dcim", "items"], queryFn: () => get<{ items: (RackItem & { rack: string | null })[] }>("/api/dcim/items") });
  const feeds = trees.filter((t) => t.kind === "feed");
  const all = (items.data?.items ?? []).filter((i) => i.status !== "removed");
  const sources = all.filter((i) => i.kind === "ups" || i.kind === "pdu");
  const [f, setF] = useState({ source: "", outlet: "", target: "", input: "A" });
  const [error, setError] = useState<string | null>(null);
  const src = sources.find((s) => `item:${s.id}` === f.source);
  const save = useMutation({
    mutationFn: () => {
      const [kind, id] = f.source.split(":");
      return post("/api/power/links", {
        source_feed_id: kind === "feed" ? id : null,
        source_item_id: kind === "item" ? id : null,
        source_outlet: f.outlet ? Number(f.outlet) : null,
        target_item_id: f.target,
        target_input: f.input,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dcim"] });
      setF({ ...f, outlet: f.outlet ? String(Number(f.outlet) + 1) : "", target: "" });
      setError(null);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  return (
    <Panel title="Connect power">
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (!f.source || !f.target) return setError("Choose a source and what it powers.");
          save.mutate();
        }}
        noValidate
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_7rem_minmax(0,1fr)_7rem]"
      >
        <Field label="From">
          {(p) => (
            <Select {...p} value={f.source} onChange={(e) => setF({ ...f, source: e.target.value, outlet: "" })}>
              <option value="">Choose a feed, UPS or PDU…</option>
              {feeds.length > 0 && <optgroup label="Feeds">{feeds.map((t) => <option key={t.id} value={`feed:${t.id}`}>{t.name}</option>)}</optgroup>}
              {sources.length > 0 && <optgroup label="UPS and PDU">{sources.map((s) => <option key={s.id} value={`item:${s.id}`}>{s.name}{s.rack ? ` · ${s.rack}` : ""}</option>)}</optgroup>}
            </Select>
          )}
        </Field>
        <Field label="Outlet">
          {(p) => (
            <Select {...p} value={f.outlet} disabled={!src} onChange={(e) => setF({ ...f, outlet: e.target.value })}>
              <option value="">—</option>
              {Array.from({ length: src?.outlet_count ?? 0 }, (_, n) => <option key={n + 1} value={n + 1}>{n + 1}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Powers">
          {(p) => (
            <Select {...p} value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })}>
              <option value="">Choose a rack item…</option>
              {all.filter((i) => `item:${i.id}` !== f.source && !["patch_panel", "cable_manager", "blank", "shelf"].includes(i.kind)).map((i) => (
                <option key={i.id} value={i.id}>{i.name}{i.rack ? ` · ${i.rack}` : ""}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Supply" hint="A or B for dual PSUs">
          {(p) => (
            <Select {...p} value={f.input} onChange={(e) => setF({ ...f, input: e.target.value })}>
              {["A", "B", "C", "D"].map((x) => <option key={x}>{x}</option>)}
            </Select>
          )}
        </Field>
        <div className="flex flex-wrap items-center gap-2 sm:col-span-2 lg:col-span-4">
          <Button type="submit" variant="primary" icon={<Plug />} loading={save.isPending}>Connect</Button>
          <Button variant="ghost" onClick={onDone}>Done</Button>
          {error && <span role="alert" className="text-sm font-semibold text-crit">{error}</span>}
          {save.isSuccess && !error && <StateLabel tone="ok" className="text-sm">Connected. Choose the next load.</StateLabel>}
        </div>
      </form>
    </Panel>
  );
}
