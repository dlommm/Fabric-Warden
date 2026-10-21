"use client";

import { ChevronDown, ChevronRight, PlugZap } from "lucide-react";
import { type FormEvent, useState } from "react";
import { ApiError, post } from "@/lib/api";
import type { Controller } from "@/lib/types";
import { Button } from "./ui/button";
import { Field, Input, Textarea } from "./ui/field";
import { Notice } from "./ui/layout-bits";

interface TestResult {
  ok: boolean;
  application_version?: string;
  sites?: { id: string; name: string }[];
  capabilities?: { capabilities: Record<string, { status: string; write: boolean }>; beyond_known_spec: boolean; newest_known_spec: string };
  error_kind?: string;
  error?: string;
  hint?: string;
}

export function ControllerForm({ onCreated, submitLabel = "Save controller" }: { onCreated: (c: Controller) => void; submitLabel?: string }) {
  const [name, setName] = useState("Home Lab UDM");
  const [baseUrl, setBaseUrl] = useState("https://");
  const [apiKey, setApiKey] = useState("");
  const [verifyTls, setVerifyTls] = useState(true);
  const [ca, setCa] = useState("");
  const [showTls, setShowTls] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const conn = () => ({ base_url: baseUrl.trim(), api_key: apiKey.trim(), verify_tls: verifyTls, ca_bundle_pem: ca.trim() || null });

  const runTest = async () => {
    setBusy("test");
    setError(null);
    setTest(null);
    try {
      setTest(await post<TestResult>("/api/controllers/test", conn()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Test failed.");
    } finally {
      setBusy(null);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy("save");
    setError(null);
    try {
      onCreated(await post<Controller>("/api/controllers", { ...conn(), name: name.trim() }));
    } catch (err) {
      if (err instanceof ApiError && err.detail && typeof err.detail === "object" && "ok" in (err.detail as object)) setTest(err.detail as TestResult);
      else setError(err instanceof ApiError ? err.message : "Couldn't save the controller.");
    } finally {
      setBusy(null);
    }
  };

  const caps = test?.capabilities?.capabilities;
  const readOk = caps ? Object.values(caps).filter((c) => !c.write && c.status === "supported").length : 0;
  const readTotal = caps ? Object.values(caps).filter((c) => !c.write).length : 0;

  return (
    <form onSubmit={save} className="flex flex-col gap-4" noValidate>
      <Field label="Name" hint="How this controller appears across Fabric Warden.">
        {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} maxLength={128} />}
      </Field>
      <Field label="Console address" hint="The UniFi console itself, e.g. https://192.168.1.1. Needs UniFi Network 9.1 or newer.">
        {(p) => <Input {...p} className="font-mono" value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setTest(null); }} inputMode="url" />}
      </Field>
      <Field
        label="API key"
        hint={<>Create one on the console under <b>Settings → Control Plane → Integrations</b>. Stored encrypted; never shown again.</>}
      >
        {(p) => <Input {...p} type="password" autoComplete="off" className="font-mono" value={apiKey} onChange={(e) => { setApiKey(e.target.value); setTest(null); }} />}
      </Field>

      <div>
        <button type="button" onClick={() => setShowTls((s) => !s)} className="inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline" aria-expanded={showTls}>
          {showTls ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />} Certificate options
        </button>
        {showTls && (
          <div className="mt-3 flex flex-col gap-3 rounded-[4px] border border-rule bg-surface-2 p-3">
            <label className="flex items-start gap-2.5 text-base">
              <input type="checkbox" className="mt-1 size-4 accent-[var(--accent)]" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
              <span>
                <span className="font-semibold">Verify the console&apos;s TLS certificate</span>
                <span className="block text-sm text-ink-3">UniFi consoles ship self-signed certificates. Prefer pasting the console CA below over turning this off.</span>
              </span>
            </label>
            <Field label="Console CA certificate (PEM)">
              {(p) => <Textarea {...p} className="font-mono text-xs" placeholder="-----BEGIN CERTIFICATE-----" value={ca} onChange={(e) => setCa(e.target.value)} />}
            </Field>
          </div>
        )}
      </div>

      {error && <Notice tone="crit">{error}</Notice>}
      {test && !test.ok && (
        <Notice tone="crit" title="Connection failed">
          <p>{test.hint}</p>
          <p className="mt-1 font-mono text-xs text-ink-3">{test.error}</p>
        </Notice>
      )}
      {test?.ok && (
        <Notice tone="ok" title={`Connected to UniFi Network ${test.application_version}`}>
          <p>
            {test.sites?.length} site{test.sites?.length === 1 ? "" : "s"} ({test.sites?.map((s) => s.name).join(", ")}). {readOk} of {readTotal} read
            capabilities available on this version.
          </p>
          {test.capabilities?.beyond_known_spec && (
            <p className="mt-1">This version is newer than the newest API spec Fabric Warden knows ({test.capabilities.newest_known_spec}); new endpoints will be probed, not assumed.</p>
          )}
        </Notice>
      )}

      <div className="flex flex-wrap gap-2">
        <Button icon={<PlugZap />} onClick={runTest} loading={busy === "test"} disabled={!apiKey || baseUrl.length < 10}>
          Test connection
        </Button>
        <Button type="submit" variant="primary" loading={busy === "save"} disabled={!test?.ok || !name.trim()}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
