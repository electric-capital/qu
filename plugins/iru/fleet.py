"""Iru fleet helpers (plugin module): reference resolution + compact views.

The Iru API keys everything by UUID, while users talk about devices by
serial number or name and about blueprints by name. The ``resolve_*``
helpers turn a free-form reference into the API object (exact match first,
then a single partial match; anything else raises :class:`IruLookupError`
with candidates), and the ``*_row`` views trim the wide upstream records
down to what a model needs to answer questions without blowing up the
tool result.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from plugins.iru.upstream import API_PREFIX, IruClient, IruError, is_uuid, page_items

MAX_CANDIDATES = 10
LOG_TAIL_CHARS = 500
AUDIT_LOG_CHARS = 300
DETAILS_JSON_CHARS = 1500

# Every Prism (Visibility) category exposed as ``GET /api/v1/prism/<category>``.
PRISM_CATEGORIES = (
    "device_information",
    "apps",
    "activation_lock",
    "application_firewall",
    "cellular",
    "certificates",
    "desktop_and_screensaver",
    "filevault",
    "gatekeeper_and_xprotect",
    "installed_profiles",
    "kernel_extensions",
    "launch_agents_and_daemons",
    "local_users",
    "startup_settings",
    "system_extensions",
    "transparency_database",
)


class IruLookupError(IruError):
    """A free-form reference matched nothing or more than one object."""

    def __init__(self, code: str, message: str, *, candidates: list | None = None):
        super().__init__(message, code=code)
        self.candidates = candidates or []


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _lower(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def truncate_text(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + f"... [{len(value) - limit} more chars]"


def tail_text(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return f"[{len(value) - limit} earlier chars]..." + value[-limit:]


def compact_json(value: Any, limit: int = DETAILS_JSON_CHARS) -> Any:
    """Keep a nested value as-is when small, else a truncated JSON string."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= limit:
        return value
    return text[:limit] + f"... [truncated, {len(text) - limit} more chars]"


def _drop_empty(row: dict) -> dict:
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


def user_ref(value: Any) -> Any:
    """Normalize the polymorphic device ``user`` field (str | dict | None)."""
    if isinstance(value, dict):
        return _drop_empty({
            "id": value.get("id"),
            "name": value.get("name"),
            "email": value.get("email"),
            "is_archived": value.get("is_archived"),
        }) or None
    return value or None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def device_row(d: dict) -> dict:
    """Compact view of a device list / record object."""
    return _drop_empty({
        "device_id": d.get("device_id"),
        "device_name": d.get("device_name"),
        "serial_number": d.get("serial_number"),
        "platform": d.get("platform"),
        "model": d.get("model"),
        "os_version": d.get("os_version"),
        "supplemental_os_version_extra": d.get("supplemental_os_version_extra"),
        "blueprint_id": d.get("blueprint_id"),
        "blueprint_name": d.get("blueprint_name"),
        "user": user_ref(d.get("user")),
        "asset_tag": d.get("asset_tag"),
        "tags": d.get("tags"),
        "last_check_in": d.get("last_check_in"),
        "first_enrollment": d.get("first_enrollment"),
        "is_missing": d.get("is_missing"),
        "is_removed": d.get("is_removed"),
        "agent_installed": d.get("agent_installed"),
        "agent_version": d.get("agent_version"),
        "mdm_enabled": d.get("mdm_enabled"),
        "lost_mode_status": d.get("lost_mode_status"),
    })


def app_row(a: dict) -> dict:
    """Compact view of one installed app (Mac / iOS / Android / Windows shapes)."""
    return _drop_empty({
        "name": a.get("app_name") or a.get("display_name") or a.get("name")
        or a.get("bundle_name"),
        "version": a.get("version") or a.get("version_name"),
        "bundle_id": a.get("bundle_id") or a.get("package_name"),
        "path": a.get("path"),
        "source": a.get("source") or a.get("application_source"),
        "app_store_vendable": a.get("app_store_vendable"),
        "bundle_size": a.get("bundle_size"),
        "modification_date": a.get("modification_date"),
    })


