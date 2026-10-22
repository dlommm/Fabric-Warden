"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, KeyRound, Pause, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useMemo, useState } from "react";
import { FindingsPlate } from "@/components/ipam";
import { InterfaceTable, TargetState } from "@/components/snmp";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel, Skeleton } from "@/components/ui/layout-bits";
import { Chip, DemoTag, StateLabel } from "@/components/ui/signs";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ago, bps, uptime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Controller, Finding, SnmpCandidate, SnmpFinding, SnmpInterface, SnmpProfile, SnmpTarget } from "@/lib/types";

type View = "up" | "physical" | "problems" | "all";

export default function SnmpPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("snmp");
  const manage = can("snmp:manage");
  const qc = useQueryClient();
  const [form, setForm] = useState<"profile" | "targets" | null>(null);
  const [confirmDemo, setConfirmDemo] = useState(false);
  const profiles = useQuery({ queryKey: ["snmp", "profiles"], queryFn: () => get<{ items: SnmpProfile[] }>("/api/snmp/profiles"), enabled: on });
  const targets = useQuery({ queryKey: ["snmp", "targets"], queryFn: () => get<{ items: SnmpTarget[] }>("/api/snmp/targets"), enabled: on, refetchInterval: 60_000 });
  const findings = useQuery({ queryKey: ["snmp", "findings"], queryFn: () => get<{ items: SnmpFinding[] }>("/api/snmp/findings"), enabled: on, refetchInterval: 60_000 });
  const controllers = useQuery({ queryKey: ["controllers"], queryFn: () => get<Controller[]>("/api/controllers"), enabled: on });
  const done = () => qc.invalidateQueries({ queryKey: ["snmp"] });
  const demo = useMutation({ mutationFn: () => post("/api/snmp/demo"), onSuccess: done });
  const undemo = useMutation({ mutationFn: () => del("/api/snmp/demo"), onSuccess: done });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Port counters" name="SNMP Port Counters" canManage={can("modules:manage")}>
        Read per-port throughput, errors and discards from your switches and gateway over SNMP, with history. The UniFi API does not expose these.
      </ModuleOff>
    );
  }
  const profileItems = profiles.data?.items;
  const targetItems = targets.data?.items;
  const hasDemo = !!profileItems?.some((p) => p.is_demo);
  const demoAvailable = !!controllers.data?.some((c) => c.is_demo);
  const hasLiveProfile = !!profileItems?.some((p) => !p.is_demo);
  const devices = new Map(targetItems?.filter((t) => t.device).map((t) => [t.id, t.device?.id]) ?? []);
  const toFinding = (f: SnmpFinding, n: number): Finding => ({
    key: `${f.kind}-${f.interface_id ?? f.target_id}-${n}`,
    kind: f.kind,
    severity: f.severity,
    title: f.title,
    detail: f.detail,
    refs: {},
    href: devices.get(f.target_id) ? `/inventory/devices/${devices.get(f.target_id)}#ports` : undefined,
  });

  return (
    <>
      <PageHeader
        title="Port counters"
        description="Throughput, errors and discards for every switch and gateway port, read over SNMP and kept as history. Read-only: Fabric Warden only reads (SNMP GET and GETBULK) and never sends SET, so it cannot change a device."
        meta={hasDemo ? <span className="inline-flex items-center gap-2 text-sm text-ink-2"><DemoTag /> Includes simulated agents</span> : undefined}
        actions={
          manage && (
            <>
              {hasDemo ? (
                <Button variant="ghost" loading={undemo.isPending} onClick={() => setConfirmDemo(true)}>Remove demo agents</Button>
              ) : (
                demoAvailable && <Button loading={demo.isPending} onClick={() => demo.mutate()}>Load demo agents</Button>
              )}
              {hasLiveProfile ? (
                <Button variant="primary" icon={<Plus />} onClick={() => setForm("targets")}>Add devices</Button>
              ) : (
                <Button variant="primary" icon={<KeyRound />} onClick={() => setForm("profile")}>Add SNMP credentials</Button>
              )}
            </>
          )
        }
      />
      {(targets.error || demo.error || undemo.error) && <Notice tone="crit" className="mb-4">{((targets.error || demo.error || undemo.error) as Error).message}</Notice>}
      <div className="flex flex-col gap-[var(--gap)]">
        {profileItems && !hasLiveProfile && !form && <SetupSteps manage={manage} onStart={() => setForm("profile")} />}
        {form === "profile" && <ProfileForm onDone={() => setForm(null)} />}
        {form === "targets" && profileItems && <TargetForm profiles={profileItems} onDone={() => setForm(null)} />}
        {findings.data && !!targetItems?.length && (
          <FindingsPlate
            label="Port findings"
            findings={findings.data.items.map(toFinding)}
            emptyText="Every polled port is error-free and below 80% utilisation."
            heading={(n) => `${n} port problem${n > 1 ? "s" : ""}`}
          />
        )}
        {!!targetItems?.length && <Interfaces />}
        <Targets items={targetItems} loading={targets.isLoading} manage={manage} />
        <Profiles items={profileItems} loading={profiles.isLoading} manage={manage} onAdd={() => setForm("profile")} />
      </div>
      <Confirm open={confirmDemo} onOpenChange={setConfirmDemo} title="Remove the demo agents?" confirmLabel="Remove demo agents" onConfirm={() => undemo.mutate()}>
        The simulated SNMP profile, its targets and the counters read from them are deleted. Metric history already recorded stays until it ages out. Your own targets stay.
      </Confirm>
    </>
  );
}

