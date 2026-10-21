import { BellRing, Blocks, ChartLine, Gauge, Globe, Grid3x3, Laptop, Layers, Network, Router, ScrollText, Server, Siren, Users } from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: typeof Gauge;
  module?: string;
  permission?: string;
}

export const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Operate",
    items: [
      { label: "Overview", href: "/", icon: Gauge, permission: "inventory:read" },
      { label: "Alerts", href: "/alerts", icon: Siren, module: "monitoring", permission: "inventory:read" },
      { label: "Dashboards", href: "/analytics", icon: ChartLine, module: "analytics-studio", permission: "inventory:read" },
    ],
  },
  {
    section: "Network",
    items: [
      { label: "Devices", href: "/inventory/devices", icon: Router, module: "inventory", permission: "inventory:read" },
      { label: "Clients", href: "/inventory/clients", icon: Laptop, module: "inventory", permission: "inventory:read" },
      { label: "Networks", href: "/inventory/networks", icon: Network, module: "inventory", permission: "inventory:read" },
      { label: "WAN", href: "/wan", icon: Globe, module: "wan", permission: "inventory:read" },
      { label: "IP plan", href: "/ipam", icon: Grid3x3, module: "ipam", permission: "inventory:read" },
      { label: "VLANs", href: "/ipam/vlans", icon: Layers, module: "ipam", permission: "inventory:read" },
    ],
  },
  {
    section: "Platform",
    items: [
      { label: "Controllers", href: "/controllers", icon: Server, module: "unifi-network", permission: "controllers:read" },
      { label: "Notifications", href: "/settings/notifications", icon: BellRing, module: "notifications", permission: "inventory:read" },
      { label: "Module Center", href: "/modules", icon: Blocks, permission: "modules:read" },
      { label: "Audit trail", href: "/audit", icon: ScrollText, permission: "audit:read" },
      { label: "Users & access", href: "/settings/users", icon: Users, permission: "users:read" },
    ],
  },
];
