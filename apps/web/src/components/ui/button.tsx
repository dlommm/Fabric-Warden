import { clsx } from "clsx";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

// Buttons are drawn: primary is solid linework (pale blueprint white on the
// cyanotype, blue-black ink on paper), secondary is an outlined frame that
// picks up the accent on hover. A press scales to 0.97; colour and transform
// share one strong ease-out.
const base =
  "inline-flex items-center justify-center gap-1.5 rounded-[var(--radius)] font-semibold whitespace-nowrap select-none transition-[background-color,border-color,color,box-shadow,transform] duration-[var(--dur-fast)] ease-[var(--ease-out)] enabled:active:scale-[0.97] aria-disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none [&_svg]:size-4 [&_svg]:shrink-0";

const variants: Record<Variant, string> = {
  primary: "border border-line bg-line text-ground shadow-[0_1px_2px_rgb(var(--shadow-tint)/0.25)] hover:border-accent hover:bg-accent",
  secondary: "border border-rule-strong bg-transparent text-ink hover:border-accent hover:bg-accent-soft",
  ghost: "text-ink-2 hover:bg-accent-soft hover:text-ink",
  danger: "border border-crit/60 bg-transparent text-crit hover:border-crit hover:bg-crit-soft",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-2.5 text-sm",
  md: "h-9 px-3.5 text-base",
};

export function buttonClass(variant: Variant = "secondary", size: Size = "md", extra?: string) {
  return clsx(base, variants[variant], sizes[size], extra);
}

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; loading?: boolean; icon?: ReactNode }) {
  return (
    <button
      type="button"
      className={buttonClass(variant, size, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Loader2 className="animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "secondary",
  size = "md",
  icon,
  children,
  className,
  ...rest
}: ComponentProps<typeof Link> & { variant?: Variant; size?: Size; icon?: ReactNode }) {
  return (
    <Link className={buttonClass(variant, size, className)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
