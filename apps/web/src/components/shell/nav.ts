import {
  BellRing,
  Blocks,
  BookOpen,
  Cable,
  ChartLine,
  ClipboardCheck,
  Gauge,
  Globe,
  Grid3x3,
  History,
  HardDrive,
  Laptop,
  Layers,
  Network,
  Package,
  PlugZap,
  Router,
  ScanSearch,
  Rows3,
  ScrollText,
  Server,
  Shield,
  Siren,
  Users,
  Waypoints,
} from "lucide-react";

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
      { label: "Topology", href: "/topology", icon: Waypoints, module: "topology", permission: "inventory:read" },
      { label: "WAN", href: "/wan", icon: Globe, module: "wan", permission: "inventory:read" },
      { label: "IP plan", href: "/ipam", icon: Grid3x3, module: "ipam", permission: "inventory:read" },
      { label: "VLANs", href: "/ipam/vlans", icon: Layers, module: "ipam", permission: "inventory:read" },
    ],
  },
  {
    section: "Security",
    items: [
      { label: "Firewall", href: "/firewall", icon: Shield, module: "firewall", permission: "inventory:read" },
      { label: "Config history", href: "/config", icon: History, module: "config-history", permission: "inventory:read" },
      { label: "Compliance", href: "/compliance", icon: ClipboardCheck, module: "compliance", permission: "inventory:read" },
      { label: "SOC", href: "/soc", icon: Siren, module: "soc", permission: "inventory:read" },
      { label: "Logs", href: "/logs", icon: ScrollText, module: "logs", permission: "inventory:read" },
      { label: "Investigate", href: "/investigate", icon: ScanSearch, module: "investigations", permission: "inventory:read" },
    ],
  },
  {
    section: "Infrastructure",
    items: [
      { label: "Racks", href: "/dcim", icon: Rows3, module: "dcim", permission: "inventory:read" },
      { label: "Cabling", href: "/dcim/cables", icon: Cable, module: "cabling", permission: "inventory:read" },
      { label: "Power", href: "/dcim/power", icon: PlugZap, module: "power", permission: "inventory:read" },
      { label: "Servers", href: "/servers", icon: HardDrive, module: "servers", permission: "inventory:read" },
      { label: "Assets", href: "/assets", icon: Package, module: "asset-lifecycle", permission: "inventory:read" },
      { label: "Docs", href: "/docs", icon: BookOpen, module: "docs", permission: "inventory:read" },
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
