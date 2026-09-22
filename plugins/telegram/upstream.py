"""Telegram upstream access helpers (plugin module).

Everything that talks to Telegram lives here: the admin MTProto app
credentials (``api_id`` / ``api_hash`` from my.telegram.org, in the
``telegram`` credential-store entry with the legacy ``telegram_app_info``
section of server_config.json migrated by the plugin's ``post_load``
hook), the per-user Telethon session (a ``user_service_credentials`` row
with ``oauth_blob`` JSON ``{"session": "<StringSession>", ...}`` -- see
plugins/telegram/auth.py for how it gets there), the
:class:`TelegramClientManager` singleton that keeps one connected
Telethon client per user for the life of the process, and the plain
async read functions the ``telegram_*`` tools wrap.

There is no OAuth: the user proves ownership of a phone number through
Telegram's own login protocol (code texted to the app, optional 2FA
password). The plugin still declares an ``oauth``-kind user connection
because that is the manifest shape that mounts a plugin-owned,
session-cookie-authed router under ``/auth/telegram``.

The read functions raise ``HTTPException`` (the historical error channel,
with structured ``{"error": <code>, "message": ...}`` details) and the tool
handlers in plugins/telegram/tools.py render that as JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest

from config.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

SERVICE = "telegram"

# Telegram historically kept its MTProto app credentials in the
# ``telegram_app_info`` section of server_config.json -- the general server
# CONFIG file, not a credentials file. The plugin's post_load hook copies
# it into the per-service store; this fallback keeps a deployment working
# if that copy could not be written (e.g. the encryption key was not
# available at load time).
LEGACY_SERVER_CONFIG_FILE = PROJECT_ROOT / "server_config.json"
LEGACY_SECTION = "telegram_app_info"

MISSING_CREDENTIALS_ERROR = (
    "Telegram API credentials not configured. Set them in "
    "Settings > Service Credentials (admin) or in the "
    "'telegram_app_info' section of server_config.json."
)


# ---------------------------------------------------------------------------
# Admin (server-level) configuration
# ---------------------------------------------------------------------------

def normalize_app_info(config: dict) -> dict:
    """Project a stored/legacy config into the store shape (strings only).

    The legacy section holds ``api_id`` as a JSON integer; the schema-driven
    admin form works in strings, so the stored shape is ``{"api_id": "<digits>",
    "api_hash": "<hex>"}``. Unknown keys are dropped.
    """
    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    return {
        "api_id": "" if api_id is None else str(api_id).strip(),
        "api_hash": "" if api_hash is None else str(api_hash).strip(),
    }


def telegram_is_configured(config: dict) -> bool:
    """Admin ``is_configured`` predicate: both app credentials present."""
    return bool(config.get("api_id") and config.get("api_hash"))


def validate_telegram_credentials(values: dict) -> dict:
    """Admin-save validation hook: the api_id must be an integer."""
    api_id = str(values.get("api_id") or "").strip()
    if not api_id.isdigit():
        raise ValueError("API ID must be a number (the api_id from my.telegram.org).")
    return {**values, "api_id": api_id}


def read_legacy_app_info() -> dict | None:
    """The legacy ``telegram_app_info`` section, or None when absent/malformed."""
    path = LEGACY_SERVER_CONFIG_FILE
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable legacy server config file: %s", path)
        return None
    section = data.get(LEGACY_SECTION) if isinstance(data, dict) else None
    if not isinstance(section, dict) or not section:
        return None
    return section


def load_telegram_credentials() -> dict:
    """Load the Telegram MTProto app credentials (api_id/api_hash).

    Prefers the per-service credential store (data/service_credentials/
    telegram.json, managed via the admin Settings UI) and falls back to
    the legacy ``telegram_app_info`` section of server_config.json. Store
    reads are fresh on every call so admin updates take effect without a
    restart.

    Returns:
        dict with 'api_id' (int) and 'api_hash' (str) keys

    Raises:
        HTTPException: If credentials are missing or invalid
    """
    from config.service_credentials import read_service_credentials

    config = read_service_credentials(SERVICE) or read_legacy_app_info()
    normalized = normalize_app_info(config) if config else None
    if not normalized or not telegram_is_configured(normalized):
        raise HTTPException(status_code=500, detail=MISSING_CREDENTIALS_ERROR)
    if not normalized["api_id"].isdigit():
        raise HTTPException(
            status_code=500,
            detail="Telegram api_id must be an integer.",
        )
    return {"api_id": int(normalized["api_id"]), "api_hash": normalized["api_hash"]}


def migrate_legacy_credentials() -> bool:
    """``post_load`` hook: move the legacy config section into the store.

    Copies server_config.json's ``telegram_app_info`` section into the
    ``telegram`` store file when the store has nothing yet, and rewrites a
    store file whose ``api_id`` is still a JSON integer (a verbatim copy
    made by an older release) into the string form the admin form expects.
    The legacy file is never modified. Returns whether anything was
    written. Runs at plugin load; a failure (e.g. the encryption key not
    yet unlocked on a manual uvicorn start) is logged by the loader and
    the loader falls back to the legacy read.
    """
    from config.service_credentials import (
        read_service_credentials,
        write_service_credentials,
    )

    stored = read_service_credentials(SERVICE)
    if stored is not None:
        normalized = normalize_app_info(stored)
        if normalized != {k: stored.get(k) for k in normalized}:
            write_service_credentials(SERVICE, normalized)
            logger.info("Normalized the stored Telegram credentials (api_id as string)")
            return True
        return False
    legacy = read_legacy_app_info()
    if legacy is None:
        return False
    write_service_credentials(SERVICE, normalize_app_info(legacy))
    logger.info(
        "Migrated Telegram credentials from server_config.json's "
        "telegram_app_info section into the per-service store",
    )
    return True


# ---------------------------------------------------------------------------
# Telethon client construction
# ---------------------------------------------------------------------------

def create_telegram_client(session_string: str = "") -> TelegramClient:
    """Create a Telethon TelegramClient with StringSession (not yet connected)."""
    creds = load_telegram_credentials()
    return TelegramClient(
        StringSession(session_string),
        creds["api_id"],
        creds["api_hash"],
    )


# ---------------------------------------------------------------------------
# Per-user connection (Telethon session)
# ---------------------------------------------------------------------------

def get_user_telegram_blob(user: dict) -> Optional[dict]:
    """The user's stored Telegram blob, or None when no row exists.

    Reads the ``user_service_credentials`` row attached to the user dict
    by db/user_store.py (service key ``telegram``). The blob carries the
    authorized ``session`` string and, mid-login, a ``pending`` entry.
    """
    rows = user.get("service_credentials") or {}
    blob = (rows.get(SERVICE) or {}).get("oauth_blob")
    return blob if isinstance(blob, dict) else None


def get_telegram_session(user: dict) -> Optional[str]:
    """The user's authorized Telethon StringSession, or None when not connected."""
    blob = get_user_telegram_blob(user) or {}
    session = blob.get("session")
    return session if isinstance(session, str) and session else None


