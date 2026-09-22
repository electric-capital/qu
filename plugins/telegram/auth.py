"""Telegram login flow (plugin-provided router under ``/auth/telegram``).

The Telegram plugin's ``oauth``-kind user connection has no OAuth
provider: the user logs in with Telegram's own protocol -- phone number,
the code Telegram sends to their app, and the cloud password when 2FA is
on. The router is mounted by quest.py's ``mount_plugin_oauth_routers()``:

- ``GET  /auth/telegram?popup=1`` -- the self-contained login page the
  Data Connections "Connect" button opens in a popup (phone step, code
  step, optional password step; on success it posts the shared
  ``oauth_callback_success`` message to the opener and closes).
- ``POST /auth/telegram/send-code`` -- ``{"phone"}``: ask Telegram to
  send a login code.
- ``POST /auth/telegram/verify`` -- ``{"code"}``: sign in with the code;
  answers ``needs_2fa: true`` when the account has a cloud password.
- ``POST /auth/telegram/2fa`` -- ``{"password"}``: finish a 2FA sign-in.
- ``POST /auth/telegram/disconnect`` -- log the session out and forget it.

Every route is session-cookie authed like the core OAuth flows. The
in-flight login (phone, ``phone_code_hash``, the not-yet-authorized
Telethon session string, the stage, expiry, and attempt counter) lives in
the user's ``user_service_credentials`` row ``oauth_blob`` under
``pending`` -- server-side and encrypted at rest (the pre-plugin flow kept
it in a signed but client-readable cookie), restart-safe, and never
counted as connected: ``telegram_connected`` only looks at ``session``.
An existing authorized ``session`` survives a failed re-login because
``pending`` is written next to it and only replaces it on success.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from auth.config import COOKIE_NAME
from auth.session import get_user_from_cookie
from db.user_service_credential_store import (
    delete_credential,
    get_credential,
    upsert_credential,
)

from plugins.telegram.upstream import (
    SERVICE,
    TelegramClientManager,
    create_telegram_client,
    get_telegram_session,
    get_user_telegram_blob,
    load_telegram_credentials,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

PENDING_TTL_SECONDS = 600  # a login code is good for 10 minutes
MAX_ATTEMPTS = 5           # wrong codes/passwords before the login is voided

STAGE_CODE = "code"
STAGE_PASSWORD = "password"

# E.164: '+' then 7-15 digits, first digit non-zero.
_E164_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")
# Characters people paste into phone numbers that carry no information.
_PHONE_NOISE_RE = re.compile(r"[\s().\-]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _require_user(request: Request) -> dict:
    signed_cookie = request.cookies.get(COOKIE_NAME)
    user = await get_user_from_cookie(signed_cookie) if signed_cookie else None
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "not_authenticated", "message": "Not authenticated"},
        )
    return user


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


async def _read_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_json", "message": "Request body must be JSON."},
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_json", "message": "Request body must be a JSON object."},
        )
    return payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def normalize_phone_number(raw) -> str:
    """Normalize a typed phone number to E.164, or raise ValueError.

    The country code is never guessed: a number without one is rejected
    so the login code cannot go to a stranger in a different country.
    """
    if not isinstance(raw, str):
        raise ValueError("Phone number must be a string.")
    cleaned = _PHONE_NOISE_RE.sub("", raw.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        raise ValueError(
            "Phone number must include the country code and start with '+', "
            "e.g. +15551234567."
        )
    if not _E164_RE.match(cleaned):
        raise ValueError(
            "Phone number must be in international format: '+' followed by "
            "7-15 digits, e.g. +15551234567."
        )
    return cleaned


def mask_phone_number(number: str | None) -> str:
    """Render a phone number for display: last 4 digits only."""
    if not number:
        return ""
    digits = number.lstrip("+")
    tail = digits[-4:]
    return "+" + "•" * max(len(digits) - len(tail), 0) + tail


async def _current_blob(user_id: int) -> dict:
    row = await get_credential(user_id, SERVICE)
    blob = (row or {}).get("oauth_blob")
    return dict(blob) if isinstance(blob, dict) else {}


def _pending_of(blob: dict) -> dict | None:
    pending = blob.get("pending")
    return pending if isinstance(pending, dict) else None


async def _void_pending(user_id: int, blob: dict) -> None:
    blob.pop("pending", None)
    if blob:
        await upsert_credential(user_id, SERVICE, oauth_blob=blob)
    else:
        await delete_credential(user_id, SERVICE)


async def _load_live_pending(user_id: int, stage: str) -> tuple[dict, dict] | JSONResponse:
    """The (blob, pending) pair for the expected stage, or an error response."""
    blob = await _current_blob(user_id)
    pending = _pending_of(blob)
    if not pending:
        return _error(400, "no_pending_login", "Request a login code first.")
    expires_at = _parse_ts(pending.get("expires_at"))
    if expires_at is None or _now() > expires_at:
        await _void_pending(user_id, blob)
        return _error(400, "code_expired", "That login attempt has expired. Start again.")
    if pending.get("stage") != stage:
        if stage == STAGE_CODE:
            return _error(400, "password_required", "Enter your Telegram cloud password to finish signing in.")
        return _error(400, "code_required", "Enter the login code first.")
    return blob, pending


async def _count_failed_attempt(user_id: int, blob: dict, pending: dict, code: str, message: str):
    attempts = int(pending.get("attempts") or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        await _void_pending(user_id, blob)
        return _error(400, "too_many_attempts", "Too many incorrect attempts. Start again.")
    pending["attempts"] = attempts
    blob["pending"] = pending
    await upsert_credential(user_id, SERVICE, oauth_blob=blob)
    return _error(400, code, f"{message} ({MAX_ATTEMPTS - attempts} attempts left).")


async def _finish_login(user: dict, blob: dict, pending: dict, client) -> dict:
    """Persist the authorized session and refresh the user's runtime state."""
    final_session = client.session.save()
    await client.disconnect()
    new_blob = {
        "session": final_session,
        "phone": pending.get("phone"),
        "connected_at": _now().isoformat(),
    }
    await upsert_credential(user["id"], SERVICE, oauth_blob=new_blob)
    logger.info("[Telegram] User authenticated (user=%s)", user["email"])

    # A cached client (from a previous session) must not outlive the login.
    await TelegramClientManager.get_instance().drop_client(user["id"])
    # Invalidate cached chat sessions so the next message picks up the
    # new system prompt (Telegram skill/tools now advertised).
    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True, "needs_2fa": False, "phone_masked": mask_phone_number(pending.get("phone"))}


