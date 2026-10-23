// The estate's figures: a name, a figure, and the state in words underneath.
// Trend lines are drawn only from recorded metric history; gaps in the record
// break the line instead of bridging it.
import { clsx } from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import type { Point } from "@/lib/types";

/** A minimal trend line. `step` is the bucket size in seconds; gaps over 2.5 buckets break the line. */
export function Sparkline({ points, step, className, label }: { points: Point[]; step: number; className?: string; label?: string }) {
  const W = 120;
  const H = 28;
  const vals = points.map((p) => p[1]);
  if (points.length < 2) return null;
  const t0 = points[0][0];
  const t1 = points[points.length - 1][0];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * (W - 4) + 2;
  const y = (v: number) => H - 3 - ((v - lo) / span) * (H - 6);
  const runs: Point[][] = [];
  for (const p of points) {
    const run = runs[runs.length - 1];
    if (!run || p[0] - run[run.length - 1][0] > step * 2.5) runs.push([p]);
    else run.push(p);
  }
  const last = points[points.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={clsx("h-7 w-full overflow-visible", className)} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      {runs.map((run, i) =>
        run.length > 1 ? (
          <g key={i}>
            <path
              d={`M${x(run[0][0])},${H} ${run.map((p) => `L${x(p[0])},${y(p[1])}`).join(" ")} L${x(run[run.length - 1][0])},${H} Z`}
              fill="currentColor"
              opacity={0.1}
            />
            <path d={run.map((p, j) => `${j ? "L" : "M"}${x(p[0])},${y(p[1])}`).join(" ")} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          </g>
        ) : null,
      )}
      <circle cx={x(last[0])} cy={y(last[1])} r={2.5} fill="currentColor" stroke="var(--surface)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * One cell of the estate's figures strip: the name, the figure in the data
 * face, and its state in words underneath. Cells sit side by side on one ruled
 * sheet (see `FigureStrip`) rather than as separate cards. `of` qualifies the
 * figure ("links", "in 24 h"); `trend` sits under the state where history is
 * recorded.
 */
export function KpiPlate({
  name,
  icon,
  value,
  of,
  state,
  trend,
  href,
  className,
}: {
  name: string;
  icon?: ReactNode;
  value: ReactNode;
  of?: ReactNode;
  state?: ReactNode;
  trend?: ReactNode;
  href?: string;
  className?: string;
}) {
  const body = (
    <>
      <span className="flex items-center gap-2 text-sm text-ink-2">
        {icon && <span className="text-ink-3 [&_svg]:size-3.5" aria-hidden>{icon}</span>}
        <span className="truncate">{name}</span>
      </span>
      <span className="flex items-baseline gap-1.5">
        <span className="tabular font-mono text-[26px] leading-tight font-medium">{value}</span>
        {of && <span className="tabular font-mono text-sm text-ink-3">{of}</span>}
      </span>
      <span className="min-h-5 text-sm">{state}</span>
      {trend && <span className="mt-1 text-trace">{trend}</span>}
    </>
  );
  const cls = clsx("flex min-w-0 flex-col gap-0.5 px-4 py-3.5 shadow-[1px_0_0_var(--rule),0_1px_0_var(--rule)] transition-colors duration-[var(--dur-fast)]", href && "hover:bg-accent-soft", className);
  return href ? (
    <Link href={href} className={cls}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

/** The ruled sheet the figures sit on. */
export function FigureStrip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="plate marks grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] overflow-hidden">
      {children}
    </section>
  );
}
