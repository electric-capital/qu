"""UniFi Network application helpers (plugin module).

Wraps the official Network Integration API (``/proxy/network/integration/v1``)
for sites, devices, and clients, plus the one legacy endpoint the
Integration API has no equivalent for: ``/proxy/network/api/s/<site>/stat/health``
(per-subsystem WAN/LAN/WLAN/WWW/VPN health, gateway WAN IP, latency,
throughput). The API key is sent the same way to both.

Only reads. Every function takes an open :class:`~plugins.unifi.upstream.UnifiClient`.
"""

from __future__ import annotations

from typing import Any, Optional

from plugins.unifi.upstream import (
    NETWORK_API_PREFIX,
    NETWORK_LEGACY_PREFIX,
    UnifiClient,
    UnifiError,
)

# Device ``state`` values the Integration API reports.
# Client ``type`` values.
CLIENT_TYPES = ("WIRED", "WIRELESS", "VPN", "TELEPORT")

# Model-name prefixes of UniFi gateways (used to spot the WAN-facing device
# when ``features`` does not say so).
_GATEWAY_MODEL_PREFIXES = ("UDM", "UDR", "UDW", "UXG", "UCG", "USG", "EFG", "UCK-G2-PLUS")


def _lower(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

def site_view(site: dict) -> dict:
    return {
        "id": site.get("id"),
        "internal_reference": site.get("internalReference"),
        "name": site.get("name"),
    }


async def list_sites(client: UnifiClient) -> list[dict]:
    return await client.get_paged(f"{NETWORK_API_PREFIX}/sites")


async def resolve_site(client: UnifiClient, site_arg: Optional[str]) -> dict:
    """Pick the site a tool call refers to.

    ``site_arg`` may be the site id (UUID), the ``internalReference``
    (e.g. ``default``), or the display name (case-insensitive). With no
    argument: the only site, else the one whose internal reference is
    ``default``, else the first. Raises :class:`UnifiError` listing the
    available sites when nothing matches.
    """
    sites = await list_sites(client)
    if not sites:
        raise UnifiError("The Network application reports no sites.", code="unifi_no_sites")
    wanted = _lower(site_arg)
    if not wanted:
        if len(sites) == 1:
            return sites[0]
        for site in sites:
            if _lower(site.get("internalReference")) == "default":
                return site
        return sites[0]
    for site in sites:
        if wanted in (
            _lower(site.get("id")),
            _lower(site.get("internalReference")),
            _lower(site.get("name")),
        ):
            return site
    available = ", ".join(
        f"{s.get('name')} (id {s.get('id')}, ref {s.get('internalReference')})"
        for s in sites
    )
    raise UnifiError(
        f"No site matches {site_arg!r}. Available sites: {available}.",
        code="unifi_unknown_site",
    )


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

def is_gateway(device: dict) -> bool:
    features = device.get("features")
    if isinstance(features, list) and any("gateway" in _lower(f) for f in features):
        return True
    model = str(device.get("model") or "").upper()
    return model.startswith(_GATEWAY_MODEL_PREFIXES)


def _interface_counts(device: dict) -> dict:
    interfaces = device.get("interfaces")
    if not isinstance(interfaces, dict):
        return {}
    out = {}
    ports = interfaces.get("ports")
    if isinstance(ports, list):
        out["port_count"] = len(ports)
        out["ports_up"] = sum(
            1 for p in ports if isinstance(p, dict) and _lower(p.get("state")) == "up"
        )
    radios = interfaces.get("radios")
    if isinstance(radios, list):
        out["radio_count"] = len(radios)
    return out


def device_view(device: dict) -> dict:
    """Compact, LLM-friendly view of a device list/detail row."""
    view = {
        "id": device.get("id"),
        "name": device.get("name"),
        "model": device.get("model"),
        "state": device.get("state"),
        "ip_address": device.get("ipAddress"),
        "mac_address": device.get("macAddress"),
        "features": device.get("features"),
        "is_gateway": is_gateway(device),
    }
    for src, dst in (
        ("firmwareVersion", "firmware_version"),
        ("firmwareUpdatable", "firmware_updatable"),
        ("adoptedAt", "adopted_at"),
        ("provisionedAt", "provisioned_at"),
        ("supported", "supported"),
        ("managed", "managed"),
        ("note", "note"),
    ):
        if src in device:
            view[dst] = device[src]
    view.update(_interface_counts(device))
    return view


async def list_devices(client: UnifiClient, site_id: str) -> list[dict]:
    return await client.get_paged(f"{NETWORK_API_PREFIX}/sites/{site_id}/devices")


async def get_device(client: UnifiClient, site_id: str, device_id: str) -> dict:
    payload = await client.get_json(f"{NETWORK_API_PREFIX}/sites/{site_id}/devices/{device_id}")
    if not isinstance(payload, dict):
        raise UnifiError("Unexpected device payload.")
    return payload


async def get_device_statistics(client: UnifiClient, site_id: str, device_id: str) -> dict:
    payload = await client.get_json(
        f"{NETWORK_API_PREFIX}/sites/{site_id}/devices/{device_id}/statistics/latest"
    )
    return payload if isinstance(payload, dict) else {}


def statistics_view(stats: dict) -> dict:
    """Trim ``statistics/latest`` to the headline numbers."""
    view = {}
    for src, dst in (
        ("uptimeSec", "uptime_seconds"),
        ("lastHeartbeatAt", "last_heartbeat_at"),
        ("nextHeartbeatAt", "next_heartbeat_at"),
        ("loadAverage1Min", "load_average_1min"),
        ("loadAverage5Min", "load_average_5min"),
        ("loadAverage15Min", "load_average_15min"),
        ("cpuUtilizationPct", "cpu_utilization_pct"),
        ("memoryUtilizationPct", "memory_utilization_pct"),
    ):
        if src in stats:
            view[dst] = stats[src]
    uplink = stats.get("uplink")
    if isinstance(uplink, dict):
        view["uplink"] = {
            "tx_rate_bps": uplink.get("txRateBps"),
            "rx_rate_bps": uplink.get("rxRateBps"),
        }
    interfaces = stats.get("interfaces")
    if isinstance(interfaces, dict) and isinstance(interfaces.get("radios"), list):
        view["radios"] = [
            {
                "frequency_ghz": r.get("frequencyGHz"),
                "tx_retries_pct": r.get("txRetriesPct"),
            }
            for r in interfaces["radios"] if isinstance(r, dict)
        ]
    return view


def find_device(devices: list[dict], needle: str) -> Optional[dict]:
    """Match a device by id, name, MAC, or IP (case-insensitive)."""
    wanted = _lower(needle)
    if not wanted:
        return None
    for device in devices:
        if wanted in (
            _lower(device.get("id")),
            _lower(device.get("name")),
            _lower(device.get("macAddress")),
            _lower(device.get("ipAddress")),
        ):
            return device
    return None


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def client_view(row: dict) -> dict:
    """Compact view of a connected-client row."""
    access = row.get("access")
    view = {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "ip_address": row.get("ipAddress"),
        "mac_address": row.get("macAddress"),
        "connected_at": row.get("connectedAt"),
        "uplink_device_id": row.get("uplinkDeviceId"),
        "access": access.get("type") if isinstance(access, dict) else access,
    }
    return {k: v for k, v in view.items() if v is not None}


async def list_clients(client: UnifiClient, site_id: str) -> list[dict]:
    return await client.get_paged(f"{NETWORK_API_PREFIX}/sites/{site_id}/clients")


async def get_client(client: UnifiClient, site_id: str, client_id: str) -> dict:
    payload = await client.get_json(f"{NETWORK_API_PREFIX}/sites/{site_id}/clients/{client_id}")
    if not isinstance(payload, dict):
        raise UnifiError("Unexpected client payload.")
    return payload


def filter_clients(rows: list[dict], *, client_type: Optional[str] = None,
                   search: Optional[str] = None) -> list[dict]:
    """Client-side filtering: by ``type`` and a substring over name/ip/mac."""
    wanted_type = _lower(client_type)
    needle = _lower(search)
    out = []
    for row in rows:
        if wanted_type and _lower(row.get("type")) != wanted_type:
            continue
        if needle and needle not in " ".join(
            _lower(row.get(k)) for k in ("name", "ipAddress", "macAddress", "id")
        ):
            continue
        out.append(row)
    return out


def find_client(rows: list[dict], needle: str) -> Optional[dict]:
    """Match a client by id, name, MAC, or IP (case-insensitive)."""
    wanted = _lower(needle)
    if not wanted:
        return None
    for row in rows:
        if wanted in (
            _lower(row.get("id")),
            _lower(row.get("name")),
            _lower(row.get("macAddress")),
            _lower(row.get("ipAddress")),
        ):
            return row
    return None


# ---------------------------------------------------------------------------
# Health (legacy endpoint)
# ---------------------------------------------------------------------------

_HEALTH_FIELDS = (
    "status", "gw_name", "gw_mac", "gw_version", "wan_ip", "nameservers", "netmask",
    "gateways", "latency", "uptime", "drops", "xput_up", "xput_down",
    "speedtest_status", "speedtest_lastrun", "speedtest_ping",
    "num_user", "num_guest", "num_iot", "num_ap", "num_gw", "num_sw",
    "num_adopted", "num_disconnected", "num_pending", "num_disabled",
    "tx_bytes-r", "rx_bytes-r", "remote_user_num_active", "remote_user_num_inactive",
)


def health_view(subsystems: list[dict]) -> dict:
    """Key the legacy ``stat/health`` rows by subsystem, trimmed to the
    fields worth showing (status, WAN IP, latency, throughput, counts)."""
    out: dict[str, dict] = {}
    for row in subsystems:
        if not isinstance(row, dict) or not row.get("subsystem"):
            continue
        out[str(row["subsystem"])] = {
            k: row[k] for k in _HEALTH_FIELDS if k in row
        }
    return out


async def get_site_health(client: UnifiClient, site_ref: str) -> list[dict]:
    """The legacy per-subsystem health rows for a site (by internal ref).

    Raises :class:`UnifiError` when the controller does not accept the
    API key on the legacy endpoint (older Network versions) -- callers
    degrade to the Integration-API-only view.
    """
    payload = await client.get_json(f"{NETWORK_LEGACY_PREFIX}/s/{site_ref}/stat/health")
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("rc") not in (None, "ok"):
            raise UnifiError(f"Health request failed: {meta.get('msg') or meta.get('rc')}")
        data = payload.get("data")
        if isinstance(data, list):
            return data
    raise UnifiError("Unexpected health payload from the legacy Network API.")


def overall_status(health: dict, devices: list[dict]) -> str:
    """One word for the top of the status report: ``ok`` / ``degraded`` /
    ``down`` / ``unknown``."""
    wan = health.get("wan") or {}
    www = health.get("www") or {}
    statuses = {_lower(wan.get("status")), _lower(www.get("status"))} - {""}
    if statuses:
        if "error" in statuses:
            return "down"
        if "warning" in statuses:
            return "degraded"
        if statuses == {"ok"}:
            return "ok"
    gateways = [d for d in devices if is_gateway(d)]
    if gateways:
        if all(_lower(d.get("state")) == "online" for d in gateways):
            return "ok"
        return "degraded"
    return "unknown"
