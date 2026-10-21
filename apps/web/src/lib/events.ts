import type { WardenEvent } from "./types";

export function describeEvent(e: WardenEvent): string {
  const p = e.payload as Record<string, string | undefined>;
  switch (e.type) {
    case "inventory.device.discovered":
      return `${p.name} discovered (${p.model})`;
    case "inventory.device.state_changed":
      return `${p.name} went from ${fmtState(p.from)} to ${fmtState(p.to)}`;
    case "inventory.device.firmware_changed":
      return `${p.name} firmware ${p.from} → ${p.to}`;
    case "inventory.device.missing":
      return `${p.name} is no longer reported by the controller`;
    case "inventory.client.connected":
      return `${p.name} connected${p.ip ? ` (${p.ip})` : ""}`;
    case "inventory.client.disconnected":
      return `${p.name} disconnected`;
    case "inventory.client.moved":
      return p.from_ip !== p.to_ip ? `${p.name} moved from ${p.from_ip ?? "no address"} to ${p.to_ip ?? "no address"}` : `${p.name} changed attachment point`;
    case "controller.status_changed":
      return `Controller status ${p.from} → ${p.to}${p.detail ? `: ${p.detail}` : ""}`;
    case "controller.sync.completed":
      return "Controller sync completed";
    case "monitoring.alert.firing":
      return `Alert: ${p.summary}${(p.silenced as unknown) ? " (silenced)" : ""}`;
    case "monitoring.alert.resolved":
      return `Resolved: ${p.summary}`;
    case "module.enabled":
      return `Module ${p.module_id} enabled`;
    case "module.disabled":
      return `Module ${p.module_id} disabled`;
    default:
      return e.type;
  }
}

function fmtState(s?: string) {
  if (!s) return "unknown";
  return s === "CONNECTION_INTERRUPTED" ? "interrupted" : s.toLowerCase().replace(/_/g, " ");
}

export function eventHref(e: WardenEvent): string | undefined {
  if (e.entity_type === "device" && e.entity_id) return `/inventory/devices/${e.entity_id}`;
  if (e.entity_type === "controller" && e.entity_id) return `/controllers/${e.entity_id}`;
  if (e.entity_type === "client" && e.entity_id) return `/inventory/clients/${e.entity_id}`;
  return undefined;
}
