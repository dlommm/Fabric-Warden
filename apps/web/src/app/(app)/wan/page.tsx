"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Play, Plus, Radar, Settings2, X } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { MetricWidget, RangePicker } from "@/components/analytics";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, KeyValue, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { DemoTag, DeviceState, StateLabel } from "@/components/ui/signs";
import { ApiError, get, post, put } from "@/lib/api";
import { ago, uptime } from "@/lib/format";
import { formatValue } from "@/lib/metrics";
import { useSession } from "@/lib/session";
import type { ProbeTarget, RangeKey, WanOverview } from "@/lib/types";

export default function WanPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("wan");
  const manage = can("monitoring:manage");
  const qc = useQueryClient();
  const [range, setRange] = useState<RangeKey>("24h");
  const [configuring, setConfiguring] = useState(false);
  const q = useQuery({ queryKey: ["wan"], queryFn: () => get<WanOverview>("/api/wan"), enabled: on, refetchInterval: 60_000 });
  const run = useMutation({
    mutationFn: () => post<{ status: string; error: string | null }>("/api/wan/probe/run"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wan"] });
      qc.invalidateQueries({ queryKey: ["metrics"] });
    },
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="WAN" name="WAN & ISP Operations" canManage={can("modules:manage")}>
        Gateway health, WAN interfaces and an optional latency and loss probe. Enable WAN & ISP Operations to see them.
      </ModuleOff>
    );
  }
  const d = q.data;
  const targets = d?.probe.targets ?? [];
  const demo = d?.wans.some((w) => w.is_demo) || d?.gateways.some((g) => g.is_demo);

  return (
    <>
      <PageHeader
        title="WAN"
        description="The controller's official API lists WAN interfaces by name only, without link state, addresses, throughput or failover events. Latency and loss come from Warden's own probe."
        meta={demo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Interfaces and gateway come from the simulated controller</span> : undefined}
      />
      {q.error && <Notice tone="crit" className="mb-4">{(q.error as Error).message}</Notice>}
      {!d ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <div className="flex flex-col gap-[var(--gap)]">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-[var(--gap)] lg:grid-cols-2">
            <Panel title="Gateway">
              {d.gateways.length === 0 ? (
                <p className="text-ink-3">No gateway reported by the controller.</p>
              ) : (
                d.gateways.map((g) => (
                  <KeyValue
                    key={g.id}
                    items={[
                      ["Device", <Link key="n" href={`/inventory/devices/${g.id}`} className="font-bold hover:underline">{g.name}</Link>],
                      ["State", <DeviceState key="s" state={g.state} />],
                      ["Model", g.model],
                      ["Address", <span key="ip" className="font-mono text-xs">{g.ip_address ?? "—"}</span>],
                      ["CPU / memory", <span key="c" className="tabular">{formatValue("pct", g.cpu_pct)} / {formatValue("pct", g.mem_pct)}</span>],
                      ["Uptime", uptime(g.uptime_sec)],
                      ["Statistics", <span key="o" className="text-ink-3">{g.stats_observed_at ? `observed ${ago(g.stats_observed_at)}` : "not reported"}</span>],
                    ]}
                  />
                ))
              )}
            </Panel>
            <Panel title="WAN interfaces" bodyClassName="p-0">
              {d.wans.length === 0 ? (
                <p className="px-4 py-4 text-ink-3">The controller reports no WAN interfaces, or its version predates the WAN endpoint (Network 10.0).</p>
              ) : (
                <ul>
                  {d.wans.map((w) => (
                    <li key={w.id} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule px-4 py-2 last:border-b-0">
                      <Globe className="size-4 text-ink-3" aria-hidden />
                      <span className="flex-1 font-bold">{w.name}</span>
                      <span className="text-sm text-ink-3">{w.present ? `listed ${ago(w.observed_at)}` : "no longer listed"}</span>
                      {w.is_demo && <DemoTag />}
                    </li>
                  ))}
                  <li className="px-4 py-3 text-sm text-ink-3">Link state, IP, ISP and failover are not exposed by the official API.</li>
                </ul>
              )}
            </Panel>
          </div>

          <Panel
            title="Latency and loss probe"
            actions={
              manage && (
                <>
                  {targets.length > 0 && (
                    <Button size="sm" icon={<Play />} loading={run.isPending} onClick={() => run.mutate()}>Probe now</Button>
                  )}
                  <Button size="sm" icon={<Settings2 />} onClick={() => setConfiguring((v) => !v)} aria-expanded={configuring}>
                    {targets.length ? "Configure" : "Set up"}
                  </Button>
                </>
              )
            }
            bodyClassName="p-0"
          >
            <p className="border-b border-rule px-4 py-3 text-sm text-ink-2">
              A TCP connection from the Warden host to each target, {d.probe.attempts} attempt{d.probe.attempts > 1 ? "s" : ""} a minute. It measures the active path from wherever Warden runs, not each WAN separately, and it is not an ICMP ping.
            </p>
            {configuring && <ProbeForm initial={d.probe} onDone={() => setConfiguring(false)} />}
            {run.data && run.data.status !== "succeeded" && <Notice tone="warn" className="m-4">Probe {run.data.status}{run.data.error ? `: ${run.data.error}` : ""}</Notice>}
            {targets.length === 0 ? (
              !configuring && (
                <EmptyState
                  icon={<Radar />}
                  title="No probe targets"
                  action={manage ? <Button variant="primary" icon={<Plus />} onClick={() => setConfiguring(true)}>Add a target</Button> : undefined}
                >
                  The probe is off until you name up to five hosts to reach, such as your ISP’s DNS server or a public resolver on port 443 or 53.
                </EmptyState>
              )
            ) : (
              <table className="w-full text-left">
                <caption className="sr-only">Probe targets</caption>
                <thead>
                  <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                    <th scope="col" className="h-9 px-4">Target</th>
                    <th scope="col" className="px-4 text-right">Latency</th>
                    <th scope="col" className="px-4">Loss</th>
                    <th scope="col" className="hidden px-4 sm:table-cell">Last probe</th>
                  </tr>
                </thead>
                <tbody>
                  {d.probe.results.map((r) => (
                    <tr key={r.target} className="border-b border-rule last:border-b-0">
                      <td className="h-[var(--row-h)] px-4">
                        <span className="font-bold">{r.label}</span> <span className="font-mono text-xs text-ink-3">{r.host}:{r.port}</span>
                      </td>
                      <td className="tabular px-4 text-right">{formatValue("ms", r.latency_ms)}</td>
                      <td className="px-4">
                        {r.stale || r.loss_pct == null ? (
                          <StateLabel tone="muted">No data</StateLabel>
                        ) : r.loss_pct >= 100 ? (
                          <StateLabel tone="crit">Unreachable</StateLabel>
                        ) : r.loss_pct > 0 ? (
                          <StateLabel tone="warn">{formatValue("pct", r.loss_pct)} lost</StateLabel>
                        ) : (
                          <StateLabel tone="ok">No loss</StateLabel>
                        )}
                      </td>
                      <td className="hidden px-4 text-ink-3 sm:table-cell">{r.last_ts ? ago(r.last_ts) : "never"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          {targets.length > 0 && (
            <>
              <div className="flex flex-wrap items-center gap-4">
                <RangePicker value={range} onChange={setRange} />
              </div>
              <div className="grid grid-cols-1 gap-[var(--gap)] md:grid-cols-6">
                <MetricWidget widget={{ id: "lat", title: "Latency", kind: "line", metric: "wan.latency_ms", entity_ids: [], size: "half", limit: 5 }} range={range} label="Latency" />
                <MetricWidget widget={{ id: "loss", title: "Probe loss", kind: "line", metric: "wan.loss_pct", entity_ids: [], size: "half", limit: 5 }} range={range} label="Probe loss" />
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}

function ProbeForm({ initial, onDone }: { initial: WanOverview["probe"]; onDone: () => void }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<(ProbeTarget & { key: number })[]>(initial.targets.map((t, i) => ({ ...t, key: i })));
  const [attempts, setAttempts] = useState(String(initial.attempts));
  const [timeout, setTimeoutMs] = useState(String(initial.timeout_ms));
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      put("/api/wan/probe", {
        targets: rows.map(({ host, port, label }) => ({ host: host.trim(), port: Number(port), label: label.trim() })),
        attempts: Number(attempts),
        timeout_ms: Number(timeout),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wan"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message.replace(/^\w+: (Value error, )?/, "") : "Couldn't save the probe settings."),
  });
  const update = (key: number, patch: Partial<ProbeTarget>) => setRows((r) => r.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (rows.some((r) => !r.host.trim() || /[/:\s]/.test(r.host.trim()))) return setError("Enter each host as a name or address, without http:// or a path.");
    setError(null);
    save.mutate();
  };
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4 border-b border-rule bg-surface-2 px-4 py-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1.5 text-sm font-semibold">Targets (up to 5)</legend>
        {rows.length === 0 && <p className="text-sm text-ink-3">No targets. Saving with none switches the probe off.</p>}
        {rows.map((r, i) => (
          <div key={r.key} className="grid grid-cols-[minmax(0,1fr)_5.5rem_auto] gap-2 sm:grid-cols-[minmax(0,1fr)_6rem_minmax(0,12rem)_auto]">
            <Input aria-label={`Target ${i + 1} host`} className="font-mono text-sm" value={r.host} onChange={(e) => update(r.key, { host: e.target.value })} placeholder="1.1.1.1" autoFocus={i === rows.length - 1 && !r.host} />
            <Input aria-label={`Target ${i + 1} port`} inputMode="numeric" className="tabular" value={String(r.port)} onChange={(e) => update(r.key, { port: Number(e.target.value.replace(/\D/g, "")) || 0 })} />
            <Input aria-label={`Target ${i + 1} label`} className="hidden sm:block" value={r.label} maxLength={64} onChange={(e) => update(r.key, { label: e.target.value })} placeholder="Label (optional)" />
            <Button variant="ghost" icon={<X />} aria-label={`Remove target ${i + 1}`} onClick={() => setRows((x) => x.filter((y) => y.key !== r.key))} />
          </div>
        ))}
        {rows.length < 5 && (
          <div>
            <Button size="sm" icon={<Plus />} onClick={() => setRows((x) => [...x, { host: "", port: 443, label: "", key: Date.now() }])}>Add target</Button>
          </div>
        )}
      </fieldset>
      <div className="grid max-w-md grid-cols-2 gap-4">
        <Field label="Attempts per run" hint="1 to 10">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={attempts} onChange={(e) => setAttempts(e.target.value.replace(/\D/g, ""))} />}</Field>
        <Field label="Timeout (ms)" hint="200 to 5000">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={timeout} onChange={(e) => setTimeoutMs(e.target.value.replace(/\D/g, ""))} />}</Field>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" loading={save.isPending}>Save probe settings</Button>
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        {error && <span role="alert" className="text-sm font-semibold text-crit">{error}</span>}
      </div>
    </form>
  );
}
