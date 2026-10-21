"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice, PageHeader, Panel } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { type Column, DataTable } from "@/components/ui/table";
import { ApiError, get, patch, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { UserRow } from "@/lib/types";

interface Role {
  id: string;
  description: string;
  permissions: string[];
}

export default function UsersPage() {
  const { can, me } = useSession();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "crit"; text: string } | null>(null);
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<UserRow[]>("/api/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<{ roles: Role[] }>("/api/auth/roles") });
  const manage = can("users:manage");
  const isOwner = me.roles.includes("owner");
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<{ roles: string[]; is_active: boolean }> }) => patch<UserRow>(`/api/users/${id}`, body),
    onSuccess: (u) => { setMsg({ tone: "ok", text: `Updated ${u.username}.` }); qc.invalidateQueries({ queryKey: ["users"] }); },
    onError: (e) => setMsg({ tone: "crit", text: e instanceof ApiError ? e.message : "Couldn't update the user." }),
  });
  const roleOptions = (roles.data?.roles ?? []).filter((r) => r.id !== "owner" || isOwner);

  const columns: Column<UserRow>[] = [
    {
      key: "user",
      header: "User",
      sort: (u) => u.username,
      cell: (u) => (
        <span>
          <span className="font-bold">{u.display_name || u.username}</span>{" "}
          <span className="font-mono text-sm text-ink-3">{u.username}</span>
          {u.id === me.id && <span className="ml-2 text-xs font-semibold text-ink-3">you</span>}
        </span>
      ),
    },
    {
      key: "role",
      header: "Role",
      sort: (u) => u.roles.join(),
      cell: (u) =>
        manage && u.id !== me.id && (isOwner || !u.roles.includes("owner")) ? (
          <Select aria-label={`Role for ${u.username}`} value={u.roles[0]} onChange={(e) => update.mutate({ id: u.id, body: { roles: [e.target.value] } })} className="h-8 w-36">
            {roleOptions.map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}
          </Select>
        ) : (
          <span className="capitalize">{u.roles.join(", ")}</span>
        ),
    },
    { key: "mfa", header: "MFA", sort: (u) => (u.mfa_enabled ? 1 : 0), cell: (u) => (u.mfa_enabled ? <StateLabel tone="ok">On</StateLabel> : <span className="text-ink-3">Off</span>) },
    { key: "status", header: "Status", sort: (u) => (u.is_active ? 1 : 0), cell: (u) => (u.is_active ? <span>Active</span> : <StateLabel tone="muted">Deactivated</StateLabel>) },
    { key: "login", header: "Last sign-in", align: "right", sort: (u) => u.last_login_at, cell: (u) => <span className="text-ink-2">{ago(u.last_login_at)}</span> },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (u) =>
        manage && u.id !== me.id && (isOwner || !u.roles.includes("owner")) ? (
          <Button size="sm" variant={u.is_active ? "ghost" : "secondary"} onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
            {u.is_active ? "Deactivate" : "Reactivate"}
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <PageHeader
        title="Users & access"
        description="Who can sign in and what each role allows. Deactivating a user ends their sessions immediately."
        actions={manage && <Button variant="primary" icon={<Plus />} onClick={() => setAdding((a) => !a)} aria-expanded={adding}>Add user</Button>}
      />
      {msg && <Notice tone={msg.tone} className="mb-4">{msg.text}</Notice>}
      {adding && <AddUser roles={roleOptions} onDone={(u) => { setAdding(false); setMsg({ tone: "ok", text: `Created ${u.username}.` }); qc.invalidateQueries({ queryKey: ["users"] }); }} />}
      <Panel bodyClassName="p-0" className="mb-[var(--gap)]">
        <DataTable caption="Users" rows={users.data} columns={columns} loading={users.isLoading} initialSort={{ key: "user", dir: "asc" }} />
      </Panel>
      <Panel title="Roles" id="roles" bodyClassName="p-0">
        <ul>
          {roles.data?.roles.map((r) => (
            <li key={r.id} className="grid grid-cols-1 gap-1 border-b border-rule px-4 py-3 last:border-b-0 md:grid-cols-[10rem_1fr]">
              <span className="font-bold capitalize">{r.id}</span>
              <span>
                <span className="text-ink-2">{r.description}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {r.permissions.map((p) => (
                    <code key={p} className="rounded-[3px] bg-surface-3 px-1.5 py-0.5 font-mono text-xs text-ink-2">{p}</code>
                  ))}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <p className="flex items-center gap-2 border-t border-rule px-4 py-3 text-sm text-ink-3">
          <ShieldCheck className="size-4" aria-hidden /> Site-scoped roles and SSO (OIDC) are on the roadmap.
        </p>
      </Panel>
    </>
  );
}

function AddUser({ roles, onDone }: { roles: Role[]; onDone: (u: UserRow) => void }) {
  const [f, setF] = useState({ username: "", display_name: "", password: "", role: "viewer" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      onDone(await post<UserRow>("/api/users", { username: f.username, display_name: f.display_name, password: f.password, roles: [f.role] }));
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Couldn't create the user.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title="Add a user" className="mb-[var(--gap)]">
      <form onSubmit={submit} className="grid max-w-3xl grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Username">{(p) => <Input {...p} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} autoComplete="off" />}</Field>
        <Field label="Display name">{(p) => <Input {...p} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />}</Field>
        <Field label="Initial password" hint="At least 12 characters. Share it securely; they can change it under Account & security.">
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />}
        </Field>
        <Field label="Role">
          {(p) => (
            <Select {...p} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
              {roles.map((r) => <option key={r.id} value={r.id}>{r.id}: {r.description}</option>)}
            </Select>
          )}
        </Field>
        {err && <Notice tone="crit" className="sm:col-span-2">{err}</Notice>}
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" loading={busy} disabled={f.username.length < 3 || f.password.length < 12}>Create user</Button>
        </div>
      </form>
    </Panel>
  );
}
