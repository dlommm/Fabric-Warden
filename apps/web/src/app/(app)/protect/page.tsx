"use client";

import { useQuery } from "@tanstack/react-query";
import { Cctv, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { type IntegrationSpec, IntegrationConnectionForm, IntegrationConnections, MatchedClient, toFinding, useIntegration } from "@/components/integrations";
import { FindingsPlate } from "@/components/ipam";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { EmptyState, KeyValue, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { Chip, DemoTag, ProvenanceLine, StateLabel, type Tone } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { get } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Controller, IntegrationFinding, ProtectDevice } from "@/lib/types";

const SPEC: IntegrationSpec = {
  api: "/api/protect",
  qk: "protect",
  product: "UniFi Protect",
  urlPlaceholder: "https://unvr.lan",
  urlHint: "The console that runs Protect (a UNVR or the gateway)",
  secretLabel: "API key",
  secretHint: "From that console: Settings › Control Plane › Integrations. Stored encrypted.",
  found: (r) => `Found ${r.cameras ?? 0} camera${r.cameras === 1 ? "" : "s"}.`,
  deleteText: "Fabric Warden stops reading this console and deletes the cameras it collected, along with the stored API key. Nothing changes in Protect.",
};

const CAMERA_STATE: Record<string, [Tone, string]> = {
  CONNECTED: ["ok", "Connected"],
  CONNECTING: ["warn", "Connecting"],
  DISCONNECTED: ["crit", "Disconnected"],
};

function CameraState({ state }: { state: string }) {
  const [tone, word] = CAMERA_STATE[state] ?? ["muted", "Unknown"];
  return <StateLabel tone={tone}>{word}</StateLabel>;
}

const ARM: Record<string, [Tone, string]> = { disabled: ["muted", "Alarm off"], arming: ["info", "Arming"], armed: ["info", "Armed"], breach: ["crit", "Breach"] };

/** Uplink device, then the port: documented by a cable, Wi-Fi, or not reported by the API. */
function Uplink({ d }: { d: ProtectDevice }) {
  if (!d.uplink) return <span className="text-sm text-ink-3">—</span>;
  const poe = d.port?.poe?.state;
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <Link href={`/inventory/devices/${d.uplink.id}`} className="truncate font-semibold text-accent hover:underline">{d.uplink.name}</Link>
      {d.link === "wifi" ? (
        <span className="text-sm text-ink-3">Wi-Fi, no PoE</span>
      ) : d.port ? (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
          <span className="font-semibold tabular">Port {d.port.idx}</span>
          {poe && (poe.toUpperCase() === "UP" ? <StateLabel tone="ok">PoE on</StateLabel> : <StateLabel tone="warn">PoE off</StateLabel>)}
          <Chip className="h-5 px-1.5 text-[11px]">documented · {d.port.cable}</Chip>
        </span>
      ) : (
        <span title={d.port_note ?? undefined}>
          <Chip dashed className="h-5 px-1.5 text-[11px]">Port not reported</Chip>
        </span>
      )}
    </span>
  );
}

