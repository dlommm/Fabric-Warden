"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Plus, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";
import { type Hop, RouteSign, pathHops } from "@/components/route-sign";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { EmptyState, KeyValue, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, ProvenanceLine, StateLabel, VlanPatch, ZonePatch } from "@/components/ui/signs";
import { ApiError, get, patch } from "@/lib/api";
import { describeEvent } from "@/lib/events";
import { ago, dateTime, duration } from "@/lib/format";
import { useSession } from "@/lib/session";
import { ServerSide } from "@/components/servers";
import type { ClientDetail } from "@/lib/types";

const CONNECTION = { WIRED: "Wired", WIRELESS: "Wi-Fi", VPN: "VPN", TELEPORT: "Teleport" } as Record<string, string>;

export default function ClientPage() {
  const { id } = useParams<{ id: string }>();
  const { moduleEnabled, modules } = useSession();
  const enabled = modules.length === 0 || moduleEnabled("client-intel");
  const q = useQuery({ queryKey: ["client", id], queryFn: () => get<ClientDetail>(`/api/client-intel/clients/${id}`), enabled });

  if (!enabled) {
    return (
      <>
        <BackLink />
        <Notice tone="info" title="Client Intelligence is turned off">
          The client view, address history and identity need the Client Intelligence module. <Link href="/modules" className="font-semibold text-accent hover:underline">Open the Module Center</Link>.
        </Notice>
      </>
    );
  }
  if (q.error) {
    return (
      <>
        <BackLink />
        <Notice tone="crit" title="Couldn't load this client">{(q.error as Error).message}</Notice>
      </>
    );
  }
  const c = q.data;
  if (!c) {
    return (
      <>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-72" />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }

  const title = c.alias || c.name;
  const hs = c.history_summary;
  return (
    <>
      <BackLink />
      <PageHeader
        title={title}
        description={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            {c.reservation?.status === "conflict" && c.reservation.prefix && (
              <Link href={`/ipam/prefixes/${c.reservation.prefix.id}?address=${c.ip_address}`} className="hover:underline">
                <StateLabel tone="crit">Address conflict</StateLabel>
              </Link>
            )}
            {c.reservation?.status === "elsewhere" && <StateLabel tone="warn">Not at its reserved address</StateLabel>}
            <span className="font-semibold">{CONNECTION[c.type] ?? c.type}</span>
            {c.network && (
              <span className="inline-flex items-center gap-1.5">
                {c.network.vlan_id != null && <VlanPatch id={c.network.vlan_id} />} {c.network.name}
              </span>
            )}
            {c.zone && <ZonePatch name={c.zone.name} />}
            {c.access_type === "GUEST" && <span className="font-semibold text-warn-text">Guest access</span>}
            {c.alias && <span className="text-ink-3">Reported as <span className="font-mono text-sm">{c.name}</span></span>}
          </span>
        }
        meta={<ProvenanceLine p={c.provenance} />}
      />

      <div className="flex flex-col gap-[var(--gap)]">
        <Panel title="Route from the internet" id="route">
          <RouteSign hops={route(c)} label={`Path from the internet to ${title}`} footer={routeFooter(c)} />
        </Panel>

        <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-2">
          <Panel title="Details" id="details">
            <KeyValue
              items={[
                ["IP address", <span key="ip" className="font-mono">{c.ip_address ?? "—"}</span>],
                ["MAC address", <span key="mac" className="font-mono">{c.mac_address ?? "—"}</span>],
                ["Subnet", c.network ? <span key="s" className="font-mono">{c.network.subnet}</span> : <span className="text-ink-3">Outside every observed network</span>],
                ["Connected via", c.path[0] ? <Link key="u" href={`/inventory/devices/${c.path[0].id}`} className="font-semibold hover:underline">{c.path[0].name}</Link> : "—"],
                ["Connected since", c.connected_at ? `${dateTime(c.connected_at)} (${ago(c.connected_at)})` : "—"],
                ["Owner", c.owner ?? <span className="text-ink-3">Not set</span>],
                ...(moduleEnabled("servers") ? ([["Server side", <ServerSide key="srv" clientId={c.id} />]] as [string, ReactNode][]) : []),
                ["Device type", <span key="fp" className="text-ink-3">Unknown: the UniFi API exposes no fingerprint</span>],
              ]}
            />
            <ReservationLine c={c} />
          </Panel>
          <IdentityPanel c={c} />
        </div>

        <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <Panel title="Addresses" id="addresses" bodyClassName="p-0">
            {c.addresses.length === 0 ? (
              <EmptyState title="No address history yet">History starts with the first sync after Client Intelligence is enabled.</EmptyState>
            ) : (
              <table className="w-full text-left">
                <caption className="sr-only">Addresses this client has used</caption>
                <thead>
                  <tr className="border-b border-rule-strong text-xs font-bold tracking-wide text-ink-2 uppercase">
                    <th scope="col" className="h-9 px-4">Address</th>
                    <th scope="col" className="px-4">Last seen</th>
                    <th scope="col" className="px-4 text-right">Stints</th>
                  </tr>
                </thead>
                <tbody>
                  {c.addresses.map((a) => (
                    <tr key={a.address} className="border-b border-rule last:border-b-0">
                      <th scope="row" className="h-[var(--row-h)] px-4 font-mono text-sm font-bold">
                        {a.address}
                        {a.address === c.ip_address && <span className="ml-2 font-sans text-xs font-semibold text-ok">current</span>}
                      </th>
                      <td className="px-4 text-sm text-ink-2">{ago(a.last_seen_at)}</td>
                      <td className="tabular px-4 text-right text-sm">{a.sightings}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel
            title="Movement"
            id="sightings"
            bodyClassName="p-0"
            actions={hs.tracking_since && <span className="text-sm text-ink-3">Tracked since {dateTime(hs.tracking_since)}</span>}
          >
            {c.sightings.length === 0 ? (
              <EmptyState title="Nothing recorded yet">Each sync extends the current stint; a new one starts when the address or attachment point changes.</EmptyState>
            ) : (
              <ol>
                {c.sightings.map((s, i) => (
                  <li key={s.id} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-0.5 border-b border-rule px-4 py-2 last:border-b-0">
                    <span className="w-28 shrink-0 font-mono text-sm font-bold">{s.ip_address ?? "no address"}</span>
                    <span className="min-w-0 flex-1">
                      via {s.uplink ? <Link href={`/inventory/devices/${s.uplink.id}`} className="font-semibold hover:underline">{s.uplink.name}</Link> : <span className="text-ink-3">unknown</span>}
                      <span className="text-ink-3"> · {CONNECTION[s.type] ?? s.type}</span>
                    </span>
                    <span className="text-sm text-ink-2">
                      {i === 0 ? "now, " : `until ${dateTime(s.last_seen_at)}, `}for {duration(s.duration_seconds)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>

        <Panel title="History" id="history" bodyClassName="p-0">
          {c.events.length === 0 ? (
            <EmptyState title="No recorded events" />
          ) : (
            <ol>
              {c.events.map((e) => (
                <li key={e.id} className="flex min-h-[var(--row-h)] items-center gap-3 border-b border-rule px-4 py-1.5 last:border-b-0">
                  <time dateTime={e.occurred_at} className="w-36 shrink-0 text-sm text-ink-3">{dateTime(e.occurred_at)}</time>
                  <span>{describeEvent(e)}</span>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>
    </>
  );
}

function BackLink() {
  return (
    <Link href="/inventory/clients" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline">
      <ChevronLeft className="size-4" aria-hidden /> Clients
    </Link>
  );
}

function route(c: ClientDetail): Hop[] {
  const hops = pathHops(c.path);
  hops.push({ key: c.id, label: c.alias || c.name, sub: c.ip_address ?? "You are here", tone: "ok", current: true });
  return hops;
}

function routeFooter(c: ClientDetail) {
  if (c.path.length === 0) return c.type === "VPN" ? "VPN clients arrive through the gateway's tunnel; the controller reports no attachment point." : "The controller doesn't report where this client is attached.";
  const n = c.path.length;
  return `Attached to ${c.path[0].name}, ${n} device${n === 1 ? "" : "s"} below the internet, following uplinks reported by the controller.`;
}

function ReservationLine({ c }: { c: ClientDetail }) {
  const r = c.reservation;
  if (!r) return null;
  const where = r.prefix ? <Link href={`/ipam/prefixes/${r.prefix.id}?address=${r.address}`} className="font-mono font-semibold text-accent hover:underline">{r.address}</Link> : <span className="font-mono">{r.address}</span>;
  return (
    <div className="mt-4 border-t border-rule pt-3">
      {r.status === "ok" && <StateLabel tone="ok"><span className="font-normal text-ink">Matches its IP plan reservation at {where}</span></StateLabel>}
      {r.status === "elsewhere" && <StateLabel tone="warn"><span className="font-normal text-ink">Reserved at {where} but using {c.ip_address}</span></StateLabel>}
      {r.status === "conflict" && <StateLabel tone="crit"><span className="font-normal text-ink">Using {where}, which is reserved for another MAC</span></StateLabel>}
    </div>
  );
}

function IdentityPanel({ c }: { c: ClientDetail }) {
  const { can } = useSession();
  const qc = useQueryClient();
  const editable = can("inventory:edit");
  const [form, setForm] = useState({ alias: c.alias ?? "", owner: c.owner ?? "", notes: c.notes });
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch(`/api/inventory/clients/${c.id}`, body),
    onSuccess: () => {
      setErr(null);
      setSaved(true);
      qc.invalidateQueries({ queryKey: ["client", c.id] });
      qc.invalidateQueries({ queryKey: ["clients"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't save."),
  });
  const dirty = form.alias !== (c.alias ?? "") || form.owner !== (c.owner ?? "") || form.notes !== c.notes;
  const addLabel = (e: FormEvent) => {
    e.preventDefault();
    const v = label.trim();
    if (!v) return;
    save.mutate({ labels: [...c.labels, v] });
    setLabel("");
  };
  return (
    <Panel title="Identity" id="identity">
      <p className="mb-4 text-sm text-ink-3">Yours, not the controller&apos;s: discovery never overwrites these.</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSaved(false);
          save.mutate(form);
        }}
        className="grid gap-4 sm:grid-cols-2"
      >
        <Field label="Display name" hint={`Shown instead of “${c.name}”.`}>
          {(p) => <Input {...p} value={form.alias} readOnly={!editable} maxLength={128} onChange={(e) => setForm({ ...form, alias: e.target.value })} placeholder={c.name} />}
        </Field>
        <Field label="Owner">
          {(p) => <Input {...p} value={form.owner} readOnly={!editable} maxLength={128} onChange={(e) => setForm({ ...form, owner: e.target.value })} placeholder="Person or team" />}
        </Field>
        <Field label="Notes" className="sm:col-span-2">
          {(p) => <Textarea {...p} value={form.notes} readOnly={!editable} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder={editable ? "What it is, where it lives, quirks…" : "No notes."} />}
        </Field>
        {editable && (
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <Button type="submit" size="sm" variant="primary" loading={save.isPending} disabled={!dirty}>Save identity</Button>
            {saved && !dirty && <StateLabel tone="ok" className="text-sm">Saved</StateLabel>}
            {err && <span role="alert" className="text-sm font-semibold text-crit">{err}</span>}
          </div>
        )}
      </form>
      <div className="mt-5 border-t border-rule pt-4">
        <p className="text-sm font-semibold">Labels</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {c.labels.length === 0 && <span className="text-sm text-ink-3">No labels.</span>}
          {c.labels.map((l) => (
            <Chip key={l}>
              {l}
              {editable && (
                <button type="button" className="-mr-1 ml-1 rounded-[2px] p-0.5 text-ink-3 hover:text-crit" aria-label={`Remove label ${l}`} onClick={() => save.mutate({ labels: c.labels.filter((x) => x !== l) })}>
                  <X className="size-3.5" aria-hidden />
                </button>
              )}
            </Chip>
          ))}
        </div>
        {editable && (
          <form onSubmit={addLabel} className="mt-3 flex gap-2">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Add a label, e.g. iot" aria-label="New label" maxLength={48} className="max-w-60" />
            <Button type="submit" icon={<Plus />} disabled={!label.trim()}>Add</Button>
          </form>
        )}
      </div>
    </Panel>
  );
}
