"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Confirm } from "@/components/ui/confirm";
import { Field, Input } from "@/components/ui/field";
import { EmptyState, Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { ApiError, del, get, post } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Token {
  id: string;
  name: string;
  prefix: string;
  permissions: string[];
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

export default function AccountPage() {
  const { me } = useSession();
  return (
    <>
      <PageHeader title="Account & security" description={`Signed in as ${me.username} (${me.roles.join(", ")}).`} />
      <div className="grid grid-cols-1 gap-[var(--gap)] xl:grid-cols-2">
        <PasswordPanel />
        <MfaPanel />
        <div className="xl:col-span-2">
          <TokensPanel />
        </div>
      </div>
    </>
  );
}

function PasswordPanel() {
  const [f, setF] = useState({ current: "", next: "", confirm: "" });
  const [msg, setMsg] = useState<{ tone: "ok" | "crit"; text: string } | null>(null);
  const m = useMutation({
    mutationFn: () => post("/api/auth/password", { current_password: f.current, new_password: f.next }),
    onSuccess: () => { setF({ current: "", next: "", confirm: "" }); setMsg({ tone: "ok", text: "Password changed. Your other sessions were signed out." }); },
    onError: (e) => setMsg({ tone: "crit", text: e instanceof ApiError ? e.message : "Couldn't change the password." }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <Panel title="Password" id="password">
      <form onSubmit={submit} className="flex max-w-md flex-col gap-4">
        <Field label="Current password">{(p) => <Input {...p} type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} />}</Field>
        <Field label="New password" hint="At least 12 characters." error={f.next && f.next.length < 12 ? "Use at least 12 characters." : null}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} />}
        </Field>
        <Field label="Confirm new password" error={f.confirm && f.confirm !== f.next ? "Passwords don't match." : null}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} />}
        </Field>
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <div>
          <Button type="submit" variant="primary" loading={m.isPending} disabled={!f.current || f.next.length < 12 || f.next !== f.confirm}>Change password</Button>
        </div>
      </form>
    </Panel>
  );
}

function MfaPanel() {
  const { me } = useSession();
  const qc = useQueryClient();
  const [enrol, setEnrol] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "crit"; text: string } | null>(null);
  const fail = (e: unknown) => setMsg({ tone: "crit", text: e instanceof ApiError ? e.message : "Something went wrong." });
  const start = useMutation({ mutationFn: () => post<{ secret: string; otpauth_uri: string }>("/api/auth/mfa/enroll"), onSuccess: (r) => { setEnrol(r); setMsg(null); }, onError: fail });
  const activate = useMutation({
    mutationFn: () => post("/api/auth/mfa/activate", { code }),
    onSuccess: () => { setEnrol(null); setCode(""); setMsg({ tone: "ok", text: "Two-step verification is on." }); qc.invalidateQueries({ queryKey: ["me"] }); },
    onError: fail,
  });
  const disable = useMutation({
    mutationFn: () => post("/api/auth/mfa/disable", { password: pw }),
    onSuccess: () => { setPw(""); setMsg({ tone: "ok", text: "Two-step verification is off." }); qc.invalidateQueries({ queryKey: ["me"] }); },
    onError: fail,
  });
  return (
    <Panel title="Two-step verification" id="mfa" actions={me.mfa_enabled ? <StateLabel tone="ok">On</StateLabel> : <StateLabel tone="warn">Off</StateLabel>}>
      {msg && <Notice tone={msg.tone} className="mb-4">{msg.text}</Notice>}
      {me.mfa_enabled ? (
        <form onSubmit={(e) => { e.preventDefault(); disable.mutate(); }} className="flex max-w-md flex-col gap-4">
          <p className="text-base text-ink-2">Sign-in asks for a code from your authenticator app. To turn it off, confirm your password.</p>
          <Field label="Password">{(p) => <Input {...p} type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />}</Field>
          <div><Button type="submit" variant="danger" loading={disable.isPending} disabled={!pw}>Turn off two-step verification</Button></div>
        </form>
      ) : enrol ? (
        <form onSubmit={(e) => { e.preventDefault(); activate.mutate(); }} className="flex max-w-md flex-col gap-4">
          <p className="text-base text-ink-2">Add this key to your authenticator app (1Password, Aegis, Google Authenticator…), then enter the 6-digit code it shows.</p>
          <div className="rounded-[4px] border border-rule bg-surface-2 p-3">
            <p className="text-xs font-bold tracking-wide text-ink-3 uppercase">Setup key</p>
            <p className="mt-1 flex items-center gap-2 font-mono text-md tracking-wider break-all">
              {enrol.secret.match(/.{1,4}/g)?.join(" ")}
              <button type="button" className="rounded p-1 text-ink-3 hover:bg-surface-3 hover:text-ink" onClick={() => navigator.clipboard?.writeText(enrol.secret)} aria-label="Copy setup key">
                <Copy className="size-4" />
              </button>
            </p>
            <p className="mt-2 text-xs break-all text-ink-3">
              Or open the <a className="text-accent underline" href={enrol.otpauth_uri}>otpauth link</a> on a device with an authenticator installed.
            </p>
          </div>
          <Field label="Code from your app">{(p) => <Input {...p} inputMode="numeric" autoComplete="one-time-code" className="max-w-40 font-mono tracking-[0.3em]" value={code} onChange={(e) => setCode(e.target.value.replace(/\s/g, ""))} maxLength={8} />}</Field>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" loading={activate.isPending} disabled={code.length < 6}>Turn on</Button>
            <Button variant="ghost" onClick={() => setEnrol(null)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="flex max-w-md flex-col items-start gap-3">
          <p className="text-base text-ink-2">Protect this account with a time-based code from an authenticator app. Strongly recommended for owners and admins.</p>
          <Button variant="primary" icon={<KeyRound />} onClick={() => start.mutate()} loading={start.isPending}>Set up two-step verification</Button>
        </div>
      )}
    </Panel>
  );
}

