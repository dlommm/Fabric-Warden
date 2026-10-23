"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { ChevronDown, ChevronRight, Menu, Pause, Pin, PinOff, Play, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { ApiError, get } from "@/lib/api";
import { type LiveStatus, useLiveEvents } from "@/lib/live";
import { SessionProvider, useMe, useSession } from "@/lib/session";
import type { SnapshotRow, Summary } from "@/lib/types";
import { Skeleton } from "../ui/layout-bits";
import { Mark } from "../ui/mark";
import { CommandPalette } from "./command-palette";
import { NAV, type NavItem } from "./nav";
import { UserMenu } from "./user-menu";

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const me = useMe();
  const signedOut = me.error instanceof ApiError && me.error.status === 401 && !me.isFetching;
  const setup = useQuery({
    queryKey: ["setup", "shell"],
    queryFn: () => get<{ needs_owner: boolean }>("/api/setup/status"),
    enabled: signedOut,
    staleTime: 0,
  });

  useEffect(() => {
    if (signedOut && setup.data && !setup.isFetching) router.replace(setup.data.needs_owner ? "/setup" : "/login");
  }, [signedOut, setup.data, setup.isFetching, router]);

  if (!me.data) {
    return (
      <div className="flex min-h-dvh">
        <div className="rail-plate hidden w-[236px] lg:block" />
        <div className="flex-1 p-8">
          <Skeleton className="mb-4 h-7 w-56" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }
  return (
    <SessionProvider me={me.data}>
      <Frame>{children}</Frame>
    </SessionProvider>
  );
}

/** Small per-browser conveniences (pins, folded groups). Never required for the page to work. */
function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw) setValue(JSON.parse(raw) as T);
    } catch {}
  }, [key]);
  const set = (v: T) => {
    setValue(v);
    try {
      window.localStorage.setItem(key, JSON.stringify(v));
    } catch {}
  };
  return [value, set];
}

function useActiveHref() {
  const pathname = usePathname();
  // The most specific matching entry wins, so /ipam/vlans doesn't also light up /ipam.
  return NAV.flatMap((g) => g.items)
    .map((i) => i.href)
    .filter((h) => (h === "/" ? pathname === "/" : pathname === h || pathname.startsWith(`${h}/`)))
    .sort((a, b) => b.length - a.length)[0];
}

