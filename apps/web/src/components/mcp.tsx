"use client";

/** Account page: how to connect an MCP client to Fabric Warden's read-only tools. */

import { Copy } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Panel } from "@/components/ui/layout-bits";
import { StateLabel } from "@/components/ui/signs";
import { useSession } from "@/lib/session";

const TOOLS: { name: string; does: string; module: string; moduleName: string }[] = [
  { name: "search_inventory", does: "Search devices, clients, networks, prefixes, racks, cables, assets and docs", module: "inventory", moduleName: "Inventory" },
  { name: "get_device", does: "A device with ports, PoE, uplink, clients and history", module: "inventory", moduleName: "Inventory" },
  { name: "get_client", does: "A client with uplink, and the server or camera it is", module: "inventory", moduleName: "Inventory" },
  { name: "list_alerts", does: "Active or resolved monitoring alerts", module: "monitoring", moduleName: "Monitoring & Alerts" },
  { name: "firewall_findings", does: "Shadowed, redundant and risky firewall policies", module: "firewall", moduleName: "Firewall & Policy Manager" },
  { name: "list_cameras", does: "Protect cameras with uplink switch and documented port", module: "unifi-protect", moduleName: "UniFi Protect" },
  { name: "home_assistant_entities", does: "UPS and temperature sensors, trackers matched to clients", module: "home-assistant", moduleName: "Home Assistant" },
];

export function McpPanel() {
  const { can, moduleEnabled } = useSession();
  const on = moduleEnabled("mcp");
  const [origin, setOrigin] = useState("https://fabric-warden.lan");
  useEffect(() => setOrigin(window.location.origin), []);
  const url = `${origin}/api/mcp`;
  const cli = `claude mcp add --transport http fabric-warden ${url} --header "Authorization: Bearer wdn_…"`;
  const json = JSON.stringify({ mcpServers: { "fabric-warden": { type: "http", url, headers: { Authorization: "Bearer wdn_…" } } } }, null, 2);
  return (
    <Panel
      title="MCP clients"
      id="mcp"
      actions={on ? <StateLabel tone="ok">Endpoint on</StateLabel> : <StateLabel tone="muted">Module off</StateLabel>}
    >
      <div className="flex flex-col gap-4">
        <p className="max-w-[70ch] text-ink-2">
          AI assistants that speak the Model Context Protocol can read Fabric Warden through <span className="font-mono text-sm text-ink">{url}</span> (Streamable HTTP). They authenticate with an API token from above, which needs <span className="font-mono text-sm text-ink">mcp:use</span>; each tool also needs its read permission and its module, so a narrowed token sees fewer tools. Every tool is read-only and every call is in the audit trail.
        </p>
        {!on && (
          <Notice tone="info" title="The MCP Gateway module is off">
            {can("modules:manage") ? (
              <>Turn it on in the <Link href="/modules" className="text-accent hover:underline">Module Center</Link>; until then the endpoint answers 409.</>
            ) : (
              "Ask an administrator to turn it on in the Module Center; until then the endpoint answers 409."
            )}
          </Notice>
        )}
        {!can("mcp:use") && <Notice tone="warn" title="Your role cannot use MCP">Tokens you create will not carry mcp:use. Operators, auditors, admins and owners can.</Notice>}
        <div className="grid gap-4 xl:grid-cols-2">
          <Snippet label="Claude Code" text={cli} />
          <Snippet label="Client configuration (JSON)" text={json} />
        </div>
        <div>
          <h3 className="mb-2 font-bold">Tools</h3>
          <ul className="rounded-[var(--radius)] border border-rule">
            {TOOLS.map((t) => {
              const ready = moduleEnabled(t.module);
              return (
                <li key={t.name} className="flex min-h-[var(--row-h)] flex-col gap-0.5 border-b border-rule px-3 py-2 last:border-b-0 sm:flex-row sm:items-center sm:gap-4">
                  <span className="font-mono text-sm font-bold sm:w-56 sm:shrink-0">{t.name}</span>
                  <span className="flex-1 text-ink-2">{t.does}</span>
                  {ready ? <StateLabel tone="ok">Available</StateLabel> : <span className="text-sm text-ink-3">Needs {t.moduleName}</span>}
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-sm text-ink-3">There are no write tools. Proposing changes through the change engine from an MCP client is planned, not built.</p>
        </div>
      </div>
    </Panel>
  );
}

function Snippet({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{label}</span>
        <Button
          size="sm"
          variant="ghost"
          icon={<Copy />}
          onClick={() => {
            navigator.clipboard?.writeText(text);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="overflow-x-auto rounded-[var(--radius)] border border-rule bg-surface-2 p-3 font-mono text-xs break-all whitespace-pre-wrap text-ink">{text}</pre>
    </div>
  );
}
