const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const secs = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(secs);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(secs / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(secs / 3600), "hour");
  return rtf.format(Math.round(secs / 86400), "day");
}

export function ageLabel(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function uptime(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return d > 0 ? `${d}d ${h}h` : `${h}h ${Math.floor((sec % 3600) / 60)}m`;
}

export function speed(mbps: number | null | undefined): string {
  if (!mbps) return "—";
  return mbps >= 1000 ? `${mbps / 1000} G` : `${mbps} M`;
}

export function bps(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)} Gb/s`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} Mb/s`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)} kb/s`;
  return `${v} b/s`;
}

export function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split(/[_\s]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function deviceRole(features: string[]): "gateway" | "accessPoint" | "switching" {
  if (features.includes("gateway")) return "gateway";
  if (features.includes("accessPoint")) return "accessPoint";
  return "switching";
}

export const ROLE_LABEL = { gateway: "Gateway", switching: "Switch", accessPoint: "Access point" } as const;

export function duration(sec: number): string {
  if (sec < 60) return "under a minute";
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h`;
  return `${Math.round(sec / 86400)} d`;
}

/** Sort key for IPv4 dotted quads; other values sort as text. */
export function ipKey(ip: string | null | undefined): string | null {
  if (!ip) return null;
  return /^\d+\.\d+\.\d+\.\d+$/.test(ip) ? ip.split(".").map((p) => p.padStart(3, "0")).join(".") : ip;
}

export function cidrKey(cidr: string): string {
  const [ip, len] = cidr.split("/");
  return `${ipKey(ip)}/${len.padStart(3, "0")}`;
}

export function bytes(v: number | null | undefined): string {
  if (v == null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let n = v;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}
