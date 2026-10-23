// The drawing's vocabulary: state labels, zone patches, demo tags and the
// provenance line. Colour never carries meaning alone; every label has an
// icon and a word.
import { clsx } from "clsx";
import { CircleCheck, CircleDashed, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { ageLabel, titleCase } from "@/lib/format";
import type { Provenance } from "@/lib/types";

export type Tone = "ok" | "warn" | "crit" | "muted" | "info";

const toneText: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn-text",
  crit: "text-crit",
  muted: "text-ink-3",
  info: "text-accent",
};

const toneIcon: Record<Tone, typeof CircleCheck> = {
  ok: CircleCheck,
  warn: TriangleAlert,
  crit: OctagonAlert,
  muted: CircleDashed,
  info: Info,
};

export function deviceTone(state: string): Tone {
  if (state === "ONLINE") return "ok";
  if (["OFFLINE", "ISOLATED", "U5G_INCORRECT_TOPOLOGY"].includes(state)) return "crit";
  if (["CONNECTION_INTERRUPTED", "UPDATING", "GETTING_READY", "ADOPTING", "PENDING_ADOPTION", "DELETING"].includes(state)) return "warn";
  return "muted";
}

export function controllerTone(status: string): Tone {
  return status === "ok" ? "ok" : status === "unreachable" ? "crit" : status === "degraded" ? "warn" : "muted";
}

export function StateLabel({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  const Icon = toneIcon[tone];
  return (
    <span className={clsx("inline-flex items-center gap-1.5 font-semibold", toneText[tone], className)}>
      <Icon className="size-4 shrink-0" aria-hidden strokeWidth={2.25} />
      <span>{children}</span>
    </span>
  );
}

export function DeviceState({ state }: { state: string }) {
  const label = state === "CONNECTION_INTERRUPTED" ? "Interrupted" : titleCase(state);
  return <StateLabel tone={deviceTone(state)}>{label}</StateLabel>;
}

// Zone colours are fixed in both themes so a zone is recognisable anywhere;
// each carries white text at >= 4.5:1. Brown is reserved for the demo tag.
const ZONE_COLORS = ["#00704a", "#0b3d91", "#14171a", "#5a3d8a", "#8a6400", "#c8102e", "#0f6e7a", "#3d5266"];
const ZONE_FIXED: Record<string, number> = { internal: 0, external: 1, gateway: 2, vpn: 3, hotspot: 4, dmz: 5 };

export function zoneColor(name: string): string {
  const key = name.toLowerCase();
  const idx = ZONE_FIXED[key] ?? 6 + ([...key].reduce((a, c) => a + c.charCodeAt(0), 0) % 2);
  return ZONE_COLORS[idx];
}

/** A VLAN id as a route-number patch: black on white with a black border. */
export function VlanPatch({ id }: { id: number }) {
  return (
    <span className="inline-flex h-5 min-w-7 items-center justify-center rounded-[3px] border-2 border-ink bg-surface px-1 font-mono text-xs font-bold text-ink" title={`VLAN ${id}`}>
      {id}
    </span>
  );
}

const ZONE_ACRONYMS = new Set(["dmz", "vpn", "iot", "wan", "lan"]);

export function zoneLabel(name: string): string {
  return ZONE_ACRONYMS.has(name.toLowerCase()) ? (name.toLowerCase() === "iot" ? "IoT" : name.toUpperCase()) : name;
}

/** A zone rendered as a road-class patch: flat colour, inset white border. */
export function ZonePatch({ name, size = "sm" }: { name: string; size?: "sm" | "md" }) {
  return (
    <span
      className={clsx(
        "frame inline-flex items-center rounded-[3px] font-bold text-white",
        size === "sm" ? "h-6 px-2.5 text-xs" : "h-8 px-3.5 text-sm",
      )}
      style={{ backgroundColor: zoneColor(name) }}
    >
      {zoneLabel(name)}
    </span>
  );
}

/** Demo data is stamped in brown on the drawing: real-looking, not your network. */
export function DemoTag({ className }: { className?: string }) {
  return (
    <span
      className={clsx("inline-flex h-5 shrink-0 items-center rounded-[2px] border border-demo/60 px-1.5 font-mono text-[11px] font-medium whitespace-nowrap text-demo", className)}
      title="Synthetic data from the built-in simulator. Not your network."
    >
      Demo data
    </span>
  );
}

export function ProvenanceLine({ p, className }: { p: Provenance; className?: string }) {
  const stale = p.age_seconds > 600;
  return (
    <span className={clsx("inline-flex flex-wrap items-center gap-2 text-sm text-ink-3", className)}>
      {p.is_demo && <DemoTag />}
      <span className="font-mono text-xs">{p.source}</span>
      <span aria-hidden>·</span>
      <span className={clsx(stale && "font-semibold text-warn-text")}>
        observed {ageLabel(p.age_seconds)} ago{stale ? " (stale)" : ""}
      </span>
      {!p.present && <span className="font-semibold text-crit">no longer reported</span>}
    </span>
  );
}

export function Chip({ children, className, dashed }: { children: ReactNode; className?: string; dashed?: boolean }) {
  return (
    <span className={clsx("inline-flex h-6 items-center rounded-[3px] border px-2 text-xs font-semibold text-ink-2", dashed ? "border-dashed border-ink-3 bg-transparent" : "border-rule-strong bg-surface-2", className)}>
      {children}
    </span>
  );
}
