"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, KeyRound, LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { DropdownMenu } from "radix-ui";
import { post } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Preferences } from "@/lib/types";

const item =
  "flex h-8 cursor-default items-center gap-2 rounded-[3px] px-2 text-base text-ink outline-none select-none data-[highlighted]:bg-surface-3";

export function UserMenu() {
  const { me, prefs, setPrefs } = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const initials = (me.display_name || me.username).slice(0, 2).toUpperCase();

  const radio = <K extends "density" | "theme">(key: K, value: Preferences[K], label: string) => (
    <DropdownMenu.RadioItem value={value} className={item} onSelect={(e) => e.preventDefault()}>
      <span className="flex w-4 justify-center">
        <DropdownMenu.ItemIndicator>
          <Check className="size-3.5" />
        </DropdownMenu.ItemIndicator>
      </span>
      {label}
    </DropdownMenu.RadioItem>
  );

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="flex h-9 items-center gap-2 rounded-[4px] px-1.5 text-base hover:bg-surface-3 data-[state=open]:bg-surface-3">
        <span className="grid size-7 place-items-center rounded-[3px] bg-rail text-xs font-bold text-white">{initials}</span>
        <span className="hidden font-semibold sm:inline">{me.display_name || me.username}</span>
        <ChevronDown className="hidden size-4 text-ink-3 sm:block" aria-hidden />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 w-60 rounded-[4px] border border-rule-strong bg-surface p-1 shadow-[0_8px_24px_rgb(0_0_0/0.14)]"
        >
          <div className="px-2 py-1.5">
            <p className="font-bold">{me.display_name || me.username}</p>
            <p className="text-sm text-ink-3">{me.roles.join(", ")}</p>
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-rule" />
          <DropdownMenu.Label className="px-2 pt-1 pb-0.5 text-xs font-bold tracking-wide text-ink-3 uppercase">Density</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={prefs.density} onValueChange={(v) => setPrefs({ density: v as Preferences["density"] })}>
            {radio("density", "comfortable", "Comfortable")}
            {radio("density", "compact", "Compact")}
          </DropdownMenu.RadioGroup>
          <DropdownMenu.Label className="px-2 pt-2 pb-0.5 text-xs font-bold tracking-wide text-ink-3 uppercase">Theme</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={prefs.theme} onValueChange={(v) => setPrefs({ theme: v as Preferences["theme"] })}>
            {radio("theme", "system", "Match system")}
            {radio("theme", "light", "Light")}
            {radio("theme", "dark", "Dark")}
          </DropdownMenu.RadioGroup>
          <DropdownMenu.Separator className="my-1 h-px bg-rule" />
          <DropdownMenu.Item className={item} onSelect={() => router.push("/settings/account")}>
            <KeyRound className="size-4 text-ink-3" /> Account & security
          </DropdownMenu.Item>
          <DropdownMenu.Item
            className={item}
            onSelect={async () => {
              await post("/api/auth/logout");
              qc.clear();
              router.replace("/login");
            }}
          >
            <LogOut className="size-4 text-ink-3" /> Sign out
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
