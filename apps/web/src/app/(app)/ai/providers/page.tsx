"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, Plus } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { BudgetLine, money, PROVIDER_KIND, tokens } from "@/components/ai";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, KeyValue, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AiProbe, AiProvider, AiProviderKindInfo, AiProviderPreset, AiUsageReport } from "@/lib/types";

interface Kinds {
  kinds: AiProviderKindInfo[];
  presets: AiProviderPreset[];
  prices: { model: string; input_per_mtok: number; output_per_mtok: number }[];
}

function ProviderState({ p }: { p: AiProvider }) {
  if (!p.enabled) return <StateLabel tone="muted">Disabled</StateLabel>;
  if (p.status === "ok") return <StateLabel tone="ok">Connected</StateLabel>;
  if (p.status === "error") return <StateLabel tone="crit">Failed</StateLabel>;
  return <StateLabel tone="muted">Not tested</StateLabel>;
}

/** Where prompts and tool results go: the question an operator asks first about an AI provider. */
function DataPath({ p }: { p: AiProvider }) {
  if (p.is_demo) return <Chip className="whitespace-nowrap">Stays in Fabric Warden</Chip>;
  if (p.local) return <Chip className="whitespace-nowrap">Your server · {hostOf(p.base_url)}</Chip>;
  return <Chip className="border-ink-3 whitespace-nowrap text-ink">Sent to {hostOf(p.base_url)}</Chip>;
}

/** Mirrors the API's check: loopback, private or link-local addresses and LAN-only names stay on your network. */
function isLocalUrl(url: string): boolean {
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase().replace(/^\[|\]$/g, "");
  } catch {
    return false;
  }
  if (host === "localhost" || /^(127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host) || /^(::1$|f[cd]|fe[89ab])/.test(host)) return true;
  if (/^[\d.]+$/.test(host) || host.includes(":")) return false;
  return !host.includes(".") || /\.(local|lan|home\.arpa|internal|localdomain)$/.test(host);
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export default function AiProvidersPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("ai-providers");
  const manage = can("ai:manage");
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["ai", "providers"], queryFn: () => get<{ items: AiProvider[] }>("/api/ai/providers"), enabled: on });
  const kinds = useQuery({ queryKey: ["ai", "kinds"], queryFn: () => get<Kinds>("/api/ai/providers/kinds"), enabled: on, staleTime: Infinity });
  const done = () => qc.invalidateQueries({ queryKey: ["ai"] });
  const demo = useMutation({ mutationFn: () => post("/api/ai/providers/demo"), onSuccess: done });
  const test = useMutation({ mutationFn: (id: string) => post<AiProbe & { provider: AiProvider }>(`/api/ai/providers/${id}/test`), onSuccess: done });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="AI providers" name="AI Providers" canManage={can("modules:manage")}>
        Connect a local model server (Ollama, LM Studio) or Anthropic Claude for the optional Agent Center. Nothing else in Fabric Warden needs a model.
      </ModuleOff>
    );
  }
  const items = list.data?.items;
  const hasDemo = !!items?.some((p) => p.is_demo);
  const editingP = items?.find((p) => p.id === editing);

  const columns: Column<AiProvider>[] = [
    {
      key: "name",
      header: "Provider",
      sort: (p) => p.name.toLowerCase(),
      cell: (p) => (
        <span className="flex min-w-0 flex-col">
          <span className="flex items-center gap-2"><span className="truncate font-bold">{p.name}</span>{p.is_demo && <DemoTag />}</span>
          <span className="truncate text-sm text-ink-3">{PROVIDER_KIND[p.kind]}{p.key_fingerprint ? ` · key …${p.key_fingerprint}` : ""}</span>
        </span>
      ),
    },
    { key: "state", header: "Status", className: "whitespace-nowrap", cell: (p) => <span className="flex flex-col"><ProviderState p={p} />{p.last_checked_at && <span className="text-xs text-ink-3">tested {ago(p.last_checked_at)}</span>}</span> },
    { key: "data", header: "Prompts go to", cell: (p) => <DataPath p={p} /> },
    { key: "model", header: "Default model", cell: (p) => (p.default_model ? <span className="font-mono text-xs whitespace-nowrap">{p.default_model}</span> : <span className="text-ink-3">None chosen</span>) },
    { key: "tokens", header: "Tokens this month", cell: (p) => <BudgetLine used={p.usage.tokens} limit={p.monthly_token_limit} format={tokens} /> },
    { key: "cost", header: "Cost this month", cell: (p) => (p.local && p.price_input_per_mtok === 0 && p.price_output_per_mtok === 0 ? <span className="text-ink-3">Free (local)</span> : <BudgetLine used={p.usage.cost_usd} limit={p.monthly_cost_limit_usd} format={money} />) },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      className: "w-px whitespace-nowrap",
      cell: (p) =>
        manage ? (
          <span className="flex items-center gap-1">
            <Button size="sm" variant="ghost" loading={test.isPending && test.variables === p.id} onClick={() => test.mutate(p.id)}>Test</Button>
            <Button size="sm" variant="ghost" aria-expanded={editing === p.id} onClick={() => setEditing(editing === p.id ? null : p.id)}>{editing === p.id ? "Close" : "Settings"}</Button>
          </span>
        ) : null,
    },
  ];

  return (
    <>
      <PageHeader
        title="AI providers"
        description="The model servers the Agent Center may use. Local servers keep everything on your network; an API provider receives each agent's instructions and the Fabric Warden data its tools return. API keys are stored encrypted and never shown again."
        actions={
          manage && (
            <>
              {!hasDemo && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Add offline demo model</Button>}
              <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add provider</Button>
            </>
          )
        }
      />
      {(list.error || demo.error) && <Notice tone="crit" className="mb-4">{((list.error || demo.error) as Error).message}</Notice>}
      {test.data && !test.data.ok && <Notice tone="crit" className="mb-4" title={test.data.error ?? "The test failed"}>{test.data.hint}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {adding && kinds.data && <ProviderForm kinds={kinds.data.kinds} presets={kinds.data.presets} onDone={() => setAdding(false)} />}
        <Panel bodyClassName="p-0">
          <DataTable
            caption="AI providers"
            rows={items}
            loading={list.isLoading}
            columns={columns}
            empty={
              <EmptyState icon={<Cpu />} title="No AI provider yet">
                Agents need a model. Add Ollama or LM Studio on your network, Anthropic Claude with an API key, or the offline demo model to see how the Agent Center works without any model at all.
              </EmptyState>
            }
          />
        </Panel>
        {editingP && <ProviderSettings key={editingP.id} p={editingP} onClose={() => setEditing(null)} />}
        {kinds.data && <PricePanel prices={kinds.data.prices} />}
      </div>
    </>
  );
}

