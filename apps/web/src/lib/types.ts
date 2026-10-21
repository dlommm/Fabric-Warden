export interface Provenance {
  source: string;
  is_demo: boolean;
  observed_at: string;
  first_seen_at: string;
  age_seconds: number;
  present: boolean;
}

export interface Me {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  roles: string[];
  permissions: string[];
  mfa_enabled: boolean;
  via: string;
}

export type OverviewPanel = "attention" | "route" | "health" | "changes" | "controllers" | "clients";

export interface Preferences {
  density: "comfortable" | "compact";
  theme: "system" | "light" | "dark";
  overview_panels: OverviewPanel[];
}

export interface DeviceSummary {
  id: string;
  name: string;
  model: string;
  mac_address: string;
  ip_address: string | null;
  firmware_version: string | null;
  firmware_updatable: boolean;
  state: string;
  features: string[];
  labels: string[];
  site_id: string;
  cpu_pct: number | null;
  memory_pct: number | null;
  uptime_sec: number | null;
  provenance: Provenance;
  uplink_id?: string | null;
}

export interface WardenEvent {
  id: string;
  type: string;
  occurred_at: string;
  source: string;
  severity: "info" | "warning" | "critical" | string;
  entity_type: string | null;
  entity_id: string | null;
  payload: Record<string, unknown>;
}

export interface Port {
  idx: number;
  connector: string;
  state: string;
  speed_mbps: number | null;
  max_speed_mbps: number;
  poe: { enabled: boolean; standard: string; state: string; type: number } | null;
}

export interface Radio {
  frequency_ghz: number;
  channel: number | null;
  channel_width_mhz: number;
  wlan_standard: string;
  tx_retries_pct: number | null;
}

export interface DeviceDetail extends DeviceSummary {
  notes: string;
  stats: Record<string, unknown>;
  stats_observed_at: string | null;
  ports: Port[];
  radios: Radio[];
  uplink: { id: string; name: string; model: string } | null;
  downstream: { id: string; name: string; model: string; state: string }[];
  clients: { id: string; name: string; ip_address: string | null; type: string }[];
  history: WardenEvent[];
}

export interface ClientRow {
  id: string;
  name: string;
  type: string;
  mac_address: string | null;
  ip_address: string | null;
  connected_at: string | null;
  access_type: string | null;
  network: { id: string; name: string; vlan_id: number | null } | null;
  uplink: { id: string; name: string } | null;
  provenance: Provenance;
}

export interface NetworkRow {
  id: string;
  name: string;
  vlan_id: number | null;
  management: string;
  enabled: boolean;
  is_default: boolean;
  origin: string | null;
  subnet: string | null;
  gateway: string | null;
  dhcp_mode: string | null;
  dhcp_range: { start: string; stop: string } | null;
  dhcp_pool_size: number | null;
  active_clients: number;
  zone: { id: string; name: string } | null;
  provenance: Provenance;
}

export interface ControllerSummary {
  id: string;
  name: string;
  status: string;
  status_detail: string;
  is_demo: boolean;
  application_version: string | null;
  last_sync_at: string | null;
}

export interface Summary {
  devices: { total: number; by_state: Record<string, number>; by_role: Record<string, number> };
  clients: { total: number; by_type: Record<string, number> };
  networks: number;
  wans: number;
  controllers: ControllerSummary[];
  attention: DeviceSummary[];
  events_24h: number;
  recent_events: WardenEvent[];
  demo_only: boolean;
}

export interface Capability {
  title: string;
  status: "supported" | "unsupported" | "unknown";
  min_version: string;
  write: boolean;
  verified: boolean;
  probe_error?: string;
}

export interface Controller extends ControllerSummary {
  kind: string;
  base_url: string;
  verify_tls: boolean;
  has_ca_bundle: boolean;
  write_mode: string;
  poll_interval_seconds: number;
  capabilities: {
    application_version?: string;
    newest_known_spec?: string;
    beyond_known_spec?: boolean;
    capabilities?: Record<string, Capability>;
    not_exposed?: { id: string; title: string }[];
  };
  capabilities_checked_at: string | null;
  credential_fingerprint: string | null;
  site_count: number;
  device_count: number;
  client_count: number;
}

export interface ModuleInfo {
  id: string;
  version: string;
  name: string;
  category: string;
  description: string;
  maturity: "available" | "preview" | "planned";
  roadmap_phase: number;
  requires: string[];
  enabled: boolean;
  always_enabled: boolean;
  dependency_closure: string[];
  dependents: string[];
  registered_jobs: string[];
  warnings: string[];
  performs_writes: boolean;
  resources: { memory_mb: number; cpu_millicores: number; disk_mb_per_day: number; extra_containers: string[] };
  publishes: string[];
  data_ownership: string[];
  enabled_at: string | null;
}

export interface Preset {
  id: string;
  name: string;
  modules: string[];
}

export interface AuditRecord {
  seq: number;
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_name: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  outcome: "success" | "denied" | "failure";
  detail: Record<string, unknown>;
  ip_address: string | null;
  hash: string;
}

export interface UserRow {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  roles: string[];
  is_active: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
}
