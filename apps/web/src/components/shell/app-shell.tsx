"use client";

import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { ApiError, get } from "@/lib/api";
import { useLiveEvents } from "@/lib/live";
import { SessionProvider, useMe, useSession } from "@/lib/session";
import { Skeleton } from "../ui/layout-bits";
import { CommandPalette } from "./command-palette";
import { NAV } from "./nav";
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
        <div className="hidden w-60 bg-rail lg:block" />
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

function Rail({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { can, moduleEnabled, modules } = useSession();
  const loaded = modules.length > 0;
  return (
    <nav aria-label="Primary" className="flex h-full flex-col gap-6 overflow-y-auto px-3 py-4">
      <Link href="/" className="flex items-center gap-2.5 px-2" onClick={onNavigate}>
        <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden>
          <rect width="32" height="32" rx="5" fill="#fff" />
          <path d="M9 23V9h10M9 16h7" fill="none" stroke="#0b3d91" strokeWidth="3" strokeLinecap="square" />
          <path d="M19 23l4-4" stroke="#c49a00" strokeWidth="3" strokeLinecap="square" />
        </svg>
        <span className="text-md leading-tight font-bold text-rail-ink">Fabric Warden</span>
      </Link>
      {NAV.map((group) => {
        const items = group.items.filter(
          (i) => (!i.permission || can(i.permission)) && (!i.module || !loaded || moduleEnabled(i.module)),
        );
        if (!items.length) return null;
        return (
          <div key={group.section}>
            <p className="mb-1.5 px-2 text-xs font-bold tracking-wider text-rail-ink-2 uppercase">{group.section}</p>
            <ul className="flex flex-col gap-0.5">
              {items.map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={clsx(
                        "flex h-9 items-center gap-2.5 rounded-[4px] px-2.5 text-base font-semibold transition-colors duration-150",
                        active ? "sign bg-white text-[#0b3d91]" : "text-rail-ink hover:bg-white/10",
                      )}
                      style={active ? { outlineColor: "#0b3d91" } : undefined}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { can } = useSession();
  useLiveEvents(can("inventory:read"));
  const pathname = usePathname();
  useEffect(() => setOpen(false), [pathname]);
  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <div className="hidden w-60 shrink-0 bg-rail lg:block">
        <aside className="sticky top-0 h-dvh">
          <Rail />
        </aside>
      </div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button type="button" className="absolute inset-0 bg-black/40" aria-label="Close navigation" onClick={() => setOpen(false)} />
          <aside className="relative h-full w-64 bg-rail">
            <Rail onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-rule bg-surface px-4 lg:px-6">
          <button
            type="button"
            className="-ml-1 rounded p-1.5 text-ink-2 hover:bg-surface-3 lg:hidden"
            aria-label={open ? "Close navigation" : "Open navigation"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <CommandPalette />
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <UserMenu />
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1480px] flex-1 px-4 py-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
