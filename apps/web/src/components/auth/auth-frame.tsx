import type { ReactNode } from "react";
import { Mark } from "../ui/mark";

export function AuthFrame({ aside, children }: { aside?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid min-h-dvh grid-rows-[auto_1fr] lg:grid-cols-[minmax(360px,32rem)_1fr] lg:grid-rows-1">
      <aside className="rail-plate on-rail relative isolate flex flex-col gap-8 overflow-hidden px-6 py-5 text-rail-ink sm:px-8 lg:px-10 lg:py-12">
        <div className="flex items-center gap-3">
          <Mark className="size-8" />
          <span className="text-lg font-bold">Fabric Warden</span>
        </div>
        {aside}
      </aside>
      <main className="flex items-start justify-center px-5 py-10 sm:px-10 lg:items-center lg:py-16">
        <div className="anim-rise w-full max-w-[30rem]">{children}</div>
      </main>
    </div>
  );
}
