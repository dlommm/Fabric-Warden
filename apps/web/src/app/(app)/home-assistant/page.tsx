"use client";

import { useQuery } from "@tanstack/react-query";
import { House, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { type IntegrationSpec, IntegrationConnectionForm, IntegrationConnections, MatchedClient, toFinding, useIntegration } from "@/components/integrations";
import { FindingsPlate } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { ago } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Controller, HaCategory, HaEntity, IntegrationFinding } from "@/lib/types";

const SPEC: IntegrationSpec = {
  api: "/api/home-assistant",
  qk: "home-assistant",
  product: "Home Assistant",
  urlPlaceholder: "http://homeassistant.lan:8123",
  urlHint: "Port 8123, or 80 when Home Assistant runs with the Supervisor",
  secretLabel: "Long-lived access token",
  secretHint: "Your Home Assistant profile › Security. A non-admin user is enough. Stored encrypted.",
  found: (r) => `Kept ${r.entities ?? 0} of ${r.states_seen ?? 0} entities (UPS, temperature, connectivity, router trackers).`,
  deleteText: "Fabric Warden stops reading this Home Assistant and deletes the entities it collected, along with the stored token. Nothing changes in Home Assistant.",
};

const CATEGORY: Record<HaCategory, string> = { ups: "UPS", temperature: "Temperature", connectivity: "Connectivity", tracker: "Tracker" };

/** The entity's state in words, with state colour only where the state is a health signal. */
function EntityState({ e }: { e: HaEntity }) {
  if (!e.available) return <StateLabel tone="muted">{e.state === "unknown" ? "Unknown" : "Unavailable"}</StateLabel>;
  if (e.category === "connectivity") return e.state === "on" ? <StateLabel tone="ok">Connected</StateLabel> : <StateLabel tone="warn">Disconnected</StateLabel>;
  if (e.category === "tracker") return e.state === "home" ? <StateLabel tone="ok">Home</StateLabel> : <StateLabel tone="muted">{e.state === "not_home" ? "Away" : e.state}</StateLabel>;
  if (e.category === "ups" && /on ?battery|discharg/i.test(e.state)) return <StateLabel tone="crit">{e.state}</StateLabel>;
  if (e.device_class === "duration" && e.unit === "s" && e.value != null)
    return (
      <span className="tabular" title={`${e.state} s`}>
        <span className="font-bold">{Math.round(e.value / 60)}</span>
        <span className="text-ink-2"> min</span>
      </span>
    );
  return (
    <span className="tabular">
      <span className="font-bold">{e.state}</span>
      {e.unit && <span className="text-ink-2"> {e.unit}</span>}
    </span>
  );
}

