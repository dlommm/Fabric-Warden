"use client";

// Metric charts. Thin 2px lines on hairline grids, a crosshair readout that
// lists every series at the hovered time (also by keyboard), a legend for two
// or more series, and a table view for every chart. Series colours follow the
// entity (sorted by label), never its rank, and never reuse status colours.
import { clsx } from "clsx";
import Link from "next/link";
import { type KeyboardEvent, type PointerEvent, type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { StateLabel, type Tone } from "@/components/ui/signs";
import { entityHref, formatValue, niceTicks, timeFull, timeLabel } from "@/lib/metrics";
import type { MetricUnit, QueryResult, QuerySeries } from "@/lib/types";

export const SERIES_COLORS = 6;
const seriesVar = (i: number) => `var(--series-${(i % SERIES_COLORS) + 1})`;

/** Colour slot per series id: sorted by label so a re-ranking never repaints a line. */
export function colorSlots(series: QuerySeries[]): Map<string, number> {
  return new Map([...series].sort((a, b) => a.label.localeCompare(b.label)).map((s, i) => [s.id, i]));
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

const M = { top: 10, right: 14, bottom: 24 };
const TIME_STEPS = [300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];

/** Tick times on whole local boundaries (on the hour, at midnight), at most `count` of them. */
function timeTicks(from: number, to: number, count: number): number[] {
  const step = TIME_STEPS.find((s) => (to - from) / s <= count) ?? TIME_STEPS[TIME_STEPS.length - 1];
  const offset = new Date(from * 1000).getTimezoneOffset() * 60;
  const out: number[] = [];
  for (let t = Math.ceil((from - offset) / step) * step + offset; t <= to; t += step) out.push(t);
  return out;
}

export function LineChart({
  result,
  height = 200,
  threshold,
  ariaLabel,
}: {
  result: QueryResult;
  height?: number;
  threshold?: { value: number; label: string };
  ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const descId = useId();
  const unit = result.metric.unit;
  const slots = useMemo(() => colorSlots(result.series), [result.series]);

  const times = useMemo(() => {
    const set = new Set<number>();
    for (const s of result.series) for (const p of s.points) set.add(p[0]);
    return [...set].sort((a, b) => a - b);
  }, [result.series]);
  const byTime = useMemo(() => result.series.map((s) => new Map(s.points.map((p) => [p[0], p]))), [result.series]);

  const values = result.series.flatMap((s) => s.points.map((p) => p[1]));
  if (threshold) values.push(threshold.value);
  const ticks = niceTicks(values.length ? Math.min(...values) : 0, values.length ? Math.max(...values) : 1, unit);
  const y0 = ticks[0];
  const y1 = ticks[ticks.length - 1];
  const tickText = (t: number) => (unit === "bool" ? (t >= 1 ? "Up" : "Down") : formatValue(unit, t, true));
  // Gutter fits the longest tick label (11px mono is about 6.7px a glyph).
  const left = 14 + Math.max(...ticks.map((t) => tickText(t).length)) * 6.7;
  const w = Math.max(width - left - M.right, 10);
  const h = height - M.top - M.bottom;
  const x = (t: number) => left + ((t - result.from) / (result.to - result.from)) * w;
  const y = (v: number) => M.top + h - ((v - y0) / (y1 - y0 || 1)) * h;
  const span = result.to - result.from;
  const gap = result.step * 2.5;

  const paths = result.series.map((s) => {
    let d = "";
    let prev: number | null = null;
    for (const [t, v] of s.points) {
      const px = x(t).toFixed(1);
      const py = y(v).toFixed(1);
      if (prev === null || t - prev > gap) d += `M${px},${py}`;
      else if (unit === "bool") d += `H${px}V${py}`;
      else d += `L${px},${py}`;
      prev = t;
    }
    return d;
  });

  const xTicks = timeTicks(result.from, result.to, Math.max(2, Math.min(7, Math.floor(w / 90))));
  // History only exists since sampling began; say so instead of drawing an empty stretch.
  const first = times[0];
  const startsLate = first != null && first - result.from > span * 0.04;

  const pick = (e: PointerEvent<SVGSVGElement>) => {
    if (!times.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const t = result.from + ((e.clientX - rect.left - left) / w) * span;
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    setHover(best);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!times.length) return;
    const last = times.length - 1;
    const move: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, PageUp: -10, PageDown: 10 };
    if (e.key in move) setHover((i) => Math.min(last, Math.max(0, (i ?? last) + move[e.key])));
    else if (e.key === "Home") setHover(0);
    else if (e.key === "End") setHover(last);
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  const ht = hover != null ? times[hover] : null;
  const readout =
    ht != null
      ? result.series
          .map((s, i) => ({ s, p: byTime[i].get(ht) }))
          .filter((r) => r.p)
          .sort((a, b) => (b.p![1] ?? 0) - (a.p![1] ?? 0))
      : [];
  const tipLeft = ht != null ? x(ht) : 0;
  // The tooltip sits on the far side of the highest hovered point, so it never covers the line being read.
  const tipY = readout.length ? Math.min(...readout.map((r) => y(r.p![1]))) : 0;
  const tipBelow = tipY < M.top + h / 2;
  const single = result.series.length === 1;

  return (
    <div
      ref={ref}
      className="relative outline-offset-4"
      tabIndex={times.length ? 0 : undefined}
      role="group"
      aria-label={`${ariaLabel}. Use the arrow keys to read values.`}
      aria-describedby={descId}
      onKeyDown={onKey}
      onBlur={() => setHover(null)}
    >
      <p id={descId} className="sr-only" aria-live="polite">
        {ht != null ? `${timeFull(ht)}: ${readout.map((r) => `${r.s.label} ${formatValue(unit, r.p![1])}`).join(", ")}` : ""}
      </p>
      {width > 0 && (
        <svg width={width} height={height} className="block touch-pan-y select-none" onPointerMove={pick} onPointerLeave={() => setHover(null)} aria-hidden>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + w} y1={y(t)} y2={y(t)} stroke="var(--rule)" strokeWidth={1} shapeRendering="crispEdges" />
              <text x={left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-3 font-mono text-[11px] tabular">
                {tickText(t)}
              </text>
            </g>
          ))}
          {xTicks.map((t) => (
            <text key={t} x={x(t)} y={height - 6} textAnchor="middle" className="fill-ink-3 font-mono text-[11px] tabular">
              {timeLabel(t, span)}
            </text>
          ))}
          {startsLate && (
            <g>
              <line x1={x(first)} x2={x(first)} y1={M.top} y2={M.top + h} stroke="var(--rule-strong)" strokeWidth={1} shapeRendering="crispEdges" />
              <text x={x(first) - 6} y={M.top + 10} textAnchor="end" className="fill-ink-3 text-[11px]">
                Recording starts
              </text>
            </g>
          )}
          {threshold && threshold.value >= y0 && threshold.value <= y1 && (
            <g>
              <line x1={left} x2={left + w} y1={y(threshold.value)} y2={y(threshold.value)} stroke="var(--crit)" strokeWidth={1} shapeRendering="crispEdges" />
              <text x={left + w} y={y(threshold.value) - 4} textAnchor="end" className="fill-crit text-[11px] font-bold">
                {threshold.label}
              </text>
            </g>
          )}
          {single &&
            unit !== "bool" &&
            result.series[0].points.length > 1 &&
            areaPaths(result.series[0], x, y, y0, gap).map((d, i) => <path key={i} d={d} fill={seriesVar(slots.get(result.series[0].id) ?? 0)} opacity={0.1} />)}
          {paths.map((d, i) => (
            <path key={result.series[i].id} d={d} fill="none" stroke={seriesVar(slots.get(result.series[i].id) ?? i)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {result.series.map((s) => {
            const p = s.points[s.points.length - 1];
            if (!p || hover != null) return null;
            return <circle key={s.id} cx={x(p[0])} cy={y(p[1])} r={4} fill={seriesVar(slots.get(s.id) ?? 0)} stroke="var(--surface)" strokeWidth={2} />;
          })}
          {ht != null && (
            <g>
              <line x1={x(ht)} x2={x(ht)} y1={M.top} y2={M.top + h} stroke="var(--ink-3)" strokeWidth={1} shapeRendering="crispEdges" />
              {readout.map(({ s, p }) => (
                <circle key={s.id} cx={x(ht)} cy={y(p![1])} r={4} fill={seriesVar(slots.get(s.id) ?? 0)} stroke="var(--surface)" strokeWidth={2} />
              ))}
            </g>
          )}
        </svg>
      )}
      {ht != null && readout.length > 0 && (
        <div
          className={clsx(
            "pointer-events-none absolute z-10 min-w-40 rounded-[3px] border border-rule-strong bg-surface px-3 py-2 text-sm shadow-[0_8px_24px_rgb(0_0_0/0.14)]",
            !tipBelow && "-translate-y-full",
          )}
          style={{ top: tipBelow ? tipY + 14 : tipY - 14, ...(tipLeft > width / 2 ? { right: width - tipLeft + 12 } : { left: tipLeft + 12 }) }}
        >
          <p className="mb-1 font-mono text-xs text-ink-3">{timeFull(ht)}</p>
          <ul className="flex flex-col gap-0.5">
            {readout.slice(0, 8).map(({ s, p }) => (
              <li key={s.id} className="flex items-center gap-2 whitespace-nowrap">
                <span aria-hidden className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: seriesVar(slots.get(s.id) ?? 0) }} />
                <span className="tabular font-bold text-ink">{formatValue(unit, p![1])}</span>
                <span className="max-w-48 truncate text-ink-2">{s.label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(result.series.length > 1 || !["site", "controller"].includes(result.series[0]?.entity_type)) && result.series.length > 0 && (
        <Legend series={result.series} slots={slots} unit={unit} />
      )}
    </div>
  );
}

function areaPaths(s: QuerySeries, x: (t: number) => number, y: (v: number) => number, base: number, gap: number): string[] {
  const out: string[] = [];
  let run: [number, number][] = [];
  const flush = () => {
    if (run.length > 1) {
      const top = run.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("");
      out.push(`${top}L${x(run[run.length - 1][0]).toFixed(1)},${y(base)}L${x(run[0][0]).toFixed(1)},${y(base)}Z`);
    }
    run = [];
  };
  let prev: number | null = null;
  for (const [t, v] of s.points) {
    if (prev !== null && t - prev > gap) flush();
    run.push([t, v]);
    prev = t;
  }
  flush();
  return out;
}

function Legend({ series, slots, unit }: { series: QuerySeries[]; slots: Map<string, number>; unit: MetricUnit }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Legend">
      {series.map((s) => {
        const href = entityHref(s.entity_type, s.entity_id);
        return (
          <li key={s.id} className="inline-flex min-w-0 items-center gap-1.5">
            <span aria-hidden className="h-0.5 w-3.5 shrink-0 rounded-full" style={{ background: seriesVar(slots.get(s.id) ?? 0) }} />
            {href ? (
              <Link href={href} className="max-w-52 truncate text-ink-2 hover:text-ink hover:underline">
                {s.label}
              </Link>
            ) : (
              <span className="max-w-52 truncate text-ink-2">{s.label}</span>
            )}
            <span className="tabular font-semibold text-ink">{s.stale ? "no data" : formatValue(unit, s.last_value)}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** The table view every chart offers: newest buckets first. */
export function SeriesTable({ result, caption, maxRows = 48 }: { result: QueryResult; caption: string; maxRows?: number }) {
  const unit = result.metric.unit;
  const times = [...new Set(result.series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => b - a).slice(0, maxRows);
  const maps = result.series.map((s) => new Map(s.points.map((p) => [p[0], p])));
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full text-left text-sm whitespace-nowrap">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-surface-2">
          <tr className="text-xs font-bold tracking-wide text-ink-2 uppercase">
            <th scope="col" className="px-3 py-2">Time</th>
            {result.series.map((s) => (
              <th key={s.id} scope="col" className="px-3 py-2 text-right normal-case">{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {times.map((t) => (
            <tr key={t} className="border-t border-rule">
              <th scope="row" className="px-3 py-1.5 font-mono text-xs font-normal text-ink-2">{timeFull(t)}</th>
              {maps.map((m, i) => {
                const p = m.get(t);
                return (
                  <td key={i} className="tabular px-3 py-1.5 text-right">
                    {p ? formatValue(unit, p[1]) : "—"}
                    {p && unit !== "bool" && p[3] !== p[2] && <span className="ml-1.5 text-ink-3">(peak {formatValue(unit, p[3])})</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {times.length === 0 && <p className="px-3 py-4 text-ink-3">No samples in this range.</p>}
    </div>
  );
}

/** Thresholds of the built-in alert rules, so ranked percentages carry the same state the alerts would. */
const PCT_LIMIT: Record<string, number> = { "device.cpu_pct": 90, "device.mem_pct": 90, "radio.tx_retries_pct": 15, "wan.loss_pct": 20 };

function pctTone(metric: string, v: number): Tone {
  const limit = PCT_LIMIT[metric] ?? 95;
  return v >= limit ? "crit" : v >= limit * 0.8 ? "warn" : "ok";
}

const BAR_FILL: Record<Tone, string> = { ok: "bg-[#00704a]", warn: "bg-warn ring-1 ring-ink/40", crit: "bg-crit", muted: "bg-ink-3", info: "bg-accent" };
const RANGE_WORD = (unit: MetricUnit, lo: number, hi: number) =>
  lo === hi ? "steady across this range" : `between ${formatValue(unit, lo)} and ${formatValue(unit, hi)} in this range`;

/**
 * One reading on a sign line: what it is, the value (as a state when the value
 * is itself a state), and what the range did. Several readings share one ruled panel.
 */
export function ReadingLine({ title, result, controls }: { title: string; result?: QueryResult; controls?: ReactNode }) {
  let value: ReactNode = <span className="text-ink-3">…</span>;
  let note = "";
  if (result) {
    const unit = result.metric.unit;
    const live = result.series.filter((s) => !s.stale && s.last_value != null);
    const one = result.series.length === 1 ? result.series[0] : null;
    if (!live.length) {
      value = <StateLabel tone="muted">No data</StateLabel>;
    } else if (unit === "bool") {
      const down = live.filter((s) => (s.last_value ?? 0) < 0.5);
      value = one ? (
        <StateLabel tone={down.length ? "crit" : "ok"}>{down.length ? "Down" : "Up"}</StateLabel>
      ) : (
        <StateLabel tone={down.length ? "crit" : "ok"}>{down.length ? `${down.length} down` : `All ${live.length} up`}</StateLabel>
      );
      note = down.length > 1 || (!one && down.length) ? down.map((s) => s.label).join(", ") : one ? one.label + (one.summary.min != null && one.summary.min < 0.5 && !down.length ? ", was down in this range" : "") : "";
    } else {
      const sum = unit === "count";
      const now = sum ? live.reduce((a, s) => a + (s.last_value ?? 0), 0) : Math.max(...live.map((s) => s.last_value ?? 0));
      if (result.metric.alarm) {
        const word = result.metric.label.toLowerCase().replace(/^devices /, "");
        value = now > 0 ? <StateLabel tone="crit">{formatValue(unit, now)} {word}</StateLabel> : <StateLabel tone="ok">None {word}</StateLabel>;
      } else if (unit === "pct" && result.metric.higher_is_worse) {
        const tone = pctTone(result.metric.id, now);
        value = tone === "ok" ? <span className="font-bold tabular">{formatValue(unit, now)}</span> : <StateLabel tone={tone}>{formatValue(unit, now)}</StateLabel>;
      } else {
        value = <span className="font-bold tabular">{formatValue(unit, now)}</span>;
      }
      if (one && one.summary.min != null && one.summary.max != null) note = RANGE_WORD(unit, one.summary.min, one.summary.max);
      else if (!one) note = sum ? `total of ${live.length}` : `highest of ${live.length}`;
    }
  }
  return (
    <div className="flex min-h-[var(--row-h)] flex-wrap items-baseline gap-x-4 gap-y-0.5 border-b border-rule px-4 py-2.5 last:border-b-0">
      <h3 className="min-w-40 text-md font-bold">{title}</h3>
      <span className="text-md">{value}</span>
      <span className="text-sm text-ink-3">{note}</span>
      {controls && <span className="ml-auto self-center">{controls}</span>}
    </div>
  );
}

/** Ranked list: which entities sit highest right now. Percentages use the usage bar and alert thresholds. */
export function RankedList({ result }: { result: QueryResult }) {
  const unit = result.metric.unit;
  const metric = result.metric.id;
  const rows = result.series.filter((s) => s.last_value != null);
  const limit = PCT_LIMIT[metric];
  const max = unit === "pct" ? (limit && limit < 50 ? limit * 2 : 100) : Math.max(1, ...rows.map((s) => s.summary.max ?? s.last_value ?? 0));
  if (!rows.length) return <p className="text-ink-3">No data in this range.</p>;
  return (
    <ol className="flex flex-col">
      {rows.map((s) => {
        const href = entityHref(s.entity_type, s.entity_id);
        const v = s.last_value ?? 0;
        const pct = Math.max(0, Math.min(100, (v / max) * 100));
        const tone: Tone = s.stale ? "muted" : unit === "pct" && result.metric.higher_is_worse ? pctTone(metric, v) : "ok";
        return (
          <li key={s.id} className="grid min-h-[var(--row-h)] grid-cols-[minmax(0,1fr)_6rem_auto] items-center gap-3 border-b border-rule last:border-b-0">
            <span className="min-w-0 truncate">
              {href ? (
                <Link href={href} className="font-semibold hover:underline">{s.label}</Link>
              ) : (
                <span className="font-semibold">{s.label}</span>
              )}
            </span>
            <span className="h-2 w-24 rounded-[2px] bg-surface-3" aria-hidden>
              <span className={clsx("block h-2 rounded-[2px]", BAR_FILL[tone])} style={{ width: `${pct}%` }} />
            </span>
            <span className="text-right whitespace-nowrap">
              {s.stale ? (
                <span className="text-ink-3">no data</span>
              ) : tone === "warn" || tone === "crit" ? (
                <StateLabel tone={tone}>{formatValue(unit, v)}</StateLabel>
              ) : (
                <span className="tabular font-bold">{formatValue(unit, v)}</span>
              )}
              {s.summary.max != null && unit !== "count" && <span className="ml-2 hidden text-sm text-ink-3 tabular sm:inline">peak {formatValue(unit, s.summary.max)}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
