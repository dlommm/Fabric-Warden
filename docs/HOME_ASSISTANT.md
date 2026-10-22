# Home Assistant

**Status: implemented in Phase 9** (module `home-assistant`, `available`). Tested against the built-in simulator only; no real Home Assistant instance has been connected yet.

## What it does

Fabric Warden reads entity states from the **Home Assistant REST API** (`GET /api/`, `/api/config`, `/api/states`; nothing else), keeps a small selection, and matches trackers to network clients. Sources and fields: [`official_api_matrix.json`](../apps/api/src/warden/integrations/homeassistant/official_api_matrix.json), summarised in [API_CAPABILITIES.md](API_CAPABILITIES.md#home-assistant).

| Kept | Rule | Basis |
|---|---|---|
| Temperature | `sensor.*` with `device_class: temperature` | device class |
| UPS | `sensor.*` whose entity id or friendly name contains the word "ups" | **name (inferred)**: Home Assistant has no UPS device class; NUT entities are usually named after the UPS |
| Connectivity | `binary_sensor.*` with `device_class: connectivity` (on = connected) | device class |
| Tracker | `device_tracker.*` with `source_type: router`, or with `ip`/`mac` attributes | domain |

Everything else is ignored. **GPS trackers are skipped** and only these attributes are stored: `friendly_name`, `unit_of_measurement`, `device_class`, `state_class`, `source_type`, `ip`, `mac`, `host_name`. No location is collected.

## Connecting

Create a **long-lived access token** on your Home Assistant profile (Security tab). A non-admin user is enough: none of the endpoints used is admin-only, and Home Assistant filters states by the user's permissions. In Fabric Warden: **Home Assistant › Add connection**, the URL (`http://homeassistant.lan:8123`, or port 80 behind the Supervisor) and the token. The token lives in the vault; only its fingerprint is shown.

## Matching trackers to clients

Router trackers carry the `ip`, `mac` and `host_name` the router sees (attribute names from `homeassistant/components/device_tracker/const.py`). Fabric Warden matches them to present UniFi clients by IP, then MAC, then hostname, and shows how. A tracker that is home but matches no client becomes an info finding.

## Findings (`GET /api/home-assistant/findings`)

| Kind | Severity | When |
|---|---|---|
| `connection` | critical / warning | Home Assistant is unreachable, or refused the token |
| `ups_on_battery` | critical | a UPS entity's state reads "On Battery", "OB" or "discharging" |
| `ups_battery_low` | warning | a UPS battery-class entity below 50 % |
| `temperature_high` | warning | a temperature at or above 40 °C (°F converted) |
| `connectivity_down` | warning | a connectivity sensor reports off |
| `unavailable` | info | a kept sensor is unavailable or unknown |
| `tracker_not_on_network` | info | a router tracker is home but no client matches |

## Not done (yet)

No history (`/api/history`), no WebSocket subscription, no metrics recorded from these sensors, no actions or services (read-only by construction: the HTTP wrapper only sends GET). Feeding the UPS readings into the Power module is planned.

## Demo

**Load demo Home Assistant** (`POST /api/home-assistant/demo`) adds a labelled connection served in-process: rack and office temperatures, an unavailable garage sensor, four NUT-style UPS sensors, two connectivity sensors (one off), router trackers for phone-a (matched by IP) and phone-b (by MAC), one tracker the network does not know, and a GPS tracker that is skipped.