def _flood_wait(exc: FloodWaitError) -> JSONResponse:
    seconds = int(getattr(exc, "seconds", 0) or 0)
    return _error(
        429, "flood_wait",
        f"Telegram asks you to wait {seconds} seconds before trying again."
        if seconds else "Telegram asks you to wait before trying again.",
    )


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

@router.post("/telegram/send-code")
async def telegram_send_code(request: Request):
    """Ask Telegram to send a login code to the typed phone number.

    Body: ``{"phone": "+15551234567"}``. A new request replaces any earlier
    pending login; an existing authorized session is left untouched until
    the new login succeeds.
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    try:
        phone = normalize_phone_number(payload.get("phone"))
    except ValueError as exc:
        return _error(400, "invalid_phone_number", str(exc))

    try:
        load_telegram_credentials()
    except HTTPException as exc:
        return _error(503, "telegram_not_configured", str(exc.detail))

    client = create_telegram_client("")
    try:
        await client.connect()
        result = await client.send_code_request(phone)
        session_string = client.session.save()
    except FloodWaitError as exc:
        return _flood_wait(exc)
    except (PhoneNumberInvalidError, PhoneNumberBannedError) as exc:
        return _error(400, "invalid_phone_number", f"Telegram rejected the phone number: {exc}")
    except Exception as exc:
        logger.warning("[Telegram] Error sending code (user=%s): %s", user["email"], exc)
        return _error(502, "telegram_error", f"Failed to send verification code: {exc}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    blob = await _current_blob(user["id"])
    blob["pending"] = {
        "phone": phone,
        "phone_code_hash": result.phone_code_hash,
        "session": session_string,
        "stage": STAGE_CODE,
        "started_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(seconds=PENDING_TTL_SECONDS)).isoformat(),
        "attempts": 0,
    }
    await upsert_credential(user["id"], SERVICE, oauth_blob=blob)
    logger.info("[Telegram] Login code requested (user=%s)", user["email"])
    return {
        "success": True,
        "phone_masked": mask_phone_number(phone),
        "expires_in": PENDING_TTL_SECONDS,
    }


@router.post("/telegram/verify")
async def telegram_verify_code(request: Request):
    """Sign in with the code Telegram sent.

    Body: ``{"code": "12345"}``. Returns ``needs_2fa: true`` (and moves the
    pending login to the password stage) when the account has a cloud
    password; otherwise the session is stored and the login is complete.
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    code = str(payload.get("code") or "").strip().replace(" ", "").replace("-", "")
    if not code.isdigit():
        return _error(400, "invalid_code", "Enter the numeric login code from your Telegram app.")

    loaded = await _load_live_pending(user["id"], STAGE_CODE)
    if isinstance(loaded, JSONResponse):
        return loaded
    blob, pending = loaded

    client = create_telegram_client(str(pending.get("session") or ""))
    try:
        await client.connect()
        try:
            await client.sign_in(
                pending["phone"], code, phone_code_hash=pending.get("phone_code_hash"),
            )
        except SessionPasswordNeededError:
            pending["session"] = client.session.save()
            pending["stage"] = STAGE_PASSWORD
            pending["attempts"] = 0
            blob["pending"] = pending
            await upsert_credential(user["id"], SERVICE, oauth_blob=blob)
            return {"success": True, "needs_2fa": True}
        except PhoneCodeInvalidError:
            return await _count_failed_attempt(
                user["id"], blob, pending, "incorrect_code", "Incorrect code",
            )
        except PhoneCodeExpiredError:
            await _void_pending(user["id"], blob)
            return _error(400, "code_expired", "That code has expired. Request a new one.")
        except FloodWaitError as exc:
            return _flood_wait(exc)
        except Exception as exc:
            logger.warning("[Telegram] Error during verification (user=%s): %s", user["email"], exc)
            return _error(502, "telegram_error", f"Verification failed: {exc}")
        return await _finish_login(user, blob, pending, client)
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass


