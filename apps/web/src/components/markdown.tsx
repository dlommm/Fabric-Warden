"use client";

// A small Markdown renderer for wiki pages. It builds React elements directly
// (no HTML injection), so page text can never run script. Supported: headings,
// paragraphs, lists, tables, fenced code, quotes, rules, inline code, bold,
// italic, links, and live inventory references written [[type:key|text]].
import { clsx } from "clsx";
import { CircleCheck, CircleDashed, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import { deviceTone, type Tone } from "@/components/ui/signs";
import type { DocRef } from "@/lib/types";

const REF_RE = /\[\[(device|client|network|vlan|prefix|rack|cable|asset|page):([^\]|]+?)(?:\|([^\]]+))?\]\]/g;

function refTone(state: string | null | undefined): Tone | null {
  if (!state) return null;
  if (["ENABLED", "ACTIVE", "INSTALLED", "IN_SERVICE", "OBSERVED"].includes(state)) return "ok";
  if (["MISSING", "DISABLED", "DECOMMISSIONED", "RETIRED", "DISPOSED", "DEPRECATED"].includes(state)) return state === "MISSING" ? "crit" : "muted";
  if (["PLANNED", "ORDERED", "RESERVED"].includes(state)) return "info";
  if (state === "OFFLINE") return "muted";
  return deviceTone(state);
}

const TONE_ICON = { ok: CircleCheck, warn: TriangleAlert, crit: OctagonAlert, muted: CircleDashed, info: Info };
const TONE_TEXT = { ok: "text-ok", warn: "text-warn-text", crit: "text-crit", muted: "text-ink-3", info: "text-accent" };

/** A reference rendered as a live chip: the object's current name and state, linked. */
export function RefChip({ r }: { r: DocRef }) {
  if (!r.resolved) {
    return (
      <span className="mx-0.5 inline-flex items-baseline gap-1 rounded-[3px] border border-dashed border-ink-3 px-1.5 text-[0.92em] text-ink-3" title="No object matches this reference now. It may have been renamed or removed.">
        <CircleDashed className="size-3.5 translate-y-0.5 self-start" aria-hidden />
        <span>
          {r.text ?? r.key}
          <span className="sr-only">, unresolved {r.type} reference</span>
        </span>
        <span className="font-mono text-[11px]">{r.type}?</span>
      </span>
    );
  }
  const tone = refTone(r.state);
  const Icon = tone ? TONE_ICON[tone] : null;
  return (
    <Link
      href={r.href ?? "#"}
      title={[r.label, r.detail, r.state?.toLowerCase().replace(/_/g, " ")].filter(Boolean).join(" · ")}
      className="mx-0.5 inline-flex items-baseline gap-1 rounded-[3px] border border-rule-strong bg-surface-2 px-1.5 text-[0.92em] font-semibold text-ink no-underline hover:border-ink-3 hover:bg-surface-3"
    >
      {Icon && <Icon className={clsx("size-3.5 translate-y-0.5 self-start", TONE_TEXT[tone!])} aria-hidden strokeWidth={2.25} />}
      <span>{r.text ?? r.label}</span>
      {tone && tone !== "ok" && <span className={clsx("text-[11px] font-bold", TONE_TEXT[tone])}>{r.state?.toLowerCase().replace(/_/g, " ")}</span>}
      {r.is_demo && <span className="sr-only">(demo data)</span>}
    </Link>
  );
}

