// The signature move: a path through the network drawn as a map-type
// direction sign. One sign-blue panel with an inset white border carries one
// heavy route line; hops are destinations along it. A hop that needs
// attention sits on the line as a yellow patch, a failed hop as a white patch
// with a red border. The Internet is always at the head of the route.
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

function Marker({ tone, current }: { tone: Tone; current?: boolean }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "relative z-10 block shrink-0 rounded-[3px]",
        current ? "size-7" : "size-5",
        tone === "warn" && "bg-[#ffd200] ring-2 ring-[#14171a]",
        tone === "crit" && "bg-white ring-[3px] ring-[#c8102e]",
        (tone === "ok" || tone === "info") && "bg-white ring-2 ring-[#0b3d91] ring-inset",
        tone === "muted" && "rounded-full bg-white",
      )}
    />
  );
}

function Label({ hop, vertical }: { hop: Hop; vertical?: boolean }) {
  const tone = hop.tone ?? "ok";
  const patch = tone === "warn" || tone === "crit";
  const body = (
    <span
      className={clsx(
        "inline-flex max-w-full flex-col rounded-[3px] text-left",
        !vertical && "sm:items-center sm:text-center",
        patch && "px-2 py-1",
        patch && !vertical && "sm:-mt-1",
        tone === "warn" && "bg-[#ffd200] text-[#14171a]",
        tone === "crit" && "bg-white text-[#14171a] ring-2 ring-[#c8102e]",
        !patch && "text-white",
      )}
    >
      <span className={clsx("font-bold", hop.current ? "text-md underline decoration-2 underline-offset-4" : "text-base")}>
        {hop.label}
        {hop.count !== undefined && <span className="tabular ml-1.5 font-semibold opacity-80">{hop.count}</span>}
      </span>
      {hop.sub && <span className={clsx("text-xs", patch ? "text-[#434c55]" : "text-[#c9d6ef]")}>{hop.sub}</span>}
    </span>
  );
  return hop.href ? (
    <Link href={hop.href} className="max-w-full rounded-[3px] hover:[&>span>span:first-child]:underline">
      {body}
    </Link>
  ) : (
    body
  );
}

/** `vertical` keeps the phone layout at every width, for signs inside narrow side panels. */
export function RouteSign({ hops, label, footer, vertical }: { hops: Hop[]; label: string; footer?: ReactNode; vertical?: boolean }) {
  // Tailwind needs literal class names, so the horizontal variants are dropped wholesale when vertical.
  const h = (cls: string) => (vertical ? cls.split(" ").filter((c) => !c.startsWith("sm:")).join(" ") : cls);
  return (
    <figure
      className={h("sign rounded-[6px] bg-[#0b3d91] px-5 pt-5 pb-4 text-white sm:pr-12")}
      style={{ "--n": hops.length } as CSSProperties}
    >
      <nav aria-label={label}>
        <div className="relative">
          {/* The route: vertical on phones, horizontal from first to last destination on wider screens. */}
          <span
            aria-hidden
            className={h("absolute top-2 bottom-2 left-[8px] w-2 bg-white sm:top-[6px] sm:bottom-auto sm:left-[calc(100%/(2*var(--n)))] sm:h-2 sm:w-auto sm:right-[calc(100%/(2*var(--n))-30px)]")}
          />
          <span
            aria-hidden
            className={h("absolute bottom-[-6px] left-[2px] size-0 border-x-[10px] border-t-[14px] border-x-transparent border-t-white sm:top-[-1px] sm:bottom-auto sm:left-auto sm:right-[calc(100%/(2*var(--n))-44px)] sm:border-y-[11px] sm:border-r-0 sm:border-l-[16px] sm:border-y-transparent sm:border-t-transparent sm:border-l-white")}
          />
        <ol className={h("relative flex flex-col gap-4 sm:grid sm:gap-0 sm:[grid-template-columns:repeat(var(--n),minmax(0,1fr))]")}>
          {hops.map((hop) => (
            <li key={hop.key} className={h("relative flex min-w-0 items-start gap-3 sm:flex-col sm:items-center sm:gap-2.5 sm:px-1.5")}>
              <span className={h("flex w-6 justify-center sm:h-5 sm:w-auto sm:items-center")}>
                <Marker tone={hop.tone ?? "ok"} current={hop.current} />
              </span>
              <Label hop={hop} vertical={vertical} />
            </li>
          ))}
        </ol>
        </div>
      </nav>
      {footer && <figcaption className="mt-4 border-t border-white/25 pt-3 text-sm text-[#c9d6ef]">{footer}</figcaption>}
    </figure>
  );
}
