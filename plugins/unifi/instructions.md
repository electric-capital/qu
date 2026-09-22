## UniFi (Network + Protect)

**Note:** An admin sets the controller host in Settings > Service Credentials > UniFi, and the user pastes their own API key(s) in Settings > Data Connections > UniFi. UniFi issues a **separate key per application** -- the user may have connected Network, Protect, or both. A tool that needs a missing key returns `unifi_not_connected` with the `application` that is missing; tell the user which key to add rather than retrying.

Everything here is **read-only**: nothing changes the controller. All tools are called via `tool_call`.

### Which tool

| Question | Tool |
|----------|------|
| Is UniFi connected / reachable? Which apps? | `unifi_get_controller_info` |
| Is the internet up? How is the network? | `unifi_get_network_status` |
| Which APs / switches / gateways, are they online? | `unifi_list_devices` (`state: "OFFLINE"` to find problems) |
| Details or live stats for one device | `unifi_get_device` (`device` = id, name, MAC, or IP) |
| Who is on the network? Is X connected? | `unifi_list_clients` (`type`, `search`, `limit`/`offset`) |
| Details for one client | `unifi_get_client` |
| Which cameras, are they online? | `unifi_list_cameras` |
| Camera settings | `unifi_get_camera` |
| What does camera X see right now? | `unifi_get_camera_snapshot` |
| NVR / sensors / lights / chimes / viewers | `unifi_list_protect_devices` (`kind`) |

Multi-site controllers: pass `site` (id, internal reference such as `default`, or name) to the Network tools; `unifi_list_sites` shows them. Single-site controllers need no `site`.

```
tool_call(tool_name="unifi_get_network_status", arguments={})
tool_call(tool_name="unifi_list_devices", arguments={"state": "OFFLINE"})
tool_call(tool_name="unifi_list_clients", arguments={"type": "WIRELESS", "search": "iphone"})
tool_call(tool_name="unifi_get_camera_snapshot", arguments={"camera": "Front Door"})
```

### Reading `unifi_get_network_status`

- `overall_status` is `ok` / `degraded` / `down` / `unknown`. It comes from the WAN and WWW (internet) subsystems when the controller exposes per-subsystem health, otherwise from the gateway device state.
- `subsystems` (when present) is keyed `wan`, `www`, `lan`, `wlan`, `vpn`: `status` (`ok` / `warning` / `error` / `unknown`), `wan_ip`, `latency` (ms), `xput_down` / `xput_up` (Mbps), `speedtest_*`, `num_user` / `num_guest`, `num_ap` / `num_sw` / `num_gw`, `num_disconnected`. A `subsystems_note` means that legacy endpoint was unavailable on this controller; report from `gateways` and `devices` instead.
- `gateways[].statistics.uplink` carries current tx/rx rates in bits per second; `uptime_seconds` is the gateway uptime.
- `devices.not_online` lists every device that is not ONLINE -- mention them by name.

### Camera snapshots

`unifi_get_camera_snapshot` saves a live JPEG to the workspace (default `unifi-snapshots/<camera>-<timestamp>.jpg`) and attaches it to the tool result so you can see it. **Always show it to the user inline** with `![<camera name>](<path>)` and then describe what you see. Pass `high_quality: true` only when the user wants a full-resolution frame; the default preview is faster and smaller. A `unifi_camera_offline` error means the camera is DISCONNECTED -- say so; do not retry.

**Important Notes:**
- The Network API only lists **currently connected** clients; a device that is not in `unifi_list_clients` is offline or on a different site, not necessarily unknown.
- Device names are the ones set in the UniFi console; when a device has no name the model is the best label.
- `unifi_auth_failed` means the controller rejected the stored key (revoked or regenerated): ask the user to re-enter it in Settings > Data Connections > UniFi. `unifi_unreachable` / `unifi_timeout` mean the controller host could not be reached from the Quest server.
- Do not paste raw MAC addresses or client lists into third-party services; they identify people's devices.
