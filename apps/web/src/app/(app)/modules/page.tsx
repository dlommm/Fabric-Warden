"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronDown, ChevronRight, Lock } from "lucide-react";
import { Switch } from "radix-ui";
import { useMemo, useState } from "react";
import { FilterBar, Segmented } from "@/components/filters";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { Chip } from "@/components/ui/signs";
import { ApiError, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ModuleInfo } from "@/lib/types";

type Filter = "all" | "enabled" | "available" | "planned";

const CATEGORY_LABEL: Record<string, string> = {
  core: "Core",
  integration: "Integrations",
  network: "Network",
  observability: "Observability",
  security: "Security",
  infrastructure: "Infrastructure",
  automation: "Automation & change",
  ai: "AI (optional)",
};

interface Pending {
  kind: "enable" | "disable";
  module: ModuleInfo;
  others: string[];
}

export default function ModulesPage() {
  const { modules, presets, can } = useSession();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "crit" | "info"; text: string } | null>(null);
  const manage = can("modules:manage");
  const byId = useMemo(() => new Map(modules.map((m) => [m.id, m])), [modules]);
  const name = (id: string) => byId.get(id)?.name ?? id;

  const refresh = () => qc.invalidateQueries();
  const toggle = useMutation({
    mutationFn: async ({ m, on, force }: { m: ModuleInfo; on: boolean; force?: boolean }) =>
      on
        ? post<{ enabled: string[] }>(`/api/modules/${m.id}/enable`, { with_dependencies: !!force })
        : post<{ disabled: string[] }>(`/api/modules/${m.id}/disable`, { cascade: !!force }),
    onSuccess: (r, { m, on }) => {
      const ids = "enabled" in r ? r.enabled : r.disabled;
      setNotice({ tone: "ok", text: `${on ? "Enabled" : "Disabled"}: ${ids.map(name).join(", ") || m.name}.` });
      setPending(null);
      refresh();
    },
    onError: (e, { m, on }) => {
      if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object") {
        const d = e.detail as { missing?: string[]; dependents?: string[]; message: string };
        const others = on ? d.missing ?? [] : d.dependents ?? [];
        if (others.length && others.every((id) => byId.get(id)?.maturity !== "planned")) {
          setPending({ kind: on ? "enable" : "disable", module: m, others });
          return;
        }
        setNotice({ tone: "crit", text: d.message });
        return;
      }
      setNotice({ tone: "crit", text: e instanceof Error ? e.message : "Couldn't change the module." });
    },
  });
  const preset = useMutation({
    mutationFn: (id: string) => post<{ enabled: string[]; unavailable: string[] }>(`/api/modules/presets/${id}/apply`),
    onSuccess: (r) => {
      setNotice({
        tone: "ok",
        text: `${r.enabled.length ? `Enabled ${r.enabled.map(name).join(", ")}.` : "Everything available in this preset was already on."}${r.unavailable.length ? ` ${r.unavailable.length} planned module${r.unavailable.length > 1 ? "s stay" : " stays"} off until released.` : ""}`,
      });
      refresh();
    },
    onError: (e) => setNotice({ tone: "crit", text: e instanceof Error ? e.message : "Couldn't apply the preset." }),
  });

  const visible = modules.filter(
    (m) =>
      filter === "all" ||
      (filter === "enabled" && m.enabled) ||
      (filter === "available" && m.maturity !== "planned") ||
      (filter === "planned" && m.maturity === "planned"),
  );
  const groups = Object.keys(CATEGORY_LABEL)
    .map((cat) => ({ cat, items: visible.filter((m) => m.category === cat) }))
    .filter((g) => g.items.length);
  const enabledCount = modules.filter((m) => m.enabled).length;
  const plannedCount = modules.filter((m) => m.maturity === "planned").length;

  return (
    <>
      <PageHeader
        title="Module Center"
        description={`${enabledCount} of ${modules.length} modules enabled. ${plannedCount} are on the roadmap and can't be switched on until they ship. Disabling a module stops its jobs and keeps its data.`}
      />
      {notice && <Notice tone={notice.tone} className="mb-4">{notice.text}</Notice>}

      {manage && (
        <Panel title="Presets" id="presets" className="mb-[var(--gap)]">
          <p className="mb-3 text-base text-ink-2">A preset switches on everything in it that is available today. It never switches anything off.</p>
          <div className="flex flex-wrap gap-2">
            {presets.map((p) => {
              const avail = p.modules.filter((id) => byId.get(id)?.maturity !== "planned");
              const done = avail.every((id) => byId.get(id)?.enabled);
              return (
                <Button key={p.id} onClick={() => preset.mutate(p.id)} loading={preset.isPending && preset.variables === p.id} disabled={done} title={done ? "Everything available is already on" : undefined}>
                  {p.name}
                  <span className="text-xs font-normal text-ink-3">{avail.length}/{p.modules.length}</span>
                </Button>
              );
            })}
          </div>
        </Panel>
      )}

      <Panel bodyClassName="p-0">
        <FilterBar>
          <Segmented
            label="Show"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: "All", count: modules.length },
              { value: "enabled", label: "Enabled", count: enabledCount },
              { value: "available", label: "Available", count: modules.length - plannedCount },
              { value: "planned", label: "Planned", count: plannedCount },
            ]}
          />
        </FilterBar>
        {groups.map((g) => (
          <section key={g.cat} aria-labelledby={`cat-${g.cat}`}>
            <h2 id={`cat-${g.cat}`} className="border-b border-rule bg-surface-2 px-4 py-2 text-xs font-bold tracking-wide text-ink-2 uppercase">
              {CATEGORY_LABEL[g.cat]}
            </h2>
            <ul>
              {g.items.map((m) => (
                <ModuleRow key={m.id} m={m} name={name} manage={manage} busy={toggle.isPending && toggle.variables?.m.id === m.id} onToggle={(on) => { setNotice(null); toggle.mutate({ m, on }); }} />
              ))}
            </ul>
          </section>
        ))}
      </Panel>

      {pending && (
        <Confirm
          open
          onOpenChange={(o) => !o && setPending(null)}
          title={pending.kind === "enable" ? `Enable ${pending.module.name}?` : `Disable ${pending.module.name}?`}
          confirmLabel={pending.kind === "enable" ? `Enable ${pending.others.length + 1} modules` : `Disable ${pending.others.length + 1} modules`}
          destructive={pending.kind === "disable"}
          onConfirm={() => toggle.mutate({ m: pending.module, on: pending.kind === "enable", force: true })}
        >
          {pending.kind === "enable" ? "It needs " : "These depend on it and will be disabled too: "}
          <b>{pending.others.map(name).join(", ")}</b>
          {pending.kind === "enable" ? ", which will be enabled as well." : ". Their data is kept."}
        </Confirm>
      )}
    </>
  );
}

