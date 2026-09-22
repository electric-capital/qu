"""Phone-number verification + template routes (plugin-provided router).

The Twilio plugin's ``oauth``-kind user connection has no OAuth provider:
the user types their own mobile number, Quest texts a 6-digit code to it,
and the user types the code back. The router lives under ``/auth/twilio``
(the namespace the loader confines a plugin router to) and is mounted by
quest.py's ``mount_plugin_oauth_routers()``:

- ``GET  /auth/twilio?popup=1`` -- the self-contained verification page
  the Data Connections "Connect" button opens in a popup (phone step,
  then code step; on success it posts the shared
  ``oauth_callback_success`` message to the opener and closes).
- ``POST /auth/twilio/start``  -- ``{"phone_number"}``: text a code.
- ``POST /auth/twilio/verify`` -- ``{"code"}``: check it, store the number.
- ``POST /auth/twilio/disconnect`` -- forget the number.
- ``GET/PUT /auth/twilio/templates`` -- the user's pre-written SMS
  messages (Settings > SMS Messages), stored in ``users.settings``.

Every route is session-cookie authed like the core OAuth flows. The
pending verification (salted code hash, expiry, attempt counter) lives in
the user's ``user_service_credentials`` row ``oauth_blob`` under
``pending`` -- server-side (a client-readable cookie would let the user
brute-force a 6-digit code offline and "verify" someone else's number)
and restart-safe (no in-process state). A pending entry never counts as
connected: ``twilio_connected`` only looks at ``phone_number``.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from auth.config import COOKIE_NAME
from auth.session import get_user_from_cookie
from db.user_service_credential_store import (
    delete_credential,
    get_credential,
    upsert_credential,
)
from db.user_store import update_user_settings

from plugins.twilio.templates import (
    MAX_TEMPLATES,
    TEMPLATES_SETTINGS_KEY,
    get_configured_templates,
    validate_templates,
)
from plugins.twilio.upstream import (
    TwilioError,
    get_user_phone_number,
    is_trusted_channel,
    load_twilio_config,
    mask_phone_number,
    normalize_phone_number,
    send_sms,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

CODE_LENGTH = 6
CODE_TTL_SECONDS = 600       # a code is good for 10 minutes
RESEND_COOLDOWN_SECONDS = 60  # one text per minute per user
MAX_ATTEMPTS = 5              # wrong guesses before the code is voided


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


def generate_code() -> str:
    """A zero-padded 6-digit code from the CSPRNG."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def code_matches(pending: dict, code: str) -> bool:
    expected = str(pending.get("code_hash") or "")
    actual = hash_code(code, str(pending.get("salt") or ""))
    return bool(expected) and hmac.compare_digest(expected, actual)


async def _current_blob(user_id: int) -> dict:
    row = await get_credential(user_id, "twilio")
    blob = (row or {}).get("oauth_blob")
    return dict(blob) if isinstance(blob, dict) else {}


# ---------------------------------------------------------------------------
# Verification flow
# ---------------------------------------------------------------------------

