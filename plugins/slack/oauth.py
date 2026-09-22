"""Slack OAuth flow endpoints (plugin-provided router).

The oauth-kind user connection's router, mounted by quest.py under the
plugin's ``/auth/slack`` namespace after plugin load. Same URLs as the
pre-plugin core flow (``/auth/slack``, ``/auth/slack/callback``) so
existing Slack app registrations keep working; tokens now land in the
``user_service_credentials`` table (``oauth_blob``) instead of the
dropped ``users.slack_oauth`` column. ``/auth/slack/disconnect`` is new,
matching the GitHub plugin's shape.
"""

import logging
from html import escape
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth.config import COOKIE_NAME, load_slack_client_config, oauth_base_url
from auth.oauth_state import clear_oauth_state, mint_oauth_state, verify_oauth_state
from auth.session import get_user_from_cookie
from auth.popup_helpers import (
    generate_oauth_popup_error_page,
    generate_oauth_popup_success_page,
)
from db.user_service_credential_store import delete_credential, upsert_credential

from plugins.slack.upstream import SLACK_USER_SCOPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

_STATE_COOKIE = "slack_oauth_state"


@router.get("/slack")
async def auth_slack(request: Request, popup: str = None):
    """Initiate Slack OAuth flow."""
    # Check if user is authenticated with Google
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
    redirect_uri = f"{base_url}/auth/slack/callback"

    try:
        slack_config = load_slack_client_config()
        client_id = slack_config.get("client_id")

        # Build Slack OAuth URL
        # Only user_scope is needed -- the bot token is a shared credential
        # in the admin "slack" service credential store entry, not obtained
        # per-user via OAuth.
        user_scope = ",".join(SLACK_USER_SCOPES)
        # Signed, session-bound state cookie (CSRF nonce + popup flag).
        issued = mint_oauth_state(
            _STATE_COOKIE, user_id=user["id"], popup=popup == "1",
        )
        state = issued.state

        auth_url = (
            f"https://slack.com/oauth/v2/authorize"
            f"?client_id={client_id}"
            f"&user_scope={user_scope}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )

        return issued.attach(RedirectResponse(auth_url))

    except Exception as e:
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head><title>Quest - Error</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Configuration Error</h1>
            <p style="color: red;">{escape(str(e))}</p>
        </body>
        </html>
        """)


@router.get("/slack/callback")
async def auth_slack_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Slack OAuth callback handler."""
    if error:
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head><title>Quest - Error</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Slack Authentication Error</h1>
            <p style="color: red;">Slack authentication failed: {escape(error)}</p>
            <p><a href="/">Back to home</a></p>
        </body>
        </html>
        """)

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
        resp = HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head><title>Quest - Error</title></head>
        <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Security Error</h1>
            <p style="color: red;">Invalid state parameter. Please try again.</p>
            <p><a href="/auth/slack">Retry Slack authentication</a></p>
        </body>
        </html>
        """)
        return clear_oauth_state(resp, _STATE_COOKIE)
    is_popup = bool(state_payload.get("popup"))

    if not code:
        return clear_oauth_state(RedirectResponse("/auth/"), _STATE_COOKIE)

    base_url = oauth_base_url(request)
    redirect_uri = f"{base_url}/auth/slack/callback"

    try:
        slack_config = load_slack_client_config()
        client_id = slack_config.get("client_id")
        client_secret = slack_config.get("client_secret")

        # Exchange code for token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri
                }
            )

            if response.status_code != 200:
                raise Exception("Failed to exchange code for token")

            token_data = response.json()

            if not token_data.get("ok"):
                raise Exception(f"Slack OAuth error: {token_data.get('error', 'Unknown error')}")

            # Extract user access token (per-user token for read operations)
            authed_user = token_data.get("authed_user", {})
            user_access_token = authed_user.get("access_token")

            if not user_access_token:
                raise Exception("No user access token in response")

            # Get user ID and home workspace via auth.test
            auth_test_response = await client.get(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {user_access_token}"}
            )
            auth_test_data = auth_test_response.json()
            slack_user_id = auth_test_data.get("user_id")

            # The user's home team is used as the default workspace for API
            # methods that require team_id (e.g., conversations.list)
            default_team_id = auth_test_data.get("team_id")

        # Store per-user Slack OAuth data in the generic credential row.
        # Note: the bot token is a shared admin credential, not per-user.
        await upsert_credential(user["id"], "slack", oauth_blob={
            "access_token": user_access_token,
            "default_team_id": default_team_id,
            "user_id": slack_user_id,
            "authorized_at": datetime.utcnow().isoformat()
        })

        # Log with enterprise info if present (for org-level installs)
        enterprise_name = (token_data.get("enterprise") or {}).get("name")
        team_name = (token_data.get("team") or {}).get("name")
        context = enterprise_name or team_name or default_team_id
        logger.info(
            "[Slack OAuth] User authenticated: %s (team=%s)",
            user["email"], context
        )

        # Invalidate cached chat sessions so the next message picks up the new system prompt
        from chat.gemini_api import invalidate_user_sessions
        invalidate_user_sessions(user["id"])

        # Redirect back to app (or close popup)
        if is_popup:
            response = HTMLResponse(generate_oauth_popup_success_page("Slack"))
        else:
            response = RedirectResponse("/", status_code=303)
        response.delete_cookie(_STATE_COOKIE)
        return response

    except Exception as e:
        if is_popup:
            response = HTMLResponse(generate_oauth_popup_error_page("Slack", str(e)))
            response.delete_cookie(_STATE_COOKIE)
            return response
        else:
            resp = HTMLResponse(f"""
            <!DOCTYPE html>
            <html>
            <head><title>Quest - Error</title></head>
            <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
                <h1>Slack Authentication Error</h1>
                <p style="color: red;">{escape(str(e))}</p>
                <p><a href="/auth/slack">Retry Slack authentication</a></p>
            </body>
            </html>
            """)
            return clear_oauth_state(resp, _STATE_COOKIE)


@router.post("/slack/disconnect")
async def disconnect_slack(request: Request):
    """Remove the Slack OAuth connection.

    POST /auth/slack/disconnect
    """
    signed_cookie = request.cookies.get(COOKIE_NAME)
    if not signed_cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_user_from_cookie(signed_cookie)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await delete_credential(user["id"], "slack")

    logger.info("[Auth] Slack OAuth disconnected (user=%s)", user["email"])

    # Invalidate session so system prompt refreshes without Slack docs
    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True}