function RailLink({ item, active, pinned, onPin, onNavigate }: { item: NavItem; active: boolean; pinned: boolean; onPin: () => void; onNavigate?: () => void }) {
  return (
    <li className="group/item relative">
      {/* Each item is a tap on its group's bus line; the current page's tap is filled. */}
      <Link
        href={item.href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={clsx(
          "tap flex h-8 items-center gap-3 rounded-[3px] pr-8 pl-[11px] text-base transition-colors duration-[var(--dur)]",
          active ? "font-semibold text-white" : "text-rail-ink-2 hover:text-rail-ink",
        )}
      >
        <span className="truncate">{item.label}</span>
      </Link>
      <button
        type="button"
        onClick={onPin}
        aria-pressed={pinned}
        aria-label={pinned ? `Unpin ${item.label}` : `Pin ${item.label}`}
        title={pinned ? "Unpin" : "Pin to the top of the rail"}
        className={clsx(
          "absolute top-1/2 right-1 grid size-7 -translate-y-1/2 place-items-center rounded-[3px] text-rail-ink-2 opacity-0 transition-opacity duration-[var(--dur-fast)] group-hover/item:opacity-100 hover:bg-white/10 hover:text-white focus-visible:opacity-100",
          pinned && "text-rail-ink",
        )}
      >
        {pinned ? <PinOff className="size-3.5" aria-hidden /> : <Pin className="size-3.5" aria-hidden />}
      </button>
    </li>
  );
}

function Rail({ onNavigate }: { onNavigate?: () => void }) {
  const { can, moduleEnabled, modules } = useSession();
  const loaded = modules.length > 0;
  const activeHref = useActiveHref();
  const [pins, setPins] = useStored<string[]>("fw.rail.pins", []);
  const [folded, setFolded] = useStored<string[]>("fw.rail.folded", []);
  const visible = (i: NavItem) => (!i.permission || can(i.permission)) && (!i.module || !loaded || moduleEnabled(i.module));
  const all = NAV.flatMap((g) => g.items).filter(visible);
  const pinned = pins.map((h) => all.find((i) => i.href === h)).filter((i): i is NavItem => !!i);
  const togglePin = (href: string) => setPins(pins.includes(href) ? pins.filter((h) => h !== href) : [...pins, href]);
  const groupLabel = "text-xs font-semibold tracking-[0.02em] text-rail-ink-2";
  return (
    <nav aria-label="Primary" className="flex h-full flex-col">
      <Link href="/" className="flex h-14 shrink-0 items-center gap-2.5 border-b border-rail-rule px-5" onClick={onNavigate}>
        <Mark className="size-[26px] shrink-0" />
        <span className="text-md leading-tight font-bold text-rail-ink">Fabric Warden</span>
      </Link>
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-3 pt-4 pb-6 [scrollbar-color:rgb(127_200_255/0.25)_transparent]">
        {pinned.length > 0 && (
          <div className="bus">
            <p className={clsx("mb-0.5 flex h-6 items-center gap-1.5 pl-[30px]", groupLabel)}>Pinned</p>
            <ul className="flex flex-col">
              {pinned.map((item) => (
                <RailLink key={item.href} item={item} active={item.href === activeHref} pinned onPin={() => togglePin(item.href)} onNavigate={onNavigate} />
              ))}
            </ul>
          </div>
        )}
        {NAV.map((group) => {
          const items = group.items.filter(visible);
          if (!items.length) return null;
          const holdsActive = items.some((i) => i.href === activeHref);
          const open = holdsActive || !folded.includes(group.section);
          const id = `rail-${group.section.toLowerCase()}`;
          return (
            <div key={group.section} className={clsx(open && "bus")}>
              <button
                type="button"
                aria-expanded={open}
                aria-controls={id}
                disabled={holdsActive}
                onClick={() => setFolded(open ? [...folded, group.section] : folded.filter((s) => s !== group.section))}
                title={holdsActive ? "Holds the page you are on" : undefined}
                className={clsx("mb-0.5 flex h-6 w-full items-center gap-1.5 rounded-[3px] pr-2 pl-[30px] transition-colors duration-[var(--dur-fast)] enabled:hover:text-white disabled:cursor-default", groupLabel)}
              >
                <span className="flex-1 text-left">{group.section}</span>
                {!open && <span className="tabular font-mono font-medium">{items.length}</span>}
                <ChevronDown
                  className={clsx("size-3.5 transition-transform duration-[var(--dur)] ease-[var(--ease-out)]", !open && "-rotate-90", holdsActive && "opacity-0")}
                  aria-hidden
                />
              </button>
              {open && (
                <ul id={id} className="flex flex-col">
                  {items.map((item) => (
                    <RailLink key={item.href} item={item} active={item.href === activeHref} pinned={pins.includes(item.href)} onPin={() => togglePin(item.href)} onNavigate={onNavigate} />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

/** Where you are: section, then page, then "Detail" for anything below a page. */
function Breadcrumbs() {
  const pathname = usePathname();
  const activeHref = useActiveHref();
  const group = NAV.find((g) => g.items.some((i) => i.href === activeHref));
  const item = group?.items.find((i) => i.href === activeHref);
  if (!group || !item) return null;
  const deeper = pathname !== item.href && item.href !== "/";
  return (
    <nav aria-label="Breadcrumb" className="hidden min-w-0 items-center text-sm md:flex">
      <ol className="flex min-w-0 items-center gap-1.5">
        <li className="text-ink-3">{group.section}</li>
        <li aria-hidden className="text-ink-3">
          <ChevronRight className="size-3.5" />
        </li>
        <li className="min-w-0 truncate">
          {deeper ? (
            <Link href={item.href} className="font-semibold text-ink-2 hover:text-ink hover:underline">{item.label}</Link>
          ) : (
            <span aria-current="page" className="font-semibold text-ink">{item.label}</span>
          )}
        </li>
        {deeper && (
          <>
            <li aria-hidden className="text-ink-3">
              <ChevronRight className="size-3.5" />
            </li>
            <li aria-current="page" className="font-semibold text-ink">Detail</li>
          </>
        )}
      </ol>
    </nav>
  );
}

const LIVE_WORD: Record<LiveStatus, string> = { live: "Live", connecting: "Reconnecting", off: "Not live" };

/** The event stream's real state: live updates arrive, or the page refreshes on its timers only. */
function LiveIndicator({ status }: { status: LiveStatus }) {
  return (
    <span
      className="hidden items-center gap-2 font-mono text-xs font-medium text-ink-2 sm:inline-flex"
      title={status === "live" ? "Changes from the controller appear as they are synced." : "Live updates are not connected; pages refresh on their own timers."}
      role="status"
    >
      <span
        aria-hidden
        className={clsx("size-2 rounded-full", status === "live" ? "beacon bg-ok text-ok" : status === "connecting" ? "bg-warn" : "bg-ink-3")}
      />
      {LIVE_WORD[status]}
    </span>
  );
}

/**
 * When the estate was last read and which configuration that was: the newest
 * successful sync, and the hash of the newest configuration snapshot.
 */
function SyncStamp() {
  const { moduleEnabled } = useSession();
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => get<Summary>("/api/inventory/summary"), enabled: moduleEnabled("inventory"), refetchInterval: 30_000 });
  const snaps = useQuery({ queryKey: ["config", "snapshots"], queryFn: () => get<{ items: SnapshotRow[] }>("/api/config/snapshots"), enabled: moduleEnabled("config-history"), refetchInterval: 60_000 });
  const last = summary.data?.controllers.map((c) => c.last_sync_at).filter((x): x is string => !!x).sort().at(-1);
  const snap = snaps.data?.items.reduce<SnapshotRow | undefined>((a, b) => (!a || b.taken_at > a.taken_at ? b : a), undefined);
  if (!last && !snap) return null;
  return (
    <span className="hidden items-center gap-1.5 text-xs text-ink-3 xl:inline-flex">
      {last && (
        <span title={new Date(last).toLocaleString()}>
          Synced <span className="font-mono">{new Date(last).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        </span>
      )}
      {last && snap && <span aria-hidden>·</span>}
      {snap && (
        <Link href="/config" className="hover:text-ink" title={`Configuration snapshot taken ${new Date(snap.taken_at).toLocaleString()}`}>
          config <span className="font-mono">{snap.hash.slice(0, 7)}</span>
        </Link>
      )}
    </span>
  );
}

/**
 * Pauses the moving traffic on schematic drawings (WCAG 2.2.2). The choice
 * is kept per browser and applied as data-live on the root element.
 */
function MotionToggle() {
  const [paused, setPaused] = useStored<boolean>("fw.motion.paused", false);
  useEffect(() => {
    if (paused) document.documentElement.dataset.live = "paused";
    else delete document.documentElement.dataset.live;
  }, [paused]);
  return (
    <button
      type="button"
      onClick={() => setPaused(!paused)}
      aria-pressed={paused}
      title={paused ? "Resume moving traffic on drawings" : "Pause moving traffic on drawings"}
      className="hidden size-8 place-items-center rounded-[3px] border border-rule text-ink-2 transition-colors duration-[var(--dur-fast)] hover:border-accent hover:text-ink sm:grid"
    >
      {paused ? <Play className="size-3.5" aria-hidden /> : <Pause className="size-3.5" aria-hidden />}
      <span className="sr-only">{paused ? "Resume traffic motion" : "Pause traffic motion"}</span>
    </button>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { can } = useSession();
  const live = useLiveEvents(can("inventory:read"));
  const pathname = usePathname();
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <div className="relative z-20 hidden w-[236px] shrink-0 lg:block">
        <aside className="rail-plate on-rail sticky top-0 h-dvh">
          <Rail />
        </aside>
      </div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button type="button" className="anim-scrim absolute inset-0 bg-[var(--scrim)]" aria-label="Close navigation" onClick={() => setOpen(false)} />
          <aside className="rail-plate on-rail relative h-full w-[272px] animate-[fw-slide-in_var(--dur-slow)_var(--ease-out)]">
            <Rail onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-rule bg-[var(--header-fill)] px-4 backdrop-blur-md lg:px-7">
          <button
            type="button"
            className="-ml-1 rounded p-1.5 text-ink-2 hover:bg-surface-3 lg:hidden"
            aria-label={open ? "Close navigation" : "Open navigation"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <Breadcrumbs />
          <div className="flex min-w-0 flex-1 justify-end md:justify-center">
            <CommandPalette />
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <SyncStamp />
            <LiveIndicator status={live} />
            <MotionToggle />
            <UserMenu />
          </div>
        </header>
        <main id="main" key={pathname} className="anim-page mx-auto w-full max-w-[1480px] flex-1 px-4 py-6 lg:px-7 lg:py-7">
          {children}
        </main>
      </div>
    </div>
  );
}