export default function ProtectPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("unifi-protect");
  const manage = can("integrations:manage");
  const [adding, setAdding] = useState(false);
  const [confirmDemo, setConfirmDemo] = useState(false);
  const { conns, demo, undemo, hasDemo } = useIntegration(SPEC, on);
  const cameras = useQuery({
    queryKey: ["protect", "cameras"],
    queryFn: () => get<{ items: ProtectDevice[]; counts: { total: number; connected: number; disconnected: number; on_network: number }; notes: string[] }>("/api/protect/cameras"),
    enabled: on,
    refetchInterval: 60_000,
  });
  const nvrs = useQuery({ queryKey: ["protect", "nvrs"], queryFn: () => get<{ items: ProtectDevice[] }>("/api/protect/nvrs"), enabled: on });
  const findings = useQuery({ queryKey: ["protect", "findings"], queryFn: () => get<{ items: IntegrationFinding[] }>("/api/protect/findings"), enabled: on });
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers"), enabled: on });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Cameras" name="UniFi Protect" canManage={can("modules:manage")}>
        Read cameras and the NVR from the Protect Integration API and see which switch and port each camera hangs off.
      </ModuleOff>
    );
  }
  const items = cameras.data?.items;
  const counts = cameras.data?.counts;
  const demoAvailable = !!controllers.data?.some((c) => c.is_demo);

  const columns: Column<ProtectDevice>[] = [
    {
      key: "name",
      header: "Camera",
      sort: (d) => d.name.toLowerCase(),
      cell: (d) => (
        <span className="flex min-w-0 max-w-[16rem] flex-col">
          <span className="truncate font-bold">{d.name}</span>
          <span className="truncate text-sm text-ink-3">{d.type || d.model_key}</span>
        </span>
      ),
    },
    { key: "state", header: "Protect", sort: (d) => d.state, className: "whitespace-nowrap", cell: (d) => <CameraState state={d.state} /> },
    { key: "mac", header: "MAC", sort: (d) => d.mac_address ?? "", cell: (d) => <span className="font-mono text-xs text-ink-2">{d.mac_address ?? "—"}</span> },
    {
      key: "net",
      header: "On the network",
      cell: (d) => (
        <span className="flex flex-col gap-0.5">
          <MatchedClient client={d.client} />
          {d.client?.ip_address && <span className="font-mono text-xs text-ink-2">{d.client.ip_address}</span>}
        </span>
      ),
    },
    { key: "uplink", header: "Uplink and port", cell: (d) => <Uplink d={d} /> },
    {
      key: "smart",
      header: "Smart detection",
      cell: (d) =>
        d.extra.smart_detect_enabled?.length ? (
          <span className="text-sm text-ink-2">{d.extra.smart_detect_enabled.join(", ")}</span>
        ) : (
          <span className="text-sm text-ink-3">Off</span>
        ),
    },
    { key: "demo", header: <span className="sr-only">Data source</span>, className: "w-28", cell: (d) => (d.is_demo ? <DemoTag /> : null) },
  ];

  return (
    <>
      <PageHeader
        title="Cameras"
        description="UniFi Protect cameras and the NVR, read through the official Protect Integration API and joined to the network by MAC address. Read-only: recording, settings and video stay in Protect."
        meta={hasDemo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes a simulated Protect console</span> : undefined}
        actions={
          manage && (
            <>
              {hasDemo ? (
                <Button variant="ghost" loading={undemo.isPending} onClick={() => setConfirmDemo(true)}>Remove demo cameras</Button>
              ) : (
                demoAvailable && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Load demo cameras</Button>
              )}
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add connection</Button>
            </>
          )
        }
      />
      {(cameras.error || demo.error) && <Notice tone="crit" className="mb-4">{((cameras.error || demo.error) as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {adding && <IntegrationConnectionForm spec={SPEC} onDone={() => setAdding(false)} />}
        {findings.data && !!items?.length && (
          <FindingsPlate
            label="Camera findings"
            findings={findings.data.items.map((f, i) => toFinding(f, i))}
            emptyText="Every camera is connected and on the network."
            heading={(n) => `${n} camera problem${n > 1 ? "s" : ""}`}
          />
        )}
        <Panel
          title="Cameras"
          actions={counts && <span className="text-sm text-ink-3 tabular">{counts.connected} of {counts.total} connected · {counts.on_network} on the network</span>}
          bodyClassName="p-0"
        >
          <DataTable
            caption="Cameras"
            rows={items}
            columns={columns}
            loading={cameras.isLoading}
            initialSort={{ key: "name", dir: "asc" }}
            empty={
              <EmptyState icon={<Cctv />} title="No cameras">
                Add the console that runs Protect with an API key. Fabric Warden only reads cameras and the NVR.
              </EmptyState>
            }
          />
          {!!items?.length && (
            <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">
              The Protect API publishes no camera IP address, so cameras are matched by MAC. The Network API does not say which switch port a client uses: document the cable in{" "}
              <Link href="/dcim/cables" className="text-accent hover:underline">Cabling</Link> (far end named after the camera) to pin the port and read its PoE state.
            </p>
          )}
        </Panel>
        {!!nvrs.data?.items.length && (
          <Panel title="NVR">
            <div className="flex flex-col gap-[var(--gap)]">
              {nvrs.data.items.map((n) => {
                const [tone, word] = ARM[n.extra.arm_status ?? ""] ?? ["muted", "Alarm state not reported"];
                return (
                  <div key={n.id} className="flex flex-col gap-3">
                    <KeyValue
                      items={[
                        ["Name", <span key="n" className="font-bold">{n.name}</span>],
                        ["Model", n.type || n.model_key],
                        ["MAC", <span key="m" className="font-mono text-xs">{n.mac_address ?? "—"}</span>],
                        ["On the network", <MatchedClient key="c" client={n.client} />],
                        ["Alarm", <StateLabel key="a" tone={tone}>{word}</StateLabel>],
                      ]}
                    />
                    <ProvenanceLine p={n.provenance} />
                  </div>
                );
              })}
              <p className="text-sm text-ink-3">Storage, disk health and recording state are not in the published Protect API, so they are not shown.</p>
            </div>
          </Panel>
        )}
        <IntegrationConnections spec={SPEC} items={conns.data?.items} loading={conns.isLoading} manage={manage} />
      </div>
      <Confirm open={confirmDemo} onOpenChange={setConfirmDemo} title="Remove the demo cameras?" confirmLabel="Remove demo cameras" onConfirm={() => undemo.mutate()}>
        The simulated Protect connection and its cameras are deleted. Your own connections stay.
      </Confirm>
    </>
  );
}
