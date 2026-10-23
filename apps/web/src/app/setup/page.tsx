"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Check, FlaskConical } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { AuthFrame } from "@/components/auth/auth-frame";
import { ControllerForm } from "@/components/controller-form";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/layout-bits";
import { ApiError, get, post } from "@/lib/api";
import type { Controller, Me, ModuleInfo, Preset } from "@/lib/types";

type Step = "owner" | "controller" | "modules";
const STEPS: { id: Step; label: string; sub: string }[] = [
  { id: "owner", label: "Owner account", sub: "The first administrator" },
  { id: "controller", label: "Controller", sub: "Your UniFi console, or the demo" },
  { id: "modules", label: "Modules", sub: "Choose what to switch on" },
];

function StepSign({ current }: { current: Step }) {
  const idx = STEPS.findIndex((s) => s.id === current);
  return (
    <ol className="flex flex-col" aria-label="Setup progress">
      {STEPS.map((s, i) => {
        const done = i < idx;
        const active = i === idx;
        return (
          <li key={s.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span
                className={clsx(
                  "grid size-7 place-items-center rounded-[4px] text-sm font-bold",
                  active ? "bg-[#7fc8ff] text-[#071426]" : done ? "border-[1.5px] border-[#4fcf8e] text-[#4fcf8e]" : "border border-white/30 text-white/70",
                )}
                aria-current={active ? "step" : undefined}
              >
                {done ? <Check className="size-4" strokeWidth={3} /> : i + 1}
              </span>
              {i < STEPS.length - 1 && <span className={clsx("my-1 w-px flex-1", done ? "bg-[#4fcf8e]" : "bg-white/25")} style={{ minHeight: 28 }} />}
            </div>
            <div className="pb-6">
              <p className={clsx("font-bold", !active && !done && "text-rail-ink-2")}>{s.label}</p>
              <p className="text-sm text-rail-ink-2">{s.sub}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default function SetupPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["setup"], queryFn: () => get<{ needs_owner: boolean; has_controller: boolean; demo_available: boolean }>("/api/setup/status") });
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/auth/me"), enabled: status.data?.needs_owner === false, retry: false });
  const [step, setStep] = useState<Step | null>(null);
  const [controller, setController] = useState<Controller | null>(null);

  useEffect(() => {
    if (step || !status.data) return;
    if (status.data.needs_owner) setStep("owner");
    else if (me.isError) router.replace("/login");
    else if (me.data) setStep(status.data.has_controller ? "modules" : "controller");
  }, [status.data, me.data, me.isError, step, router]);

  return (
    <AuthFrame aside={<div className="mt-4">{step && <StepSign current={step} />}</div>}>
      {step === "owner" && <OwnerStep onDone={async () => { await qc.invalidateQueries({ queryKey: ["me"] }); setStep("controller"); }} />}
      {step === "controller" && <ControllerStep demoAvailable={status.data?.demo_available ?? false} onDone={(c) => { setController(c); setStep("modules"); }} />}
      {step === "modules" && <ModulesStep controller={controller} onDone={() => { qc.clear(); router.replace("/"); }} />}
    </AuthFrame>
  );
}

function OwnerStep({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ username: "", display_name: "", email: "", password: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = form.confirm.length > 0 && form.confirm !== form.password;
  const short = form.password.length > 0 && form.password.length < 12;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/api/setup/owner", { username: form.username, display_name: form.display_name, email: form.email || null, password: form.password });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the account.");
    } finally {
      setBusy(false);
    }
  };
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <>
      <h1 className="text-xl font-bold">Create the owner account</h1>
      <p className="mt-1 text-base text-ink-2">The owner can manage everything, including other owners. You can add MFA right after setup.</p>
      <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
        {error && <Notice tone="crit">{error}</Notice>}
        <Field label="Username" hint="Letters, numbers, dots, dashes and underscores.">
          {(p) => <Input {...p} autoComplete="username" value={form.username} onChange={set("username")} autoFocus />}
        </Field>
        <Field label="Display name">{(p) => <Input {...p} autoComplete="name" value={form.display_name} onChange={set("display_name")} />}</Field>
        <Field label="Email (optional)">{(p) => <Input {...p} type="email" autoComplete="email" value={form.email} onChange={set("email")} />}</Field>
        <Field label="Password" hint="At least 12 characters. A passphrase works well." error={short ? "Use at least 12 characters." : null}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        <Field label="Confirm password" error={mismatch ? "Passwords don't match." : null}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />}
        </Field>
        <Button type="submit" variant="primary" loading={busy} disabled={form.username.length < 3 || form.password.length < 12 || form.confirm !== form.password}>
          Create owner and continue
        </Button>
      </form>
    </>
  );
}

