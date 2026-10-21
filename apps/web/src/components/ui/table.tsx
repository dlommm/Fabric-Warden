"use client";

import { clsx } from "clsx";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, useMemo, useState } from "react";
import { Skeleton } from "./layout-bits";

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  sort?: (row: T) => string | number | null;
  className?: string;
  align?: "left" | "right";
}

export function DataTable<T extends { id: string }>({
  rows,
  columns,
  loading,
  empty,
  href,
  initialSort,
  caption,
  rowClassName,
}: {
  rows: T[] | undefined;
  columns: Column<T>[];
  loading?: boolean;
  empty?: ReactNode;
  href?: (row: T) => string;
  initialSort?: { key: string; dir: "asc" | "desc" };
  caption?: string;
  rowClassName?: (row: T) => string | undefined;
}) {
  const router = useRouter();
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!rows || !sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const get = col.sort;
    return [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const r = av < bv ? -1 : 1;
      return sort.dir === "asc" ? r : -r;
    });
  }, [rows, sort, columns]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-rule-strong">
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const Icon = active ? (sort!.dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                  className={clsx(
                    "h-9 px-[var(--row-pad)] text-xs font-bold tracking-wide whitespace-nowrap text-ink-2 uppercase",
                    c.align === "right" && "text-right",
                    c.className,
                  )}
                >
                  {c.sort ? (
                    <button
                      type="button"
                      onClick={() => toggle(c.key)}
                      className={clsx("inline-flex items-center gap-1 uppercase hover:text-ink", active && "text-ink")}
                    >
                      {c.header}
                      <Icon className={clsx("size-3.5", !active && "opacity-40")} aria-hidden />
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 6 }).map((_, i) => (
              <tr key={i} className="border-b border-rule">
                {columns.map((c) => (
                  <td key={c.key} className="h-[var(--row-h)] px-[var(--row-pad)]">
                    <Skeleton className="h-3.5 w-3/4" />
                  </td>
                ))}
              </tr>
            ))}
          {!loading &&
            sorted?.map((row) => {
              const link = href?.(row);
              return (
                <tr
                  key={row.id}
                  onClick={link ? (e) => {
                    if ((e.target as HTMLElement).closest("a,button,input,select")) return;
                    router.push(link);
                  } : undefined}
                  className={clsx(
                    "border-b border-rule transition-colors duration-100 last:border-b-0",
                    link && "cursor-pointer hover:bg-surface-2",
                    rowClassName?.(row),
                  )}
                >
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={clsx("h-[var(--row-h)] px-[var(--row-pad)] align-middle text-base", c.align === "right" && "text-right tabular", c.className)}
                    >
                      {c.cell(row)}
                    </td>
                  ))}
                </tr>
              );
            })}
        </tbody>
      </table>
      {!loading && sorted && sorted.length === 0 && empty}
    </div>
  );
}
