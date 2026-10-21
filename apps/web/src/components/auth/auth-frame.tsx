import type { ReactNode } from "react";

export function AuthFrame({ aside, children }: { aside?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(360px,32rem)_1fr]">
      <aside className="flex flex-col gap-8 bg-rail px-8 py-8 text-rail-ink lg:px-10 lg:py-12">
        <div className="flex items-center gap-3">
          <svg viewBox="0 0 32 32" className="size-9" aria-hidden>
            <rect width="32" height="32" rx="5" fill="#fff" />
            <path d="M9 23V9h10M9 16h7" fill="none" stroke="#0b3d91" strokeWidth="3" strokeLinecap="square" />
            <path d="M19 23l4-4" stroke="#c49a00" strokeWidth="3" strokeLinecap="square" />
          </svg>
          <span className="text-lg font-bold">Fabric Warden</span>
        </div>
        {aside}
      </aside>
      <main className="flex items-start justify-center px-5 py-10 sm:px-10 lg:items-center lg:py-16">
        <div className="w-full max-w-[30rem]">{children}</div>
      </main>
    </div>
  );
}
