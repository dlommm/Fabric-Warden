// The signature move: a path through the network drawn as a map-type
// direction sign. Hops are flat sign panels joined by a route line; a hop that
// needs attention becomes a yellow temporary-sign panel, a failed hop a white
// panel with a red border, and the line into it changes with it.
import { clsx } from "clsx";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import type { Tone } from "./ui/signs";

export interface Hop {
  key: string;
  label: string;
  sub?: ReactNode;
  href?: string;
  tone?: Tone;
  count?: number;
}

const panel: Record<Tone, string> = {
  ok: "bg-[#0b3d91] text-white",
  info: "bg-[#0b3d91] text-white",
  muted: "bg-[#3d5266] text-white",
  warn: "bg-warn text-warn-ink",
  crit: "bg-white text-[#14171a] ring-2 ring-inset ring-[#c8102e]",
};

function HopPanel({ hop }: { hop: Hop }) {
  const tone = hop.tone ?? "ok";
  const body = (
    <span className={clsx("sign flex w-full flex-col rounded-[4px] px-3.5 py-2.5 sm:min-w-32", panel[tone])}>
      <span className="flex items-baseline justify-between gap-3">
        <span className="truncate text-base font-bold">{hop.label}</span>
        {hop.count !== undefined && <span className="tabular text-sm font-bold opacity-90">{hop.count}</span>}
      </span>
      {hop.sub && <span className="mt-0.5 line-clamp-2 text-xs opacity-85">{hop.sub}</span>}
    </span>
  );
  return hop.href ? (
    <Link href={hop.href} className="flex rounded-[4px] transition-transform duration-150 hover:-translate-y-0.5">
      {body}
    </Link>
  ) : (
    <span className="flex">{body}</span>
  );
}

function RouteLine({ tone }: { tone: Tone }) {
  const color = tone === "crit" ? "#c8102e" : tone === "warn" ? "#ffd200" : "var(--ok)";
  return (
    <span className="relative mx-auto flex h-8 w-6 flex-col items-center sm:mx-0 sm:h-14 sm:w-auto sm:min-w-6 sm:flex-1 sm:flex-row" aria-hidden>
      <span
        className="h-full w-1.5 rounded-full sm:h-1.5 sm:w-full"
        style={
          tone === "warn"
            ? { backgroundImage: `repeating-linear-gradient(var(--dash-dir, 180deg), ${color} 0 8px, transparent 8px 14px)` }
            : { backgroundColor: color }
        }
      />
      <span
        className="absolute bottom-0 size-0 rotate-90 border-y-[7px] border-l-[10px] border-y-transparent sm:right-0 sm:bottom-auto sm:rotate-0"
        style={{ borderLeftColor: color }}
      />
    </span>
  );
}

export function RouteSign({ hops, label }: { hops: Hop[]; label: string }) {
  return (
    <nav aria-label={label} className="[--dash-dir:180deg] sm:[--dash-dir:90deg]">
      <ol className="flex flex-col items-stretch gap-1 py-1 sm:flex-row sm:items-center">
        {hops.map((hop, i) => (
          <Fragment key={hop.key}>
            {i > 0 && (
              <li aria-hidden className="flex sm:min-w-6 sm:flex-1">
                <RouteLine tone={hop.tone === "crit" || hop.tone === "warn" ? hop.tone : "ok"} />
              </li>
            )}
            <li className="min-w-0 sm:max-w-60 sm:shrink">
              <HopPanel hop={hop} />
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  );
}