function ModuleRow({ m, name, manage, busy, onToggle }: { m: ModuleInfo; name: (id: string) => string; manage: boolean; busy: boolean; onToggle: (on: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const planned = m.maturity === "planned";
  const deps = m.requires.filter((d) => d !== "core");
  return (
    <li className="border-b border-rule last:border-b-0">
      <div className="flex items-start gap-4 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className={clsx("font-bold", planned && "text-ink-2")}>{m.name}</h3>
            {planned ? <Chip>Planned · release {m.roadmap_phase}</Chip> : m.maturity === "preview" ? <Chip>Preview</Chip> : null}
            {m.always_enabled && <Chip><Lock className="mr-1 size-3" aria-hidden /> Required</Chip>}
          </div>
          <p className={clsx("mt-0.5 max-w-[80ch] text-base", planned ? "text-ink-3" : "text-ink-2")}>{m.description}</p>
          {deps.length > 0 && <p className="mt-1 text-sm text-ink-3">Needs {deps.map(name).join(", ")}</p>}
          {!planned && (
            <button type="button" onClick={() => setOpen((o) => !o)} className="mt-1.5 inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline" aria-expanded={open}>
              {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />} Details
            </button>
          )}
          {open && (
            <div className="mt-3 grid max-w-3xl grid-cols-1 gap-3 rounded-[4px] border border-rule bg-surface-2 p-3 text-sm sm:grid-cols-2">
              <div>
                <p className="font-bold">Resources</p>
                <p className="text-ink-2">~{m.resources.memory_mb} MB memory{m.resources.cpu_millicores ? `, ${m.resources.cpu_millicores}m CPU` : ""}{m.resources.disk_mb_per_day ? `, ~${m.resources.disk_mb_per_day} MB/day disk` : ""}</p>
                {m.resources.extra_containers.length > 0 && <p className="text-ink-2">Starts: {m.resources.extra_containers.join(", ")}</p>}
              </div>
              <div>
                <p className="font-bold">Background jobs</p>
                <p className="font-mono text-xs text-ink-2">{m.registered_jobs.join(", ") || "none"}</p>
              </div>
              <div>
                <p className="font-bold">Writes to controllers</p>
                <p className="text-ink-2">{m.performs_writes ? "Yes, through the change engine" : "No"}</p>
              </div>
              <div>
                <p className="font-bold">Data it owns</p>
                <p className="font-mono text-xs text-ink-2">{m.data_ownership.join(", ") || "none"}</p>
              </div>
              {m.dependents.length > 0 && (
                <div className="sm:col-span-2">
                  <p className="font-bold">Used by</p>
                  <p className="text-ink-2">{m.dependents.map(name).join(", ")}</p>
                </div>
              )}
              {m.warnings.map((w) => (
                <Notice key={w} tone="info" className="sm:col-span-2">{w}</Notice>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-0.5">
          <span className={clsx("text-sm font-semibold", m.enabled ? "text-ok" : "text-ink-3")}>{planned ? "Not yet available" : m.enabled ? "On" : "Off"}</span>
          {!planned && (
            <Switch.Root
              checked={m.enabled}
              disabled={!manage || m.always_enabled || busy}
              onCheckedChange={onToggle}
              aria-label={`${m.enabled ? "Disable" : "Enable"} ${m.name}`}
              className="relative h-6 w-11 shrink-0 rounded-full border border-rule-strong bg-surface-3 transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-[#00704a] data-[state=checked]:bg-[#00704a]"
            >
              <Switch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white shadow-[0_1px_2px_rgb(0_0_0/0.25)] transition-transform duration-150 data-[state=checked]:translate-x-[1.3rem]" />
            </Switch.Root>
          )}
        </div>
      </div>
    </li>
  );
}
