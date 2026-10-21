"use client";

// IPAM vocabulary. The address map is a plan drawn like a car-park bay chart:
// each bay is an address, its fill and border say what occupies it, and the
// legend and readout always spell the state out in words.
import { clsx } from "clsx";
import { CircleCheck, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { StateLabel, type Tone } from "@/components/ui/signs";
import type { AddressCell, AddressState, Finding, IpStatus, PrefixRow, Reservation } from "@/lib/types";

// -- utilisation ---------------------------------------------------------------------

export function usageTone(pct: number): Tone {
  return pct >= 95 ? "crit" : pct >= 80 ? "warn" : "ok";
}

export function UsageBar({ used, usable, pct }: { used: number; usable: number; pct: number }) {
  const tone = usageTone(pct);
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="h-2 w-24 shrink-0 rounded-[2px] bg-surface-3" aria-hidden>
        <span
          className={clsx("block h-full rounded-[2px]", tone === "crit" ? "bg-crit" : tone === "warn" ? "bg-warn ring-1 ring-ink/40" : "bg-[#00704a]")}
          style={{ width: `${Math.min(100, Math.max(pct, used > 0 ? 2 : 0))}%` }}
        />
      </span>
      <span className={clsx("tabular text-sm", tone === "ok" ? "text-ink-2" : tone === "warn" ? "font-bold text-warn-text" : "font-bold text-crit")}>
        {pct.toFixed(pct > 0 && pct < 10 ? 1 : 0)}%
      </span>
      <span className="tabular text-xs text-ink-3">
        {used.toLocaleString()} / {usable.toLocaleString()}
      </span>
    </span>
  );
}

// -- documented status -----------------------------------------------------------------

const STATUS_WORD: Record<IpStatus, string> = { active: "Active", planned: "Planned", reserved: "Held", deprecated: "Deprecated" };

/** The documented status read against observation: "Active" only means something if the controller agrees. */
export function PrefixStatus({ p }: { p: Pick<PrefixRow, "status" | "observed"> & { contains_observed?: boolean } }) {
  if (p.status === "active") {
    if (p.observed) return <StateLabel tone="ok">In service</StateLabel>;
    if (p.contains_observed) return <StateLabel tone="info">Container</StateLabel>;
    return <StateLabel tone="warn">Not observed</StateLabel>;
  }
  if (p.status === "deprecated" && p.observed) return <StateLabel tone="warn">Deprecated, still in use</StateLabel>;
  return <StateLabel tone="muted">{STATUS_WORD[p.status]}</StateLabel>;
}

export const STATUS_OPTIONS: { value: IpStatus; label: string; hint: string }[] = [
  { value: "active", label: "Active", hint: "Should exist on the controller" },
  { value: "planned", label: "Planned", hint: "Not built yet" },
  { value: "reserved", label: "Held", hint: "Set aside, not to be used" },
  { value: "deprecated", label: "Deprecated", hint: "Being retired" },
];

// -- findings ---------------------------------------------------------------------------

const SEVERITY_TONE: Record<Finding["severity"], Tone> = { critical: "crit", warning: "warn", info: "info" };

function findingHref(f: Finding): string | undefined {
  if (f.refs.prefix_id) return `/ipam/prefixes/${f.refs.prefix_id}${f.refs.address ? `?address=${f.refs.address}` : ""}`;
  if (f.refs.network_id) return "/inventory/networks";
  if (f.refs.address) return `/inventory/clients?q=${encodeURIComponent(f.refs.address)}`;
  return undefined;
}

function FindingRow({ f, hover }: { f: Finding; hover: string }) {
  const href = f.href ?? findingHref(f);
  const body = (
    <>
      <StateLabel tone={SEVERITY_TONE[f.severity]} className={f.detail ? "sm:w-[24rem] sm:shrink-0" : undefined}>
        <span className="text-ink">{f.title}</span>
      </StateLabel>
      {f.detail && <span className="text-ink-2">{f.detail}</span>}
    </>
  );
  const cls = "flex min-h-[var(--row-h)] flex-col gap-0.5 px-3 py-2 sm:flex-row sm:items-center sm:gap-4";
  return (
    <li className="border-b border-rule last:border-b-0">
      {href ? (
        <Link href={href} className={clsx(cls, hover)}>
          {body}
        </Link>
      ) : (
        <div className={cls}>{body}</div>
      )}
    </li>
  );
}