function TokensPanel() {
  const { me } = useSession();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [readOnly, setReadOnly] = useState(true);
  const [created, setCreated] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => get<Token[]>("/api/auth/tokens") });
  const readPerms = me.permissions.filter((p) => p.endsWith(":read"));
  const create = useMutation({
    mutationFn: () => post<{ token: string }>("/api/auth/tokens", { name, expires_in_days: Number(days) || null, permissions: readOnly ? readPerms : [] }),
    onSuccess: (r) => { setCreated(r.token); setName(""); setErr(null); qc.invalidateQueries({ queryKey: ["tokens"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't create the token."),
  });
  const revoke = useMutation({ mutationFn: (id: string) => del(`/api/auth/tokens/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["tokens"] }) });

  return (
    <Panel title="API tokens" id="tokens" bodyClassName="p-0">
      <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="flex flex-wrap items-end gap-3 border-b border-rule p-4">
        <Field label="Token name" className="w-64">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. home-assistant" maxLength={128} />}</Field>
        <Field label="Expires after (days)" className="w-40">{(p) => <Input {...p} type="number" min={1} max={3650} value={days} onChange={(e) => setDays(e.target.value)} />}</Field>
        <label className="flex h-9 items-center gap-2 text-base">
          <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)} />
          Read-only
        </label>
        <Button type="submit" icon={<Plus />} loading={create.isPending} disabled={!name.trim()}>Create token</Button>
      </form>
      {err && <Notice tone="crit" className="m-4">{err}</Notice>}
      {created && (
        <Notice tone="warn" className="m-4" title="Copy this token now. It won't be shown again." action={<Button size="sm" icon={<Copy />} onClick={() => navigator.clipboard?.writeText(created)}>Copy</Button>}>
          <code className="font-mono text-sm break-all text-ink">{created}</code>
        </Notice>
      )}
      {tokens.data && tokens.data.length === 0 ? (
        <EmptyState title="No API tokens">Tokens let scripts and integrations call the API as you, limited to the permissions you choose.</EmptyState>
      ) : (
        <ul>
          {tokens.data?.map((t) => (
            <li key={t.id} className="flex min-h-[var(--row-h)] flex-wrap items-center gap-x-5 gap-y-1 border-b border-rule px-4 py-2 last:border-b-0">
              <span className="min-w-40 font-bold">{t.name}</span>
              <span className="font-mono text-sm text-ink-3">{t.prefix}…</span>
              <span className="text-sm text-ink-2">{t.permissions.length ? `${t.permissions.length} permissions` : "all your permissions"}</span>
              <span className="text-sm text-ink-3">created {dateTime(t.created_at)}</span>
              <span className="text-sm text-ink-3">{t.expires_at ? `expires ${dateTime(t.expires_at)}` : "never expires"}</span>
              <span className="text-sm text-ink-3">last used {ago(t.last_used_at)}</span>
              <Confirm
                title={`Revoke “${t.name}”?`}
                confirmLabel="Revoke token"
                onConfirm={() => revoke.mutate(t.id)}
                trigger={<Button size="sm" variant="ghost" className="ml-auto" icon={<Trash2 />}>Revoke</Button>}
              >
                Anything using this token stops working immediately.
              </Confirm>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
