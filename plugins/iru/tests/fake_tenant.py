"""An in-memory Iru tenant for the plugin tests (httpx.MockTransport).

Serves the subset of the Iru Endpoint Management API the plugin uses with
the real response shapes: a bare list for ``/devices``, ``{count, next,
previous, results}`` for most lists, ``{cursor, data}`` for Prism, cursor
URLs for users / audit events, and ``{results, size, total}`` for
vulnerabilities. ``TOKEN`` is the only accepted bearer token; anything else
gets a 401 in Iru's ``{"detail": ...}`` error shape. Knobs on
:class:`FakeTenant` force 403s per path, a 429, or a failing licensing
endpoint.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

from plugins.iru.upstream import IruClient

API_URL = "https://acme.api.iru.com"
TOKEN = "0f2a6c1e-8b7d-4c3a-9e21-5d4f6a7b8c9d"

BP_ENG = {
    "id": "b38787e4-401c-4c4b-86dc-f1cca2072531", "name": "Engineering", "type": "classic",
    "description": "Engineering Macs", "computers_count": 2, "color": "aqua-500",
    "enrollment_code": {"code": "123456", "is_active": True}, "icon": "ss-files", "params": {},
}
BP_OPS = {
    "id": "cbe7daa9-2bcb-4cb1-aff9-024cd3a0330c", "name": "Ops", "type": "map",
    "description": "", "computers_count": 1,
    "enrollment_code": {"code": "012345", "is_active": False}, "params": {},
}
BLUEPRINTS = [BP_ENG, BP_OPS]

LIBRARY_ITEM_ZOOM = "4a98cb3d-3b55-46bf-829d-7dd7bd6ed832"
LIBRARY_ITEM_WIFI = "c0148e35-c734-4402-b2fb-1c61aab72550"
BLUEPRINT_ITEMS = {
    BP_ENG["id"]: [{"id": LIBRARY_ITEM_ZOOM, "name": "Zoom"}, {"id": LIBRARY_ITEM_WIFI, "name": "Office Wi-Fi"}],
    BP_OPS["id"]: [{"id": LIBRARY_ITEM_WIFI, "name": "Office Wi-Fi"}],
}

USER_ADA = {
    "id": "69c009ca-1f78-4bdf-bb93-08d6d39041db", "name": "Ada Lovelace",
    "email": "ada@acme.example", "active": True, "archived": False, "device_count": 1,
    "department": "Engineering", "job_title": "Engineer", "created_at": "2024-09-06T21:00:04Z",
    "updated_at": "2024-09-06T21:00:04Z",
    "integration": {"id": 634, "name": "acme-okta", "type": "okta", "uuid": "f7461096-4ef9-43aa-88e9-ca1967ba0b38"},
}
USER_BOB = {
    "id": "adacf177-fbc2-4485-be26-05d8d965a8a6", "name": "Bob Builder",
    "email": "bob@acme.example", "active": True, "archived": False, "device_count": 0,
    "department": None, "job_title": None, "integration": None,
}
USER_OLD = {
    "id": "0d6a2b58-2f4e-4b8a-9d3c-1e2f3a4b5c6d", "name": "Old Timer",
    "email": "old@acme.example", "active": False, "archived": True, "device_count": 0,
}
USERS = [USER_ADA, USER_BOB, USER_OLD]

MAC_ADA = {
    "device_id": "4820db3b-dec7-40b2-9c8f-eb771ac1a250", "device_name": "Ada's MacBook Pro",
    "serial_number": "C02AAA111111", "platform": "Mac", "model": "MacBook Pro (14-inch, 2023)",
    "os_version": "14.5", "supplemental_os_version_extra": "", "blueprint_id": BP_ENG["id"],
    "blueprint_name": "Engineering", "asset_tag": "ENG-001", "tags": ["engineering"],
    "last_check_in": "2026-09-16T21:46:26Z", "first_enrollment": "2024-01-10 10:00:00+00:00",
    "last_enrollment": "2024-01-10 10:00:00+00:00", "is_missing": False, "is_removed": False,
    "agent_installed": True, "agent_version": "5.1.0", "mdm_enabled": True, "lost_mode_status": "",
    "user": {"email": USER_ADA["email"], "id": USER_ADA["id"], "is_archived": False, "name": USER_ADA["name"]},
    "filevault_enabled": True,
}
MAC_BUILD = {
    "device_id": "f3d1c9a7-8b6b-4e64-9c2e-2af1c028b4e5", "device_name": "Build Mac",
    "serial_number": "C02BBB222222", "platform": "Mac", "model": "Mac mini (2023)",
    "os_version": "13.6.7", "blueprint_id": BP_OPS["id"], "blueprint_name": "Ops",
    "asset_tag": "", "tags": [], "last_check_in": "2026-09-01T08:00:00Z",
    "first_enrollment": "2023-05-01 10:00:00+00:00", "is_missing": True, "is_removed": False,
    "agent_installed": True, "agent_version": "5.0.2", "mdm_enabled": True, "lost_mode_status": "",
    "user": "", "filevault_enabled": False,
}
IPAD_LOBBY = {
    "device_id": "77883d40-5656-4a24-9d70-49b6e751a923", "device_name": "Lobby iPad",
    "serial_number": "DMPCCC333333", "platform": "iPad", "model": "iPad (10th generation)",
    "os_version": "17.5.1", "blueprint_id": BP_ENG["id"], "blueprint_name": "Engineering",
    "asset_tag": "", "tags": ["kiosk"], "last_check_in": "2026-09-16T20:00:00Z",
    "first_enrollment": "2024-03-01 10:00:00+00:00", "is_missing": False, "is_removed": False,
    "agent_installed": False, "agent_version": "", "mdm_enabled": True, "lost_mode_status": "enabled",
    "user": "", "filevault_enabled": None,
}
DEVICES = [MAC_ADA, MAC_BUILD, IPAD_LOBBY]

DETAILS = {
    "general": {"device_id": MAC_ADA["device_id"], "device_name": MAC_ADA["device_name"],
                "platform": "Mac", "os_version": "14.5", "blueprint_name": "Engineering"},
    "hardware": {"model_name": "MacBook Pro", "cpu": "Apple M2 Pro", "total_ram": "32"},
    "security": {"filevault_enabled": True, "sip_enabled": True, "firewall_enabled": True},
}
LONG_LOG = "line\n" * 400
STATUS_ITEMS = [
    {"id": 1222, "item_id": LIBRARY_ITEM_ZOOM, "name": "Zoom", "type": "custom-app", "status": "PASS",
     "reported_at": "2026-09-16T19:45:19Z", "last_audit_log": "Zoom 6.1 installed.", "log": LONG_LOG},
    {"id": 1218, "item_id": LIBRARY_ITEM_WIFI, "name": "Office Wi-Fi", "type": "profile", "status": "ERROR",
     "reported_at": "2026-09-16T17:20:55Z", "last_audit_log": "Profile install failed: " + "x" * 400, "log": None},
]
STATUS_PARAMS = [{"item_id": "param-1", "status": "PASS"}, {"item_id": "param-2", "status": "ERROR"}]
APPS = [
    {"app_id": "1", "app_name": "Zoom", "version": "6.1.0", "bundle_id": "us.zoom.xos",
     "path": "/Applications/zoom.us.app", "source": "Kandji", "app_store_vendable": "no"},
    {"app_id": "2", "app_name": "Safari", "version": "17.5", "bundle_id": "com.apple.Safari",
     "path": "/Applications/Safari.app", "source": "Apple"},
    {"app_id": "3", "app_name": "1Password", "version": "8.10", "bundle_id": "com.1password.1password",
     "path": "/Applications/1Password.app", "source": "Kandji"},
]
ACTIVITY = [
    {"id": 44577, "action_type": "enrollment", "created_at": "2024-01-10T10:00:27Z",
     "blueprint": {"id": BP_ENG["id"], "name": "Engineering"}, "user": None,
     "details": {"blueprint_name": "Engineering", "enrollment_type": "ADE"}},
    {"id": 44580, "action_type": "library_item_install", "created_at": "2026-09-16T19:45:19Z",
     "blueprint": {"id": BP_ENG["id"], "name": "Engineering"}, "user": None,
     "details": {"payload": "y" * 3000}},
]
COMMANDS = [{"id": 1, "command": "DeviceInformation", "status": "acknowledged", "created_at": "2026-09-16T19:00:00Z"}]

ITEM_STATUSES = [
    {"id": 14603, "status": "PASS", "type": "custom-app", "reported_at": "2026-09-16T19:45:19Z",
     "blueprint": {"id": BP_ENG["id"], "name": "Engineering"},
     "computer": {"id": MAC_ADA["device_id"], "name": MAC_ADA["device_name"]},
     "last_audit_log": "ok", "log": "installed"},
    {"id": 14602, "status": "ERROR", "type": "custom-app", "reported_at": "2026-09-15T10:00:00Z",
     "blueprint": {"id": BP_OPS["id"], "name": "Ops"},
     "computer": {"id": MAC_BUILD["device_id"], "name": MAC_BUILD["device_name"]},
     "last_audit_log": "Download failed", "log": "Downloading zoom\nfailed"},
    {"id": 14600, "status": "PENDING", "type": "custom-app", "reported_at": None,
     "blueprint": {"id": BP_ENG["id"], "name": "Engineering"},
     "computer": {"id": IPAD_LOBBY["device_id"], "name": IPAD_LOBBY["device_name"]},
     "last_audit_log": None, "log": None},
]

TAGS = [
    {"id": "71dd53f1-104e-41a2-82ca-43a375b7f63d", "name": "engineering"},
    {"id": "4fe23c39-7a9a-435c-bab3-d2e9fd1974c6", "name": "kiosk"},
]

PRISM_FILEVAULT = [
    {"device_id": MAC_ADA["device_id"], "device__name": MAC_ADA["device_name"], "serial_number": "C02AAA111111",
     "device__user_email": "ada@acme.example", "blueprint_name": "Engineering", "filevault_enabled": True,
     "filevault_recovery_key_escrowed": True, "last_collected_at": "2026-09-16T19:00:00Z"},
    {"device_id": MAC_BUILD["device_id"], "device__name": "Build Mac", "serial_number": "C02BBB222222",
     "device__user_email": "", "blueprint_name": "Ops", "filevault_enabled": False,
     "filevault_recovery_key_escrowed": False, "last_collected_at": "2026-09-01T08:00:00Z"},
]

THREATS = [
    {"threat_name": "malware_5", "classification": "MALWARE", "status": "QUARANTINED",
     "device_id": MAC_BUILD["device_id"], "device_name": "Build Mac", "device_serial_number": "C02BBB222222",
     "file_path": "/Users/Shared/malware/malware_5", "file_hash": "2ab79665", "process_name": "chmod",
     "detection_date": "2026-09-10T17:23:13Z", "date_of_quarantine": "2026-09-10T17:23:15Z"},
]

VULNS = [
    {"cve_id": "CVE-2024-24795", "cve_link": "https://nvd.nist.gov/vuln/detail/CVE-2024-24795",
     "cvss_score": 7.5, "device_count": 1, "severity": "High", "software": ["macOS 13 Ventura"],
     "status": "Active", "first_detection_date": "2026-05-23T13:21:37Z", "kev_score": 0},
    {"cve_id": "CVE-2025-1111", "cvss_score": 9.8, "device_count": 2, "severity": "Critical",
     "software": ["Zoom"], "status": "Active"},
]
VULN_DEVICES = [
    {"device_id": MAC_BUILD["device_id"], "name": "Build Mac", "serial_number": "C02BBB222222",
     "blueprint_name": "Ops", "os_version": "13.6.7", "no_of_installs": 1,
     "software_summary": {"name": "macOS 13 Ventura", "version": "13.6.7", "path": "/"}},
]
VULN_SOFTWARE = [{"name": "macOS 13 Ventura", "version": "13.6.7", "device_count": 1}]

AUDIT_EVENTS = [
    {"id": "01JNGZW47KZKPXE1JWCFE4PHDW", "action": "update", "actor_id": USER_ADA["id"], "actor_type": "user",
     "occurred_at": "2026-09-16T16:29:55Z", "target_type": "blueprint", "target_id": BP_ENG["id"],
     "target_component": "library_items", "metadata": {},
     "new_state": {"library_items_added": [{"id": LIBRARY_ITEM_ZOOM, "name": "Zoom"}], "big": "z" * 3000}},
    {"id": "01JNGZWQ7WAN45PVTJ3686TXHT", "action": "create", "actor_id": USER_ADA["id"], "actor_type": "user",
     "occurred_at": "2026-09-16T16:30:14Z", "target_type": "blueprint", "target_id": BP_OPS["id"],
     "new_state": {"name": "Ops", "type": "map"}},
]

ADE_DEVICES = [
    {"id": "aca3e9f7-48d9-44a5-9007-25bc0b1d451f", "serial_number": "C02DDD444444", "model": "MacBook Air",
     "device_family": "Mac", "os": "OSX", "profile_status": "assigned", "blueprint_id": BP_ENG["id"],
     "user_id": USER_BOB["id"], "mdm_device": None, "dep_account": {"id": "821b216b", "server_name": "acme"}},
]

LICENSING = {
    "counts": {"computers_count": 3, "ios_count": 0, "ipados_count": 1, "macos_count": 2, "tvos_count": 0},
    "limits": {"max_devices": 10, "plan_type": "single_limit"},
    "tenantOverLicenseLimit": False,
}


def _json(status: int, payload) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _qs(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in
            parse_qs(request.url.query.decode(), keep_blank_values=True).items()}


def _contains(haystack, needle: str | None) -> bool:
    if needle is None:
        return True
    return needle.lower() in str(haystack or "").lower()


def _paged_results(items: list, qs: dict, *, default_limit: int = 300,
                   page_size: int | None = None) -> httpx.Response:
    """``{count, next, previous, results}`` honoring limit/offset."""
    offset = int(qs.get("offset", "0"))
    limit = int(qs.get("limit", str(default_limit)))
    if page_size:
        limit = min(limit, page_size)
    rows = items[offset:offset + limit]
    has_more = offset + len(rows) < len(items)
    return _json(200, {
        "count": len(items),
        "next": f"{API_URL}/x?offset={offset + limit}" if has_more else None,
        "previous": None,
        "results": rows,
    })


class FakeTenant:
    """Routes requests; records every (method, path) and the last query."""

    def __init__(self, *, forbidden_paths: set[str] | None = None,
                 rate_limited: bool = False, licensing_status: int = 200,
                 status_page_size: int | None = 2):
        self.calls: list[tuple[str, str]] = []
        self.last_params: dict[str, dict[str, str]] = {}
        self.forbidden_paths = forbidden_paths or set()
        self.rate_limited = rate_limited
        self.licensing_status = licensing_status
        self.status_page_size = status_page_size

    # -- dispatch ----------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        qs = _qs(request)
        self.calls.append((request.method, path))
        self.last_params[path] = qs
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return _json(401, {"detail": "Invalid token."})
        if self.rate_limited:
            return httpx.Response(429, json={"detail": "Request was throttled."},
                                  headers={"Retry-After": "42"})
        if path in self.forbidden_paths:
            return _json(403, {"detail": "You do not have permission to perform this action."})
        p = "/api/v1"
        if not path.startswith(p + "/"):
            return _json(404, {"detail": "Not found."})
        rest = path[len(p):]

        if rest == "/settings/licensing":
            if self.licensing_status != 200:
                return _json(self.licensing_status, {"detail": "permission denied"})
            return _json(200, LICENSING)
        if rest == "/devices":
            return self._devices(qs)
        if rest.startswith("/devices/"):
            return self._device(rest[len("/devices/"):], qs)
        if rest == "/blueprints":
            rows = [b for b in BLUEPRINTS if _contains(b["name"], qs.get("name"))]
            if qs.get("id"):
                rows = [b for b in rows if b["id"] == qs["id"]]
            return _paged_results(rows, qs)
        if rest.startswith("/blueprints/"):
            bp_id, _, tail = rest[len("/blueprints/"):].partition("/")
            bp = next((b for b in BLUEPRINTS if b["id"] == bp_id), None)
            if bp is None:
                return _json(404, {"detail": "Not found."})
            if tail == "list-library-items":
                return _paged_results(BLUEPRINT_ITEMS.get(bp_id, []), qs)
            return _json(200, bp)
        if rest.startswith("/library/library-items/"):
            item_id, _, tail = rest[len("/library/library-items/"):].partition("/")
            if tail == "status":
                if item_id != LIBRARY_ITEM_ZOOM:
                    return _paged_results([], qs)
                rows = ITEM_STATUSES
                if qs.get("computer_id"):
                    rows = [r for r in rows if r["computer"]["id"] == qs["computer_id"]]
                return _paged_results(rows, qs, page_size=self.status_page_size)
            return _json(404, {"detail": "Not found."})
        if rest == "/users":
            return self._users(qs)
        if rest.startswith("/users/"):
            user = next((u for u in USERS if u["id"] == rest[len("/users/"):]), None)
            return _json(200, user) if user else _json(404, {"detail": "Not found."})
        if rest == "/tags":
            rows = [t for t in TAGS if _contains(t["name"], qs.get("search"))]
            return _paged_results(rows, qs)
        if rest == "/prism/count":
            return _json(200, {"approximate": False, "count": len(PRISM_FILEVAULT)})
        if rest.startswith("/prism/"):
            return self._prism(rest[len("/prism/"):], qs)
        if rest == "/threat-details":
            rows = THREATS
            if qs.get("status"):
                rows = [t for t in rows if t["status"].lower() == qs["status"]]
            resp = _paged_results(rows, qs)
            payload = json.loads(resp.content)
            payload.update({"malware_count": len(rows), "pup_count": 0})
            return _json(200, payload)
        if rest == "/vulnerability-management/vulnerabilities":
            size = int(qs.get("size", "50"))
            page = int(qs.get("page", "1"))
            rows = VULNS[(page - 1) * size: page * size]
            return _json(200, {"results": rows, "size": size, "total": len(VULNS)})
        if rest.startswith("/vulnerability-management/vulnerabilities/"):
            cve, _, tail = rest[len("/vulnerability-management/vulnerabilities/"):].partition("/")
            vuln = next((v for v in VULNS if v["cve_id"] == cve), None)
            if vuln is None:
                return _json(404, {"detail": "Not found."})
            if tail == "devices":
                return _json(200, {"page": 1, "results": VULN_DEVICES, "size": 50, "total": len(VULN_DEVICES)})
            if tail == "software":
                return _json(200, {"page": 1, "results": VULN_SOFTWARE, "size": 50, "total": len(VULN_SOFTWARE)})
            return _json(200, {**vuln, "description": "A bad bug."})
        if rest == "/audit/events":
            if qs.get("cursor") == "page2":
                return _json(200, {"next": None, "previous": None, "results": AUDIT_EVENTS[1:]})
            return _json(200, {
                "next": f"{API_URL}/api/v1/audit/events?limit=1&cursor=page2",
                "previous": None, "results": AUDIT_EVENTS[:1],
            })
        if rest == "/integrations/apple/ade/devices":
            rows = ADE_DEVICES
            if qs.get("profile_status"):
                rows = [d for d in rows if d["profile_status"] == qs["profile_status"]]
            return _json(200, {"count": len(rows), "next": None, "previous": None, "results": rows})
        return _json(404, {"detail": "Not found."})

    # -- devices -------------------------------------------------------------

    def _devices(self, qs: dict) -> httpx.Response:
        rows = DEVICES
        for key in ("serial_number", "device_name", "model", "os_version", "blueprint_id"):
            if qs.get(key):
                rows = [d for d in rows if _contains(d.get(key), qs[key])]
        if qs.get("platform"):
            rows = [d for d in rows if d["platform"] == qs["platform"]]
        if qs.get("user_email"):
            rows = [d for d in rows if isinstance(d["user"], dict)
                    and _contains(d["user"]["email"], qs["user_email"])]
        if qs.get("user_id"):
            rows = [d for d in rows if isinstance(d["user"], dict) and d["user"]["id"] == qs["user_id"]]
        if qs.get("tag_name"):
            rows = [d for d in rows if qs["tag_name"] in d["tags"]]
        if qs.get("filevault_enabled"):
            wanted = qs["filevault_enabled"] == "true"
            rows = [d for d in rows if d.get("filevault_enabled") is wanted]
        if qs.get("device_id"):
            rows = [d for d in rows if d["device_id"] == qs["device_id"]]
        offset = int(qs.get("offset", "0"))
        limit = int(qs.get("limit", "300"))
        return _json(200, [{k: v for k, v in d.items() if k != "filevault_enabled"}
                           for d in rows[offset:offset + limit]])

    def _device(self, rest: str, qs: dict) -> httpx.Response:
        device_id, _, tail = rest.partition("/")
        device = next((d for d in DEVICES if d["device_id"] == device_id), None)
        if device is None:
            return _json(404, {"detail": "Not found."})
        record = {k: v for k, v in device.items() if k != "filevault_enabled"}
        if tail == "":
            return _json(200, record)
        if tail == "details":
            return _json(200, DETAILS)
        if tail == "status":
            return _json(200, {"device_id": device_id, "library_items": STATUS_ITEMS,
                               "parameters": STATUS_PARAMS})
        if tail == "library-items":
            items = [{**li, "blueprint": {"id": BP_ENG["id"], "name": "Engineering"},
                      "computer": {"id": device_id, "name": device["device_name"]}}
                     for li in STATUS_ITEMS]
            return _json(200, {"device_id": device_id, "library_items": items})
        if tail == "apps":
            return _json(200, {"apps": APPS})
        if tail == "activity":
            limit = int(qs.get("limit", "300"))
            return _json(200, {"activity": {"count": len(ACTIVITY), "next": None, "previous": None,
                                            "results": ACTIVITY[:limit]}})
        if tail == "commands":
            return _json(200, {"count": len(COMMANDS), "next": None, "previous": None, "results": COMMANDS})
        if tail == "parameters":
            return _json(200, {"parameters": [{"item_id": "param-1", "status": "PASS"}]})
        if tail == "details/lostmode":
            return _json(200, {"lost_mode_status": device["lost_mode_status"]})
        return _json(404, {"detail": "Not found."})

    # -- users / prism ---------------------------------------------------------

    def _users(self, qs: dict) -> httpx.Response:
        rows = [u for u in USERS if _contains(u["email"], qs.get("email"))]
        if qs.get("archived"):
            wanted = qs["archived"] == "true"
            rows = [u for u in rows if u["archived"] is wanted]
        if qs.get("cursor") == "cD0y":
            return _json(200, {"next": None, "previous": None, "results": rows[2:]})
        if len(rows) > 2:
            return _json(200, {"next": f"{API_URL}/api/v1/users?cursor=cD0y", "previous": None,
                               "results": rows[:2]})
        return _json(200, {"next": None, "previous": None, "results": rows})

    def _prism(self, category: str, qs: dict) -> httpx.Response:
        if category != "filevault":
            return _json(200, {"cursor": None, "data": []})
        rows = PRISM_FILEVAULT
        if qs.get("filter"):
            flt = json.loads(qs["filter"])
            for column, spec in flt.items():
                if "eq" in spec:
                    rows = [r for r in rows if r.get(column) == spec["eq"]]
        offset = int(qs.get("offset", "0"))
        limit = int(qs.get("limit", "300"))
        return _json(200, {"cursor": None, "data": rows[offset:offset + limit]})

    # -- plumbing ------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def make_client(self, config: dict, token: str, *, transport=None) -> IruClient:
        """Drop-in for ``plugins.iru.tools.make_client``."""
        return IruClient(config["api_url"], token, transport=self.transport())