@router.post("/telegram/2fa")
async def telegram_verify_password(request: Request):
    """Finish a sign-in that needs the account's cloud (2FA) password.

    Body: ``{"password": "..."}``.
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        return _error(400, "invalid_password", "Enter your Telegram cloud password.")

    loaded = await _load_live_pending(user["id"], STAGE_PASSWORD)
    if isinstance(loaded, JSONResponse):
        return loaded
    blob, pending = loaded

    client = create_telegram_client(str(pending.get("session") or ""))
    try:
        await client.connect()
        try:
            await client.sign_in(password=password)
        except PasswordHashInvalidError:
            return await _count_failed_attempt(
                user["id"], blob, pending, "incorrect_password", "Incorrect password",
            )
        except FloodWaitError as exc:
            return _flood_wait(exc)
        except Exception as exc:
            logger.warning("[Telegram] Error during 2FA (user=%s): %s", user["email"], exc)
            return _error(502, "telegram_error", f"2FA verification failed: {exc}")
        return await _finish_login(user, blob, pending, client)
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass


@router.post("/telegram/disconnect")
async def telegram_disconnect(request: Request):
    """Log the Telegram session out (best effort) and forget it."""
    user = await _require_user(request)
    manager = TelegramClientManager.get_instance()
    await manager.drop_client(user["id"])

    session = get_telegram_session(user)
    if session:
        try:
            client = create_telegram_client(session)
            await client.connect()
            try:
                await client.log_out()
            finally:
                if client.is_connected():
                    await client.disconnect()
        except Exception:
            logger.info(
                "[Telegram] Best-effort log_out failed (user=%s)", user["email"], exc_info=True,
            )

    await delete_credential(user["id"], SERVICE)
    logger.info("[Auth] Telegram disconnected (user=%s)", user["email"])

    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True}


# ---------------------------------------------------------------------------
# The popup page
# ---------------------------------------------------------------------------

def _page(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 420px; margin: 40px auto; padding: 0 20px; color: #222; }}
  h1 {{ font-size: 1.25rem; margin-bottom: 0.25rem; }}
  p {{ line-height: 1.45; }}
  label {{ display: block; font-size: 0.875rem; margin: 1rem 0 0.25rem; }}
  input {{ width: 100%; box-sizing: border-box; font-size: 1.1rem; padding: 0.5rem; border: 1px solid #bbb; border-radius: 6px; }}
  button {{ margin-top: 1rem; padding: 0.5rem 1rem; font-size: 0.95rem; border-radius: 6px; border: 1px solid #555; background: #333; color: #fff; cursor: pointer; }}
  button.secondary {{ background: transparent; color: #333; }}
  button:disabled {{ opacity: 0.5; cursor: default; }}
  .error {{ color: #c62828; font-size: 0.875rem; margin-top: 0.75rem; }}
  .muted {{ color: #666; font-size: 0.875rem; }}
  .hidden {{ display: none; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


_CONNECT_PAGE_BODY = """
<h1>Connect Telegram</h1>
<p class="muted" id="current"></p>
<div id="step-phone">
  <p>Enter the phone number of your Telegram account. Include the country code. Telegram will send a login code to your Telegram app.</p>
  <label for="phone">Phone number</label>
  <input id="phone" type="tel" placeholder="+15551234567" autocomplete="tel">
  <button id="send">Send login code</button>
