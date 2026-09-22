"""UniFi Protect application helpers (plugin module).

Wraps the official Protect Integration API (``/proxy/protect/integration/v1``):
cameras (list / detail / JPEG snapshot) and the other Protect device
families (NVRs, sensors, lights, chimes, viewers, live views). Reads only;
snapshots are the one "action" and they change nothing on the NVR.

Every function takes an open :class:`~plugins.unifi.upstream.UnifiClient`
built with the user's PROTECT API key.
"""

from __future__ import annotations

from typing import Any, Optional

from plugins.unifi.upstream import (
    PROTECT_API_PREFIX,
    UnifiClient,
    UnifiError,
    _SNAPSHOT_TIMEOUT,
)

# Protect device families exposed by the Integration API, keyed by the
# ``kind`` argument of unifi_list_protect_devices.
PROTECT_DEVICE_KINDS: dict[str, str] = {
    "nvrs": "/nvrs",
    "sensors": "/sensors",
    "lights": "/lights",
    "chimes": "/chimes",
    "viewers": "/viewers",
    "liveviews": "/liveviews",
}

# Snapshot bytes above this are refused rather than attached to the model
# request (Anthropic's per-image cap is 5 MB; a high-quality 4K JPEG can
# approach it).
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024

_CAMERA_SUMMARY_KEYS = (
    ("id", "id"),
    ("name", "name"),
    ("state", "state"),
    ("modelKey", "model_key"),
    ("model", "model"),
    ("type", "type"),
    ("mac", "mac_address"),
    ("host", "ip_address"),
    ("lastSeen", "last_seen"),
    ("isMicEnabled", "mic_enabled"),
    ("isRecording", "recording"),
    ("isMotionDetected", "motion_detected"),
    ("videoMode", "video_mode"),
    ("hdrType", "hdr_type"),
    ("hdrMode", "hdr_mode"),
    ("activePatrolSlot", "active_patrol_slot"),
    ("firmwareVersion", "firmware_version"),
    ("isThirdPartyCamera", "third_party"),
)


def _lower(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def camera_view(camera: dict) -> dict:
    """Compact camera row: identity, connection state, headline settings."""
    view = {dst: camera[src] for src, dst in _CAMERA_SUMMARY_KEYS if src in camera}
    flags = camera.get("featureFlags")
    if isinstance(flags, dict):
        view["feature_flags"] = {
            k: v for k, v in flags.items()
            if isinstance(v, bool) and v
        }
    smart = camera.get("smartDetectSettings")
    if isinstance(smart, dict) and isinstance(smart.get("objectTypes"), list):
        view["smart_detect_object_types"] = smart["objectTypes"]
    return view


def is_camera_connected(camera: dict) -> bool:
    return _lower(camera.get("state")) == "connected"


async def list_cameras(client: UnifiClient) -> list[dict]:
    payload = await client.get_json(f"{PROTECT_API_PREFIX}/cameras")
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [c for c in payload["data"] if isinstance(c, dict)]
    raise UnifiError("Unexpected cameras payload from the Protect API.")


async def get_camera(client: UnifiClient, camera_id: str) -> dict:
    payload = await client.get_json(f"{PROTECT_API_PREFIX}/cameras/{camera_id}")
    if not isinstance(payload, dict):
        raise UnifiError("Unexpected camera payload from the Protect API.")
    return payload


def find_camera(cameras: list[dict], needle: str) -> Optional[dict]:
    """Match a camera by id or name (case-insensitive)."""
    wanted = _lower(needle)
    if not wanted:
        return None
    for camera in cameras:
        if wanted in (_lower(camera.get("id")), _lower(camera.get("name"))):
            return camera
    return None


async def get_snapshot(client: UnifiClient, camera_id: str, *,
                       high_quality: bool = False) -> tuple[bytes, str]:
    """A live JPEG from the camera; returns ``(bytes, content_type)``."""
    data, content_type = await client.get_bytes(
        f"{PROTECT_API_PREFIX}/cameras/{camera_id}/snapshot",
        params={"highQuality": "true" if high_quality else "false"},
        timeout=_SNAPSHOT_TIMEOUT,
        accept="image/jpeg",
    )
    if not data:
        raise UnifiError("The Protect API returned an empty snapshot.", code="unifi_empty_snapshot")
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise UnifiError(
            f"Snapshot is {len(data):,} bytes, above the "
            f"{MAX_SNAPSHOT_BYTES // (1024 * 1024)} MB limit; retry without high_quality.",
            code="unifi_snapshot_too_large",
        )
    if "json" in content_type.lower():
        raise UnifiError("The Protect API returned JSON instead of an image for the snapshot.")
    return data, content_type


async def get_protect_info(client: UnifiClient) -> dict:
    payload = await client.get_json(f"{PROTECT_API_PREFIX}/meta/info")
    return payload if isinstance(payload, dict) else {}


async def list_protect_devices(client: UnifiClient, kind: str) -> list[Any]:
    path = PROTECT_DEVICE_KINDS.get(kind)
    if path is None:
        raise UnifiError(
            f"Unknown Protect device kind {kind!r}; expected one of "
            f"{', '.join(PROTECT_DEVICE_KINDS)}.",
            code="invalid_arguments",
        )
    payload = await client.get_json(f"{PROTECT_API_PREFIX}{path}")
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        # ``/nvrs`` returns the single NVR object on some versions.
        return [payload]
    raise UnifiError(f"Unexpected payload from the Protect {kind} endpoint.")