def telegram_connected(row: dict) -> bool:
    """``UserConnectionSpec.connected`` hook: an authorized session is stored.

    A ``pending`` login (code sent, not yet verified) never counts.
    """
    blob = row.get("oauth_blob") or {}
    return bool(isinstance(blob, dict) and blob.get("session"))


def _not_connected() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": "telegram_not_connected",
            "message": (
                "Telegram not connected. Connect it via "
                "Settings > Data Connections > Telegram."
            ),
        },
    )


def _session_expired() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": "telegram_session_expired",
            "message": (
                "Telegram session expired. Reconnect it via "
                "Settings > Data Connections > Telegram."
            ),
        },
    )


class TelegramClientManager:
    """Singleton manager for persistent per-user Telegram client connections.

    Clients are created lazily on first use, keyed by user id, reused
    while connected, dropped when the user reconnects or disconnects
    (:meth:`drop_client`), and all closed on server shutdown via the
    plugin's ``on_shutdown`` hook (:meth:`close_all`).
    """

    _instance: Optional["TelegramClientManager"] = None
    _clients: dict[int, TelegramClient]
    _locks: dict[int, asyncio.Lock]

    def __new__(cls) -> "TelegramClientManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._clients = {}
            cls._instance._locks = {}
        return cls._instance

    @classmethod
    def get_instance(cls) -> "TelegramClientManager":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    async def get_client(self, user: dict) -> TelegramClient:
        """Get a connected Telegram client for a user.

        Raises:
            HTTPException: ``telegram_not_connected`` (no stored session),
                ``telegram_session_expired`` (Telegram rejects the
                session), or ``telegram_api_error`` (connection failure).
        """
        session_string = get_telegram_session(user)
        if not session_string:
            raise _not_connected()
        user_id = int(user["id"])

        lock = self._get_lock(user_id)
        async with lock:
            existing = self._clients.get(user_id)
            if existing is not None:
                if existing.is_connected():
                    return existing
                del self._clients[user_id]

            client = create_telegram_client(session_string)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    raise _session_expired()
                self._clients[user_id] = client
                return client
            except HTTPException:
                raise
            except AuthKeyUnregisteredError:
                raise _session_expired()
            except Exception as e:
                if client.is_connected():
                    await client.disconnect()
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "telegram_api_error",
                        "message": f"Failed to connect to Telegram: {str(e)}",
                    },
                )

    async def drop_client(self, user_id: int) -> None:
        """Disconnect and forget a user's cached client (reconnect/disconnect)."""
        client = self._clients.pop(int(user_id), None)
        if client is None:
            return
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            logger.debug("Ignoring error while dropping Telegram client", exc_info=True)

    async def close_all(self) -> None:
        """Close all client connections. Called on server shutdown."""
        for client in list(self._clients.values()):
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass  # Ignore errors during cleanup
        self._clients.clear()
        self._locks.clear()