def library_item_status_row(li: dict) -> dict:
    """Compact view of a per-device library item status entry (the device
    status / device library-items / library-item status shapes)."""
    blueprint = li.get("blueprint") if isinstance(li.get("blueprint"), dict) else None
    computer = li.get("computer") if isinstance(li.get("computer"), dict) else None
    return _drop_empty({
        "item_id": li.get("item_id"),
        "name": li.get("name"),
        "type": li.get("type"),
        "status": li.get("status"),
        "reported_at": li.get("reported_at"),
        "last_audit_run": li.get("last_audit_run"),
        "last_audit_log": truncate_text(li.get("last_audit_log"), AUDIT_LOG_CHARS),
        "log_tail": tail_text(li.get("log"), LOG_TAIL_CHARS),
        "control_reported_at": li.get("control_reported_at"),
        "edr_status": li.get("edr_status"),
        "blueprint_name": blueprint.get("name") if blueprint else None,
        "device_id": computer.get("id") if computer else None,
        "device_name": computer.get("name") if computer else None,
    })


def parameter_status_row(p: dict) -> dict:
    return _drop_empty({
        "item_id": p.get("item_id") or p.get("id"),
        "status": p.get("status"),
        "reported_at": p.get("reported_at"),
        "last_audit_log": truncate_text(p.get("last_audit_log"), AUDIT_LOG_CHARS),
    })


def activity_row(a: dict) -> dict:
    blueprint = a.get("blueprint") if isinstance(a.get("blueprint"), dict) else None
    return _drop_empty({
        "id": a.get("id"),
        "action_type": a.get("action_type"),
        "created_at": a.get("created_at"),
        "user": user_ref(a.get("user")),
        "blueprint_name": blueprint.get("name") if blueprint else None,
        "details": compact_json(a.get("details")),
    })


def blueprint_row(b: dict) -> dict:
    code = b.get("enrollment_code") if isinstance(b.get("enrollment_code"), dict) else None
    return _drop_empty({
        "id": b.get("id"),
        "name": b.get("name"),
        "type": b.get("type"),
        "description": b.get("description"),
        "computers_count": b.get("computers_count"),
        "enrollment_code_active": code.get("is_active") if code else None,
    })


def user_row(u: dict) -> dict:
    integration = u.get("integration") if isinstance(u.get("integration"), dict) else None
    return _drop_empty({
        "id": u.get("id"),
        "name": u.get("name"),
        "email": u.get("email"),
        "active": u.get("active"),
        "archived": u.get("archived"),
        "device_count": u.get("device_count"),
        "department": u.get("department"),
        "job_title": u.get("job_title"),
        "integration": _drop_empty({
            "name": integration.get("name"),
            "type": integration.get("type"),
        }) if integration else None,
        "created_at": u.get("created_at"),
        "updated_at": u.get("updated_at"),
    })


def project_fields(row: dict, fields: list[str] | None) -> dict:
    """Keep only ``fields`` of a wide row (unknown names are ignored)."""
    if not fields:
        return row
    return {k: row[k] for k in fields if k in row}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _pick_match(rows: list[dict], ref: str, *keys: str) -> tuple[Optional[dict], list[dict]]:
    """``(exact match or sole partial match, candidates)`` over ``rows``."""
    wanted = _lower(ref)
    exact = [r for r in rows if any(_lower(r.get(k)) == wanted for k in keys)]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact
    if len(rows) == 1:
        return rows[0], []
    return None, rows


