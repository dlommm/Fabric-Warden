// Formatting and scales for metric values. Units come from the API's metric catalog.
import type { MetricUnit, RangeKey } from "./types";

export const RANGES: { key: RangeKey; label: string; long: string }[] = [
  { key: "1h", label: "1 h", long: "last hour" },
  { key: "6h", label: "6 h", long: "last 6 hours" },
  { key: "24h", label: "24 h", long: "last 24 hours" },
  { key: "7d", label: "7 d", long: "last 7 days" },
  { key: "30d", label: "30 d", long: "last 30 days" },
  { key: "90d", label: "90 d", long: "last 90 days" },
];

export function isRange(v: string | null | undefined): v is RangeKey {
  return RANGES.some((r) => r.key === v);
}

/** A value in its unit, for labels and tooltips. */
export function formatValue(unit: MetricUnit | "", v: number | null | undefined, compact = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  switch (unit) {
    case "pct":
      return `${v.toFixed(compact || v >= 10 ? 0 : 1)}%`;
    case "ms":
      return `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`;
    case "bool":
      return v >= 0.5 ? "Up" : "Down";
    case "bps": {
      const units: [number, string][] = [[1e9, "Gb/s"], [1e6, "Mb/s"], [1e3, "kb/s"]];
      for (const [d, s] of units) if (Math.abs(v) >= d) return `${(v / d).toFixed(compact ? 0 : 1)} ${s}`;
      return `${Math.round(v)} b/s`;
    }
    case "count":
      return v % 1 === 0 ? v.toLocaleString() : v.toFixed(1);
    case "load":
      return v.toFixed(2);
    default:
      return String(Math.round(v * 100) / 100);
  }
}

/** Round, readable y ticks from 0 (or the data floor) to a clean max. */
export function niceTicks(min: number, max: number, unit: MetricUnit | "", count = 4): number[] {
  if (unit === "bool") return [0, 1];
  let lo = Math.min(0, min);
  let hi = max;
  if (unit === "pct") hi = Math.max(hi, 10);
  if (hi <= lo) hi = lo + (unit === "count" ? 4 : 1);
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const s = unit === "count" ? Math.max(1, Math.ceil(step)) : step;
  lo = Math.floor(lo / s) * s;
  const ticks: number[] = [];
  for (let t = lo; t <= hi + s * 0.999; t += s) ticks.push(Math.round(t * 1e6) / 1e6);
  if (unit === "pct" && ticks[ticks.length - 1] > 100 && max <= 100) return ticks.filter((t) => t <= 100).concat(ticks.includes(100) ? [] : [100]);
  return ticks;
}

export function timeLabel(epoch: number, spanSeconds: number): string {
  const d = new Date(epoch * 1000);
  if (spanSeconds <= 86400) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function timeFull(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function holdLabel(seconds: number): string {
  if (seconds === 0) return "immediately";
  if (seconds < 60) return `for ${seconds} s`;
  if (seconds < 3600) return `for ${Math.round(seconds / 60)} min`;
  return `for ${+(seconds / 3600).toFixed(1)} h`;
}

/** "CPU > 90% for 5 min", or "Online is down for 2 min" for up/down metrics. */
export function conditionLabel(r: { metric_label: string; unit: MetricUnit | ""; comparator: "gt" | "lt"; threshold: number; for_seconds: number }): string {
  if (r.unit === "bool") return `${r.metric_label}: ${r.comparator === "lt" ? "down" : "up"} ${holdLabel(r.for_seconds)}`;
  return `${r.metric_label} ${r.comparator === "gt" ? ">" : "<"} ${formatValue(r.unit, r.threshold)} ${holdLabel(r.for_seconds)}`;
}

export function entityHref(entityType: string, entityId: string): string | undefined {
  if (entityType === "device") return `/inventory/devices/${entityId}`;
  if (entityType === "controller") return `/controllers/${entityId}`;
  if (entityType === "network") return "/inventory/networks";
  if (entityType === "probe") return "/wan";
  return undefined;
}