async def close_all_clients() -> None:
    """The plugin's ``on_shutdown`` hook."""
    await TelegramClientManager.get_instance().close_all()


async def _connected_client(user: dict) -> TelegramClient:
    """Resolve the caller's connected Telethon client via the manager."""
    return await TelegramClientManager.get_instance().get_client(user)


# ---------------------------------------------------------------------------
# Read functions (wrapped by the telegram_* tools in plugins/telegram/tools.py)
# ---------------------------------------------------------------------------

def _api_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "telegram_api_error", "message": message},
    )


async def get_me(user: dict) -> dict:
    """The authenticated user's Telegram profile (id, username, names, phone)."""
    client = await _connected_client(user)
    try:
        me = await client.get_me()
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
            "is_premium": getattr(me, "premium", False),
        }
    except Exception as e:
        raise _api_error(f"Failed to get user info: {str(e)}")


async def list_dialogs(user: dict, limit: int = 20) -> dict:
    """The user's dialogs (chats, groups, channels), most recent first."""
    client = await _connected_client(user)
    try:
        dialogs = await client.get_dialogs(limit=limit)
        result = []
        for dialog in dialogs:
            result.append({
                "id": dialog.id,
                "name": dialog.name,
                "is_user": dialog.is_user,
                "is_group": dialog.is_group,
                "is_channel": dialog.is_channel,
                "unread_count": dialog.unread_count,
                "last_message_date": dialog.date.isoformat() if dialog.date else None,
            })
        return {"dialogs": result}
    except Exception as e:
        raise _api_error(f"Failed to list dialogs: {str(e)}")


def parse_iso8601_date(date_str: str, param_name: str) -> datetime:
    """Parse an ISO8601 datetime string (``Z`` accepted; naive = UTC)."""
    try:
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_date",
                "message": f"{param_name} must be ISO8601 format (e.g., 2024-01-15T10:30:00Z)",
            },
        )


async def get_messages(
    user: dict,
    dialog_id: int,
    limit: int = 50,
    offset_id: int = 0,
    offset_date: Optional[str] = None,
    reverse: bool = False,
) -> dict:
    """Messages from one dialog, newest first unless ``reverse``.

    ``offset_id`` pages by message id; ``offset_date`` (ISO8601) returns
    only messages before that time (exclusive). The date is parsed before
    connecting so a bad value never costs a Telegram round trip.
    """
    parsed_offset_date = None
    if offset_date:
        parsed_offset_date = parse_iso8601_date(offset_date, "offset_date")

    client = await _connected_client(user)
    try:
        messages = await client.get_messages(
            dialog_id,
            limit=limit,
            offset_id=offset_id,
            offset_date=parsed_offset_date,
            reverse=reverse,
        )
        result = []
        for msg in messages:
            result.append({
                "id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "text": msg.text,
                "sender_id": msg.sender_id,
                "is_outgoing": msg.out,
                "reply_to_msg_id": msg.reply_to_msg_id if msg.reply_to else None,
                "has_media": msg.media is not None,
            })
        return {"messages": result}
    except Exception as e:
        raise _api_error(f"Failed to get messages: {str(e)}")


async def list_contacts(user: dict) -> dict:
    """The user's Telegram contacts (id, username, names, phone)."""
    client = await _connected_client(user)
    try:
        contacts_result = await client(GetContactsRequest(hash=0))
        result = []
        for contact in contacts_result.users:
            result.append({
                "id": contact.id,
                "username": contact.username,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "phone": getattr(contact, "phone", None),
            })
        return {"contacts": result}
    except Exception as e:
        raise _api_error(f"Failed to list contacts: {str(e)}")


async def resolve_dialog_name(dialog_id: int, user: dict) -> str | None:
    """Resolve a dialog id to a display name (user name or group/channel title).

    Never raises: returns None when the user is not connected or the
    lookup fails, so the approval card falls back to the raw id.
    """
    try:
        client = await _connected_client(user)
        entity = await client.get_entity(dialog_id)
        if hasattr(entity, "first_name"):
            name = entity.first_name or ""
            if getattr(entity, "last_name", None):
                name = f"{name} {entity.last_name}".strip()
            return name or None
        if hasattr(entity, "title"):
            return entity.title or None
    except Exception:
        logger.warning("[resolve_dialog_name] Failed to resolve %s", dialog_id, exc_info=True)
    return None
