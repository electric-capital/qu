"""Microsoft 365 OAuth flow endpoints (plugin-provided router).

The oauth-kind user connection's router, mounted by quest.py under the
plugin's ``/auth/m365`` namespace after plugin load. Implements the
Microsoft identity platform (Entra ID) v2.0 authorization-code flow with
``offline_access`` so a refresh token lands in the stored blob; token JSON
lives in the ``user_service_credentials`` table (``oauth_blob``), same as
the GitHub plugin.
"""

import logging
from html import escape
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth.config import COOKIE_NAME, oauth_base_url
from auth.oauth_state import clear_oauth_state, mint_oauth_state, verify_oauth_state
from auth.session import get_user_from_cookie
from auth.popup_helpers import (
    generate_oauth_popup_error_page,
    generate_oauth_popup_success_page,
)
from db.user_service_credential_store import delete_credential, upsert_credential

from plugins.m365.upstream import (
    GRAPH_API_BASE,
    M365_SCOPE_REQUEST,
    build_token_blob,
    load_m365_client_config,
    login_base_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

_STATE_COOKIE = "m365_oauth_state"

_HTTP_TIMEOUT = 30.0


def _error_page(title: str, message: str, retry_link: bool = False) -> HTMLResponse:
    retry = (
        '<p><a href="/auth/m365">Retry Microsoft 365 authentication</a></p>'
        if retry_link else '<p><a href="/">Back to home</a></p>'
    )
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head><title>Quest - Error</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>{escape(title)}</h1>
        <p style="color: red;">{escape(message)}</p>
        {retry}
    </body>
    </html>
    """)


@router.get("/m365")
async def auth_m365(request: Request, popup: str = None):
    """Initiate the Microsoft 365 OAuth flow."""
    signed_cookie = request.cookies.get(COOKIE_NAME)
    if not signed_cookie:
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head><title>Quest - Error</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Authentication Required</h1>
            <p>Please authenticate with Google first.</p>
            <p><a href="/">Sign in with Google</a></p>
        </body>
        </html>
        """)

    user = await get_user_from_cookie(signed_cookie)
    if not user:
        return RedirectResponse("/auth/")

    base_url = oauth_base_url(request)
    redirect_uri = f"{base_url}/auth/m365/callback"

    try:
        config = load_m365_client_config()

        # Signed, session-bound state cookie (CSRF nonce + popup flag).
        issued = mint_oauth_state(
            _STATE_COOKIE, user_id=user["id"], popup=popup == "1",
        )
        state = issued.state

        auth_url = f"{login_base_url(config['tenant_id'])}/authorize?" + urlencode({
            "client_id": config["client_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": M365_SCOPE_REQUEST,
            "state": state,
            # Let users with several Microsoft accounts pick the mailbox.
            "prompt": "select_account",
        })

        return issued.attach(RedirectResponse(auth_url))

    except HTTPException as e:
        return _error_page("Configuration Error", str(e.detail))
    except Exception as e:
        return _error_page("Configuration Error", str(e))


@router.get("/m365/callback")
async def auth_m365_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
):
    """Microsoft 365 OAuth callback handler."""
    if error:
        detail = error_description or error
        return _error_page(
            "Microsoft 365 Authentication Error",
            f"Microsoft authentication failed: {detail}",
        )

    # The state cookie is bound to the session that started the flow:
    # resolve the session first, then verify the signed nonce against it.
    signed_cookie = request.cookies.get(COOKIE_NAME)
    user = await get_user_from_cookie(signed_cookie) if signed_cookie else None
    if not user:
        return clear_oauth_state(RedirectResponse("/auth/"), _STATE_COOKIE)

    state_payload = verify_oauth_state(
        request, _STATE_COOKIE, state, user_id=user["id"],
    )
    if state_payload is None:
        resp = _error_page(
            "Security Error",
            "Invalid state parameter. Please try again.",
            retry_link=True,
        )
        return clear_oauth_state(resp, _STATE_COOKIE)
    is_popup = bool(state_payload.get("popup"))

    if not code:
        return clear_oauth_state(RedirectResponse("/auth/"), _STATE_COOKIE)

    try:
        config = load_m365_client_config()
        base_url = oauth_base_url(request)
        redirect_uri = f"{base_url}/auth/m365/callback"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            token_response = await client.post(
                f"{login_base_url(config['tenant_id'])}/token",
                data={
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "scope": M365_SCOPE_REQUEST,
                },
            )

            if token_response.status_code != 200:
                try:
                    detail = token_response.json().get("error_description", "")
                except Exception:
                    detail = token_response.text[:300]
                raise Exception(f"Failed to exchange code for token: {detail}")

            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise Exception("No access token in response")

            # Fetch the connected mailbox identity: the M365 address can
            # differ from the (Google) Quest login email, and
            # m365_send_mail_to_self needs the mailbox address.
            account = None
            me_response = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={"$select": "id,displayName,mail,userPrincipalName"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_response.status_code == 200:
                me = me_response.json()
                account = {
                    "id": me.get("id"),
                    "display_name": me.get("displayName"),
                    "email": me.get("mail") or me.get("userPrincipalName"),
                }

        blob = build_token_blob(token_data, account=account)
        blob["authorized_at"] = datetime.now(timezone.utc).isoformat()
        await upsert_credential(user["id"], "m365", oauth_blob=blob)

        logger.info(
            "[M365 OAuth] User authenticated: %s (m365_account=%s)",
            user["email"], (account or {}).get("email", "unknown"),
        )

        # Invalidate cached chat sessions so the next message picks up the
        # new system prompt (Microsoft 365 skill/tools now advertised).
        from chat.gemini_api import invalidate_user_sessions
        invalidate_user_sessions(user["id"])

        if is_popup:
            response = HTMLResponse(generate_oauth_popup_success_page("Microsoft 365"))
        else:
            response = RedirectResponse("/", status_code=303)
        response.delete_cookie(_STATE_COOKIE)
        return response

    except Exception as e:
        if is_popup:
            response = HTMLResponse(generate_oauth_popup_error_page("Microsoft 365", str(e)))
        else:
            response = _error_page(
                "Microsoft 365 Authentication Error", str(e), retry_link=True,
            )
        response.delete_cookie(_STATE_COOKIE)
        return response


@router.post("/m365/disconnect")
async def disconnect_m365(request: Request):
    """Remove the Microsoft 365 OAuth connection.

    POST /auth/m365/disconnect
    """
    signed_cookie = request.cookies.get(COOKIE_NAME)
    if not signed_cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_user_from_cookie(signed_cookie)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await delete_credential(user["id"], "m365")

    logger.info("[Auth] Microsoft 365 OAuth disconnected (user=%s)", user["email"])

    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True}