</div>
<div id="step-code" class="hidden">
  <p>Enter the code Telegram sent to <strong id="masked"></strong> (check your Telegram app).</p>
  <label for="code">Login code</label>
  <input id="code" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="8" autocomplete="one-time-code">
  <button id="verify">Verify</button>
  <button id="back" class="secondary">Use a different number</button>
</div>
<div id="step-password" class="hidden">
  <p>Your account has two-step verification enabled. Enter your Telegram cloud password (not your device PIN).</p>
  <label for="password">Cloud password</label>
  <input id="password" type="password" autocomplete="current-password">
  <button id="submit-password">Sign in</button>
</div>
<div id="step-done" class="hidden">
  <p>Telegram is connected. This window will close automatically.</p>
</div>
<p class="error hidden" id="error"></p>
<script>
(function () {
  var SERVICE = 'Telegram';
  var current = __CURRENT__;
  var $ = function (id) { return document.getElementById(id); };
  var errorEl = $('error');
  function showError(msg) { errorEl.textContent = msg; errorEl.classList.remove('hidden'); }
  function clearError() { errorEl.textContent = ''; errorEl.classList.add('hidden'); }
  function show(step) {
    ['step-phone', 'step-code', 'step-password', 'step-done'].forEach(function (id) {
      $(id).classList.toggle('hidden', id !== step);
    });
    clearError();
  }
  if (current) { $('current').textContent = 'Currently connected as ' + current + '. Signing in again replaces that session.'; }
  async function post(path, body) {
    var resp = await fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    var data = {};
    try { data = await resp.json(); } catch (e) {}
    if (!resp.ok) { throw new Error(data.message || (data.detail && data.detail.message) || ('Request failed (' + resp.status + ')')); }
    return data;
  }
  function finish() {
    show('step-done');
    if (window.opener) {
      window.opener.postMessage({ type: 'oauth_callback_success', service: SERVICE }, window.location.origin);
      setTimeout(function () { window.close(); }, 800);
    } else {
      setTimeout(function () { window.location.href = '/'; }, 1500);
    }
  }
  $('send').onclick = async function () {
    var btn = $('send'); btn.disabled = true; clearError();
    try {
      var data = await post('/auth/telegram/send-code', { phone: $('phone').value });
      $('masked').textContent = data.phone_masked;
      show('step-code'); $('code').value = ''; $('code').focus();
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  $('verify').onclick = async function () {
    var btn = $('verify'); btn.disabled = true; clearError();
    try {
      var data = await post('/auth/telegram/verify', { code: $('code').value });
      if (data.needs_2fa) { show('step-password'); $('password').value = ''; $('password').focus(); }
      else { finish(); }
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  $('submit-password').onclick = async function () {
    var btn = $('submit-password'); btn.disabled = true; clearError();
    try {
      await post('/auth/telegram/2fa', { password: $('password').value });
      finish();
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  $('back').onclick = function () { show('step-phone'); $('phone').focus(); };
  $('phone').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('send').click(); });
  $('code').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('verify').click(); });
  $('password').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('submit-password').click(); });
  $('phone').focus();
})();
</script>
"""


def render_connect_page(current_phone_masked: str | None) -> str:
    """The popup page HTML; ``current_phone_masked`` is shown when already connected."""
    return _page(
        "Quest - Connect Telegram",
        _CONNECT_PAGE_BODY.replace("__CURRENT__", json.dumps(current_phone_masked or "", ensure_ascii=False)),
    )


@router.get("/telegram")
async def telegram_connect_page(request: Request, popup: str = None):
    """Serve the Telegram login page (popup or full window)."""
    signed_cookie = request.cookies.get(COOKIE_NAME)
    user = await get_user_from_cookie(signed_cookie) if signed_cookie else None
    if not user:
        return HTMLResponse(_page(
            "Quest - Error",
            "<h1>Authentication Required</h1>"
            "<p>Please sign in to Quest first.</p>"
            '<p><a href="/">Sign in</a></p>',
        ))
    try:
        load_telegram_credentials()
    except HTTPException as exc:
        return HTMLResponse(_page(
            "Quest - Error",
            "<h1>Telegram not configured</h1>"
            f"<p class=\"error\">{html.escape(str(exc.detail))}</p>",
        ))
    current = None
    if get_telegram_session(user):
        current = mask_phone_number((get_user_telegram_blob(user) or {}).get("phone")) or "an authorized session"
    return HTMLResponse(render_connect_page(current))