function ControllerStep({ demoAvailable, onDone }: { demoAvailable: boolean; onDone: (c: Controller) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const demo = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await post<Controller>("/api/controllers/demo"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't add the demo controller.");
      setBusy(false);
    }
  };
  return (
    <>
      <h1 className="text-xl font-bold">Connect a UniFi controller</h1>
      <p className="mt-1 text-base text-ink-2">
        Fabric Warden connects read-only through the official Integration API. Nothing on the controller changes.
      </p>
      <div className="mt-6">
        <ControllerForm onCreated={onDone} submitLabel="Save and continue" />
      </div>
      {demoAvailable && (
        <div className="mt-8 border-t border-rule pt-6">
          <p className="font-bold">Just looking around?</p>
          <p className="mt-1 text-base text-ink-2">
            Use the built-in simulated home lab: a gateway, four switches, four access points and 26 clients. Everything it shows is labelled as demo data.
          </p>
          {error && <Notice tone="crit" className="mt-3">{error}</Notice>}
          <Button className="mt-3" icon={<FlaskConical />} onClick={demo} loading={busy}>
            Use the demo controller
          </Button>
        </div>
      )}
    </>
  );
}

function ModulesStep({ controller, onDone }: { controller: Controller | null; onDone: () => void }) {
  const q = useQuery({ queryKey: ["modules"], queryFn: () => get<{ modules: ModuleInfo[]; presets: Preset[] }>("/api/modules") });
  const [preset, setPreset] = useState("minimal");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const byId = new Map(q.data?.modules.map((m) => [m.id, m]));

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/modules/presets/${preset}/apply`);
      if (controller) await post(`/api/controllers/${controller.id}/sync`);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't apply the preset.");
      setBusy(false);
    }
  };

  return (
    <>
      <h1 className="text-xl font-bold">Choose what to switch on</h1>
      <p className="mt-1 text-base text-ink-2">Start with a preset; you can enable or disable any module later in the Module Center.</p>
      <fieldset className="mt-6 flex flex-col gap-2">
        <legend className="sr-only">Install preset</legend>
        {q.data?.presets.map((p) => {
          const available = p.modules.filter((id) => byId.get(id)?.maturity !== "planned");
          const planned = p.modules.length - available.length;
          return (
            <label
              key={p.id}
              className={clsx(
                "flex cursor-pointer gap-3 rounded-[4px] border px-4 py-3 transition-colors duration-150",
                preset === p.id ? "border-accent bg-accent-soft" : "border-rule bg-surface hover:border-rule-strong",
              )}
            >
              <input type="radio" name="preset" value={p.id} checked={preset === p.id} onChange={() => setPreset(p.id)} className="mt-1 size-4 accent-[var(--accent)]" />
              <span className="min-w-0">
                <span className="block font-bold">{p.name}</span>
                <span className="block text-sm text-ink-2">
                  {available.length} available now{planned > 0 ? ` · ${planned} arrive in later releases and stay off until then` : ""}
                </span>
              </span>
            </label>
          );
        })}
      </fieldset>
      {error && <Notice tone="crit" className="mt-4">{error}</Notice>}
      <Button variant="primary" className="mt-6 w-full" onClick={apply} loading={busy} disabled={!q.data}>
        {controller ? "Apply and run first sync" : "Apply preset"}
      </Button>
    </>
  );
}
