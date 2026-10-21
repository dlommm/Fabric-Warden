"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { AuthFrame } from "@/components/auth/auth-frame";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/layout-bits";
import { ApiError, get, post } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [step, setStep] = useState<"password" | "mfa">("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const setup = useQuery({ queryKey: ["setup"], queryFn: () => get<{ needs_owner: boolean }>("/api/setup/status") });

  useEffect(() => {
    if (setup.data?.needs_owner) router.replace("/setup");
  }, [setup.data, router]);

  const done = async () => {
    // Drop every query cached while signed out so the shell starts clean.
    qc.clear();
    router.replace("/");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (step === "password") {
        const r = await post<{ mfa_required: boolean }>("/api/auth/login", { username, password });
        if (r.mfa_required) setStep("mfa");
        else await done();
      } else {
        await post("/api/auth/mfa/verify", { code: code.replace(/\s/g, "") });
        await done();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server. Check that the API is running.");
      if (err instanceof ApiError && err.status === 401 && step === "mfa" && /pending/i.test(err.message)) setStep("password");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame
      aside={
        <div className="mt-auto hidden lg:block">
          <ul className="flex max-w-[22rem] flex-col gap-2.5" aria-label="How Fabric Warden works">
            {[
              ["Read-only by default", "Nothing on your controller changes without an explicit, audited request."],
              ["Evidence on every fact", "Each record shows its source, how fresh it is, and whether it's demo data."],
              ["Runs without the cloud", "Self-hosted. No subscriptions, no phone-home."],
            ].map(([t, d]) => (
              <li key={t} className="sign rounded-[4px] bg-white/8 px-4 py-3" style={{ outlineColor: "rgb(255 255 255 / 0.55)" }}>
                <p className="font-bold">{t}</p>
                <p className="mt-0.5 text-sm text-rail-ink-2">{d}</p>
              </li>
            ))}
          </ul>
        </div>
      }
    >
      <h1 className="text-xl font-bold">{step === "password" ? "Sign in" : "Two-step verification"}</h1>
      <p className="mt-1 text-base text-ink-2">
        {step === "password" ? "Use your Fabric Warden account." : "Enter the 6-digit code from your authenticator app."}
      </p>
      <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
        {error && <Notice tone="crit">{error}</Notice>}
        {step === "password" ? (
          <>
            <Field label="Username">
              {(p) => <Input {...p} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />}
            </Field>
            <Field label="Password">
              {(p) => <Input {...p} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />}
            </Field>
          </>
        ) : (
          <Field label="Verification code">
            {(p) => (
              <Input
                {...p}
                inputMode="numeric"
                autoComplete="one-time-code"
                className="font-mono tracking-[0.3em]"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                maxLength={8}
                autoFocus
              />
            )}
          </Field>
        )}
        <Button type="submit" variant="primary" loading={busy} disabled={step === "password" ? !username || !password : code.length < 6}>
          {step === "password" ? "Sign in" : "Verify"}
        </Button>
        {step === "mfa" && (
          <Button variant="ghost" onClick={() => { setStep("password"); setCode(""); setError(null); }}>
            Use a different account
          </Button>
        )}
      </form>
    </AuthFrame>
  );
}
