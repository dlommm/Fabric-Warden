// A path through the network drawn as a trace: one line of linework with a
// node per hop. A hop that needs attention is drawn as a dashed amber ring,
// a failed hop as a dashed red ring with its word beside it. The Internet is
// always at the head of the route.
import { clsx } from "clsx";
import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";
import { deviceTone, type Tone } from "./ui/signs";

/** Uplink path (nearest device first, as the API reports it) as hops from the internet down. */
export function pathHops(path: { id: string; name: string; model: string; state: string; features: string[] }[]): Hop[] {
  const devices = [...path].reverse();
  const hops: Hop[] = devices.map((d) => ({ key: d.id, label: d.name, sub: d.model, href: `/inventory/devices/${d.id}`, tone: deviceTone(d.state) }));
  if (devices[0]?.features.includes("gateway")) hops.unshift({ key: "wan", label: "Internet", sub: "via WAN", tone: "muted" });
  return hops;
}

export interface Hop {
  key: string;
  label: string;
  sub?: ReactNode;
  href?: string;
  tone?: Tone;
  count?: number;
  /** Marks the hop the page is about ("you are here"). */
  current?: boolean;
}

const WORD: Partial<Record<Tone, string>> = { warn: "needs attention", crit: "not online" };

function Node({ tone, current }: { tone: Tone; current?: boolean }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "relative z-10 block shrink-0 rounded-full border-[1.5px] bg-surface",
        current ? "size-4 border-accent bg-accent shadow-[0_0_0_4px_var(--accent-soft)]" : "size-3",
        !current && tone === "warn" && "border-dashed border-warn-line",
        !current && tone === "crit" && "border-dashed border-crit",
        !current && (tone === "ok" || tone === "info") && "border-line",
        !current && tone === "muted" && "border-ink-3",
      )}
    />
  );
}

function Label({ hop, vertical }: { hop: Hop; vertical?: boolean }) {
  const tone = hop.tone ?? "ok";
  const body = (
    <span className={clsx("inline-flex max-w-full min-w-0 flex-col text-left", !vertical && "sm:items-center sm:text-center")}>
      <span className={clsx("truncate font-semibold", hop.current ? "text-md text-accent" : "text-base text-ink")}>
        {hop.label}
        {hop.count !== undefined && <span className="tabular ml-1.5 font-mono text-sm font-medium text-ink-3">{hop.count}</span>}
      </span>
      {hop.sub && <span className="truncate text-xs text-ink-3">{hop.sub}</span>}
      {WORD[tone] && <span className={clsx("text-xs font-semibold", tone === "crit" ? "text-crit" : "text-warn-text")}>{WORD[tone]}</span>}
    </span>
  );
  return hop.href ? (
    <Link href={hop.href} className="max-w-full min-w-0 rounded-[3px] hover:[&>span>span:first-child]:underline">
      {body}
    </Link>
  ) : (
    body
  );
}

/**
 * `vertical` keeps the phone layout at every width, for traces inside narrow side panels.
 * `bare` drops the trace's own sheet so it can sit inside another one.
 * The line inks itself in once and the hops appear down it (skipped under reduced motion).
 */
export function RouteTrace({ hops, label, footer, vertical, bare }: { hops: Hop[]; label: string; footer?: ReactNode; vertical?: boolean; bare?: boolean }) {
  // Tailwind needs literal class names, so the horizontal variants are dropped wholesale when vertical.
  const h = (cls: string) => (vertical ? cls.split(" ").filter((c) => !c.startsWith("sm:")).join(" ") : cls);
  return (
    <figure className={bare ? "" : "plate marks px-5 pt-5 pb-4"} style={{ "--n": hops.length } as CSSProperties}>
      <nav aria-label={label}>
        <div className="relative">
          <span
            aria-hidden
            className={h("absolute top-2 bottom-2 left-[11px] w-px origin-top animate-[fw-route-draw-y_var(--dur-draw)_var(--ease-out)] bg-line sm:origin-left sm:animate-[fw-route-draw_var(--dur-draw)_var(--ease-out)] sm:top-[7px] sm:bottom-auto sm:left-[calc(100%/(2*var(--n)))] sm:h-px sm:w-auto sm:right-[calc(100%/(2*var(--n)))]")}
          />
          <ol className={h("relative flex flex-col gap-3.5 sm:grid sm:gap-0 sm:[grid-template-columns:repeat(var(--n),minmax(0,1fr))]")}>
            {hops.map((hop, i) => (
              <li
                key={hop.key}
                className={h("anim-rise relative flex min-w-0 items-start gap-3 sm:flex-col sm:items-center sm:gap-2 sm:px-1.5")}
                style={{ animationDelay: `${120 + i * 70}ms` }}
              >
                <span className={h("flex h-[18px] w-6 shrink-0 items-center justify-center sm:h-4 sm:w-auto")}>
                  <Node tone={hop.tone ?? "ok"} current={hop.current} />
                </span>
                <Label hop={hop} vertical={vertical} />
              </li>
            ))}
          </ol>
        </div>
      </nav>
      {footer && <figcaption className="mt-4 border-t border-rule pt-3 text-sm text-ink-3">{footer}</figcaption>}
    </figure>
  );
}
