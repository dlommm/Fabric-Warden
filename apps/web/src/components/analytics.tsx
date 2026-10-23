"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChartLine, Table2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { LineChart, PCT_LIMIT, RankedList, ReadingSign, SeriesTable } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { Panel, Skeleton } from "@/components/ui/layout-bits";
import { get, post } from "@/lib/api";
import { RANGES, formatValue } from "@/lib/metrics";
import type { DeviceSummary, QueryResult, RangeKey, Widget } from "@/lib/types";

const ENTITY_WORD: Record<string, [string, string]> = {
  device: ["device", "devices"],
  radio: ["radio", "radios"],
  network: ["network", "networks"],
  site: ["site", "sites"],
  controller: ["controller", "controllers"],
  probe: ["target", "targets"],
};

export function entityWord(type: string, n: number): string {
  const w = ENTITY_WORD[type] ?? ["series", "series"];
  return n === 1 ? w[0] : w[1];
}

type MetricQuery = { metric: string; entity_ids?: string[]; range: RangeKey; limit?: number };

/** Query options for one metric; shared so a page can read the same cached results its widgets load. */
export function metricQuery(q: MetricQuery, enabled = true) {
  return {
    queryKey: ["metrics", q.metric, q.entity_ids ?? [], q.range, q.limit ?? 5],
    queryFn: () => post<QueryResult>("/api/metrics/query", { entity_ids: [], limit: 5, ...q }),
    enabled,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  };
}

export function useMetric(q: MetricQuery, enabled = true) {
  return useQuery(metricQuery(q, enabled));
}

/** Presets as one radio row above everything they scope. */
export function RangePicker({ value, onChange, label = "Time range" }: { value: RangeKey; onChange: (r: RangeKey) => void; label?: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-[var(--radius)] border border-rule bg-surface-3/70 p-0.5 shadow-[inset_0_1px_2px_rgb(var(--shadow-tint)/0.08)]">
      {RANGES.map((r) => {
        const on = r.key === value;
        return (
          <button
            key={r.key}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={r.long}
            onClick={() => onChange(r.key)}
            onKeyDown={(e) => {
              const i = RANGES.findIndex((x) => x.key === value);
              const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
              if (d) {
                e.preventDefault();
                onChange(RANGES[(i + d + RANGES.length) % RANGES.length].key);
                (e.currentTarget.parentElement?.children[(i + d + RANGES.length) % RANGES.length] as HTMLElement | undefined)?.focus();
              }
            }}
            tabIndex={on ? 0 : -1}
            className={clsx(
              "h-8 min-w-11 rounded-[4px] px-2.5 text-sm font-semibold tabular transition-[background-color,color,box-shadow] duration-[var(--dur)] ease-[var(--ease-out)]",
              on ? "bg-accent text-accent-ink shadow-[inset_0_1px_0_rgb(255_255_255/0.2),0_1px_3px_rgb(var(--shadow-tint)/0.25)]" : "text-ink-2 hover:bg-surface hover:text-ink",
            )}
          >
            {r.label}
          </button>
        );
      })}
    </div>
  );
}

export const SIZE_CLASS: Record<Widget["size"], string> = {
  third: "md:col-span-3 xl:col-span-2",
  half: "md:col-span-3",
  full: "md:col-span-6",
};

