"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { BookOpen, Bookmark, Cable, CornerDownLeft, Grid3x3, Laptop, Network, Package, Router, Rows3, Search, Server } from "lucide-react";
import { useRouter } from "next/navigation";
import { Dialog } from "radix-ui";
import { useEffect, useMemo, useState } from "react";
import { get } from "@/lib/api";
import { useSession } from "@/lib/session";
import { DemoTag } from "../ui/signs";
import { NAV } from "./nav";

interface Result {
  kind: "device" | "client" | "network" | "prefix" | "reservation" | "rack" | "rack_item" | "cable" | "asset" | "doc" | "page";
  id: string;
  title: string;
  subtitle: string;
  href: string;
  is_demo?: boolean;
}

const KIND_ICON = { device: Router, client: Laptop, network: Network, prefix: Grid3x3, reservation: Bookmark, rack: Rows3, rack_item: Server, cable: Cable, asset: Package, doc: BookOpen };
const KIND_WORD: Record<string, string> = { rack_item: "rack item", doc: "doc page" };

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const router = useRouter();
  const { can, moduleEnabled } = useSession();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !(e.target as HTMLElement).closest("input,textarea,select,[contenteditable]"))) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const term = q.trim();
  const search = useQuery({
    queryKey: ["search", term],
    queryFn: () => get<{ results: Result[] }>(`/api/inventory/search?q=${encodeURIComponent(term)}`),
    enabled: open && term.length > 0 && moduleEnabled("inventory") && can("inventory:read"),
    staleTime: 10_000,
  });

  const pages: Result[] = useMemo(
    () =>
      NAV.flatMap((g) => g.items.map((i) => ({ ...i, section: g.section })))
        .filter((i) => (!i.permission || can(i.permission)) && (!i.module || moduleEnabled(i.module)))
        .filter((i) => !term || i.label.toLowerCase().includes(term.toLowerCase()) || i.section.toLowerCase().includes(term.toLowerCase()))
        .map((i) => ({ kind: "page" as const, id: i.href, title: i.label, subtitle: `${i.section} › ${i.label}`, href: i.href })),
    [term, can, moduleEnabled],
  );
  const results = [...(search.data?.results ?? []), ...pages];

  const go = (r: Result | undefined) => {
    if (!r) return;
    setOpen(false);
    setQ("");
    router.push(r.href);
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ(""); }}>
      <Dialog.Trigger className="flex h-9 w-full min-w-0 max-w-md flex-1 items-center gap-2 rounded-[var(--radius)] border border-rule-strong bg-surface-2 px-3 text-base text-ink-3 shadow-[inset_0_1px_2px_rgb(var(--shadow-tint)/0.06)] transition-colors duration-[var(--dur)] hover:border-ink-3 hover:text-ink-2">
        <Search className="size-4" aria-hidden />
        <span className="truncate">Search devices, clients, IPs, MACs…</span>
        <kbd className="ml-auto hidden rounded-[3px] border border-rule-strong bg-surface px-1.5 font-mono text-xs whitespace-nowrap shadow-[0_1px_0_var(--rule-strong)] sm:inline">Ctrl K</kbd>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-scrim fixed inset-0 z-50 bg-[var(--scrim)] backdrop-blur-[2px]" />
        <Dialog.Content
          className="fixed top-[12vh] left-1/2 z-50 w-[min(640px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden dialog anim-pop"
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">Search</Dialog.Title>
          <div className="flex items-center gap-2 border-b border-rule px-4">
            <Search className="size-4 text-ink-3" aria-hidden />
            <input
              autoFocus
              value={q}
              onChange={(e) => { setQ(e.target.value); setCursor(0); }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, results.length - 1)); }
                if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
                if (e.key === "Enter") go(results[cursor]);
              }}
              placeholder="Name, IP, MAC or page"
              className="h-12 flex-1 bg-transparent text-md outline-none placeholder:text-ink-3 focus-visible:outline-none"
              aria-label="Search"
              role="combobox"
              aria-expanded
              aria-controls="palette-results"
              aria-activedescendant={results[cursor] ? `pr-${cursor}` : undefined}
            />
          </div>
          <ul id="palette-results" role="listbox" className="max-h-[50vh] overflow-y-auto p-1.5">
            {results.length === 0 && (
              <li className="px-3 py-6 text-center text-base text-ink-3">{search.isFetching ? "Searching…" : `Nothing matches “${term}”.`}</li>
            )}
            {results.map((r, i) => {
              const Icon = r.kind === "page" ? CornerDownLeft : KIND_ICON[r.kind];
              return (
                <li
                  key={`${r.kind}-${r.id}`}
                  id={`pr-${i}`}
                  role="option"
                  aria-selected={i === cursor}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => go(r)}
                  className={clsx("flex cursor-pointer items-center gap-3 rounded-[6px] px-3 py-2 transition-colors duration-[var(--dur-fast)]", i === cursor && "bg-accent-soft shadow-[inset_2px_0_0_var(--accent)]")}
                >
                  <Icon className="size-4 shrink-0 text-ink-3" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold">{r.title}</span>
                    <span className="block truncate font-mono text-xs text-ink-3">{r.subtitle}</span>
                  </span>
                  {r.is_demo && <DemoTag />}
                  <span className="text-xs text-ink-3 capitalize">{KIND_WORD[r.kind] ?? r.kind}</span>
                </li>
              );
            })}
          </ul>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
