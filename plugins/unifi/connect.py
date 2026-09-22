"""UniFi API-key entry routes (plugin-provided router).

The UniFi plugin's ``oauth``-kind user connection has no OAuth provider:
the user pastes the API key(s) they created inside the Network and/or
Protect application on their own controller. The generic ``api_key``
connection kind stores exactly one secret, but a UniFi console issues a
SEPARATE key per application, so the plugin uses the router shape (the
Twilio pattern) to host a small popup form with two fields. The router
lives under ``/auth/unifi`` and is mounted by quest.py's
``mount_plugin_oauth_routers()``:

- ``GET  /auth/unifi?popup=1``   -- the key-entry page the Data Connections
  "Connect" button opens in a popup (posts the shared
  ``oauth_callback_success`` message to the opener on save).
- ``POST /auth/unifi/keys``      -- ``{"network_api_key"?, "protect_api_key"?}``:
  each key is TESTED against the controller before it is stored; a
  missing/null field keeps the stored key, an empty string removes it.
- ``POST /auth/unifi/disconnect`` -- forget both keys.

Every route is session-cookie authed like the core OAuth flows. Keys are
stored in the user's ``user_service_credentials`` row ``oauth_blob``
(``{network_api_key, protect_api_key, verified_at, ...}``).
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from auth.config import COOKIE_NAME
from auth.session import get_user_from_cookie
from db.user_service_credential_store import (
    delete_credential,
    get_credential,
    upsert_credential,
)

from plugins.unifi.upstream import (
    NETWORK_KEY,
    PROTECT_KEY,
    SERVICE_ID,
    UnifiAuthError,
    UnifiError,
    load_unifi_config,
    mask_key,
    verify_network_key,
    verify_protect_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

MAX_KEY_LENGTH = 512


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _current_blob(user_id: int) -> dict:
    row = await get_credential(user_id, SERVICE_ID)
    blob = (row or {}).get("oauth_blob")
    return dict(blob) if isinstance(blob, dict) else {}


def _clean_key(raw) -> str | None:
    """``None`` -> keep; ``""`` -> remove; otherwise the trimmed key.

    Raises ValueError for a non-string or an absurdly long value.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("API keys must be strings.")
    key = raw.strip()
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError("That does not look like a UniFi API key (too long).")
    if any(ch.isspace() for ch in key):
        raise ValueError("API keys cannot contain whitespace.")
    return key