function safeHref(href: string): string | null {
  return /^(https?:\/\/|\/|#|mailto:)/i.test(href) ? href : null;
}

function inline(text: string, refs: Map<string, DocRef>, key = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  // Tokens: references, code, links, bold, italic. Earliest match wins.
  const pattern = /(\[\[[^\]]+\]\])|(`[^`]+`)|(\[([^\]]+)\]\(([^)\s]+)\))|(\*\*([^*]+)\*\*)|(\*([^*]+)\*)|(_([^_]+)_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = pattern.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${n++}`;
    if (m[1]) {
      const r = refs.get(m[1]);
      out.push(r ? <RefChip key={k} r={r} /> : <span key={k}>{m[1]}</span>);
    } else if (m[2]) {
      out.push(<code key={k} className="rounded-[3px] bg-surface-3 px-1 py-0.5 font-mono text-[0.86em]">{m[2].slice(1, -1)}</code>);
    } else if (m[3]) {
      const href = safeHref(m[5]);
      out.push(
        href ? (
          <a key={k} href={href} className="font-semibold text-accent underline" {...(href.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}>
            {inline(m[4], refs, k)}
          </a>
        ) : (
          <span key={k}>{m[4]}</span>
        ),
      );
    } else if (m[6]) {
      out.push(<strong key={k} className="font-bold">{inline(m[7], refs, k)}</strong>);
    } else if (m[8] || m[10]) {
      out.push(<em key={k}>{inline(m[9] ?? m[11], refs, k)}</em>);
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const HEADING = ["text-xl font-bold tracking-tight", "text-lg font-bold", "text-md font-bold", "text-base font-bold"];

export function Markdown({ body, refs }: { body: string; refs: DocRef[] }) {
  const byRaw = new Map(refs.map((r) => [r.raw, r]));
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let b = 0;
  const para: string[] = [];
  const flush = () => {
    if (para.length) {
      blocks.push(<p key={`p${b++}`} className="max-w-[72ch]">{inline(para.join(" "), byRaw, `p${b}`)}</p>);
      para.length = 0;
    }
  };
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush();
      const lang = line.slice(3).trim();
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++;
      blocks.push(
        <pre key={`c${b++}`} className="overflow-x-auto rounded-[var(--radius)] border border-rule bg-surface-2 p-3 font-mono text-xs leading-5" aria-label={lang ? `${lang} code` : "Code"}>
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      flush();
      const level = h[1].length;
      const Tag = `h${Math.min(level + 1, 5)}` as "h2";
      blocks.push(<Tag key={`h${b++}`} className={clsx(HEADING[level - 1], "mt-2 text-balance")}>{inline(h[2], byRaw, `h${b}`)}</Tag>);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      flush();
      blocks.push(<hr key={`r${b++}`} className="border-rule" />);
      i++;
      continue;
    }
    if (/^>\s?/.test(line)) {
      flush();
      const q: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={`q${b++}`} className="max-w-[72ch] border-l-2 border-rule-strong pl-4 text-ink-2">{inline(q.join(" "), byRaw, `q${b}`)}</blockquote>);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      flush();
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i])) items[items.length - 1] += ` ${lines[i++].trim()}`;
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(
        <L key={`l${b++}`} className={clsx("flex max-w-[72ch] flex-col gap-1 pl-6", ordered ? "list-decimal" : "list-disc")}>
          {items.map((it, n) => (
            <li key={n} className="pl-1 marker:text-ink-3">{inline(it, byRaw, `l${b}-${n}`)}</li>
          ))}
        </L>,
      );
      continue;
    }
    if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      flush();
      const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      blocks.push(
        <div key={`t${b++}`} className="relative overflow-x-auto">
          <table className="border-collapse text-left">
            <thead>
              <tr className="border-b border-rule-strong">
                {head.map((c, n) => <th key={n} scope="col" className="h-9 px-3 text-sm font-semibold text-ink-3">{inline(c, byRaw, `th${n}`)}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, rn) => (
                <tr key={rn} className="border-b border-rule last:border-b-0">
                  {r.map((c, n) => <td key={n} className="h-[var(--row-h)] px-3">{inline(c, byRaw, `td${rn}-${n}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (!line.trim()) {
      flush();
      i++;
      continue;
    }
    para.push(line.trim());
    i++;
  }
  flush();
  return <div className="flex flex-col gap-3 text-base leading-[1.6rem]">{blocks.map((x, n) => <Fragment key={n}>{x}</Fragment>)}</div>;
}

/** References as written, for a quick count without a round trip. */
export function countRefs(body: string): number {
  return [...body.matchAll(REF_RE)].length;
}