@router.post("/twilio/start")
async def twilio_start_verification(request: Request):
    """Text a verification code to the number the user typed.

    Body: ``{"phone_number": "+15551234567"}``. The number must carry a
    country code (never guessed). One text per user per minute; a new
    request replaces any earlier pending code.
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    try:
        phone_number = normalize_phone_number(payload.get("phone_number"))
    except ValueError as exc:
        return _error(400, "invalid_phone_number", str(exc))

    try:
        config = load_twilio_config()
    except HTTPException as exc:
        return _error(503, "twilio_not_configured", str(exc.detail))

    blob = await _current_blob(user["id"])
    pending = blob.get("pending") if isinstance(blob.get("pending"), dict) else None
    if pending:
        sent_at = _parse_ts(pending.get("sent_at"))
        if sent_at and (_now() - sent_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
            wait = RESEND_COOLDOWN_SECONDS - int((_now() - sent_at).total_seconds())
            return _error(
                429, "resend_too_soon",
                f"A code was just sent. Wait {max(wait, 1)} seconds before requesting another.",
            )

    code = generate_code()
    salt = secrets.token_hex(16)
    try:
        await send_sms(
            phone_number,
            f"Your Quest verification code is {code}. It expires in "
            f"{CODE_TTL_SECONDS // 60} minutes.",
            config,
        )
    except TwilioError as exc:
        logger.warning("[Twilio verify] send failed for user %s: %s", user["email"], exc)
        return _error(502, "sms_send_failed", str(exc))

    blob["pending"] = {
        "phone_number": phone_number,
        "code_hash": hash_code(code, salt),
        "salt": salt,
        "sent_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(seconds=CODE_TTL_SECONDS)).isoformat(),
        "attempts": 0,
    }
    await upsert_credential(user["id"], "twilio", oauth_blob=blob)
    logger.info("[Twilio verify] code sent (user=%s)", user["email"])
    return {
        "success": True,
        "phone_number_masked": mask_phone_number(phone_number),
        "expires_in": CODE_TTL_SECONDS,
    }


@router.post("/twilio/verify")
async def twilio_verify_code(request: Request):
    """Check the typed code; on success store the number as verified.

    Body: ``{"code": "123456"}``. Five wrong guesses or the 10-minute
    expiry void the pending code (the user requests a new one).
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    code = str(payload.get("code") or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != CODE_LENGTH:
        return _error(400, "invalid_code", f"Enter the {CODE_LENGTH}-digit code from the text message.")

    blob = await _current_blob(user["id"])
    pending = blob.get("pending") if isinstance(blob.get("pending"), dict) else None
    if not pending:
        return _error(400, "no_pending_verification", "Request a verification code first.")

    expires_at = _parse_ts(pending.get("expires_at"))
    if expires_at is None or _now() > expires_at:
        blob.pop("pending", None)
        await upsert_credential(user["id"], "twilio", oauth_blob=blob)
        return _error(400, "code_expired", "That code has expired. Request a new one.")

    if not code_matches(pending, code):
        attempts = int(pending.get("attempts") or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            blob.pop("pending", None)
            await upsert_credential(user["id"], "twilio", oauth_blob=blob)
            return _error(
                400, "too_many_attempts",
                "Too many incorrect codes. Request a new one.",
            )
        pending["attempts"] = attempts
        blob["pending"] = pending
        await upsert_credential(user["id"], "twilio", oauth_blob=blob)
        return _error(
            400, "incorrect_code",
            f"Incorrect code ({MAX_ATTEMPTS - attempts} attempts left).",
        )

    phone_number = pending["phone_number"]
    blob = {
        "phone_number": phone_number,
        "verified_at": _now().isoformat(),
    }
    await upsert_credential(user["id"], "twilio", oauth_blob=blob)
    logger.info("[Twilio verify] number verified (user=%s)", user["email"])

    # Invalidate cached chat sessions so the next message picks up the
    # new system prompt (Twilio skill/tools now advertised).
    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True, "phone_number": phone_number}


@router.post("/twilio/disconnect")
async def twilio_disconnect(request: Request):
    """Forget the verified number (and any pending verification)."""
    user = await _require_user(request)
    await delete_credential(user["id"], "twilio")
    logger.info("[Auth] Twilio phone disconnected (user=%s)", user["email"])

    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True}


# ---------------------------------------------------------------------------
# Pre-written messages (Settings > SMS Messages)
# ---------------------------------------------------------------------------

def _templates_view(user: dict) -> dict:
    try:
        trusted = is_trusted_channel(load_twilio_config())
    except HTTPException:
        trusted = False
    return {
        "templates": get_configured_templates(user.get("settings")),
        "trusted_channel": trusted,
        "phone_number": get_user_phone_number(user),
        "max_templates": MAX_TEMPLATES,
    }


@router.get("/twilio/templates")
async def twilio_get_templates(request: Request):
    """The user's pre-written SMS messages plus the trusted-channel state."""
    user = await _require_user(request)
    return _templates_view(user)


