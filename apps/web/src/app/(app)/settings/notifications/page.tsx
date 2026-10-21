"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Pencil, Plus, Send, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ModuleOff, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ChannelKind, ChannelRow, Delivery, Severity } from "@/lib/types";

const KIND_LABEL: Record<ChannelKind, string> = { webhook: "Webhook", ntfy: "ntfy", email: "Email" };
const SEVERITY_LABEL: Record<Severity, string> = { critical: "Critical only", warning: "Warning and up", info: "Everything" };

type Draft = {
  id?: string;
  name: string;
  kind: ChannelKind;
  min_severity: Severity;
  enabled: boolean;
  url: string;
  signing_secret: string;
  server: string;
  topic: string;
  token: string;
  host: string;
  port: string;
  security: "starttls" | "tls" | "none";
  username: string;
  password: string;
  from_addr: string;
  to: string;
  has_secret?: boolean;
};

const BLANK: Draft = {
  name: "",
  kind: "webhook",
  min_severity: "warning",
  enabled: true,
  url: "",
  signing_secret: "",
  server: "https://ntfy.sh",
  topic: "",
  token: "",
  host: "",
  port: "587",
  security: "starttls",
  username: "",
  password: "",
  from_addr: "",
  to: "",
};

function fromRow(c: ChannelRow): Draft {
  const cfg = c.config as Record<string, string | number | string[]>;
  return {
    ...BLANK,
    id: c.id,
    name: c.name,
    kind: c.kind,
    min_severity: c.min_severity,
    enabled: c.enabled,
    server: String(cfg.server ?? BLANK.server),
    host: String(cfg.host ?? ""),
    port: String(cfg.port ?? "587"),
    security: (cfg.security as Draft["security"]) ?? "starttls",
    username: String(cfg.username ?? ""),
    from_addr: String(cfg.from_addr ?? ""),
    to: Array.isArray(cfg.to) ? cfg.to.join(", ") : "",
    has_secret: c.has_secret,
  };
}

