"""An in-memory UniFi OS console for the plugin tests (httpx.MockTransport).

Serves the subset of the Network Integration API, the legacy
``stat/health`` endpoint, and the Protect Integration API the plugin uses.
Keys: ``NETWORK_KEY`` / ``PROTECT_KEY`` are accepted by their application;
anything else gets a 401 in the Integration APIs' error shape.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

from plugins.unifi.upstream import UnifiClient

HOST = "https://unifi.test"
NETWORK_KEY = "network-key-1234"
PROTECT_KEY = "protect-key-5678"

SITES = [{"id": "site-1", "internalReference": "default", "name": "Default"}]

GATEWAY = {
    "id": "dev-gw", "name": "Gateway", "model": "UDM-Pro", "state": "ONLINE",
    "ipAddress": "192.168.1.1", "macAddress": "aa:bb:cc:00:00:01",
    "features": ["gateway", "switching"], "firmwareVersion": "4.1.13",
    "firmwareUpdatable": False,
    "interfaces": {"ports": [{"idx": 1, "state": "UP"}, {"idx": 2, "state": "DOWN"}], "radios": []},
}
ACCESS_POINT = {
    "id": "dev-ap", "name": "Office AP", "model": "U6-Lite", "state": "OFFLINE",
    "ipAddress": "192.168.1.20", "macAddress": "aa:bb:cc:00:00:02",
    "features": ["accessPoint"], "firmwareVersion": "6.6.77",
    "interfaces": {"ports": [{"idx": 1, "state": "UP"}], "radios": [{"frequencyGHz": 2.4}, {"frequencyGHz": 5}]},
}
DEVICES = [GATEWAY, ACCESS_POINT]

GATEWAY_STATS = {
    "uptimeSec": 86400, "lastHeartbeatAt": "2026-09-03T10:00:00Z",
    "cpuUtilizationPct": 12.5, "memoryUtilizationPct": 40.0,
    "uplink": {"txRateBps": 1000, "rxRateBps": 2000},
}

CLIENTS = [
    {"id": "cl-1", "name": "Laptop", "type": "WIRELESS", "ipAddress": "192.168.1.50",
     "macAddress": "11:22:33:44:55:66", "connectedAt": "2026-09-03T08:00:00Z",
     "uplinkDeviceId": "dev-ap", "access": {"type": "DEFAULT"}},
    {"id": "cl-2", "name": "NAS", "type": "WIRED", "ipAddress": "192.168.1.60",
     "macAddress": "11:22:33:44:55:77", "connectedAt": "2026-09-01T08:00:00Z",
     "uplinkDeviceId": "dev-gw", "access": {"type": "DEFAULT"}},
    {"id": "cl-3", "name": "Phone", "type": "WIRELESS", "ipAddress": "192.168.1.51",
     "macAddress": "11:22:33:44:55:88", "connectedAt": "2026-09-03T09:00:00Z",
     "uplinkDeviceId": "dev-ap", "access": {"type": "DEFAULT"}},
]

HEALTH = [
    {"subsystem": "wan", "status": "ok", "wan_ip": "203.0.113.5", "latency": 12,
     "xput_down": 940.1, "xput_up": 41.0, "gw_name": "Gateway", "num_gw": 1},
    {"subsystem": "www", "status": "ok", "latency": 14, "uptime": 86000},
    {"subsystem": "lan", "status": "ok", "num_user": 2, "num_sw": 0},
    {"subsystem": "wlan", "status": "warning", "num_user": 2, "num_ap": 1, "num_disconnected": 1},
]

CAMERAS = [
    {"id": "cam-1", "name": "Front Door", "state": "CONNECTED", "modelKey": "camera",
     "lastSeen": 1756890000000, "isMicEnabled": True, "videoMode": "default",
     "hdrType": "auto", "featureFlags": {"hasHdr": True, "hasLedStatus": False},
     "smartDetectSettings": {"objectTypes": ["person", "vehicle"]}},
    {"id": "cam-2", "name": "Garage", "state": "DISCONNECTED", "modelKey": "camera",
     "lastSeen": 1756800000000, "isMicEnabled": False},
]
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
NVR = {"id": "nvr-1", "name": "UNVR", "version": "6.0.1", "modelKey": "nvr"}


def _json(status: int, payload) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _unauthorized() -> httpx.Response:
    return _json(401, {"statusCode": 401, "statusName": "UNAUTHORIZED", "message": "Invalid API key"})


def _paged(items: list, request: httpx.Request, *, page_size: int | None = None) -> httpx.Response:
    """A ``{offset, limit, count, totalCount, data}`` page honoring the
    request's offset; ``page_size`` forces small pages to exercise paging."""
    qs = parse_qs(request.url.query.decode())
    offset = int(qs.get("offset", ["0"])[0])
    limit = int(qs.get("limit", ["200"])[0])
    if page_size:
        limit = min(limit, page_size)
    data = items[offset:offset + limit]
    return _json(200, {
        "offset": offset, "limit": limit, "count": len(data),
        "totalCount": len(items), "data": data,
    })