async def resolve_device(client: IruClient, ref: str) -> dict:
    """The device record for a device id, serial number, or device name.

    UUIDs go straight to ``GET /devices/{id}``. Otherwise the list endpoint
    is queried by ``serial_number`` (a "contains" filter upstream) and, when
    that yields nothing, by ``device_name``; an exact match wins, a single
    partial match is accepted, anything else raises ``iru_unknown_device``
    / ``iru_ambiguous_device`` with the candidates.
    """
    ref = (ref or "").strip()
    if not ref:
        raise IruLookupError("invalid_arguments", "A device reference is required.")
    if is_uuid(ref):
        try:
            record = await client.get_json(f"{API_PREFIX}/devices/{ref}")
        except IruError as exc:
            if exc.code == "iru_not_found":
                raise IruLookupError(
                    "iru_unknown_device", f"No device with id {ref!r}.",
                ) from exc
            raise
        if not isinstance(record, dict):
            raise IruError("Unexpected device record shape.")
        return record

    candidates: list[dict] = []
    for param in ("serial_number", "device_name"):
        payload = await client.get_json(
            f"{API_PREFIX}/devices", params={param: ref, "limit": MAX_CANDIDATES * 3},
        )
        rows = [r for r in page_items(payload, "/devices") if isinstance(r, dict)]
        if not rows:
            continue
        match, rest = _pick_match(rows, ref, "serial_number", "device_name")
        if match is not None:
            return match
        candidates.extend(rest)
        break  # a non-empty but ambiguous serial search is already the answer
    if not candidates:
        raise IruLookupError(
            "iru_unknown_device",
            f"No device matches {ref!r} (tried device id, serial number, and name).",
        )
    raise IruLookupError(
        "iru_ambiguous_device",
        f"{len(candidates)} devices match {ref!r}; pass the device_id or the "
        "exact serial number.",
        candidates=[device_row(c) for c in candidates[:MAX_CANDIDATES]],
    )


async def resolve_blueprint(client: IruClient, ref: str) -> dict:
    """The blueprint object for a blueprint id or name."""
    ref = (ref or "").strip()
    if not ref:
        raise IruLookupError("invalid_arguments", "A blueprint reference is required.")
    if is_uuid(ref):
        try:
            record = await client.get_json(f"{API_PREFIX}/blueprints/{ref}")
        except IruError as exc:
            if exc.code == "iru_not_found":
                raise IruLookupError(
                    "iru_unknown_blueprint", f"No blueprint with id {ref!r}.",
                ) from exc
            raise
        if not isinstance(record, dict):
            raise IruError("Unexpected blueprint record shape.")
        return record
    payload = await client.get_json(
        f"{API_PREFIX}/blueprints", params={"name": ref, "limit": MAX_CANDIDATES * 3},
    )
    rows = [r for r in page_items(payload, "/blueprints") if isinstance(r, dict)]
    match, rest = _pick_match(rows, ref, "name")
    if match is not None:
        return match
    if not rest:
        raise IruLookupError(
            "iru_unknown_blueprint", f"No blueprint matches {ref!r} (by id or name).",
        )
    raise IruLookupError(
        "iru_ambiguous_blueprint",
        f"{len(rest)} blueprints match {ref!r}; pass the blueprint id or exact name.",
        candidates=[blueprint_row(b) for b in rest[:MAX_CANDIDATES]],
    )


async def resolve_user(client: IruClient, ref: str) -> dict:
    """The directory user for a user id or an exact email address."""
    ref = (ref or "").strip()
    if not ref:
        raise IruLookupError("invalid_arguments", "A user reference is required.")
    if is_uuid(ref):
        try:
            record = await client.get_json(f"{API_PREFIX}/users/{ref}")
        except IruError as exc:
            if exc.code == "iru_not_found":
                raise IruLookupError("iru_unknown_user", f"No user with id {ref!r}.") from exc
            raise
        if not isinstance(record, dict):
            raise IruError("Unexpected user record shape.")
        return record
    payload = await client.get_json(f"{API_PREFIX}/users", params={"email": ref})
    rows = [r for r in page_items(payload, "/users") if isinstance(r, dict)]
    match, rest = _pick_match(rows, ref, "email")
    if match is not None:
        return match
    if not rest:
        raise IruLookupError(
            "iru_unknown_user", f"No user matches {ref!r} (by id or email).",
        )
    raise IruLookupError(
        "iru_ambiguous_user",
        f"{len(rest)} users match {ref!r}; pass the user id or the exact email.",
        candidates=[user_row(u) for u in rest[:MAX_CANDIDATES]],
    )
