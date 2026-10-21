"use client";

// The physical side of an observed device on its detail page: where it is
// racked, which documented cables land on its ports, linked lifecycle records
// and wiki pages that mention it. Each part appears only with its module.
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";
import { ASSET_KIND_WORD, DaysLabel } from "@/components/infra";
import { KeyValue, Panel } from "@/components/ui/layout-bits";
import { get } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Asset, CableEndView, DocSummary } from "@/lib/types";

interface Placement {
  item_id: string;
  rack_id: string;
  rack: string | null;
  location: string | null;
  position: number | null;
  height_u: number;
  face: string;
}

export function DevicePhysical({ deviceId, name }: { deviceId: string; name: string }) {
  const { moduleEnabled } = useSession();
  const dcim = moduleEnabled("dcim");
  const cabling = moduleEnabled("cabling");
  const assets = moduleEnabled("asset-lifecycle");
  const docs = moduleEnabled("docs");
  const placement = useQuery({ queryKey: ["dcim", "placement", deviceId], queryFn: () => get<{ placement: Placement | null }>(`/api/dcim/devices/${deviceId}/placement`), enabled: dcim });
  const ports = useQuery({
    queryKey: ["dcim", "device-ports", deviceId],
    queryFn: () => get<{ items: { port_idx: number; cable: { id: string; label: string; type: string }; far: CableEndView; hops: number }[] }>(`/api/cabling/devices/${deviceId}/ports`),
    enabled: cabling,
  });
  const linked = useQuery({ queryKey: ["assets", "device", deviceId], queryFn: () => get<{ items: Asset[] }>(`/api/assets?device_id=${deviceId}`), enabled: assets });
  const pages = useQuery({ queryKey: ["docs", "backlinks", deviceId], queryFn: () => get<{ items: DocSummary[] }>(`/api/wiki/backlinks?type=device&id=${deviceId}`), enabled: docs });
  if (!dcim && !cabling && !assets && !docs) return null;

  const p = placement.data?.placement;
  const facts: [string, ReactNode][] = [];
  if (dcim)
    facts.push([
      "Rack",
      p ? (
        <Link key="r" href={`/dcim/racks/${p.rack_id}?item=${p.item_id}`} className="font-semibold text-accent hover:underline">
          {[p.location, p.rack].filter(Boolean).join(" › ")}
          {p.position != null ? ` · U${p.position}${p.height_u > 1 ? `–U${p.position + p.height_u - 1}` : ""} ${p.face}` : " · not placed"}
        </Link>
      ) : (
        <span key="r" className="text-ink-3">Not placed in a rack</span>
      ),
    ]);
  if (cabling)
    facts.push([
      "Cabled ports",
      ports.data?.items.length ? (
        <ul key="c" className="flex flex-col gap-0.5">
          {ports.data.items.map((x) => (
            <li key={x.port_idx} className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-xs text-ink-2 tabular">Port {x.port_idx}</span>
              <span aria-hidden>→</span>
              <span className="font-semibold">{x.far.summary}</span>
              <Link href={`/dcim/cables?trace=${x.cable.id}`} className="font-mono text-xs text-accent hover:underline">
                {x.cable.label}{x.hops > 1 ? ` +${x.hops - 1}` : ""}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <span key="c" className="text-ink-3">No documented cables</span>
      ),
    ]);
  if (assets)
    facts.push([
      "Lifecycle",
      linked.data?.items.length ? (
        <ul key="a" className="flex flex-col gap-0.5">
          {linked.data.items.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-x-2">
              <Link href={`/assets/${a.id}`} className="font-semibold text-accent hover:underline">{a.name}</Link>
              <span className="text-sm text-ink-3">{ASSET_KIND_WORD[a.kind]}</span>
              {a.next_date && <span className="text-sm text-ink-2">{a.next_date.label}</span>}
              {a.next_date && <DaysLabel days={a.next_date.days} autoRenew={a.auto_renew} />}
            </li>
          ))}
        </ul>
      ) : (
        <span key="a" className="text-ink-3">No linked records</span>
      ),
    ]);
  if (docs)
    facts.push([
      "Mentioned in",
      pages.data?.items.length ? (
        <span key="d" className="flex flex-wrap gap-x-3">
          {pages.data.items.map((d) => <Link key={d.id} href={`/docs/${d.slug}`} className="font-semibold text-accent hover:underline">{d.title}</Link>)}
        </span>
      ) : (
        <span key="d" className="text-ink-3">No pages reference {name}</span>
      ),
    ]);
  return (
    <Panel title="Physical and documentation" id="physical">
      <KeyValue items={facts} />
    </Panel>
  );
}