/**
 * Planned-vs-observed findings. Anything critical or warning goes on the yellow
 * attention plate; informational notes sit below it on plain ground.
 */
export function FindingsPlate({
  findings,
  emptyText,
  action,
  label = "Planned versus observed",
  heading = (n) => `${n} difference${n > 1 ? "s" : ""} between plan and network`,
}: {
  findings: Finding[];
  emptyText: string;
  action?: ReactNode;
  label?: string;
  heading?: (n: number) => string;
}) {
  const severe = findings.filter((f) => f.severity !== "info");
  const notes = findings.filter((f) => f.severity === "info");
  if (findings.length === 0) {
    return (
      <section aria-label={label} className="flex items-center gap-3 rounded-[var(--radius)] border border-ok/40 bg-ok-soft px-5 py-4">
        <CircleCheck className="size-5 shrink-0 text-ok" aria-hidden />
        <p className="font-bold">{emptyText}</p>
      </section>
    );
  }
  return (
    <div className="flex flex-col gap-[var(--gap)]">
      {severe.length > 0 && (
        <section aria-labelledby="findings-h" className="sign rounded-[var(--radius)] bg-warn text-warn-ink" style={{ outlineColor: "#14171a" }}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 pt-4 pb-2">
            <TriangleAlert className="size-5 translate-y-0.5 self-start" aria-hidden strokeWidth={2.5} />
            <h2 id="findings-h" className="text-lg font-bold">
              {heading(severe.length)}
            </h2>
          </div>
          <ul className="mx-2 mb-2 rounded-[3px] bg-surface text-ink">
            {severe.map((f) => (
              <FindingRow key={f.key} f={f} hover="hover:bg-warn-soft" />
            ))}
          </ul>
        </section>
      )}
      {notes.length > 0 && (
        <section aria-labelledby="notes-h" className="rounded-[var(--radius)] border border-rule bg-surface">
          <div className="flex min-h-11 flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-2">
            <h2 id="notes-h" className="text-md font-bold">
              {notes.length} note{notes.length > 1 ? "s" : ""}
            </h2>
            {action}
          </div>
          <ul>
            {notes.map((f) => (
              <FindingRow key={f.key} f={f} hover="hover:bg-surface-2" />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// -- reservations -----------------------------------------------------------------------

export function ReservationState({ r }: { r: Pick<Reservation, "state" | "mac_seen_at"> }) {
  switch (r.state) {
    case "in_use":
      return <StateLabel tone="ok">In use</StateLabel>;
    case "conflict":
      return <StateLabel tone="crit">Held by another device</StateLabel>;
    case "elsewhere":
      return <StateLabel tone="warn">Device seen at {r.mac_seen_at}</StateLabel>;
    default:
      return <StateLabel tone="muted">Not seen</StateLabel>;
  }
}

// -- address map ------------------------------------------------------------------------

type CellKind = AddressState | "conflict";

const CELL_STYLE: Record<CellKind, string> = {
  // Weight ranks the bays: occupied and attention states are bold, empty ones recede.
  free: "border border-rule-strong bg-surface font-normal text-ink-3",
  dhcp: "border border-dashed border-accent/50 bg-accent-soft/60 font-normal text-ink-3",
  reserved: "border-2 border-accent bg-surface font-bold text-ink",
  observed: "bg-[#00704a] font-bold text-white",
  gateway: "bg-ink font-bold text-surface",
  network: "bg-surface-3 font-normal text-ink-3",
  broadcast: "bg-surface-3 font-normal text-ink-3",
  conflict: "bg-surface font-bold text-ink ring-[3px] ring-[#c8102e] ring-inset",
};

export const CELL_LABEL: Record<CellKind, string> = {
  observed: "In use",
  reserved: "Reserved, idle",
  dhcp: "DHCP pool, free",
  free: "Free",
  gateway: "Gateway",
  network: "Network address",
  broadcast: "Broadcast",
  conflict: "Conflict",
};

const LEGEND_ORDER: CellKind[] = ["observed", "reserved", "dhcp", "free", "gateway", "conflict", "network"];

export function cellKind(c: AddressCell): CellKind {
  return c.conflict ? "conflict" : c.state;
}

/** Documented-and-in-use bays keep the reservation's sign border on top of the green. */
function documentedInUse(c: AddressCell) {
  return c.state === "observed" && c.reservation && !c.conflict;
}

function Swatch({ kind }: { kind: CellKind }) {
  return <span aria-hidden className={clsx("block size-4 shrink-0 rounded-[2px]", CELL_STYLE[kind])} />;
}

export function AddressMap({
  cells,
  selected,
  onSelect,
  label,
}: {
  cells: AddressCell[];
  selected: string | null;
  onSelect: (address: string) => void;
  label: string;
}) {
  // 16 bays per row where they fit (a /24 reads as 16 rows of 16), otherwise 8.
  const [cols, setCols] = useState(16);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setCols(e.contentRect.width >= 16 * 40 + 68 ? 16 : 8));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = useMemo(() => Math.max(0, cells.findIndex((c) => c.address === selected)), [cells, selected]);
  const [focusIdx, setFocusIdx] = useState(index);
  useEffect(() => setFocusIdx(index), [index]);
  const counts = useMemo(() => {
    const out: Partial<Record<CellKind, number>> = {};
    for (const c of cells) {
      const k = c.state === "broadcast" ? "network" : cellKind(c);
      out[k] = (out[k] ?? 0) + 1;
    }
    return out;
  }, [cells]);

  const move = (to: number) => {
    const i = Math.min(cells.length - 1, Math.max(0, to));
    setFocusIdx(i);
    refs.current[i]?.focus();
    onSelect(cells[i].address);
  };
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const step: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols };
    if (e.key in step) {
      e.preventDefault();
      move(i + step[e.key]);
    } else if (e.key === "Home") {
      e.preventDefault();
      move(i - (i % cols));
    } else if (e.key === "End") {
      e.preventDefault();
      move(i - (i % cols) + cols - 1);
    }
  };

  // Rows are labelled with the first address's last two octets, like a bay chart's row letters.
  const rows = Array.from({ length: Math.ceil(cells.length / cols) }, (_, r) => cells.slice(r * cols, r * cols + cols));
  return (
    <div ref={wrap}>
      <div role="grid" aria-label={label} aria-describedby="map-help" className="flex flex-col gap-1 overflow-x-auto pb-1">
        {rows.map((row, r) => (
          <div role="row" key={r} className="flex items-center gap-1">
            <span role="rowheader" className={clsx("tabular shrink-0 pr-1 text-right font-mono text-xs text-ink-3", cols === 16 ? "w-16" : "w-9")}>
              .{row[0].address.split(".").slice(cols === 16 ? -2 : -1).join(".")}
            </span>
            {row.map((c, j) => {
              const i = r * cols + j;
              const kind = cellKind(c);
              const isSel = c.address === selected;
              const octet = c.address.split(".").pop();
              return (
                <span role="gridcell" key={c.address} aria-selected={isSel}>
                  <button
                    ref={(el) => {
                      refs.current[i] = el;
                    }}
                    type="button"
                    tabIndex={i === focusIdx ? 0 : -1}
                    onClick={() => {
                      setFocusIdx(i);
                      onSelect(c.address);
                    }}
                    onKeyDown={(e) => onKey(e, i)}
                    aria-label={`${c.address}, ${CELL_LABEL[kind]}${c.holders?.length ? `, ${c.holders.map((h) => h.name).join(", ")}` : ""}${c.reservation ? `, reserved for ${c.reservation.hostname || c.reservation.mac || "unnamed"}` : ""}`}
                    className={clsx(
                      "tabular relative flex size-[30px] items-center justify-center rounded-[3px] font-mono text-[11px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:size-9",
                      CELL_STYLE[kind],
                      documentedInUse(c) && "sign",
                      isSel && "outline-[2.5px] outline-offset-2 outline-accent",
                    )}
                  >
                    {octet}
                  </button>
                </span>
              );
            })}
          </div>
        ))}
      </div>
      <p id="map-help" className="sr-only">
        Use the arrow keys to move between addresses. Details for the focused address appear beside the map.
      </p>
      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm text-ink-2" aria-label="Legend">
        {LEGEND_ORDER.filter((k) => counts[k]).map((k) => (
          <li key={k} className="inline-flex items-center gap-1.5">
            <Swatch kind={k} />
            {k === "network" ? "Network / broadcast" : CELL_LABEL[k]}
            <span className="tabular font-semibold text-ink">{counts[k]}</span>
          </li>
        ))}
        {cells.some(documentedInUse) && (
          <li className="inline-flex items-center gap-1.5">
            <span aria-hidden className="sign block size-4 rounded-[2px] bg-[#00704a] text-white" />
            Inner border: matches a reservation
          </li>
        )}
      </ul>
    </div>
  );
}
