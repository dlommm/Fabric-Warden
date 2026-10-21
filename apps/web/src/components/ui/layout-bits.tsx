import { clsx } from "clsx";
import { CircleCheck, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ title, description, actions, meta }: { title: string; description?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-bold tracking-tight text-balance">{title}</h1>
        {description && <p className="mt-1 max-w-[70ch] text-base text-ink-2">{description}</p>}
        {meta && <div className="mt-2">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section id={id} className={clsx("rounded-[var(--radius)] border border-rule bg-surface", className)} aria-labelledby={id && title ? `${id}-h` : undefined}>
      {(title || actions) && (
        <div className="flex min-h-11 items-center justify-between gap-3 border-b border-rule px-4 py-2">
          {title && (
            <h2 id={id ? `${id}-h` : undefined} className="text-md font-bold">
              {title}
            </h2>
          )}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={clsx(bodyClassName ?? "p-4")}>{children}</div>
    </section>
  );
}

type NoticeTone = "info" | "warn" | "crit" | "ok";

const noticeStyle: Record<NoticeTone, string> = {
  info: "border-accent/30 bg-accent-soft text-ink",
  warn: "border-warn bg-warn-soft text-ink",
  crit: "border-crit/40 bg-crit-soft text-ink",
  ok: "border-ok/40 bg-ok-soft text-ink",
};

const noticeIcon = { info: Info, warn: TriangleAlert, crit: OctagonAlert, ok: CircleCheck };
const noticeIconColor = { info: "text-accent", warn: "text-warn-text", crit: "text-crit", ok: "text-ok" };

export function Notice({ tone = "info", title, children, action, className }: { tone?: NoticeTone; title?: ReactNode; children?: ReactNode; action?: ReactNode; className?: string }) {
  const Icon = noticeIcon[tone];
  return (
    <div role={tone === "crit" ? "alert" : "status"} className={clsx("flex gap-3 rounded-[var(--radius)] border px-4 py-3", noticeStyle[tone], className)}>
      <Icon className={clsx("mt-0.5 size-4 shrink-0", noticeIconColor[tone])} aria-hidden strokeWidth={2.25} />
      <div className="min-w-0 flex-1">
        {title && <p className="font-bold">{title}</p>}
        {children && <div className={clsx("text-base text-ink-2", title && "mt-0.5")}>{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-[3px] bg-surface-3", className)} aria-hidden />;
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-10 sm:items-center sm:text-center">
      {icon && <div className="text-ink-3 [&_svg]:size-6">{icon}</div>}
      <p className="text-md font-bold">{title}</p>
      {children && <div className="max-w-[60ch] text-base text-ink-2">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function KeyValue({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={clsx("grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-6 gap-y-2", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-sm text-ink-3">{k}</dt>
          <dd className="min-w-0 text-base break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Shown in place of a page whose module is switched off. */
export function ModuleOff({ title, name, children, canManage }: { title: string; name: string; children: ReactNode; canManage: boolean }) {
  return (
    <>
      <PageHeader title={title} />
      <section className="rounded-[var(--radius)] border border-rule bg-surface">
        <EmptyState
          title={`${name} is switched off`}
          action={
            canManage ? (
              <a href="/modules" className="inline-flex h-9 items-center rounded-[var(--radius)] bg-accent px-3.5 font-semibold text-accent-ink hover:bg-accent-hover">
                Open Module Center
              </a>
            ) : undefined
          }
        >
          {children}
        </EmptyState>
      </section>
    </>
  );
}