export default function HomeAssistantPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("home-assistant");
  const manage = can("integrations:manage");
  const [adding, setAdding] = useState(false);
  const [confirmDemo, setConfirmDemo] = useState(false);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<"" | HaCategory>("");
  const { conns, demo, undemo, hasDemo } = useIntegration(SPEC, on);
  const ents = useQuery({
    queryKey: ["home-assistant", "entities"],
    queryFn: () => get<{ items: HaEntity[]; counts: Partial<Record<HaCategory, number>> }>("/api/home-assistant/entities"),
    enabled: on,
    refetchInterval: 60_000,
  });
  const findings = useQuery({ queryKey: ["home-assistant", "findings"], queryFn: () => get<{ items: IntegrationFinding[] }>("/api/home-assistant/findings"), enabled: on });
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers"), enabled: on });
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return ents.data?.items.filter((e) => (!cat || e.category === cat) && (!t || [e.name, e.entity_id, e.client?.name ?? "", e.attributes.ip ?? "", e.attributes.mac ?? ""].some((v) => v.toLowerCase().includes(t))));
  }, [ents.data, q, cat]);

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Home Assistant" name="Home Assistant" canManage={can("modules:manage")}>
        Read UPS and temperature sensors and router trackers from Home Assistant, and see which network client each tracker is.
      </ModuleOff>
    );
  }
  const counts = ents.data?.counts ?? {};
  const demoAvailable = !!controllers.data?.some((c) => c.is_demo);

  const columns: Column<HaEntity>[] = [
    {
      key: "name",
      header: "Entity",
      sort: (e) => e.name.toLowerCase(),
      cell: (e) => (
        <span className="flex min-w-0 max-w-[18rem] flex-col">
          <span className="truncate font-bold">{e.name}</span>
          <span className="truncate font-mono text-xs text-ink-3">{e.entity_id}</span>
        </span>
      ),
    },
    {
      key: "cat",
      header: "Kind",
      sort: (e) => e.category,
      className: "whitespace-nowrap",
      cell: (e) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold">{CATEGORY[e.category]}</span>
          {e.category_basis === "name" && <Chip dashed className="h-5 px-1.5 text-[11px]" >by name</Chip>}
        </span>
      ),
    },
    { key: "state", header: "State", sort: (e) => e.value ?? e.state, cell: (e) => <EntityState e={e} /> },
    {
      key: "net",
      header: "On the network",
      cell: (e) =>
        e.category === "tracker" ? (
          <span className="flex flex-col gap-0.5">
            <MatchedClient client={e.client} />
            {(e.attributes.ip || e.attributes.mac) && <span className="font-mono text-xs text-ink-3">{[e.attributes.ip, e.attributes.mac].filter(Boolean).join(" · ")}</span>}
          </span>
        ) : (
          <span className="text-sm text-ink-3">—</span>
        ),
    },
    { key: "changed", header: "Changed", sort: (e) => e.last_changed ?? "", className: "whitespace-nowrap", cell: (e) => <span className="text-sm text-ink-2">{e.last_changed ? `${ago(e.last_changed)}` : "—"}</span> },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (e) => (e.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Home Assistant"
        description="UPS and temperature sensors, connectivity sensors and router-based trackers, read through the Home Assistant REST API. Read-only: no action or service is ever called."
        meta={hasDemo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes a simulated Home Assistant</span> : undefined}
        actions={
          manage && (
            <>
              {hasDemo ? (
                <Button variant="ghost" loading={undemo.isPending} onClick={() => setConfirmDemo(true)}>Remove demo Home Assistant</Button>
              ) : (
                demoAvailable && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Load demo Home Assistant</Button>
              )}
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add connection</Button>
            </>
          )
        }
      />
      {(ents.error || demo.error) && <Notice tone="crit" className="mb-4">{((ents.error || demo.error) as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {adding && <IntegrationConnectionForm spec={SPEC} onDone={() => setAdding(false)} />}
        {findings.data && !!ents.data?.items.length && (
          <FindingsPlate
            label="Home Assistant findings"
            findings={findings.data.items.map((f, i) => toFinding(f, i))}
            emptyText="UPS on mains, temperatures normal, every connectivity sensor connected."
            heading={(n) => `${n} sensor problem${n > 1 ? "s" : ""}`}
          />
        )}
        <Panel title="Entities" bodyClassName="p-0">
          <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
            <Field label="Search" className="w-full sm:w-72">{(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, entity ID, client, address" />}</Field>
            <Field label="Kind" className="w-48">
              {(p) => (
                <Select {...p} value={cat} onChange={(e) => setCat(e.target.value as "" | HaCategory)}>
                  <option value="">All kinds</option>
                  {(Object.keys(CATEGORY) as HaCategory[]).map((k) => (
                    <option key={k} value={k}>{CATEGORY[k]} ({counts[k] ?? 0})</option>
                  ))}
                </Select>
              )}
            </Field>
            <span className="ml-auto self-center text-sm text-ink-3 tabular">{rows ? `${rows.length} shown` : ""}</span>
          </div>
          <DataTable
            caption="Home Assistant entities"
            rows={rows}
            columns={columns}
            loading={ents.isLoading}
            initialSort={{ key: "cat", dir: "asc" }}
            empty={
              ents.data?.items.length ? (
                <EmptyState title="No entities match">Clear the search or kind filter.</EmptyState>
              ) : (
                <EmptyState icon={<House />} title="No Home Assistant connected">
                  Add Home Assistant with a long-lived access token. Fabric Warden only reads states.
                </EmptyState>
              )
            }
          />
          <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">
            Kept: sensors with the temperature device class, sensors named &ldquo;ups&rdquo; (Home Assistant has no UPS device class, so these are marked by name), connectivity binary sensors and router-based trackers. GPS trackers are skipped, so no location is stored. Trackers are matched to network clients by IP, then MAC, then hostname.
          </p>
        </Panel>
        <IntegrationConnections spec={SPEC} items={conns.data?.items} loading={conns.isLoading} manage={manage} />
      </div>
      <Confirm open={confirmDemo} onOpenChange={setConfirmDemo} title="Remove the demo Home Assistant?" confirmLabel="Remove demo Home Assistant" onConfirm={() => undemo.mutate()}>
        The simulated Home Assistant connection and its entities are deleted. Your own connections stay.
      </Confirm>
    </>
  );
}