@router.put("/twilio/templates")
async def twilio_put_templates(request: Request):
    """Replace the user's pre-written SMS messages.

    Body: ``{"templates": [{"name": ..., "body": ...}, ...]}``. Whole-list
    replacement; an empty list clears them.
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    try:
        templates = validate_templates(payload.get("templates"))
    except ValueError as exc:
        return _error(400, "invalid_templates", str(exc))
    updated = await update_user_settings(user["email"], {TEMPLATES_SETTINGS_KEY: templates})
    if updated is None:
        return _error(404, "user_not_found", "User not found.")
    return _templates_view(updated)


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


_VERIFY_PAGE_BODY = """
<h1>Connect your phone</h1>
<p class="muted" id="current"></p>
<div id="step-phone">
  <p>Quest will text a verification code to your mobile number. Include the country code.</p>
  <label for="phone">Mobile number</label>
  <input id="phone" type="tel" placeholder="+15551234567" autocomplete="tel">
  <button id="send">Text me a code</button>
</div>
<div id="step-code" class="hidden">
  <p>Enter the 6-digit code we sent to <strong id="masked"></strong>.</p>
  <label for="code">Verification code</label>
  <input id="code" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code">
  <button id="verify">Verify</button>
  <button id="back" class="secondary">Use a different number</button>
</div>
<div id="step-done" class="hidden">
  <p>Your number <strong id="done-number"></strong> is verified. This window will close automatically.</p>
</div>
<p class="error hidden" id="error"></p>
<script>
(function () {
  var SERVICE = 'Twilio';
  var current = __CURRENT__;
  var $ = function (id) { return document.getElementById(id); };
  var errorEl = $('error');
  function showError(msg) { errorEl.textContent = msg; errorEl.classList.remove('hidden'); }
  function clearError() { errorEl.textContent = ''; errorEl.classList.add('hidden'); }
  function show(step) {
    ['step-phone', 'step-code', 'step-done'].forEach(function (id) {
      $(id).classList.toggle('hidden', id !== step);
    });
    clearError();
  }
  if (current) { $('current').textContent = 'Currently verified: ' + current + '. Verifying a new number replaces it.'; }
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
  $('send').onclick = async function () {
    var btn = $('send'); btn.disabled = true; clearError();
    try {
      var data = await post('/auth/twilio/start', { phone_number: $('phone').value });
      $('masked').textContent = data.phone_number_masked;
      show('step-code'); $('code').value = ''; $('code').focus();
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  $('verify').onclick = async function () {
    var btn = $('verify'); btn.disabled = true; clearError();
    try {
      var data = await post('/auth/twilio/verify', { code: $('code').value });
      $('done-number').textContent = data.phone_number;
      show('step-done');
      if (window.opener) {
        window.opener.postMessage({ type: 'oauth_callback_success', service: SERVICE }, window.location.origin);
        setTimeout(function () { window.close(); }, 800);
      } else {
        setTimeout(function () { window.location.href = '/'; }, 1500);
      }
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  $('back').onclick = function () { show('step-phone'); $('phone').focus(); };
  $('phone').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('send').click(); });
  $('code').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('verify').click(); });
  $('phone').focus();
})();
</script>
"""


def render_verify_page(current_number: str | None) -> str:
    """The popup page HTML; ``current_number`` is shown when already verified."""
    return _page(
        "Quest - Connect your phone",
        _VERIFY_PAGE_BODY.replace("__CURRENT__", json.dumps(current_number or "")),
    )


@router.get("/twilio")
async def twilio_connect_page(request: Request, popup: str = None):
    """Serve the phone-verification page (popup or full window)."""
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
        load_twilio_config()
    except HTTPException as exc:
        return HTMLResponse(_page(
            "Quest - Error",
            "<h1>Twilio not configured</h1>"
            f"<p class=\"error\">{html.escape(str(exc.detail))}</p>",
        ))
    return HTMLResponse(render_verify_page(get_user_phone_number(user)))
