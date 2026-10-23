"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Blocks, Bot, ChartLine, Check, ChevronDown, ChevronRight, CircleDashed, Cpu, Lock, Network, PlugZap, Rows3, Shield, Workflow } from "lucide-react";
import { Switch } from "radix-ui";
import { useMemo, useState } from "react";
import { FilterBar, SearchInput, Segmented } from "@/components/filters";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { Chip, StateLabel } from "@/components/ui/signs";
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

const CATEGORY_ICON: Record<string, typeof Cpu> = {
  core: Cpu,
  integration: PlugZap,
  network: Network,
  observability: ChartLine,
  security: Shield,
  infrastructure: Rows3,
  automation: Workflow,
  ai: Bot,
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
  const [term, setTerm] = useState("");
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

  const needle = term.trim().toLowerCase();
  const visible = modules.filter(
    (m) =>
      (!needle || m.name.toLowerCase().includes(needle) || m.description.toLowerCase().includes(needle)) &&
      (filter === "all" ||
      (filter === "enabled" && m.enabled) ||
      (filter === "available" && m.maturity !== "planned") ||
      (filter === "planned" && m.maturity === "planned")),
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

      <section aria-label="Modules at a glance" className="plate-raised mb-[var(--gap)] flex flex-col gap-4 px-5 py-4">
        <dl className="flex flex-wrap gap-x-8 gap-y-2">
          <div className="flex items-baseline gap-2">
            <dd className="tabular text-2xl font-bold">{enabledCount}</dd>
            <dt className="text-sm text-ink-2"><StateLabel tone="ok" className="font-normal">enabled</StateLabel></dt>
          </div>
          <div className="flex items-baseline gap-2">
            <dd className="tabular text-2xl font-bold">{modules.length - plannedCount - enabledCount}</dd>
            <dt className="text-sm text-ink-2"><StateLabel tone="muted" className="font-normal">available, off</StateLabel></dt>
          </div>
          <div className="flex items-baseline gap-2">
            <dd className="tabular text-2xl font-bold text-ink-3">{plannedCount}</dd>
            <dt className="text-sm text-ink-3">planned</dt>
          </div>
        </dl>
        {manage && (
          <div className="border-t border-rule pt-4">
            <p className="mb-2.5 text-sm text-ink-2">
              <b className="text-ink">Presets</b> switch on everything in them that is available today. A preset never switches anything off.
            </p>
            <div className="flex flex-wrap gap-2">
              {presets.map((p) => {
                const avail = p.modules.filter((id) => byId.get(id)?.maturity !== "planned");
                const done = avail.every((id) => byId.get(id)?.enabled);
                return (
                  <Button key={p.id} size="sm" onClick={() => preset.mutate(p.id)} loading={preset.isPending && preset.variables === p.id} disabled={done} title={done ? "Everything available is already on" : undefined} icon={done ? <Check /> : undefined}>
                    {p.name}
                    <span className="text-xs font-normal text-ink-3">{avail.length}/{p.modules.length}</span>
                  </Button>
                );
              })}
            </div>
          </div>
        )}
      </section>

      <div className="plate mb-[var(--gap)] overflow-hidden">
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
          <div className="sm:ml-auto">
            <SearchInput value={term} onChange={setTerm} placeholder="Find a module" label="Find a module" />
          </div>
        </FilterBar>
      </div>
      <div className="flex flex-col gap-7">
        {groups.length === 0 && (
          <Panel>
            <EmptyState icon={<Blocks />} title="No module matches">Try another name, or show all modules.</EmptyState>
          </Panel>
        )}
        {groups.map((g) => {
          const Icon = CATEGORY_ICON[g.cat] ?? Blocks;
          const on = g.items.filter((m) => m.enabled).length;
          return (
            <section key={g.cat} aria-labelledby={`cat-${g.cat}`}>
              <h2 id={`cat-${g.cat}`} className="mb-3 flex items-center gap-2.5 text-md font-bold">
                <span className="grid size-7 place-items-center rounded-[6px] bg-accent-soft text-accent" aria-hidden>
                  <Icon className="size-4" />
                </span>
                {CATEGORY_LABEL[g.cat]}
                <span className="tabular text-sm font-normal text-ink-3">{on} of {g.items.length} on</span>
              </h2>
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] lg:grid-cols-2 2xl:grid-cols-3">
                {g.items.map((m) => (
                  <ModuleCard key={m.id} m={m} byId={byId} name={name} manage={manage} busy={toggle.isPending && toggle.variables?.m.id === m.id} onToggle={(on) => { setNotice(null); toggle.mutate({ m, on }); }} />
                ))}
              </ul>
            </section>
          );
        })}
      </div>

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

function ModuleCard({
  m,
  byId,
  name,
  manage,
  busy,
  onToggle,
}: {
  m: ModuleInfo;
  byId: Map<string, ModuleInfo>;
  name: (id: string) => string;
  manage: boolean;
  busy: boolean;
  onToggle: (on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const planned = m.maturity === "planned";
  const deps = m.requires.filter((d) => d !== "core");
  const missing = deps.filter((d) => !byId.get(d)?.enabled);
  const state = planned ? (
    <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink-3">
      <CircleDashed className="size-4" aria-hidden /> Release {m.roadmap_phase}, not yet available
    </span>
  ) : m.enabled ? (
    <StateLabel tone="ok" className="text-sm">{m.always_enabled ? "On, required" : "On"}</StateLabel>
  ) : missing.length ? (
    <StateLabel tone="muted" className="text-sm">{`Off · needs ${missing.length} more`}</StateLabel>
  ) : (
    <StateLabel tone="muted" className="text-sm">Off</StateLabel>
  );
  return (
    <li
      className={clsx(
        "flex min-w-0 flex-col",
        planned ? "rounded-[var(--r-plate)] border border-dashed border-rule-strong bg-transparent" : m.enabled ? "plate" : "plate-quiet",
      )}
    >
      <div className="flex items-start gap-3 px-4 pt-3.5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className={clsx("font-bold", planned ? "text-ink-2" : "text-ink")}>{m.name}</h3>
            {m.maturity === "preview" && <Chip>Preview</Chip>}
            {m.always_enabled && <Chip><Lock className="mr-1 size-3" aria-hidden /> Required</Chip>}
          </div>
          <div className="mt-0.5">{state}</div>
        </div>
        {!planned && (
          <Switch.Root
            checked={m.enabled}
            disabled={!manage || m.always_enabled || busy}
            onCheckedChange={onToggle}
            aria-label={`${m.enabled ? "Disable" : "Enable"} ${m.name}`}
            className="relative mt-0.5 h-6 w-11 shrink-0 rounded-full border border-rule-strong bg-surface-3 shadow-[inset_0_1px_2px_rgb(var(--shadow-tint)/0.15)] transition-colors duration-[var(--dur)] disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-[#00704a] data-[state=checked]:bg-[#00704a]"
          >
            <Switch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white shadow-[0_1px_3px_rgb(0_0_0/0.3)] transition-transform duration-[var(--dur)] ease-[var(--ease-out)] data-[state=checked]:translate-x-[1.3rem]" />
          </Switch.Root>
        )}
      </div>
      <p className={clsx("mt-2 px-4 text-base", planned ? "text-ink-3" : "text-ink-2")}>{m.description}</p>
      {deps.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 px-4">
          <span className="text-sm text-ink-3">Needs</span>
          {deps.map((d) => {
            const on = byId.get(d)?.enabled;
            return (
              <Chip key={d} dashed={!on} className={on ? undefined : "text-ink-3"}>
                {on ? <Check className="mr-1 size-3 text-ok" aria-hidden /> : <CircleDashed className="mr-1 size-3" aria-hidden />}
                {name(d)}
                <span className="sr-only">{on ? " (on)" : " (off)"}</span>
              </Chip>
            );
          })}
        </div>
      )}
      <div className="mt-auto px-4 pt-2 pb-3">
        {!planned && (
          <button type="button" onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline" aria-expanded={open}>
            {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />} Details
          </button>
        )}
        {open && (
          <div className="anim-pop mt-3 grid grid-cols-1 gap-3 rounded-[var(--r-control)] border border-rule bg-surface-2 p-3 text-sm sm:grid-cols-2">
            <div>
              <p className="font-bold">Resources</p>
              <p className="text-ink-2">~{m.resources.memory_mb} MB memory{m.resources.cpu_millicores ? `, ${m.resources.cpu_millicores}m CPU` : ""}{m.resources.disk_mb_per_day ? `, ~${m.resources.disk_mb_per_day} MB/day disk` : ""}</p>
              {m.resources.extra_containers.length > 0 && <p className="text-ink-2">Starts: {m.resources.extra_containers.join(", ")}</p>}
            </div>
            <div>
              <p className="font-bold">Background jobs</p>
              <p className="font-mono text-xs break-words text-ink-2">{m.registered_jobs.join(", ") || "none"}</p>
            </div>
            <div>
              <p className="font-bold">Writes to controllers</p>
              <p className="text-ink-2">{m.performs_writes ? "Yes, through the change engine" : "No"}</p>
            </div>
            <div>
              <p className="font-bold">Data it owns</p>
              <p className="font-mono text-xs break-words text-ink-2">{m.data_ownership.join(", ") || "none"}</p>
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
    </li>
  );
}