/** One dashboard widget: a panel holding a chart, a reading or a ranked list. */
export function MetricWidget({
  widget,
  range,
  label,
  controls,
  height = 200,
}: {
  widget: Widget;
  range: RangeKey;
  label: string;
  controls?: ReactNode;
  height?: number;
}) {
  const [table, setTable] = useState(false);
  const q = useMetric({ metric: widget.metric, entity_ids: widget.entity_ids, range, limit: widget.limit });
  const r = q.data;
  const title = widget.title || label;
  const shown = r?.series.length ?? 0;
  return (
    <Panel
      title={title}
      className={clsx("flex min-w-0 flex-col", SIZE_CLASS[widget.size])}
      bodyClassName="flex flex-1 flex-col p-4"
      actions={
        <>
          {widget.kind === "line" && r && r.series.length > 0 && (
            <Button size="sm" variant="ghost" icon={table ? <ChartLine /> : <Table2 />} onClick={() => setTable((t) => !t)} aria-pressed={table}>
              {table ? "Chart" : "Table"}
            </Button>
          )}
          {controls}
        </>
      }
    >
      {q.error ? (
        <p className="text-crit">{(q.error as Error).message}</p>
      ) : !r ? (
        <Skeleton className={widget.kind === "line" ? "h-48 w-full" : "h-16 w-full"} />
      ) : (
        <div className={clsx("flex flex-1 flex-col transition-opacity duration-150", q.isFetching && q.isPlaceholderData && "opacity-60")}>
          {r.series.length === 0 ? (
            <NoHistory unit={r.metric.label} />
          ) : widget.kind === "line" ? (
            table ? (
              <SeriesTable result={r} caption={title} />
            ) : (
              <>
                <NowLine result={r} range={range} />
                <LineChart
                  result={r}
                  height={height}
                  ariaLabel={`${title}, ${RANGES.find((x) => x.key === range)?.long}`}
                  threshold={
                    r.metric.unit === "pct" && r.metric.higher_is_worse && PCT_LIMIT[r.metric.id] != null
                      ? { value: PCT_LIMIT[r.metric.id], label: `Default alert at ${PCT_LIMIT[r.metric.id]}%` }
                      : undefined
                  }
                />
              </>
            )
          ) : (
            <RankedList result={r} />
          )}
          {r.total_series > shown && shown > 0 && (
            <p className="mt-auto pt-3 text-sm text-ink-3">
              {widget.kind === "top" ? "Top" : "Showing"} {shown} of {r.total_series} {entityWord(r.metric.entity_type, r.total_series)},{" "}
              {r.metric.higher_is_worse ? "highest" : "lowest"} first
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

/** For one series: the latest value and the range it moved through, above the chart. */
function NowLine({ result, range }: { result: QueryResult; range: RangeKey }) {
  const s = result.series.length === 1 ? result.series[0] : null;
  if (!s || s.last_value == null || result.metric.unit === "bool") return null;
  const unit = result.metric.unit;
  const { min, max } = s.summary;
  return (
    <p className="mb-2 flex flex-wrap items-baseline gap-x-2 text-sm text-ink-3">
      <span className="tabular text-xl font-bold text-ink">{formatValue(unit, s.last_value)}</span>
      <span>{s.stale ? "last reported" : "now"}</span>
      {min != null && max != null && (
        <span>
          · {RANGES.find((x) => x.key === range)?.label} range {formatValue(unit, min)} to {formatValue(unit, max)}
        </span>
      )}
    </p>
  );
}

export function NoHistory({ unit }: { unit: string }) {
  return (
    <div className="flex flex-1 flex-col justify-center gap-1 py-6 text-center">
      <p className="font-bold">No {unit} history in this range</p>
      <p className="mx-auto max-w-[44ch] text-sm text-ink-3">
        History starts when a metrics module is enabled and builds one sample per sync. Try a shorter range, or check that the controller is syncing.
      </p>
    </div>
  );
}

function ReadingRow({ widget, range, label, controls }: { widget: Widget; range: RangeKey; label: string; controls?: ReactNode }) {
  const q = useMetric({ metric: widget.metric, entity_ids: widget.entity_ids, range, limit: widget.limit });
  // The offline count is per site; the inventory says which devices those are (the same cached list the Overview reads).
  const offline = widget.metric === "site.devices_offline";
  const devices = useQuery({ queryKey: ["devices", "all"], queryFn: () => get<{ items: DeviceSummary[] }>("/api/inventory/devices"), enabled: offline });
  const offenders = offline ? devices.data?.items.filter((d) => d.state !== "ONLINE").map((d) => d.name) : undefined;
  return <ReadingSign title={widget.title || label} result={q.data} controls={controls} offenders={offenders} />;
}

/** Consecutive reading widgets form one row of distance signs across the dashboard. */
export function ReadingsPanel({
  widgets,
  range,
  labelOf,
  controlsOf,
}: {
  widgets: Widget[];
  range: RangeKey;
  labelOf: (w: Widget) => string;
  controlsOf?: (w: Widget) => ReactNode;
}) {
  return (
    <section aria-label="Readings" className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,15rem),1fr))] items-start gap-[var(--gap)] md:col-span-6">
      {widgets.map((w) => (
        <ReadingRow key={w.id} widget={w} range={range} label={labelOf(w)} controls={controlsOf?.(w)} />
      ))}
    </section>
  );
}