function SetupSteps({ manage, onStart }: { manage: boolean; onStart: () => void }) {
  return (
    <Panel title="Turn on SNMP in UniFi">
      <ol className="flex max-w-[70ch] list-decimal flex-col gap-2 pl-5">
        <li>
          In UniFi Network, open <span className="font-semibold">Settings › CyberSecure › Traffic Logging</span>, turn on <span className="font-semibold">SNMP</span> and choose{" "}
          <span className="font-semibold">v3</span>. Set a username and a password of at least 8 characters. The setting covers every device on the site.
        </li>
        <li>Enter the same username and password here. They are stored encrypted in Fabric Warden&apos;s vault and never shown again.</li>
        <li>Pick the switches and gateway to poll. Fabric Warden reads them every minute from UDP port 161.</li>
      </ol>
      <p className="mt-3 max-w-[70ch] text-sm text-ink-3">
        UniFi uses SHA authentication and AES-128 privacy with the one password for both. USW Flex and USW Ultra switches have no SNMP agent.
      </p>
      {manage && <Button className="mt-4" icon={<KeyRound />} onClick={onStart}>Enter the credentials</Button>}
    </Panel>
  );
}

function Interfaces() {
  const [q, setQ] = useState("");
  const [view, setView] = useState<View>("up");
  const list = useQuery({
    queryKey: ["snmp", "interfaces", view === "up" ? "physical" : view],
    queryFn: () => get<{ items: SnmpInterface[] }>(`/api/snmp/interfaces?physical=${view !== "all"}&problems=${view === "problems"}`),
    refetchInterval: 60_000,
  });
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return list.data?.items.filter((i) => (view !== "up" || i.oper_status === "up") && (!t || [i.name, i.alias, i.descr, i.device?.name ?? i.target?.name ?? ""].some((v) => v.toLowerCase().includes(t))));
  }, [list.data, q, view]);
  const up = rows?.filter((i) => i.oper_status === "up").length ?? 0;
  const total = list.data?.items.length ?? 0;
  const searching = !!q.trim();
  return (
    <Panel title="Interfaces" bodyClassName="p-0">
      <div className="flex flex-wrap items-end gap-3 border-b border-rule px-4 py-3">
        <Field label="Search" className="w-full sm:w-72">{(p) => <Input {...p} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Device, port or port name" />}</Field>
        <Field label="Show" className="w-56">
          {(p) => (
            <Select {...p} value={view} onChange={(e) => setView(e.target.value as View)}>
              <option value="up">Ports with link</option>
              <option value="physical">All physical ports</option>
              <option value="problems">Ports with problems</option>
              <option value="all">All interfaces, including virtual</option>
            </Select>
          )}
        </Field>
        <span className="ml-auto self-center text-sm text-ink-3 tabular">
          {!rows ? "" : view === "up" ? `${rows.length} with link · ${total - rows.length} without link hidden` : `${rows.length} shown · ${up} up`}
        </span>
      </div>
      {list.error && <div className="border-b border-rule p-4"><Notice tone="crit" title="Couldn't load the interfaces">{(list.error as Error).message}</Notice></div>}
      <InterfaceTable
        caption="Interfaces"
        rows={rows}
        loading={list.isLoading}
        withDevice
        empty={
          searching ? (
            <EmptyState title="No interfaces match">Clear the search.</EmptyState>
          ) : view === "problems" ? (
            <EmptyState title="No ports with problems">No errors, discards or utilisation above 80% in the last poll.</EmptyState>
          ) : total === 0 ? (
            <EmptyState title="No interfaces read yet">They appear after the first poll; rates after the second.</EmptyState>
          ) : (
            <EmptyState title="No ports with link">Every polled port is down. Show all physical ports to see them.</EmptyState>
          )
        }
      />
      <p className="border-t border-rule px-4 py-3 text-sm text-ink-3">
        Rates come from the change in each counter between two polls, so a newly added device shows them after its second poll. Errors and discards are per minute.
      </p>
    </Panel>
  );
}

