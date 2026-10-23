import { clsx } from "clsx";
import { CircleCheck, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { buttonClass } from "./button";

/**
 * The page's heading. With `icon` it becomes an entity header for a detail
 * page: a raised sheet led by the object's symbol drawn in a frame, so the
 * thing you are looking at reads as the page's subject.
 */
export function PageHeader({
  title,
  description,
  actions,
  meta,
  icon,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
  icon?: ReactNode;
}) {
  if (icon) {
    return (
      <header className="plate-raised marks mb-6 flex flex-wrap items-start gap-x-4 gap-y-4 px-4 py-4 sm:items-center sm:gap-x-5 sm:px-5 lg:mb-7">
        <span
          aria-hidden
          className="grid size-11 shrink-0 place-items-center rounded-[3px] border-[1.5px] border-line text-accent sm:size-12 [&_svg]:size-6 [&_svg]:stroke-[1.75]"
        >
          {icon}
        </span>
        <div className="min-w-0 flex-[1_1_14rem]">
          <h1 className="text-2xl font-bold tracking-[-0.01em] break-words">{title}</h1>
          {description && <div className="mt-1 max-w-[70ch] text-base text-ink-2">{description}</div>}
          {meta && <div className="mt-2">{meta}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
    );
  }
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4 lg:mb-7">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-[-0.01em]">{title}</h1>
        {description && <div className="mt-1 max-w-[70ch] text-base text-ink-2">{description}</div>}
        {meta && <div className="mt-2">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/**
 * A drawing sheet with a title strip. `raised` is for the sheet that leads a
 * page or carries an operational decision; `quiet` recedes (secondary
 * reference material).
 */
export function Panel({
  title,
  actions,
  children,
  className,
  bodyClassName,
  id,
  variant = "default",
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
  variant?: "default" | "raised" | "quiet";
}) {
  return (
    <section
      id={id}
      className={clsx(variant === "raised" ? "plate-raised marks" : variant === "quiet" ? "plate-quiet" : "plate marks", className)}
      aria-labelledby={id && title ? `${id}-h` : undefined}
    >
      {(title || actions) && (
        <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-rule px-4 py-1.5">
          {title && (
            <h2 id={id ? `${id}-h` : undefined} className="flex items-center gap-2.5 text-base font-semibold tracking-[0.01em]">
              <span className="junction" aria-hidden />
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
  info: "border-accent/40 bg-accent-soft text-ink",
  warn: "border-warn-line/60 bg-warn-soft text-ink",
  crit: "border-crit/40 bg-crit-soft text-ink",
  ok: "border-ok/40 bg-ok-soft text-ink",
};

const noticeIcon = { info: Info, warn: TriangleAlert, crit: OctagonAlert, ok: CircleCheck };
const noticeIconColor = { info: "text-accent", warn: "text-warn-text", crit: "text-crit", ok: "text-ok" };

export function Notice({ tone = "info", title, children, action, className }: { tone?: NoticeTone; title?: ReactNode; children?: ReactNode; action?: ReactNode; className?: string }) {
  const Icon = noticeIcon[tone];
  return (
    <div role={tone === "crit" ? "alert" : "status"} className={clsx("flex flex-wrap gap-x-3 gap-y-2 rounded-[var(--r-plate)] border px-4 py-3 shadow-[var(--edge-hi)] sm:flex-nowrap", noticeStyle[tone], className)}>
      <Icon className={clsx("mt-0.5 size-4 shrink-0", noticeIconColor[tone])} aria-hidden strokeWidth={2.25} />
      <div className="min-w-0 flex-1">
        {title && <p className="font-bold">{title}</p>}
        {children && <div className={clsx("text-base text-ink-2", title && "mt-0.5")}>{children}</div>}
      </div>
      {action && <div className="shrink-0 basis-full pl-7 sm:basis-auto sm:self-center sm:pl-0">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        "rounded-[var(--r-control)] bg-surface-3 bg-[length:200%_100%] [background-image:linear-gradient(100deg,transparent_30%,color-mix(in_srgb,var(--surface)_55%,transparent)_50%,transparent_70%)] motion-safe:animate-[fw-shimmer_1.6s_linear_infinite]",
        className,
      )}
      aria-hidden
    />
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-10 sm:items-center sm:text-center">
      {icon && (
        <div className="mb-1 grid size-11 place-items-center rounded-[3px] border border-dashed border-construct text-accent [&_svg]:size-5 [&_svg]:stroke-[1.75]">
          {icon}
        </div>
      )}
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
      <section className="plate marks">
        <EmptyState
          title={`${name} is switched off`}
          action={
            canManage ? (
              <a href="/modules" className={buttonClass("primary")}>
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