class FakeController:
    """Routes requests; records every (method, path) it served."""

    def __init__(self, *, health_status: int = 200, client_page_size: int | None = 2,
                 snapshot_status: int = 200):
        self.calls: list[tuple[str, str]] = []
        self.health_status = health_status
        self.client_page_size = client_page_size
        self.snapshot_status = snapshot_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        key = request.headers.get("X-API-KEY")

        if path.startswith("/proxy/network/"):
            if key != NETWORK_KEY:
                return _unauthorized()
            return self._network(path, request)
        if path.startswith("/proxy/protect/"):
            if key != PROTECT_KEY:
                return _unauthorized()
            return self._protect(path, request)
        return _json(404, {"statusCode": 404, "statusName": "NOT_FOUND", "message": "no route"})

    # -- Network -------------------------------------------------------------

    def _network(self, path: str, request: httpx.Request) -> httpx.Response:
        p = "/proxy/network/integration/v1"
        if path == f"{p}/info":
            return _json(200, {"applicationVersion": "9.3.45"})
        if path == f"{p}/sites":
            return _paged(SITES, request)
        if path == f"{p}/sites/site-1/devices":
            return _paged(DEVICES, request)
        if path.startswith(f"{p}/sites/site-1/devices/"):
            rest = path[len(f"{p}/sites/site-1/devices/"):]
            device_id, _, tail = rest.partition("/")
            device = next((d for d in DEVICES if d["id"] == device_id), None)
            if device is None:
                return _json(404, {"statusCode": 404, "statusName": "NOT_FOUND", "message": "device"})
            if tail == "statistics/latest":
                return _json(200, GATEWAY_STATS)
            return _json(200, {**device, "adoptedAt": "2025-01-01T00:00:00Z"})
        if path == f"{p}/sites/site-1/clients":
            return _paged(CLIENTS, request, page_size=self.client_page_size)
        if path.startswith(f"{p}/sites/site-1/clients/"):
            client_id = path.rsplit("/", 1)[1]
            row = next((c for c in CLIENTS if c["id"] == client_id), None)
            if row is None:
                return _json(404, {"statusCode": 404, "statusName": "NOT_FOUND", "message": "client"})
            return _json(200, {**row, "lastSeen": "2026-09-03T10:00:00Z"})
        if path == "/proxy/network/api/s/default/stat/health":
            if self.health_status == 200:
                return _json(200, {"meta": {"rc": "ok"}, "data": HEALTH})
            return _json(self.health_status, {"meta": {"rc": "error", "msg": "api.err.LoginRequired"}})
        return _json(404, {"statusCode": 404, "statusName": "NOT_FOUND", "message": path})

    # -- Protect -------------------------------------------------------------

    def _protect(self, path: str, request: httpx.Request) -> httpx.Response:
        p = "/proxy/protect/integration/v1"
        if path == f"{p}/meta/info":
            return _json(200, {"applicationVersion": "6.0.1"})
        if path == f"{p}/cameras":
            return _json(200, CAMERAS)
        if path.startswith(f"{p}/cameras/"):
            rest = path[len(f"{p}/cameras/"):]
            camera_id, _, tail = rest.partition("/")
            camera = next((c for c in CAMERAS if c["id"] == camera_id), None)
            if camera is None:
                return _json(404, {"statusCode": 404, "statusName": "NOT_FOUND", "message": "camera"})
            if tail == "snapshot":
                if self.snapshot_status != 200:
                    return _json(self.snapshot_status, {"statusCode": self.snapshot_status, "message": "no snapshot"})
                return httpx.Response(200, content=JPEG_BYTES, headers={"content-type": "image/jpeg"})
            return _json(200, {**camera, "osdSettings": {"isNameEnabled": True}})
        if path == f"{p}/nvrs":
            return _json(200, NVR)
        if path == f"{p}/sensors":
            return _json(200, [])
        return _json(404, {"statusCode": 404, "statusName": "NOT_FOUND", "message": path})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def make_client(self, config: dict, api_key: str, *, transport=None) -> UnifiClient:
        """Drop-in for ``plugins.unifi.tools.make_client``."""
        return UnifiClient(config["host"], api_key, transport=self.transport())
