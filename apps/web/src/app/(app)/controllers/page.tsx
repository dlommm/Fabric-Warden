"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus, Server } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ControllerForm } from "@/components/controller-form";
import { Button } from "@/components/ui/button";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { DemoTag, StateLabel, controllerTone } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, get, post } from "@/lib/api";
import { ago, titleCase } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Controller } from "@/lib/types";

export default function ControllersPage() {
  const { can } = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const q = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers") });
  const demo = useMutation({
    mutationFn: () => post<Controller>("/api/controllers/demo"),
    onSuccess: async (c) => {
      await post(`/api/controllers/${c.id}/sync`).catch(() => undefined);
      qc.invalidateQueries();
      router.push(`/controllers/${c.id}`);
    },
  });
  const manage = can("controllers:manage");
  const hasDemo = q.data?.some((c) => c.is_demo);

  const columns: Column<Controller>[] = [
    {
      key: "name",
      header: "Name",
      sort: (c) => c.name,
      cell: (c) => (
        <span className="inline-flex items-center gap-2">
          <Link href={`/controllers/${c.id}`} className="font-bold hover:underline">{c.name}</Link>
          {c.is_demo && <DemoTag />}
        </span>
      ),
    },
    { key: "status", header: "Status", sort: (c) => c.status, cell: (c) => <StateLabel tone={controllerTone(c.status)}>{titleCase(c.status)}</StateLabel> },
    { key: "version", header: "Network version", sort: (c) => c.application_version, cell: (c) => <span className="font-mono text-sm">{c.application_version ?? "—"}</span> },
    { key: "address", header: "Address", cell: (c) => <span className="font-mono text-sm text-ink-2">{c.is_demo ? "built-in simulator" : c.base_url}</span> },
    { key: "mode", header: "Mode", cell: () => <span className="text-ink-2">Read-only</span> },
    { key: "inv", header: "Devices / clients", align: "right", cell: (c) => `${c.device_count} / ${c.client_count}` },
    { key: "sync", header: "Last sync", align: "right", sort: (c) => c.last_sync_at, cell: (c) => <span className="text-ink-2">{ago(c.last_sync_at)}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Controllers"
        description="UniFi consoles Fabric Warden reads from. Connections use the official Integration API and are read-only."
        actions={
          manage && (
            <>
              {!hasDemo && (
                <Button icon={<FlaskConical />} onClick={() => demo.mutate()} loading={demo.isPending}>
                  Add demo controller
                </Button>
              )}
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding((a) => !a)} aria-expanded={adding}>
                Connect controller
              </Button>
            </>
          )
        }
      />
      {demo.error && <Notice tone="crit" className="mb-4">{demo.error instanceof ApiError ? demo.error.message : "Couldn't add the demo controller."}</Notice>}
      {adding && (
        <Panel title="Connect a UniFi controller" className="mb-[var(--gap)]" bodyClassName="p-4 max-w-2xl">
          <ControllerForm
            onCreated={async (c) => {
              setAdding(false);
              await post(`/api/controllers/${c.id}/sync`).catch(() => undefined);
              qc.invalidateQueries();
              router.push(`/controllers/${c.id}`);
            }}
          />
        </Panel>
      )}
      <Panel bodyClassName="p-0">
        <DataTable
          caption="Controllers"
          rows={q.data}
          columns={columns}
          loading={q.isLoading}
          href={(c) => `/controllers/${c.id}`}
          empty={<EmptyState icon={<Server />} title="No controllers yet">Connect your UniFi console with an API key, or add the demo controller to explore.</EmptyState>}
        />
      </Panel>
    </>
  );
}