function Targets({ items, loading, manage }: { items?: SnmpTarget[]; loading: boolean; manage: boolean }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<SnmpTarget | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["snmp"] });
  const poll = useMutation({ mutationFn: (id: string) => post(`/api/snmp/targets/${id}/poll`), onSuccess: done });
  const toggle = useMutation({ mutationFn: (t: SnmpTarget) => patch(`/api/snmp/targets/${t.id}`, { enabled: !t.enabled }), onSuccess: done });
  const remove = useMutation({ mutationFn: (id: string) => del(`/api/snmp/targets/${id}`), onSuccess: done });
  return (
    <Panel title="Polled devices" bodyClassName="p-0">
      {loading ? (
        <div className="p-4"><Skeleton className="h-16" /></div>
      ) : !items?.length ? (
        <EmptyState icon={<Activity />} title="No devices polled">Add SNMP credentials, then pick the switches and gateway to read.</EmptyState>
      ) : (
        <ul>
          {items.map((t) => (
            <li key={t.id} className="flex flex-col gap-2 border-b border-rule px-4 py-3 last:border-b-0 md:flex-row md:items-center md:gap-5">
              <span className="flex min-w-0 flex-col md:w-60 md:shrink-0">
                {t.device ? <Link href={`/inventory/devices/${t.device.id}#ports`} className="truncate font-bold hover:underline">{t.name}</Link> : <span className="truncate font-bold">{t.name}</span>}
                <span className="truncate text-sm text-ink-3">{t.device?.model ?? (t.sys_name || "Not in the inventory")}</span>
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate font-mono text-xs text-ink-2">{t.host}:{t.port} · {t.profile ? `${t.profile.name}, SNMP ${t.profile.version}` : "no credentials"}</span>
                {t.status === "ok" && (
                  <span className="text-sm text-ink-3 tabular">
                    {t.interfaces_up} of {t.interfaces} ports up · {bps(t.rx_bps)} in, {bps(t.tx_bps)} out{t.uptime_s != null ? ` · agent up ${uptime(t.uptime_s)}` : ""}
                  </span>
                )}
              </span>
              <span className="flex flex-col md:w-64">
                <TargetState t={t} />
                {t.status === "ok" || t.status === "unknown" ? (
                  <span className="text-sm text-ink-3">{t.last_poll_at ? `polled ${ago(t.last_poll_at)}` : "never polled"}</span>
                ) : (
                  <span className="text-sm text-ink-2" title={t.hint ?? undefined}>{t.status_detail}</span>
                )}
                {!!t.with_errors && <StateLabel tone="warn" className="text-sm">{`${t.with_errors} port${t.with_errors > 1 ? "s" : ""} logging errors`}</StateLabel>}
              </span>
              {t.is_demo && <DemoTag className="self-start md:self-center" />}
              {manage && (
                <span className="flex gap-1">
                  <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={poll.isPending && poll.variables === t.id} onClick={() => poll.mutate(t.id)}>Poll</Button>
                  <Button size="sm" variant="ghost" icon={t.enabled ? <Pause /> : <Play />} aria-label={`${t.enabled ? "Pause" : "Resume"} polling ${t.name}`} onClick={() => toggle.mutate(t)} />
                  {!t.is_demo && <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Stop polling ${t.name}`} onClick={() => setPending(t)} />}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {(poll.error || toggle.error || remove.error) && (
        <div className="border-t border-rule p-4"><Notice tone="crit">{((poll.error || toggle.error || remove.error) as Error).message}</Notice></div>
      )}
      {items?.some((t) => t.status !== "ok" && t.hint) && (
        <div className="border-t border-rule p-4">
          <Notice tone="warn" title="Some devices don't answer">{items.find((t) => t.status !== "ok" && t.hint)?.hint}</Notice>
        </div>
      )}
      <Confirm open={pending !== null} onOpenChange={(o) => !o && setPending(null)} title={`Stop polling ${pending?.name}?`} confirmLabel="Stop polling" onConfirm={() => pending && remove.mutate(pending.id)}>
        Fabric Warden stops reading this device and deletes its interface list. Metric history already recorded stays until it ages out. Nothing changes on the device.
      </Confirm>
    </Panel>
  );
}

function Profiles({ items, loading, manage, onAdd }: { items?: SnmpProfile[]; loading: boolean; manage: boolean; onAdd: () => void }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<SnmpProfile | null>(null);
  const remove = useMutation({ mutationFn: (id: string) => del(`/api/snmp/profiles/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["snmp"] }) });
  return (
    <Panel title="SNMP credentials" bodyClassName="p-0" actions={manage && !!items?.length && <Button size="sm" icon={<Plus />} onClick={onAdd}>Add credentials</Button>}>
      {loading ? (
        <div className="p-4"><Skeleton className="h-12" /></div>
      ) : !items?.length ? (
        <p className="px-4 py-3 text-ink-3">No credentials yet.</p>
      ) : (
        <ul>
          {items.map((p) => (
            <li key={p.id} className="flex flex-col gap-2 border-b border-rule px-4 py-3 last:border-b-0 md:flex-row md:items-center md:gap-5">
              <span className="font-bold md:w-60 md:shrink-0">{p.name}</span>
              <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <Chip>SNMP {p.version}</Chip>
                {p.version === "v3" && (
                  <>
                    <span className="font-mono text-xs text-ink-2">{p.username}</span>
                    <span className="text-sm text-ink-3">{p.auth_protocol === "none" ? "no authentication" : p.auth_protocol} · {p.priv_protocol === "none" ? "no privacy" : p.priv_protocol === "AES" ? "AES-128" : p.priv_protocol}</span>
                  </>
                )}
                {p.credential_fingerprint && <span className="text-sm text-ink-3">fingerprint <span className="font-mono text-xs">{p.credential_fingerprint}</span></span>}
              </span>
              <span className="text-sm text-ink-3 tabular md:w-40">{p.target_count} device{p.target_count === 1 ? "" : "s"}</span>
              {p.is_demo && <DemoTag className="self-start md:self-center" />}
              {manage && !p.is_demo && (
                <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Delete credentials ${p.name}`} disabled={p.target_count > 0} title={p.target_count > 0 ? "Stop polling its devices first" : undefined} onClick={() => setPending(p)} />
              )}
            </li>
          ))}
        </ul>
      )}
      {remove.error && <div className="border-t border-rule p-4"><Notice tone="crit">{(remove.error as Error).message}</Notice></div>}
      <Confirm open={pending !== null} onOpenChange={(o) => !o && setPending(null)} title={`Delete ${pending?.name}?`} confirmLabel="Delete credentials" onConfirm={() => pending && remove.mutate(pending.id)}>
        The stored username and password are deleted from the vault. SNMP stays turned on in UniFi until you turn it off there.
      </Confirm>
    </Panel>
  );
}

const AUTH = ["SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512", "MD5", "none"];
const PRIV = ["AES", "AES-192", "AES-256", "DES", "none"];

function ProfileForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "UniFi site", version: "v3" as "v3" | "v2c", username: "", password: "", priv_password: "", community: "", auth_protocol: "SHA", priv_protocol: "AES", advanced: false });
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      post("/api/snmp/profiles", {
        name: f.name.trim(),
        version: f.version,
        username: f.username.trim(),
        password: f.password,
        priv_password: f.advanced ? f.priv_password : "",
        community: f.community,
        auth_protocol: f.auth_protocol,
        priv_protocol: f.priv_protocol,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["snmp"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save the credentials."),
  });
  const short = f.version === "v3" && f.auth_protocol !== "none" && f.password.length > 0 && f.password.length < 8;
  const ready = f.name.trim() && (f.version === "v2c" ? f.community : f.username.trim() && (f.auth_protocol === "none" || f.password.length >= 8));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    save.mutate();
  };
  return (
    <Panel title="Add SNMP credentials">
      <form onSubmit={submit} noValidate className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 xl:items-start">
        <Field label="Name">{(p) => <Input {...p} value={f.name} maxLength={128} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
        <Field label="Version" hint={f.version === "v2c" ? "Sends the community in clear text; prefer v3" : "Matches UniFi's v3 setting"}>
          {(p) => (
            <Select {...p} value={f.version} onChange={(e) => setF({ ...f, version: e.target.value as "v3" | "v2c" })}>
              <option value="v3">SNMP v3</option>
              <option value="v2c">SNMP v2c</option>
            </Select>
          )}
        </Field>
        {f.version === "v3" ? (
          <>
            <Field label="Username">{(p) => <Input {...p} className="font-mono" autoComplete="off" value={f.username} maxLength={64} onChange={(e) => setF({ ...f, username: e.target.value })} />}</Field>
            <Field label="Password" hint="At least 8 characters; stored encrypted, never shown again" error={short ? "UniFi and SNMP v3 need at least 8 characters" : undefined}>
              {(p) => <Input {...p} type="password" autoComplete="new-password" className="font-mono" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />}
            </Field>
            <div className="md:col-span-2 xl:col-span-4">
              <label className="inline-flex items-center gap-2 text-sm font-semibold">
                <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={f.advanced} onChange={(e) => setF({ ...f, advanced: e.target.checked, auth_protocol: "SHA", priv_protocol: "AES", priv_password: "" })} />
                Not UniFi: choose protocols and a separate privacy password
              </label>
              {!f.advanced && <p className="mt-1 text-sm text-ink-3">UniFi uses SHA authentication and AES-128 privacy with the one password for both.</p>}
            </div>
            {f.advanced && (
              <>
                <Field label="Authentication">
                  {(p) => <Select {...p} value={f.auth_protocol} onChange={(e) => setF({ ...f, auth_protocol: e.target.value, priv_protocol: e.target.value === "none" ? "none" : f.priv_protocol })}>{AUTH.map((a) => <option key={a} value={a}>{a === "none" ? "None" : a}</option>)}</Select>}
                </Field>
                <Field label="Privacy">
                  {(p) => (
                    <Select {...p} value={f.priv_protocol} disabled={f.auth_protocol === "none"} onChange={(e) => setF({ ...f, priv_protocol: e.target.value })}>
                      {PRIV.map((a) => <option key={a} value={a}>{a === "none" ? "None" : a === "AES" ? "AES-128" : a}</option>)}
                    </Select>
                  )}
                </Field>
                <Field label="Privacy password" hint="Optional; defaults to the password">
                  {(p) => <Input {...p} type="password" autoComplete="new-password" className="font-mono" disabled={f.priv_protocol === "none"} value={f.priv_password} onChange={(e) => setF({ ...f, priv_password: e.target.value })} />}
                </Field>
              </>
            )}
          </>
        ) : (
          <Field label="Community" hint="Stored encrypted; never shown again" className="xl:col-span-2">
            {(p) => <Input {...p} type="password" autoComplete="off" className="font-mono" value={f.community} onChange={(e) => setF({ ...f, community: e.target.value })} />}
          </Field>
        )}
        {error && <Notice tone="crit" className="md:col-span-2 xl:col-span-4">{error}</Notice>}
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-4">
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!ready}>Save credentials</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}

function TargetForm({ profiles, onDone }: { profiles: SnmpProfile[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [profileId, setProfileId] = useState(profiles.find((p) => !p.is_demo)?.id ?? profiles[0].id);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [host, setHost] = useState("");
  const [port, setPort] = useState("161");
  const [result, setResult] = useState<{ ok: boolean; error?: string; hint?: string; sys_name?: string; physical?: number; interfaces?: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const profile = profiles.find((p) => p.id === profileId);
  const cands = useQuery({ queryKey: ["snmp", "candidates"], queryFn: () => get<{ items: SnmpCandidate[] }>("/api/snmp/candidates") });
  // Demo credentials only reach demo devices and live ones only live devices; the API enforces the same.
  const list = cands.data?.items.filter((c) => c.is_demo === !!profile?.is_demo);
  const test = useMutation({
    mutationFn: () => post<typeof result>("/api/snmp/targets/test", { profile_id: profileId, host: host.trim(), port: Number(port) || 161 }),
    onSuccess: setResult,
    onError: (e) => setResult({ ok: false, error: e instanceof ApiError ? e.message : "Couldn't reach the agent." }),
  });
  const save = useMutation({
    mutationFn: () => post("/api/snmp/targets", host.trim() ? { profile_id: profileId, host: host.trim(), port: Number(port) || 161 } : { profile_id: profileId, device_ids: [...picked] }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["snmp"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't add the devices."),
  });
  const flip = (id: string) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });
  const free = list?.filter((c) => !c.targeted && !c.unavailable) ?? [];
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    save.mutate();
  };
  return (
    <Panel title="Add devices to poll">
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label="Credentials" className="w-full sm:w-80">
          {(p) => (
            <Select {...p} value={profileId} onChange={(e) => { setProfileId(e.target.value); setPicked(new Set()); setResult(null); }}>
              {profiles.map((x) => <option key={x.id} value={x.id}>{x.name}{x.is_demo ? " (demo)" : ""}</option>)}
            </Select>
          )}
        </Field>
        <fieldset disabled={!!host.trim()} className="flex flex-col gap-2 disabled:opacity-50">
          <legend className="mb-1 flex w-full flex-wrap items-baseline justify-between gap-2 text-sm font-semibold">
            <span>Switches and gateways the controller reports</span>
            {free.length > 1 && (
              <button type="button" className="font-semibold text-accent hover:underline" onClick={() => setPicked(picked.size === free.length ? new Set() : new Set(free.map((c) => c.id)))}>
                {picked.size === free.length ? "Clear" : `Select all ${free.length}`}
              </button>
            )}
          </legend>
          {cands.isLoading ? (
            <Skeleton className="h-24" />
          ) : !list?.length ? (
            <p className="text-ink-3">{profile?.is_demo ? "No demo devices; connect the demo controller first." : "The controller reports no switches or gateways yet."}</p>
          ) : (
            <ul className="grid gap-x-6 gap-y-1 rounded-[var(--radius)] border border-rule p-2 md:grid-cols-2">
              {list.map((c) => {
                const blocked = c.targeted || !!c.unavailable;
                return (
                  <li key={c.id}>
                    <label className={`flex min-h-[var(--row-h)] items-center gap-3 rounded-[3px] px-2 ${blocked ? "text-ink-3" : "cursor-pointer hover:bg-surface-2"}`}>
                      <input type="checkbox" className="size-4 shrink-0 accent-[var(--accent)]" disabled={blocked} checked={picked.has(c.id)} onChange={() => flip(c.id)} />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate font-semibold">{c.name} <span className="font-normal text-ink-3">{c.model}</span></span>
                        <span className="truncate text-sm text-ink-3">
                          <span className="font-mono text-xs">{c.ip_address ?? "no IP"}</span> · {c.ports} ports{c.targeted ? " · already polled" : ""}{c.unavailable ? ` · ${c.unavailable}` : ""}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </fieldset>
        {!profile?.is_demo && (
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Or an address" hint="An agent that is not in the inventory" className="w-full sm:w-72">
              {(p) => <Input {...p} className="font-mono" value={host} onChange={(e) => { setHost(e.target.value); setResult(null); }} placeholder="10.0.1.5" />}
            </Field>
            <Field label="UDP port" className="w-28">{(p) => <Input {...p} className="font-mono" inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} />}</Field>
            <Button onClick={() => test.mutate()} loading={test.isPending} disabled={!host.trim()}>Test</Button>
          </div>
        )}
        {result &&
          (result.ok ? (
            <Notice tone="ok" title={`Answered${result.sys_name ? ` as ${result.sys_name}` : ""}`}>{result.physical} physical ports among {result.interfaces} interfaces.</Notice>
          ) : (
            <Notice tone="crit" title={result.error ?? "No answer"}>{result.hint}</Notice>
          ))}
        {error && <Notice tone="crit">{error}</Notice>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!host.trim() && picked.size === 0}>
            {host.trim() ? "Add and poll" : picked.size ? `Poll ${picked.size} device${picked.size > 1 ? "s" : ""}` : "Poll selected devices"}
          </Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