export default function NotificationsPage() {
  const { can, moduleEnabled, modules } = useSession();
  const on = moduleEnabled("notifications");
  const editable = can("notifications:manage");
  const qc = useQueryClient();
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => get<{ items: ChannelRow[] }>("/api/notifications/channels"), enabled: on });
  const deliveries = useQuery({ queryKey: ["deliveries"], queryFn: () => get<{ items: Delivery[] }>("/api/notifications/deliveries?limit=25"), enabled: on, refetchInterval: 60_000 });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState<ChannelRow | null>(null);
  const [tested, setTested] = useState<{ id: string; status: string; error: string | null } | null>(null);
  const test = useMutation({
    mutationFn: (id: string) => post<{ status: string; error: string | null }>(`/api/notifications/channels/${id}/test`),
    onSuccess: (r, id) => {
      setTested({ id, ...r });
      qc.invalidateQueries({ queryKey: ["channels"] });
      qc.invalidateQueries({ queryKey: ["deliveries"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/api/notifications/channels/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });

  if (modules.length > 0 && !on) {
    return (
      <ModuleOff title="Notifications" name="Notification Channels" canManage={can("modules:manage")}>
        Send firing and resolved alerts to a webhook, an ntfy topic or email. Enable Notification Channels to set them up.
      </ModuleOff>
    );
  }

  const names = new Map(channels.data?.items.map((c) => [c.id, c.name]));
  const columns: Column<ChannelRow>[] = [
    {
      key: "name",
      header: "Channel",
      sort: (c) => c.name.toLowerCase(),
      cell: (c) => (
        <span className="flex flex-col py-1">
          <span className="font-bold">{c.name}</span>
          <span className="text-sm text-ink-2">
            {KIND_LABEL[c.kind]} · <span className="font-mono text-xs">{c.display || "—"}</span>
          </span>
        </span>
      ),
    },
    { key: "sev", header: "Sends", sort: (c) => c.min_severity, cell: (c) => <span className="text-ink-2">{SEVERITY_LABEL[c.min_severity]}</span> },
    {
      key: "status",
      header: "Last delivery",
      sort: (c) => c.last_sent_at,
      cell: (c) =>
        !c.enabled ? (
          <StateLabel tone="muted">Disabled</StateLabel>
        ) : c.last_status === "failed" ? (
          <span className="flex flex-col">
            <StateLabel tone="crit">Failed {ago(c.last_sent_at)}</StateLabel>
            {c.last_error && <span className="max-w-72 truncate text-sm text-ink-2" title={c.last_error}>{c.last_error}</span>}
          </span>
        ) : c.last_status === "ok" ? (
          <StateLabel tone="ok">Sent {ago(c.last_sent_at)}</StateLabel>
        ) : (
          <StateLabel tone="muted">Not used yet</StateLabel>
        ),
    },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (c) =>
        editable && (
          <span className="inline-flex items-center gap-1">
            <Button size="sm" icon={<Send />} loading={test.isPending && test.variables === c.id} onClick={() => test.mutate(c.id)}>Send test</Button>
            <Button size="sm" variant="ghost" icon={<Pencil />} aria-label={`Edit ${c.name}`} onClick={() => setDraft(fromRow(c))} />
            <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={`Delete ${c.name}`} onClick={() => setPending(c)} />
          </span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Notifications"
        description="Where firing and resolved alerts go. Secrets (webhook URLs, topics, tokens, passwords) are stored in the credential vault and never shown again."
        actions={editable && !draft && <Button variant="primary" icon={<Plus />} onClick={() => setDraft({ ...BLANK })}>Add channel</Button>}
      />
      <div className="flex flex-col gap-[var(--gap)]">
        {!editable && <Notice tone="info">Only admins and owners can add or change channels.</Notice>}
        {draft && <ChannelForm key={draft.id ?? "new"} draft={draft} onDone={() => setDraft(null)} />}
        {tested && (
          <Notice tone={tested.status === "sent" ? "ok" : "crit"} title={tested.status === "sent" ? `Test sent to ${names.get(tested.id)}` : `Test to ${names.get(tested.id)} failed`}>
            {tested.status === "sent" ? "Check the destination for a message titled “Fabric Warden test notification”." : tested.error}
          </Notice>
        )}
        {remove.error && <Notice tone="crit">{(remove.error as Error).message}</Notice>}
        <Panel bodyClassName="p-0">
          <DataTable
            caption="Notification channels"
            rows={channels.data?.items}
            columns={columns}
            loading={channels.isLoading}
            empty={
              <EmptyState icon={<BellRing />} title="No channels yet" action={editable ? <Button variant="primary" icon={<Plus />} onClick={() => setDraft({ ...BLANK })}>Add channel</Button> : undefined}>
                Alerts are recorded either way; a channel sends them to you. A webhook works with most chat tools and automation; ntfy pushes to your phone.
              </EmptyState>
            }
          />
        </Panel>
        <Panel title="Recent deliveries" bodyClassName="p-0">
          {deliveries.data && deliveries.data.items.length === 0 ? (
            <p className="px-4 py-4 text-ink-3">Nothing sent yet.</p>
          ) : (
            <ul>
              {deliveries.data?.items.map((d) => (
                <li key={d.id} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-4 gap-y-0.5 border-b border-rule px-4 py-2 last:border-b-0">
                  <span className="w-24">{d.status === "sent" ? <StateLabel tone="ok">Sent</StateLabel> : <StateLabel tone="crit">Failed</StateLabel>}</span>
                  <span className="w-20 text-ink-2">{d.kind === "test" ? "Test" : d.kind === "firing" ? "Firing" : "Resolved"}</span>
                  <span className="min-w-0 flex-1 font-semibold">{names.get(d.channel_id) ?? "Removed channel"}</span>
                  {d.error && <span className="w-full text-sm text-crit sm:order-last">{d.error}</span>}
                  <span className="text-sm text-ink-3">{dateTime(d.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <Confirm open={pending !== null} onOpenChange={(o) => !o && setPending(null)} title={`Delete “${pending?.name}”?`} confirmLabel="Delete channel" onConfirm={() => pending && remove.mutate(pending.id)}>
        Its stored secrets are destroyed. Rules that named only this channel will notify every remaining channel instead.
      </Confirm>
    </>
  );
}

function ChannelForm({ draft, onDone }: { draft: Draft; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState(draft);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const editingSecret = Boolean(f.id);
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const settings = () => {
    if (f.kind === "webhook") return { webhook: { url: f.url.trim(), signing_secret: f.signing_secret || null } };
    if (f.kind === "ntfy") return { ntfy: { server: f.server.trim(), topic: f.topic.trim(), token: f.token || null } };
    return {
      email: {
        host: f.host.trim(),
        port: Number(f.port),
        security: f.security,
        username: f.username.trim(),
        password: f.password || null,
        from_addr: f.from_addr.trim(),
        to: f.to.split(/[,\s]+/).filter(Boolean),
      },
    };
  };
  // On edit, secret-bearing settings are only sent when the person re-enters them.
  const touched = f.kind === "webhook" ? f.url !== "" : f.kind === "ntfy" ? f.topic !== "" : true;

  const save = useMutation({
    mutationFn: () => {
      const base = { name: f.name.trim(), min_severity: f.min_severity, enabled: f.enabled };
      if (f.id) return patch(`/api/notifications/channels/${f.id}`, { ...base, ...(touched ? settings() : {}) });
      return post("/api/notifications/channels", { ...base, kind: f.kind, ...settings() });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channels"] });
      onDone();
    },
    onError: (e) => {
      const msg = e instanceof ApiError ? e.message : "Couldn't save the channel.";
      const field = ["url", "topic", "server", "host", "from_addr", "to"].find((k) => msg.toLowerCase().includes(k));
      setErrors(field ? { [field]: msg.replace(/^.*?: (Value error, )?/, "") } : { form: msg });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!f.name.trim()) next.name = "Name the channel.";
    if (f.kind === "webhook" && !f.id && !/^https?:\/\/\S+$/.test(f.url.trim())) next.url = "Enter the full http:// or https:// URL.";
    if (f.kind === "ntfy" && !f.id && !/^[A-Za-z0-9_-]{1,64}$/.test(f.topic.trim())) next.topic = "Letters, digits, - and _ only.";
    if (f.kind === "email") {
      if (!f.host.trim()) next.host = "Enter the SMTP server.";
      if (!/^\S+@\S+$/.test(f.from_addr.trim())) next.from_addr = "Enter the sender address.";
      if (!f.to.trim()) next.to = "Enter at least one recipient.";
    }
    setErrors(next);
    if (Object.keys(next).length === 0) save.mutate();
  };
  const keep = (what: string) => (editingSecret ? `Leave blank to keep the stored ${what}.` : undefined);

  return (
    <Panel title={f.id ? `Edit “${draft.name}”` : "Add a channel"}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,10rem)_minmax(0,12rem)]">
          <Field label="Name" error={errors.name}>{(p) => <Input {...p} autoFocus value={f.name} maxLength={128} onChange={set("name")} placeholder="Phone, Ops chat…" />}</Field>
          <Field label="Type" hint={f.id ? "Fixed once created." : undefined}>
            {(p) => (
              <Select {...p} disabled={!!f.id} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as ChannelKind })}>
                {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Sends">
            {(p) => (
              <Select {...p} value={f.min_severity} onChange={(e) => setF({ ...f, min_severity: e.target.value as Severity })}>
                {Object.entries(SEVERITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            )}
          </Field>
        </div>
        {f.kind === "webhook" && (
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="URL" error={errors.url} hint={keep("URL") ?? "Receives a JSON POST for each firing and resolved alert."}>
              {(p) => <Input {...p} className="font-mono text-sm" value={f.url} onChange={set("url")} placeholder="https://hooks.example.com/…" autoComplete="off" />}
            </Field>
            <Field label="Signing secret" hint={keep("secret") ?? "Optional. Adds an X-Warden-Signature HMAC-SHA256 header so the receiver can verify the sender."}>
              {(p) => <Input {...p} type="password" value={f.signing_secret} onChange={set("signing_secret")} autoComplete="new-password" />}
            </Field>
          </div>
        )}
        {f.kind === "ntfy" && (
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Server" error={errors.server}>{(p) => <Input {...p} className="font-mono text-sm" value={f.server} onChange={set("server")} />}</Field>
            <Field label="Topic" error={errors.topic} hint={keep("topic") ?? "On a public server, anyone who knows the topic can read it. Pick something unguessable."}>
              {(p) => <Input {...p} type="password" value={f.topic} onChange={set("topic")} autoComplete="off" />}
            </Field>
            <Field label="Access token" hint={keep("token") ?? "Optional, for protected topics."}>{(p) => <Input {...p} type="password" value={f.token} onChange={set("token")} autoComplete="new-password" />}</Field>
          </div>
        )}
        {f.kind === "email" && (
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,6rem)_minmax(0,10rem)]">
            <Field label="SMTP server" error={errors.host}>{(p) => <Input {...p} className="font-mono text-sm" value={f.host} onChange={set("host")} placeholder="smtp.example.com" />}</Field>
            <Field label="Port">{(p) => <Input {...p} inputMode="numeric" className="tabular" value={f.port} onChange={(e) => setF({ ...f, port: e.target.value.replace(/\D/g, "") })} />}</Field>
            <Field label="Security">
              {(p) => (
                <Select {...p} value={f.security} onChange={(e) => setF({ ...f, security: e.target.value as Draft["security"] })}>
                  <option value="starttls">STARTTLS</option>
                  <option value="tls">TLS</option>
                  <option value="none">None</option>
                </Select>
              )}
            </Field>
            <Field label="Username">{(p) => <Input {...p} value={f.username} onChange={set("username")} autoComplete="off" />}</Field>
            <Field label="Password" hint={keep("password")} className="md:col-span-2">{(p) => <Input {...p} type="password" value={f.password} onChange={set("password")} autoComplete="new-password" />}</Field>
            <Field label="From" error={errors.from_addr}>{(p) => <Input {...p} type="email" value={f.from_addr} onChange={set("from_addr")} placeholder="warden@example.com" />}</Field>
            <Field label="To" error={errors.to} hint="Separate addresses with commas." className="md:col-span-2">{(p) => <Input {...p} value={f.to} onChange={set("to")} />}</Field>
          </div>
        )}
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} className="size-4 accent-[var(--accent)]" />
          Enabled
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={save.isPending}>{f.id ? "Save channel" : "Add channel"}</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
          {errors.form && <span role="alert" className="text-sm font-semibold text-crit">{errors.form}</span>}
        </div>
      </form>
    </Panel>
  );
}
