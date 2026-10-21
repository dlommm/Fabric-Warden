"use client";

import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { ApiError, get, put } from "./api";
import type { Me, ModuleInfo, Preferences, Preset } from "./types";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            retry: (count, err) => !(err instanceof ApiError && [401, 403, 404, 409].includes(err.status)) && count < 2,
            refetchOnWindowFocus: true,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export const DEFAULT_PREFS: Preferences = {
  density: "comfortable",
  theme: "system",
  overview_panels: ["attention", "route", "health", "changes", "controllers", "clients"],
};

interface SessionValue {
  me: Me;
  can: (perm: string) => boolean;
  prefs: Preferences;
  setPrefs: (p: Partial<Preferences>) => void;
  modules: ModuleInfo[];
  presets: Preset[];
  moduleEnabled: (id: string) => boolean;
}

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const v = useContext(SessionContext);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/api/auth/me"), staleTime: 60_000 });
}

export function applyPrefs(p: Preferences) {
  const root = document.documentElement;
  root.dataset.density = p.density;
  if (p.theme === "system") delete root.dataset.theme;
  else root.dataset.theme = p.theme;
}

export function SessionProvider({ me, children }: { me: Me; children: ReactNode }) {
  const qc = useQueryClient();
  const prefsQ = useQuery({ queryKey: ["prefs"], queryFn: () => get<Preferences>("/api/auth/preferences") });
  const modulesQ = useQuery({
    queryKey: ["modules"],
    queryFn: () => get<{ modules: ModuleInfo[]; presets: Preset[] }>("/api/modules"),
    enabled: me.permissions.includes("modules:read"),
  });
  const save = useMutation({
    mutationFn: (p: Preferences) => put<Preferences>("/api/auth/preferences", p),
    onSuccess: (p) => qc.setQueryData(["prefs"], p),
  });
  const prefs = prefsQ.data ?? DEFAULT_PREFS;

  useEffect(() => {
    applyPrefs(prefs);
  }, [prefs]);

  const value: SessionValue = {
    me,
    can: (perm) => me.permissions.includes(perm),
    prefs,
    setPrefs: (p) => {
      const next = { ...prefs, ...p };
      qc.setQueryData(["prefs"], next);
      save.mutate(next);
    },
    modules: modulesQ.data?.modules ?? [],
    presets: modulesQ.data?.presets ?? [],
    moduleEnabled: (id) => modulesQ.data?.modules.some((m) => m.id === id && m.enabled) ?? false,
  };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
