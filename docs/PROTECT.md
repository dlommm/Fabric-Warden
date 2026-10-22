# UniFi Protect

Provided by the `unifi-protect` module.

## What it does

Fabric Warden reads cameras and the NVR from the **official UniFi Protect Integration API**, read-only, and joins them to the network inventory:

| Shown | From |
|---|---|
| Camera name, model, MAC, connection state (Connected / Connecting / Disconnected), smart-detection types | `GET /v1/cameras` |
| NVR name, model, MAC, alarm arm mode | `GET /v1/nvrs` |
| Protect version | `GET /v1/meta/info` |
| Network client, IP address, uplink device, Wi-Fi vs wired | the UniFi Network inventory, matched by the camera's MAC |
| Switch port and its PoE state | a **documented cable** in Cabling (see below) |

Nothing else is called. Every endpoint, the fields used and the published spec it was checked against are in [`official_api_matrix.json`](../apps/api/src/warden/integrations/protect/official_api_matrix.json), summarised in [API_CAPABILITIES.md](API_CAPABILITIES.md#unifi-protect).

## Connecting

1. On the console that runs Protect (a UNVR, or the gateway itself): **Settings › Control Plane › Integrations**, create an API key. A key from a separate gateway does not authenticate on a UNVR (third-party report, not verified).
2. In Fabric Warden: **Cameras › Add connection**, the console URL (`https://unvr.lan`; `/proxy/protect/integration` is added for you) and the key. Self-signed consoles: paste the CA certificate or turn verification off on a trusted network.

The key is stored in the credential vault; responses show only its fingerprint. Managing connections needs `integrations:manage` (owner, admin); viewing needs `inventory:read`.

## Correlation with the network

- **Camera → client:** by MAC address (the Protect API publishes no camera IP), then by exact name. The match reason is shown ("by MAC").
- **Client → uplink:** the client's `uplinkDeviceId` from the Network API. A wireless client on an access point is shown as "Wi-Fi, no PoE".
- **Uplink → port:** the official Network API does not report which switch port a client uses, and LLDP is not exposed. Fabric Warden therefore pins a port **only** when a cable in Cabling runs from a port on that switch to an item or external point whose name (or text) equals the camera's name or MAC. The port's PoE state then comes from the Network API's port list, and the cable label is shown. Otherwise the table says "Port not reported". No port is ever guessed.

## Findings (`GET /api/protect/findings`)

| Kind | Severity | When |
|---|---|---|
| `connection` | critical / warning | the console is unreachable, or refused the key |
| `camera_offline` | critical (disconnected) / warning (connecting) | Protect does not report the camera as connected; the detail says whether the network still sees it, and whether PoE is off on its documented port |
| `not_on_network` | info | no network client has the camera's MAC |
| `alarm_breach` | critical | the NVR's arm mode reports a breach |

## Not available from the published API

Camera IP address, uptime, firmware, recording state and bitrate; NVR storage, disk health, CPU, memory and temperature; event history (only a WebSocket subscription is published, not used). These are not shown and never estimated. Snapshots, video, PTZ, talkback and every PATCH/POST endpoint are out of scope: the module is read-only.

## Demo

**Load demo cameras** (`POST /api/protect/demo`, needs the demo controller setting) adds a labelled connection served by an in-process simulator (Protect 7.3.70 schema): three wired cameras on the Rack PoE Switch (one disconnected), a Wi-Fi doorbell on AP Patio, a camera the network does not report, and the UNVR. `DELETE /api/protect/demo` removes it.
