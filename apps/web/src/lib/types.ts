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
  asset_tag?: string | null;
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
  id: string;
  frequency_ghz: number;
  channel: number | null;
  channel_width_mhz: number;
  wlan_standard: string;
  tx_retries_pct: number | null;
}

export interface AssetRecord {
  asset_tag?: string;
  serial_number?: string;
  location?: string;
  owner?: string;
  supplier?: string;
  purchased_on?: string;
  warranty_until?: string;
}

export interface DeviceDetail extends DeviceSummary {
  notes: string;
  asset: AssetRecord;
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
  alias: string | null;
  owner: string | null;
  labels: string[];
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

export interface ClientSighting {
  id: string;
  ip_address: string | null;
  uplink: { id: string; name: string } | null;
  type: string;
  first_seen_at: string;
  last_seen_at: string;
  duration_seconds: number;
}

export interface ClientDetail {
  id: string;
  name: string;
  alias: string | null;
  owner: string | null;
  notes: string;
  labels: string[];
  type: string;
  mac_address: string | null;
  ip_address: string | null;
  connected_at: string | null;
  access_type: string | null;
  network: { id: string; name: string; vlan_id: number | null; subnet: string } | null;
  zone: { id: string; name: string } | null;
  /** Attachment point first, then each uplink up to the top of the tree. */
  path: { id: string; name: string; model: string; state: string; features: string[] }[];
  sightings: ClientSighting[];
  addresses: { address: string; first_seen_at: string; last_seen_at: string; sightings: number }[];
  history_summary: { sightings: number; distinct_addresses: number; distinct_uplinks: number; tracking_since: string | null };
  reservation: {
    id: string;
    address: string;
    hostname: string;
    kind: string;
    prefix: { id: string; cidr: string } | null;
    status: "ok" | "conflict" | "elsewhere";
  } | null;
  events: WardenEvent[];
  provenance: Provenance;
  fingerprint: null;
}

export type IpStatus = "active" | "planned" | "reserved" | "deprecated";

export interface PrefixRow {
  id: string;
  cidr: string;
  family: number;
  vlan_id: number | null;
  status: IpStatus;
  description: string;
  labels: string[];
  is_demo: boolean;
  site: { id: string; name: string | null } | null;
  size: number;
  usable: number;
  used: number;
  utilization_pct: number;
  reservations: number;
  observed: {
    network_id: string;
    name: string;
    vlan_id: number | null;
    gateway: string | null;
    dhcp_range: { start: string; stop: string } | null;
    dhcp_pool_size: number | null;
  } | null;
  updated_at: string;
  parent_id?: string | null;
  depth?: number;
}

export interface Holder {
  kind: "client" | "device";
  id: string;
  name: string;
  mac: string | null;
}

export type AddressState = "network" | "broadcast" | "gateway" | "observed" | "reserved" | "dhcp" | "free";

export interface AddressCell {
  address: string;
  state: AddressState;
  in_dhcp_pool: boolean;
  holders?: Holder[];
  reservation?: { id: string; hostname: string; kind: string; mac: string | null };
  conflict?: boolean;
}

export interface Reservation {
  id: string;
  prefix_id: string;
  address: string;
  kind: "static" | "dhcp" | "infrastructure";
  mac_address: string | null;
  hostname: string;
  description: string;
  is_demo: boolean;
  state: "in_use" | "idle" | "elsewhere" | "conflict";
  observed_holders: Holder[];
  mac_seen_at: string | null;
}

export interface PrefixDetail extends PrefixRow {
  parents: { id: string; cidr: string }[];
  children: { id: string; cidr: string; status: IpStatus }[];
  reservation_items: Reservation[];
  address_map: AddressCell[] | null;
  address_map_omitted: string | null;
  next_free: string | null;
  free_count: number | null;
}

export interface Finding {
  key: string;
  kind: string;
  severity: "critical" | "warning" | "info";
  title: string;
  detail?: string;
  refs: { prefix_id?: string; network_id?: string; reservation_id?: string; address?: string };
  /** Overrides the IPAM link resolution (Phase 4 findings link to racks, cables, assets). */
  href?: string;
}

export interface VlanRow {
  vlan_id: number;
  record: { id: string; name: string; status: IpStatus; description: string; is_demo: boolean } | null;
  state: "documented" | "deprecated_in_use" | "unobserved" | "undocumented" | "prefix_only" | "planned" | "reserved" | "deprecated";
  observed_networks: { id: string; name: string; subnet: string | null; enabled: boolean; is_demo: boolean }[];
  prefixes: { id: string; cidr: string; status: IpStatus }[];
  name_mismatch: boolean;
}

// -- Phase 3: metrics, dashboards, alerts, notifications, WAN --------------------------

export type MetricUnit = "pct" | "bps" | "count" | "ms" | "bool" | "load";
export type RangeKey = "1h" | "6h" | "24h" | "7d" | "30d" | "90d";
export type Severity = "critical" | "warning" | "info";

export interface MetricDef {
  id: string;
  label: string;
  unit: MetricUnit;
  entity_type: string;
  description: string;
  higher_is_worse: boolean;
  alarm: boolean;
}

export interface SeriesInfo {
  id: string;
  entity_type: string;
  entity_id: string;
  label: string;
  is_demo: boolean;
  last_value: number | null;
  last_ts: string | null;
  stale: boolean;
}

export interface MetricCatalog {
  items: (MetricDef & { series: SeriesInfo[] })[];
  ranges: RangeKey[];
  retention: { raw_hours: number; five_minute_days: number; hourly_days: number };
}

/** [epoch seconds, avg, min, max] */
export type Point = [number, number, number, number];

export interface QuerySeries extends SeriesInfo {
  points: Point[];
  summary: { avg: number | null; min: number | null; max: number | null };
}

export interface QueryResult {
  metric: MetricDef;
  range: RangeKey;
  tier: "raw" | "5m" | "1h";
  step: number;
  from: number;
  to: number;
  series: QuerySeries[];
  total_series: number;
}

export type WidgetKind = "line" | "stat" | "top";
export type WidgetSize = "third" | "half" | "full";

export interface Widget {
  id: string;
  title: string;
  kind: WidgetKind;
  metric: string;
  entity_ids: string[];
  size: WidgetSize;
  limit: number;
}

export interface DashboardRow {
  id: string;
  name: string;
  description: string;
  default_range: RangeKey;
  template: string | null;
  widget_count: number;
  created_by: string | null;
  updated_at: string;
}

export interface Dashboard extends DashboardRow {
  widgets: Widget[];
}

export interface DashboardTemplate {
  id: string;
  name: string;
  description: string;
  widgets: Widget[];
}

export interface AlertItem {
  id: string;
  rule_id: string;
  rule: string | null;
  metric: string | null;
  unit: MetricUnit | "";
  comparator: "gt" | "lt" | null;
  entity_type: string;
  entity_id: string;
  series_id: string | null;
  label: string;
  state: "pending" | "firing" | "resolved";
  severity: Severity;
  value: number | null;
  threshold: number;
  summary: string;
  pending_since: string;
  fired_at: string | null;
  resolved_at: string | null;
  resolution: "recovered" | "no_data" | "rule_changed" | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  silenced: boolean;
  is_demo: boolean;
}

export interface AlertCounts {
  firing: number;
  pending: number;
  critical: number;
  unacknowledged: number;
  silenced: number;
}

export interface AlertRuleRow {
  id: string;
  name: string;
  description: string;
  metric: string;
  metric_label: string;
  unit: MetricUnit | "";
  entity_type: string | null;
  entity_ids: string[];
  comparator: "gt" | "lt";
  threshold: number;
  for_seconds: number;
  severity: Severity;
  enabled: boolean;
  channel_ids: string[];
  builtin: boolean;
  active_alerts: number;
  updated_at: string;
}

export interface MaintenanceWindowRow {
  id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  entity_ids: string[];
  reason: string;
  created_by: string | null;
  state: "active" | "scheduled" | "ended";
}

export type ChannelKind = "webhook" | "ntfy" | "email";

export interface ChannelRow {
  id: string;
  name: string;
  kind: ChannelKind;
  enabled: boolean;
  min_severity: Severity;
  display: string;
  config: Record<string, unknown>;
  has_secret: boolean;
  last_status: "ok" | "failed" | null;
  last_error: string | null;
  last_sent_at: string | null;
  updated_at: string;
}

export interface Delivery {
  id: string;
  channel_id: string;
  alert_id: string | null;
  kind: "firing" | "resolved" | "test";
  status: "sent" | "failed";
  error: string | null;
  created_at: string;
}

export interface ProbeTarget {
  host: string;
  port: number;
  label: string;
}

export interface WanOverview {
  wans: { id: string; name: string; site: string | null; observed_at: string; present: boolean; is_demo: boolean }[];
  gateways: {
    id: string;
    name: string;
    model: string;
    state: string;
    ip_address: string | null;
    firmware_version: string | null;
    cpu_pct: number | null;
    mem_pct: number | null;
    uptime_sec: number | null;
    stats_observed_at: string | null;
    is_demo: boolean;
  }[];
  probe: {
    targets: ProbeTarget[];
    attempts: number;
    timeout_ms: number;
    results: { target: string; host: string; port: number; label: string; latency_ms: number | null; loss_pct: number | null; last_ts: string | null; stale: boolean }[];
  };
}

// -- Phase 4: infrastructure ------------------------------------------------------------

export type RackItemKind = "device" | "server" | "patch_panel" | "pdu" | "ups" | "shelf" | "cable_manager" | "blank" | "other";

export interface InfraFinding {
  key: string;
  kind: string;
  severity: "critical" | "warning" | "info";
  title: string;
  detail?: string;
  rack_id?: string;
  device_id?: string;
  item_id?: string;
  cable_id?: string;
  source_id?: string;
  asset_id?: string;
  date?: string;
}

export interface Location {
  id: string;
  parent_id: string | null;
  site_id: string | null;
  kind: "site" | "building" | "floor" | "room" | "row" | "cage";
  name: string;
  description: string;
  rack_count: number;
  is_demo: boolean;
}

export interface Occupancy {
  used_u: number;
  planned_u: number;
  free_u: number;
  utilisation_pct: number;
  documented_draw_w: number;
}

export interface RackSummary {
  id: string;
  name: string;
  location_id: string | null;
  location: string | null;
  u_height: number;
  descending_units: boolean;
  width_in: number;
  depth_mm: number | null;
  status: "active" | "planned" | "retired";
  description: string;
  item_count: number;
  occupancy: Occupancy;
  is_demo: boolean;
}

export interface RackItem {
  id: string;
  rack_id: string;
  name: string;
  kind: RackItemKind;
  position: number | null;
  height_u: number;
  face: "front" | "rear";
  full_depth: boolean;
  status: "installed" | "planned" | "removed";
  device_id: string | null;
  device: { id: string; name: string; model: string; state: string; ip_address: string | null; present: boolean } | null;
  manufacturer: string;
  model: string;
  serial: string;
  port_count: number;
  connector: string;
  power_draw_w: number | null;
  power_capacity_w: number | null;
  outlet_count: number;
  runtime_full_load_min: number | null;
  notes: string;
  is_demo: boolean;
}

export interface RackDetail extends RackSummary {
  units: { unit: number; label: number; front: string | null; rear: string | null }[];
  items: RackItem[];
  unplaced: string[];
  conflicts: { kind: string; items: string[]; detail: string }[];
}

export interface CableEndView {
  kind: "device_port" | "panel_port" | "item" | "external";
  port_idx: number | null;
  side: "front" | "rear" | null;
  text: string;
  device_id?: string;
  item_id?: string;
  name: string;
  rack?: string | null;
  port?: { state: string; speed_mbps: number | null; connector: string } | null;
  summary: string;
  is_demo?: boolean;
}

export interface CableRow {
  id: string;
  label: string;
  type: string;
  medium: "copper" | "fiber" | "power";
  color: string;
  length_m: number | null;
  status: "planned" | "installed" | "decommissioned";
  description: string;
  a: CableEndView | null;
  b: CableEndView | null;
  is_demo: boolean;
}

export interface TraceHop {
  cable: { id: string; label: string; type: string; color: string; status: string };
  from: CableEndView;
  to: CableEndView;
}

export interface Trace {
  hops: TraceHop[];
  a: CableEndView | null;
  b: CableEndView | null;
  complete: boolean;
}

export interface PanelView {
  id: string;
  name: string;
  rack: string | null;
  connector: string;
  used: number;
  ports: {
    port: number;
    front: { cable_id: string; label: string; status: string; far: CableEndView } | null;
    rear: { cable_id: string; label: string; status: string; far: CableEndView } | null;
  }[];
}

export interface PowerLoad {
  capacity_w: number | null;
  load_w: number;
  failover_load_w: number;
  projected_failover_w: number;
  utilisation_pct: number | null;
  failover_utilisation_pct: number | null;
  projected_utilisation_pct: number | null;
  headroom_w: number | null;
  runtime_estimate_min?: number;
}

export interface PowerNode extends Partial<PowerLoad> {
  link_id: string;
  outlet: number | null;
  input: string;
  item_id: string;
  name: string;
  kind: RackItemKind;
  status: string;
  rack: string | null;
  draw_w: number | null;
  inputs: number;
  is_demo: boolean;
  children?: PowerNode[];
}

export interface PowerTree extends PowerLoad {
  id: string;
  kind: "feed" | "ups" | "pdu";
  name: string;
  voltage_v?: number;
  amperage_a?: number;
  max_utilisation_pct?: number;
  description?: string;
  is_demo: boolean;
  children: PowerNode[];
}

export interface PoeSwitch {
  device_id: string;
  name: string;
  model: string;
  poe_ports: number;
  active_ports: number;
  upper_bound_w: number;
  ports: { idx: number; standard: string | null; type: number | null; state: string | null }[];
  is_demo: boolean;
}

export type AssetKind = "hardware" | "spare" | "licence" | "subscription" | "consumable";
export type AssetStatus = "ordered" | "in_service" | "spare" | "in_repair" | "retired" | "disposed";

export interface Asset {
  id: string;
  name: string;
  kind: AssetKind;
  status: AssetStatus;
  asset_tag: string;
  vendor: string;
  model: string;
  serial: string;
  quantity: number;
  min_quantity: number | null;
  supplier: string;
  order_ref: string;
  purchase_date: string | null;
  purchase_cost: string | null;
  currency: string;
  warranty_until: string | null;
  support_until: string | null;
  end_of_sale: string | null;
  end_of_life: string | null;
  seats: number | null;
  expires_on: string | null;
  auto_renew: boolean;
  device_id: string | null;
  device: string | null;
  rack_item_id: string | null;
  rack_item: string | null;
  location_id: string | null;
  location: string | null;
  labels: string[];
  notes: string;
  next_date: { date: string; label: string; days: number } | null;
  is_demo: boolean;
}

export interface CalendarEntry {
  source: "asset" | "device";
  id: string;
  name: string;
  kind: string;
  field: string;
  label: string;
  date: string;
  days: number;
  auto_renew: boolean;
  is_demo: boolean;
}

export interface AssetSummary {
  total: number;
  active: number;
  by_kind: Record<string, number>;
  by_status: Record<string, number>;
  spend: Record<string, string>;
  due_within_90_days: number;
}

export interface TopoNode {
  id: string;
  type: "device" | "client" | "network";
  role: string;
  name: string;
  model?: string;
  ip_address?: string | null;
  state?: string;
  health: "ok" | "degraded" | "down" | "alerting" | "disabled";
  alerts?: number;
  depth: number;
  clients?: number;
  placement?: string | null;
  vlan_clients?: number | null;
  vlan_id?: number | null;
  cidr?: string | null;
  network?: string | null;
  enabled?: boolean;
  highlight?: boolean;
  zone?: string | null;
  is_demo: boolean;
}

export interface TopoEdge {
  id: string;
  source: string;
  target: string;
  kind: "uplink" | "client" | "routes";
  tx_bps?: number | null;
  rx_bps?: number | null;
  in_vlan: boolean;
}

export interface Topology {
  layer: "physical" | "logical";
  vlan: number | null;
  nodes: TopoNode[];
  edges: TopoEdge[];
  vlans: { vlan_id: number; name: string }[];
  notes: string[];
}

export interface DocSummary {
  id: string;
  slug: string;
  title: string;
  parent_id: string | null;
  tags: string[];
  version: number;
  updated_by: string;
  updated_at: string;
  is_demo: boolean;
}

export interface DocRef {
  raw: string;
  type: string;
  key: string;
  text: string | null;
  resolved: boolean;
  id?: string;
  label?: string;
  href?: string;
  state?: string | null;
  detail?: string;
  is_demo?: boolean;
}

export interface DocPage extends DocSummary {
  body: string;
  references: DocRef[];
  children: DocSummary[];
  parent: DocSummary | null;
  backlinks: DocSummary[];
}

export interface DocVersion {
  version: number;
  title: string;
  message: string;
  author: string;
  created_at: string;
  size: number;
}

// -- Phase 4b: servers ------------------------------------------------------------------

export type HostKind = "proxmox-node" | "unraid" | "docker" | "linux";
export type ConnectionKind = "proxmox" | "unraid" | "docker" | "node_exporter";

export interface ClientRef {
  id: string;
  name: string;
  ip_address: string | null;
  mac_address: string | null;
  present: boolean;
  match: "ip" | "mac" | "hostname" | null;
}

export interface HostConnection {
  id: string;
  name: string;
  kind: ConnectionKind;
  base_url: string;
  token_id: string;
  credential_fingerprint: string | null;
  verify_tls: boolean;
  enabled: boolean;
  status: "ok" | "degraded" | "unreachable" | "unknown";
  status_detail: string;
  version: string | null;
  last_sync_at: string | null;
  host_count: number;
  workload_count: number;
  is_demo: boolean;
}

export interface UpsInfo {
  name: string;
  model: string;
  status: string;
  charge_pct: number | null;
  runtime_s: number | null;
  battery_health: string | null;
  load_pct: number | null;
}

export interface ServerHost {
  id: string;
  name: string;
  kind: HostKind;
  status: "online" | "offline" | "unknown";
  hostname: string;
  os: string;
  version: string;
  ip_address: string | null;
  mac_addresses: string[];
  cpu_count: number | null;
  cpu_pct: number | null;
  mem_total: number | null;
  mem_used: number | null;
  mem_pct: number | null;
  load_1m: number | null;
  uptime_s: number | null;
  extra: { cluster?: string | null; array_state?: string; api_version?: string; ups?: UpsInfo[]; containers?: number; running?: number; machine?: string };
  client: ClientRef | null;
  connection: { id: string; name: string; kind: ConnectionKind; status: string } | null;
  workloads: { total: number; running: number };
  labels: string[];
  notes: string;
  provenance: Provenance;
  storage_worst?: { name: string; used_pct: number | null } | null;
  storage_unhealthy?: number;
  is_demo: boolean;
}

export interface ServerWorkload {
  id: string;
  host_id: string;
  host: string | null;
  host_kind: HostKind | null;
  kind: "vm" | "lxc" | "container";
  name: string;
  status: "running" | "stopped" | "paused" | "other";
  raw_status: string;
  vmid: number | null;
  cpu_pct: number | null;
  cpu_count: number | null;
  mem_used: number | null;
  mem_max: number | null;
  mem_pct: number | null;
  disk_max: number | null;
  uptime_s: number | null;
  ip_addresses: string[];
  mac_addresses: string[];
  image: string;
  ports: { private: number | null; public: number | null; type: string | null }[];
  autostart: boolean | null;
  template: boolean;
  tags: string[];
  client: ClientRef | null;
  present: boolean;
  is_demo: boolean;
}

export interface ServerStorage {
  id: string;
  host_id: string;
  name: string;
  kind: "datastore" | "array" | "disk" | "pool" | "filesystem";
  total_bytes: number | null;
  used_bytes: number | null;
  used_pct: number | null;
  status: string;
  healthy: boolean | null;
  shared: boolean;
  temperature_c: number | null;
  extra: { role?: string; device?: string; rotational?: boolean; plugin?: string };
  is_demo: boolean;
}

export interface ServerHostDetail extends ServerHost {
  workload_items: ServerWorkload[];
  storage_items: ServerStorage[];
}

// -- Phase 5a: firewall, config history, compliance ------------------------------------

export interface FwPolicy {
  id: string;
  name: string;
  index: number | null;
  enabled: boolean;
  action: "ALLOW" | "BLOCK" | "REJECT" | string;
  source_zone: string;
  destination_zone: string;
  match_all: boolean;
  filter_summary: string;
  ip_scope: string | null;
  logging: boolean;
  origin: string | null;
  analysis: "exact" | "not_determined" | "unordered";
  relation: "shadowed" | "redundant" | null;
  covered_by: string | null;
  overlaps: string[];
  is_demo: boolean;
}

export interface FwMatrixCell {
  source: string;
  destination: string;
  action: string | null;
  policy: { id: string; name: string } | null;
  filtered_before: number;
  system_policies: number;
}

export interface FwSite {
  site: { id: string; name: string; is_demo: boolean };
  zones: { id: string; name: string; networks: number; origin: string | null }[];
  policies: FwPolicy[];
  system_policies: FwPolicy[];
  disabled: FwPolicy[];
  matrix: FwMatrixCell[];
}

export interface SnapshotRow {
  id: string;
  site_id: string;
  site: string | null;
  taken_at: string;
  hash: string;
  summary: Record<string, number>;
  changes: Record<string, { added: number; removed: number; changed: number }>;
  change_total: number;
  trigger: string;
  source: string;
  is_baseline: boolean;
  note: string;
  is_demo: boolean;
}

export interface DiffEntry {
  id: string;
  name: string | null;
  value?: Record<string, unknown>;
  fields?: { field: string; from: unknown; to: unknown }[];
}

export type ConfigDiff = Record<string, { added: DiffEntry[]; removed: DiffEntry[]; changed: DiffEntry[] }>;

export interface ComplianceControl {
  id: string;
  title: string;
  category: string;
  rationale: string;
  status: "pass" | "fail" | "unknown" | "waived";
  raw_status: string;
  detail: string;
  evidence: { object: string; fact: string }[];
  failing_since: string | null;
  waiver: { by: string; reason: string; until: string } | null;
  evaluated_at: string;
}
