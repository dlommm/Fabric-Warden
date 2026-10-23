"use client";

import { clsx } from "clsx";
import { Search, X } from "lucide-react";
import type { ReactNode } from "react";
import { inputClass } from "./ui/field";

export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-3">{children}</div>;
}

export function SearchInput({ value, onChange, placeholder, label }: { value: string; onChange: (v: string) => void; placeholder: string; label: string }) {
  return (
    <div className="relative w-full sm:w-72">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
      <input
        type="search"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={clsx(inputClass, "h-9 pr-8 pl-8 [&::-webkit-search-cancel-button]:hidden")}
      />
      {value && (
        <button type="button" onClick={() => onChange("")} className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-ink-3 hover:text-ink" aria-label="Clear search">
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string; count?: number }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex h-9 overflow-hidden rounded-[var(--radius)] border border-rule-strong">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          tabIndex={value === o.value ? 0 : -1}
          onClick={() => onChange(o.value)}
          onKeyDown={(e) => {
            const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
            if (!d) return;
            e.preventDefault();
            const i = options.findIndex((x) => x.value === value);
            const n = (i + d + options.length) % options.length;
            onChange(options[n].value);
            (e.currentTarget.parentElement?.children[n] as HTMLElement | undefined)?.focus();
          }}
          className={clsx(
            "inline-flex items-center gap-1.5 px-3 text-sm font-semibold transition-[background-color,color,box-shadow] duration-[var(--dur)] ease-[var(--ease-out)] not-first:border-l not-first:border-rule",
            value === o.value ? "bg-accent-soft text-ink shadow-[inset_0_-2px_0_var(--accent)]" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.label}
          {o.count !== undefined && <span className="tabular text-xs text-ink-3">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}
