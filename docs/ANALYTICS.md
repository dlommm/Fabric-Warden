# Analytics, alerts, notifications and WAN

**Status: implemented in Phase 3** (modules `analytics-studio`, `monitoring`, `notifications` and `wan`, all `available`). Everything below is tested against the simulator only; no live controller has been connected yet (see [ROADMAP.md](ROADMAP.md)). `switching`, `wifi` and `fiber` remain **planned**.

## Where the numbers come from

The official UniFi Integration API returns only the *latest* statistics (`GET …/devices/{id}/statistics/latest`) and has no history endpoint (see [API_CAPABILITIES.md](API_CAPABILITIES.md)). So Fabric Warden records history itself:

- Every inventory sync (60 s) takes one raw sample per series, **while Analytics Studio, Monitoring or WAN is enabled**. History therefore starts when the first of those modules is enabled; charts mark that point "Recording starts".
- A device that is not online, or a controller that does not support the statistics endpoint, produces no CPU/memory/uplink samples for that sync; charts show the gap.
- Nothing is interpolated or estimated.

### Metrics

| Metric | Entity | Source |
|---|---|---|
| `device.online` | device | device state = `ONLINE` (1/0) |
| `device.cpu_pct`, `device.mem_pct`, `device.load_1m` | device | latest statistics |
| `device.uplink_tx_bps`, `device.uplink_rx_bps` | device | latest statistics `uplink` (not reported for the gateway's WAN) |
| `device.clients` | device | clients whose uplink is the device |
| `radio.tx_retries_pct` | radio | latest statistics per radio |
| `site.clients`, `site.clients_wired`, `site.clients_wireless`, `site.devices_online`, `site.devices_offline` | site | counts at sync time |
| `network.clients` | network | clients whose address is inside the network's subnet |
| `controller.reachable` | controller | last sync succeeded (1/0) |
| `wan.latency_ms`, `wan.loss_pct` | probe target | Warden's own TCP probe (below) |
| `port.rx_bps`, `port.tx_bps` | interface | SNMP `ifHCIn/OutOctets` delta between two polls (SNMP Port Counters) |
| `port.errors`, `port.discards` | interface | SNMP `ifIn+OutErrors`, `ifIn+OutDiscards` per minute; a counter that goes backwards or an agent restart gives no value for that interval |

Not available, and not faked: per-client traffic, WAN throughput, link state or failover events, optical levels. Per-port counters exist only for devices polled over SNMP. The built-in alert rule "Port errors" fires when a port logs errors for 10 minutes.

### Storage and retention

Two tables: `metric_series` (one per metric × entity, with the latest value) and `metric_samples` (`series_id`, `tier`, `ts`, `avg`, `min`, `max`, `count`).

| Tier | Resolution | Kept |
|---|---|---|
| raw | each sync | 48 hours |
| 5-minute rollup | 5 min | 30 days |
| hourly rollup | 1 hour | 400 days |

`core.metrics` (every 15 min) folds completed periods into the next tier, keeping average, minimum, maximum and count so a coarse range still shows peaks, then prunes each tier. A query picks the tier from the range (≤ 48 h raw, ≤ 30 d five-minute, else hourly) and fills the not-yet-rolled-up tail from the finer tier. Results are bucketed to at most about 240 points. The table is a single indexed table on SQLite and PostgreSQL; native PostgreSQL partitioning is not set up yet.

## Dashboards (Analytics Studio)

`/analytics`: dashboards built from widgets, shared by everyone on the installation. A widget charts one metric as a **line chart** (up to six series, the six chart colours), a **reading** (current value and the range's span) or a **ranked list** (highest, or lowest for "higher is better" metrics, first). Widgets cover either the top *N* series at each refresh or chosen entities. Templates: Network health, Devices, Clients, Wi-Fi, WAN.

Charts: a hairline grid, a crosshair readout of every series at the hovered time (also by keyboard: arrow keys, Home/End, Escape), a legend with current values, and a table view on every line chart. Series colours are fixed by entity name, so a re-ranking never repaints a line, and the palette never reuses status colours. Device detail shows the same charts for that device.

API: `GET /api/metrics` (catalog with series), `POST /api/metrics/query`, `GET/POST/PATCH/DELETE /api/analytics/dashboards`, `GET /api/analytics/templates`.

## Alerts (Monitoring & Alerts)

A rule is a threshold on one metric: above or below a value, held for a duration, with a severity, optionally limited to chosen entities and channels. `monitoring.evaluate` (every 60 s) checks the newest sample of each matching series:

- breach → **pending**; still breaching after the hold time → **firing**;
- recovers → **resolved (recovered)**; no sample for 5 minutes → **resolved (no data)**, so a controller outage raises one "controller unreachable" alert instead of a storm of device alerts; rule disabled, rescoped or deleted → **resolved (rule changed)**.

Firing alerts can be acknowledged. A **maintenance window** (everything, or chosen devices) marks matching alerts *silenced*: they are recorded and shown, but notify nobody. Firing and resolved alerts publish `monitoring.alert.firing` / `monitoring.alert.resolved` events, so they appear in device history and on the Overview's attention panel.

Built-in rules (created once, never recreated after deletion): Device offline, Controller unreachable, High CPU, High memory, High Wi-Fi retries, Probe loss, High latency.

Not yet: escalation policies, on-call schedules, rate limiting or retries of failed deliveries.

## Notification channels

| Kind | Secret in the vault | Delivery |
|---|---|---|
| Webhook | URL, optional signing secret | JSON `POST`; with a secret, `X-Warden-Signature: sha256=<HMAC of the body>` |
| ntfy | topic, optional access token | `POST {server}/{topic}` with Title, Priority, Tags and Click headers |
| Email | SMTP password | SMTP with STARTTLS, TLS or none |

Each channel has a minimum severity. A rule with no channels selected notifies every enabled channel at or above its minimum. Secrets are stored as one encrypted document per channel and never returned; the UI shows a hint (host, masked topic, recipients). Every delivery, including test sends, is recorded; deliveries older than 30 days are pruned. When `WARDEN_PUBLIC_URL` is set, notifications link back to the alert.

## WAN

`/wan` shows the WAN interfaces as the controller lists them (name only: the official WAN endpoint has no state, addresses or throughput), the gateway's health, and the **probe**: an opt-in TCP connect from the Warden host to up to five targets, `attempts` times a minute, recording median latency and loss. It measures the active path from wherever Warden runs, not each WAN separately, and it is not an ICMP ping. It is off until targets are configured. Speed and bufferbloat tests are not implemented.

## Permissions and audit

Reading needs `inventory:read`. `analytics:edit` (dashboards) and `monitoring:manage` (rules, acknowledgements, maintenance, probe settings) belong to owner, admin and operator; `notifications:manage` to owner and admin only, because channels hold delivery secrets. Every change is audited (`analytics.dashboard.*`, `monitoring.*`, `notifications.channel.*`, `wan.probe.configure`).