function ProviderForm({ kinds, presets, onDone }: { kinds: AiProviderKindInfo[]; presets: AiProviderPreset[]; onDone: () => void }) {
  const qc = useQueryClient();
  // "kind:<id>" for a provider kind, "preset:<id>" for a hosted service on the OpenAI-compatible kind.
  const [service, setService] = useState("kind:ollama");
  const preset = service.startsWith("preset:") ? presets.find((p) => `preset:${p.id}` === service) : undefined;
  const kind: AiProviderKindInfo["id"] = preset ? preset.kind : (service.slice(5) as AiProviderKindInfo["id"]);
  const base = kinds.find((k) => k.id === kind)!;
  const [f, setF] = useState({ name: "", base_url: "", api_key: "", default_model: "", token_limit: "", cost_limit: "", price_in: "", price_out: "" });
  const [result, setResult] = useState<AiProbe | null>(null);
  const url = f.base_url.trim() || preset?.base_url || base.default_base_url;
  const info = { ...base, needs_key: preset ? true : base.needs_key, local: preset ? false : isLocalUrl(url) };
  const defaultName = preset?.label ?? PROVIDER_KIND[kind];
  const errorOf = (e: unknown) => {
    if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "error" in (e.detail as object)) setResult(e.detail as AiProbe);
    else setResult({ ok: false, models: [], error: e instanceof ApiError ? e.message : "Couldn't connect." });
  };
  const test = useMutation({ mutationFn: () => post<AiProbe>("/api/ai/providers/test", { kind, base_url: url, api_key: f.api_key }), onSuccess: setResult, onError: errorOf });
  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const save = useMutation({
    mutationFn: () =>
      post("/api/ai/providers", {
        kind,
        name: f.name.trim() || defaultName,
        base_url: url,
        api_key: f.api_key,
        default_model: f.default_model,
        monthly_token_limit: num(f.token_limit),
        monthly_cost_limit_usd: num(f.cost_limit),
        price_input_per_mtok: num(f.price_in) ?? 0,
        price_output_per_mtok: num(f.price_out) ?? 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai"] });
      onDone();
    },
    onError: errorOf,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const models = result?.ok ? result.models : [];
  const missingKey = info.needs_key && !f.api_key;
  return (
    <Panel title="Add a provider">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 xl:items-start">
        <Field label="Service" hint={preset ? "Saved as an OpenAI-compatible connection" : undefined}>
          {(p) => (
            <Select {...p} value={service} onChange={(e) => { setService(e.target.value); setF({ ...f, base_url: "" }); setResult(null); }}>
              <optgroup label="Hosted">
                {kinds.filter((k) => !k.local).map((k) => <option key={k.id} value={`kind:${k.id}`}>{k.label}</option>)}
                {presets.map((pr) => <option key={pr.id} value={`preset:${pr.id}`}>{pr.label}</option>)}
              </optgroup>
              <optgroup label="Your server">
                {kinds.filter((k) => k.local).map((k) => <option key={k.id} value={`kind:${k.id}`}>{k.label}</option>)}
              </optgroup>
            </Select>
          )}
        </Field>
        <Field label="Name" hint="Optional">{(p) => <Input {...p} value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={defaultName} />}</Field>
        <Field label="URL" className="xl:col-span-2" hint={info.local ? "The server must be reachable from Fabric Warden's container" : "The official endpoint; change it only for a proxy you run"}>
          {(p) => <Input {...p} className="font-mono" value={f.base_url} onChange={(e) => { setF({ ...f, base_url: e.target.value }); setResult(null); }} placeholder={preset?.base_url ?? base.default_base_url} />}
        </Field>
        <Field
          label={info.needs_key ? "API key" : "API key (optional)"}
          hint={preset ? <>Stored encrypted. <a href={preset.keys_url} target="_blank" rel="noreferrer" className="font-semibold text-accent hover:underline">Create a {preset.label} key</a></> : "Stored encrypted; only a fingerprint is shown afterwards"}
        >
          {(p) => <Input {...p} type="password" autoComplete="off" className="font-mono" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} />}
        </Field>
        <Field label="Default model" hint={models.length ? `${models.length} listed by the provider` : "Test the connection to list the models"}>
          {(p) => (
            <Select {...p} value={f.default_model} disabled={!models.length} onChange={(e) => setF({ ...f, default_model: e.target.value })}>
              <option value="">{models.length ? "First listed" : "—"}</option>
              {models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Monthly token limit" hint="Optional; agents stop when it is reached">
          {(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.token_limit} onChange={(e) => setF({ ...f, token_limit: e.target.value.replace(/[^0-9]/g, "") })} placeholder="No limit" />}
        </Field>
        <Field label="Monthly cost limit (USD)" hint="Optional">
          {(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.cost_limit} onChange={(e) => setF({ ...f, cost_limit: e.target.value.replace(/[^0-9.]/g, "") })} placeholder="No limit" />}
        </Field>
        {kind !== "anthropic" && (
          <>
            <Field label="Price per million input tokens (USD)" hint={info.local ? "Leave empty for a free local model" : "From the service's price list; counts towards the cost limit"}>
              {(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.price_in} onChange={(e) => setF({ ...f, price_in: e.target.value.replace(/[^0-9.]/g, "") })} placeholder="0" />}
            </Field>
            <Field label="Price per million output tokens (USD)">
              {(p) => <Input {...p} inputMode="decimal" className="tabular" value={f.price_out} onChange={(e) => setF({ ...f, price_out: e.target.value.replace(/[^0-9.]/g, "") })} placeholder="0" />}
            </Field>
          </>
        )}
        {!info.local && (
          <div className="md:col-span-2 xl:col-span-4">
            <Notice tone="info" title="This provider is outside your network">
              When an agent runs on it, its instructions and the Fabric Warden data its tools read (device names, addresses, metrics, log lines, firewall policies) are sent to {hostOf(url)}.
            </Notice>
          </div>
        )}
        {result && (
          <div className="md:col-span-2 xl:col-span-4">
            {result.ok ? (
              <Notice tone="ok" title="Connected">
                {result.models.length ? `Models: ${result.models.slice(0, 6).map((m) => m.id).join(", ")}${result.models.length > 6 ? ` and ${result.models.length - 6} more` : ""}.` : "The provider listed no models."}
              </Notice>
            ) : (
              <Notice tone="crit" title={result.error ?? "Couldn't connect"}>{result.hint}</Notice>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-4">
          <Button onClick={() => test.mutate()} loading={test.isPending} disabled={missingKey}>Test connection</Button>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={missingKey}>Save provider</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {missingKey && <span className="text-sm text-ink-3">Paste an API key to test or save.</span>}
        </div>
      </form>
    </Panel>
  );
}

function ProviderSettings({ p, onClose }: { p: AiProvider; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    default_model: p.default_model,
    token_limit: p.monthly_token_limit?.toString() ?? "",
    cost_limit: p.monthly_cost_limit_usd?.toString() ?? "",
    api_key: "",
  });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const usage = useQuery({ queryKey: ["ai", "usage", p.id], queryFn: () => get<AiUsageReport>(`/api/ai/providers/${p.id}/usage?limit=20`) });
  const done = () => qc.invalidateQueries({ queryKey: ["ai"] });
  const save = useMutation({
    mutationFn: () =>
      patch(`/api/ai/providers/${p.id}`, {
        default_model: f.default_model,
        ...(f.token_limit.trim() ? { monthly_token_limit: Number(f.token_limit) } : { clear_token_limit: true }),
        ...(f.cost_limit.trim() ? { monthly_cost_limit_usd: Number(f.cost_limit) } : { clear_cost_limit: true }),
        ...(f.api_key ? { api_key: f.api_key } : {}),
      }),
    onSuccess: () => {
      setF({ ...f, api_key: "" });
      done();
    },
  });
  const toggle = useMutation({ mutationFn: () => patch(`/api/ai/providers/${p.id}`, { enabled: !p.enabled }), onSuccess: done });
  const remove = useMutation({
    mutationFn: () => del(`/api/ai/providers/${p.id}`),
    onSuccess: () => {
      done();
      onClose();
    },
  });
  const u = usage.data;
  return (
    <Panel
      title={`${p.name}: settings and usage`}
      actions={<Button size="sm" variant="ghost" onClick={onClose}>Close</Button>}
    >
      <div className="grid gap-6 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="flex flex-col gap-4"
        >
          <KeyValue items={[["Kind", PROVIDER_KIND[p.kind]], ["URL", <span key="u" className="font-mono text-xs">{p.base_url}</span>], ...(p.status_detail ? [["Last error", <span key="e" className="text-crit">{p.status_detail}</span>] as [string, React.ReactNode]] : [])]} />
          <Field label="Default model" hint={p.models.length ? undefined : "Test the provider to list its models"}>
            {(fp) => (
              <Select {...fp} value={f.default_model} onChange={(e) => setF({ ...f, default_model: e.target.value })}>
                {!p.models.some((m) => m.id === f.default_model) && <option value={f.default_model}>{f.default_model || "—"}</option>}
                {p.models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
              </Select>
            )}
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Monthly token limit">{(fp) => <Input {...fp} inputMode="numeric" className="tabular" value={f.token_limit} onChange={(e) => setF({ ...f, token_limit: e.target.value.replace(/[^0-9]/g, "") })} placeholder="No limit" />}</Field>
            <Field label="Monthly cost limit (USD)">{(fp) => <Input {...fp} inputMode="decimal" className="tabular" value={f.cost_limit} onChange={(e) => setF({ ...f, cost_limit: e.target.value.replace(/[^0-9.]/g, "") })} placeholder="No limit" />}</Field>
          </div>
          {!p.is_demo && (
            <Field label="Replace the API key" hint={p.key_fingerprint ? `Stored key ends …${p.key_fingerprint} (fingerprint)` : "No key stored"}>
              {(fp) => <Input {...fp} type="password" autoComplete="off" className="font-mono" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} />}
            </Field>
          )}
          {(save.error || toggle.error || remove.error) && <Notice tone="crit">{((save.error || toggle.error || remove.error) as Error).message}</Notice>}
          {save.isSuccess && !save.isPending && <Notice tone="ok">Saved.</Notice>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" loading={save.isPending}>Save settings</Button>
            <Button loading={toggle.isPending} onClick={() => toggle.mutate()}>{p.enabled ? "Disable" : "Enable"}</Button>
            <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete…</Button>
          </div>
        </form>
        <div className="min-w-0">
          <h3 className="mb-1 text-sm font-semibold text-ink-3">This month, since {u ? dateTime(u.since) : "…"}</h3>
          {u?.budget_problem && <Notice tone="warn" className="my-2" title="Budget reached">{u.budget_problem}. Agents on this provider stop until the month ends or the limit is raised.</Notice>}
          <p className="mb-3 tabular text-ink-2">{u ? `${tokens(u.tokens)} tokens · ${money(u.cost_usd)}` : "…"}</p>
          {u && u.items.length > 0 ? (
            <table className="w-full text-left">
              <caption className="sr-only">Recent model calls</caption>
              <thead>
                <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
                  <th className="py-2 pr-3 font-bold">When</th>
                  <th className="py-2 pr-3 font-bold">Run</th>
                  <th className="py-2 pr-3 font-bold">Model</th>
                  <th className="py-2 pr-3 text-right font-bold">In</th>
                  <th className="py-2 pr-3 text-right font-bold">Out</th>
                  <th className="py-2 text-right font-bold">Cost</th>
                </tr>
              </thead>
              <tbody>
                {u.items.map((i, n) => (
                  <tr key={n} className="border-b border-rule last:border-b-0">
                    <td className="py-1.5 pr-3 font-mono text-xs text-ink-3">{dateTime(i.at)}</td>
                    <td className="py-1.5 pr-3">{i.run_id && i.run ? <Link href={`/ai/runs/${i.run_id}`} className="font-mono text-xs font-bold text-accent hover:underline">{i.run}</Link> : "—"}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs">{i.model}</td>
                    <td className="py-1.5 pr-3 text-right tabular">{i.input_tokens.toLocaleString()}</td>
                    <td className="py-1.5 pr-3 text-right tabular">{i.output_tokens.toLocaleString()}</td>
                    <td className="py-1.5 text-right tabular">{money(i.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-ink-3">No model calls this month.</p>
          )}
        </div>
      </div>
      <Confirm open={confirmDelete} onOpenChange={setConfirmDelete} title={`Delete ${p.name}?`} confirmLabel="Delete provider" onConfirm={() => remove.mutate()}>
        The stored key is deleted. Past runs keep their evidence and usage; agents set to this provider fall back to the first enabled one.
      </Confirm>
    </Panel>
  );
}

function PricePanel({ prices }: { prices: Kinds["prices"] }) {
  return (
    <Panel title="Prices used for cost">
      <p className="mb-3 max-w-[70ch] text-ink-2">
        Costs are estimates from the tokens each provider reports. Claude models use Anthropic&apos;s published first-party prices below; local models are free unless you set a price; other API providers use the price set on the provider.
      </p>
      <table className="text-left">
        <caption className="sr-only">Built-in prices</caption>
        <thead>
          <tr className="border-b border-rule-strong text-sm font-semibold text-ink-3">
            <th className="py-2 pr-6 font-bold">Model</th>
            <th className="py-2 pr-6 text-right font-bold">Input / M tokens</th>
            <th className="py-2 text-right font-bold">Output / M tokens</th>
          </tr>
        </thead>
        <tbody>
          {prices.map((p) => (
            <tr key={p.model} className="border-b border-rule last:border-b-0">
              <td className="py-1.5 pr-6 font-mono text-xs whitespace-nowrap">{p.model}</td>
              <td className="py-1.5 pr-6 text-right tabular">${p.input_per_mtok.toFixed(2)}</td>
              <td className="py-1.5 text-right tabular">${p.output_per_mtok.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