def connection_state(blob: dict) -> dict:
    """What the popup shows about the stored keys (masked)."""
    network = blob.get(NETWORK_KEY) if isinstance(blob.get(NETWORK_KEY), str) else None
    protect = blob.get(PROTECT_KEY) if isinstance(blob.get(PROTECT_KEY), str) else None
    return {
        "network_key_masked": mask_key(network) if network else None,
        "protect_key_masked": mask_key(protect) if protect else None,
        "network_version": blob.get("network_version"),
        "protect_version": blob.get("protect_version"),
        "verified_at": blob.get("verified_at"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/unifi/keys")
async def unifi_save_keys(request: Request):
    """Test and store the user's Network / Protect API keys.

    Body: ``{"network_api_key": ..., "protect_api_key": ...}`` -- omit
    (or ``null``) a field to keep the stored key, send ``""`` to remove
    it. Each new key is verified live against the controller before
    anything is written; at least one key must remain afterwards.
    """
    user = await _require_user(request)
    payload = await _read_json(request)
    try:
        network_key = _clean_key(payload.get(NETWORK_KEY))
        protect_key = _clean_key(payload.get(PROTECT_KEY))
    except ValueError as exc:
        return _error(400, "invalid_api_key", str(exc))

    try:
        config = load_unifi_config()
    except HTTPException as exc:
        return _error(503, "unifi_not_configured", str(exc.detail))

    blob = await _current_blob(user["id"])
    updated = {
        k: v for k, v in blob.items()
        if k in (NETWORK_KEY, PROTECT_KEY, "network_version", "protect_version")
    }

    if network_key is not None:
        if network_key == "":
            updated.pop(NETWORK_KEY, None)
            updated.pop("network_version", None)
        else:
            try:
                info = await verify_network_key(config, network_key)
            except UnifiAuthError:
                return _error(
                    400, "invalid_network_api_key",
                    "The Network application rejected that API key. Create one "
                    "in the Network app under Settings > Control Plane > "
                    "Integrations and paste it exactly.",
                )
            except UnifiError as exc:
                return _error(502, exc.code, str(exc))
            updated[NETWORK_KEY] = network_key
            updated["network_version"] = info.get("applicationVersion")

    if protect_key is not None:
        if protect_key == "":
            updated.pop(PROTECT_KEY, None)
            updated.pop("protect_version", None)
        else:
            try:
                info = await verify_protect_key(config, protect_key)
            except UnifiAuthError:
                return _error(
                    400, "invalid_protect_api_key",
                    "The Protect application rejected that API key. Create one "
                    "in the Protect app under Settings > Control Plane > "
                    "Integrations and paste it exactly.",
                )
            except UnifiError as exc:
                return _error(502, exc.code, str(exc))
            updated[PROTECT_KEY] = protect_key
            updated["protect_version"] = info.get("applicationVersion")

    if not updated.get(NETWORK_KEY) and not updated.get(PROTECT_KEY):
        return _error(
            400, "no_keys",
            "Enter at least one API key (Network and/or Protect).",
        )

    updated["verified_at"] = _now_iso()
    await upsert_credential(user["id"], SERVICE_ID, oauth_blob=updated)
    logger.info(
        "[UniFi connect] keys saved (user=%s, network=%s, protect=%s)",
        user["email"], bool(updated.get(NETWORK_KEY)), bool(updated.get(PROTECT_KEY)),
    )

    # Invalidate cached chat sessions so the next message picks up the
    # new system prompt (UniFi skill/tools now advertised).
    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True, **connection_state(updated)}


@router.post("/unifi/disconnect")
async def unifi_disconnect(request: Request):
    """Forget both API keys."""
    user = await _require_user(request)
    await delete_credential(user["id"], SERVICE_ID)
    logger.info("[Auth] UniFi keys removed (user=%s)", user["email"])

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
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 460px; margin: 40px auto; padding: 0 20px; color: #222; }}
  h1 {{ font-size: 1.25rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1rem; margin: 1.25rem 0 0.25rem; }}
  p {{ line-height: 1.45; }}
  label {{ display: block; font-size: 0.875rem; margin: 0.75rem 0 0.25rem; }}
  input[type=password], input[type=text] {{ width: 100%; box-sizing: border-box; font-size: 1rem; padding: 0.5rem; border: 1px solid #bbb; border-radius: 6px; font-family: ui-monospace, monospace; }}
  button {{ margin-top: 1rem; margin-right: 0.5rem; padding: 0.5rem 1rem; font-size: 0.95rem; border-radius: 6px; border: 1px solid #555; background: #333; color: #fff; cursor: pointer; }}
  button.secondary {{ background: transparent; color: #333; }}
  button.danger {{ background: transparent; color: #c62828; border-color: #c62828; }}
  button:disabled {{ opacity: 0.5; cursor: default; }}
  .error {{ color: #c62828; font-size: 0.875rem; margin-top: 0.75rem; }}
  .muted {{ color: #666; font-size: 0.875rem; }}
  .hidden {{ display: none; }}
  .remove {{ font-size: 0.8rem; color: #666; margin-top: 0.25rem; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


_CONNECT_PAGE_BODY = """
<h1>Connect UniFi</h1>
<p class="muted">Controller: <strong id="host"></strong></p>
<p>Paste the API key(s) you created on your controller. Each UniFi application issues its own key
(open the app, then <em>Settings &rsaquo; Control Plane &rsaquo; Integrations &rsaquo; Create API Key</em>).
You can add one now and the other later.</p>
<div id="form">
  <h2>Network</h2>
  <p class="muted" id="network-current"></p>
  <label for="network">Network API key</label>
  <input id="network" type="password" autocomplete="off" spellcheck="false" placeholder="Paste Network API key">
  <div class="remove hidden" id="network-remove-wrap"><label><input id="network-remove" type="checkbox"> Remove the stored Network key</label></div>

  <h2>Protect</h2>
  <p class="muted" id="protect-current"></p>
  <label for="protect">Protect API key</label>
  <input id="protect" type="password" autocomplete="off" spellcheck="false" placeholder="Paste Protect API key">
  <div class="remove hidden" id="protect-remove-wrap"><label><input id="protect-remove" type="checkbox"> Remove the stored Protect key</label></div>

  <button id="save">Test &amp; save</button>
  <button id="disconnect" class="danger hidden">Disconnect</button>
</div>
<div id="done" class="hidden">
  <p>UniFi is connected. This window will close automatically.</p>
</div>
<p class="error hidden" id="error"></p>
<script>
(function () {
  var SERVICE = 'UniFi';
  var state = __STATE__;
  var $ = function (id) { return document.getElementById(id); };
  var errorEl = $('error');
  function showError(msg) { errorEl.textContent = msg; errorEl.classList.remove('hidden'); }
  function clearError() { errorEl.textContent = ''; errorEl.classList.add('hidden'); }
  $('host').textContent = state.host || '';
  function renderCurrent() {
    var n = state.network_key_masked, p = state.protect_key_masked;
    $('network-current').textContent = n ? ('Stored key: ' + n + (state.network_version ? ' (Network ' + state.network_version + ')' : '') + '. Leave blank to keep it.') : 'Not connected.';
    $('protect-current').textContent = p ? ('Stored key: ' + p + (state.protect_version ? ' (Protect ' + state.protect_version + ')' : '') + '. Leave blank to keep it.') : 'Not connected.';
    $('network-remove-wrap').classList.toggle('hidden', !n);
    $('protect-remove-wrap').classList.toggle('hidden', !p);
    $('disconnect').classList.toggle('hidden', !(n || p));
  }
  renderCurrent();
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
    $('form').classList.add('hidden');
    $('done').classList.remove('hidden');
    if (window.opener) {
      window.opener.postMessage({ type: 'oauth_callback_success', service: SERVICE }, window.location.origin);
      setTimeout(function () { window.close(); }, 800);
    } else {
      setTimeout(function () { window.location.href = '/'; }, 1500);
    }
  }
  $('save').onclick = async function () {
    var btn = $('save'); btn.disabled = true; clearError();
    var body = {};
    var n = $('network').value.trim(), p = $('protect').value.trim();
    if ($('network-remove').checked) { body.network_api_key = ''; } else if (n) { body.network_api_key = n; }
    if ($('protect-remove').checked) { body.protect_api_key = ''; } else if (p) { body.protect_api_key = p; }
    if (Object.keys(body).length === 0) { showError('Paste at least one API key.'); btn.disabled = false; return; }
    try {
      await post('/auth/unifi/keys', body);
      finish();
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  $('disconnect').onclick = async function () {
    if (!confirm('Remove both stored UniFi API keys?')) { return; }
    var btn = $('disconnect'); btn.disabled = true; clearError();
    try {
      await post('/auth/unifi/disconnect', {});
      state.network_key_masked = null; state.protect_key_masked = null;
      renderCurrent();
      if (window.opener) { window.opener.postMessage({ type: 'oauth_callback_success', service: SERVICE }, window.location.origin); }
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  };
  ['network', 'protect'].forEach(function (id) {
    $(id).addEventListener('keydown', function (e) { if (e.key === 'Enter') $('save').click(); });
  });
  $('network').focus();
})();
</script>
"""


def _json_for_inline_script(value) -> str:
    """Serialize ``value`` for embedding inside an inline ``<script>``.

    ``json.dumps`` alone is not safe in a script context: the HTML parser
    ends the script element at the first ``</script>`` regardless of JS
    string quoting, so controller-supplied text (e.g. the stored
    ``applicationVersion``) could break out and inject markup. Escaping
    ``<``, ``>`` and ``&`` as JS unicode escapes keeps the runtime value
    identical while making the serialized text inert to the HTML parser.
    """
    return (
        json.dumps(value)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_connect_page(host: str, blob: dict) -> str:
    """The popup page HTML with the stored-key state embedded (masked)."""
    state = {"host": host, **connection_state(blob)}
    return _page(
        "Quest - Connect UniFi",
        _CONNECT_PAGE_BODY.replace("__STATE__", _json_for_inline_script(state)),
    )


@router.get("/unifi")
async def unifi_connect_page(request: Request, popup: str = None):
    """Serve the API-key entry page (popup or full window)."""
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
        config = load_unifi_config()
    except HTTPException as exc:
        return HTMLResponse(_page(
            "Quest - Error",
            "<h1>UniFi not configured</h1>"
            f"<p class=\"error\">{html.escape(str(exc.detail))}</p>",
        ))
    blob = await _current_blob(user["id"])
    return HTMLResponse(render_connect_page(config["host"], blob))
