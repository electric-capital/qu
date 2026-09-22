"""Twitter/X OAuth 2.0 PKCE flow endpoints (plugin-provided router).

The oauth-kind user connection's router, mounted by quest.py under the
plugin's ``/auth/twitter`` namespace after plugin load. Twitter/X is the
repo's only PKCE flow: the authorize request carries an S256
``code_challenge`` and the callback exchanges the code with the matching
``code_verifier`` (stored in the signed state cookie alongside the CSRF
token). Token JSON lives in the ``user_service_credentials`` table
(``oauth_blob``), same as the GitHub and M365 plugins.
"""

import base64
import hashlib
import logging
from html import escape
import secrets
from datetime import datetime, timezone

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

from plugins.twitter.upstream import (
    TWITTER_AUTHORIZE_URL,
    TWITTER_SCOPE_REQUEST,
    TWITTER_TOKEN_URL,
    build_token_blob,
    load_twitter_client_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

_STATE_COOKIE = "twitter_oauth_state"

_HTTP_TIMEOUT = 30.0


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256).

    Returns:
        (code_verifier, code_challenge) tuple where code_challenge is
        the base64url-encoded SHA-256 hash of code_verifier.
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _error_page(title: str, message: str, retry_link: bool = False) -> HTMLResponse:
    retry = (
        '<p><a href="/auth/twitter">Retry Twitter authentication</a></p>'
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


@router.get("/twitter")
async def auth_twitter(request: Request, popup: str = None):
    """Initiate the Twitter/X OAuth 2.0 PKCE flow."""
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
    redirect_uri = f"{base_url}/auth/twitter/callback"

    try:
        config = load_twitter_client_config()
        client_id = config.get("client_id")

        # Generate CSRF state and PKCE pair
        code_verifier, code_challenge = _generate_pkce_pair()

        # Signed, session-bound state cookie carrying the CSRF nonce, the
        # PKCE verifier and the popup flag.
        issued = mint_oauth_state(
            _STATE_COOKIE,
            user_id=user["id"],
            popup=popup == "1",
            extra={"code_verifier": code_verifier},
        )
        state = issued.state

        auth_url = (
            f"{TWITTER_AUTHORIZE_URL}"
            f"?response_type=code"
            f"&client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={TWITTER_SCOPE_REQUEST}"
            f"&state={state}"
            f"&code_challenge={code_challenge}"
            f"&code_challenge_method=S256"
        )

        return issued.attach(RedirectResponse(auth_url))

    except HTTPException as e:
        return _error_page("Configuration Error", str(e.detail))
    except Exception as e:
        return _error_page("Configuration Error", str(e))


@router.get("/twitter/callback")
async def auth_twitter_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
):
    """Handle the Twitter/X OAuth 2.0 callback."""
    if error:
        return _error_page(
            "Twitter Authentication Error",
            f"Twitter authentication failed: {error}",
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
    code_verifier = state_payload.get("code_verifier")
    if not isinstance(code_verifier, str) or not code_verifier:
        resp = _error_page(
            "Security Error",
            "Invalid state parameter. Please try again.",
            retry_link=True,
        )
        return clear_oauth_state(resp, _STATE_COOKIE)

    if not code:
        return clear_oauth_state(RedirectResponse("/auth/"), _STATE_COOKIE)

    base_url = oauth_base_url(request)
    redirect_uri = f"{base_url}/auth/twitter/callback"

    try:
        config = load_twitter_client_config()
        client_id = config.get("client_id")
        client_secret = config.get("client_secret")

        # Exchange the authorization code for tokens
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                TWITTER_TOKEN_URL,
                data={
                    "code": code,
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(client_id, client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                raise Exception(f"Failed to exchange code for token: {response.text}")

            token_data = response.json()

            if token_data.get("error"):
                raise Exception(
                    f"Twitter OAuth error: {token_data.get('error_description', token_data.get('error'))}"
                )

            if not token_data.get("access_token"):
                raise Exception("No access token in response")

        blob = build_token_blob(token_data)
        blob["authorized_at"] = datetime.now(timezone.utc).isoformat()
        await upsert_credential(user["id"], "twitter", oauth_blob=blob)

        logger.info("[Twitter OAuth] User authenticated: %s", user["email"])

        # Invalidate cached chat sessions so the next message picks up the
        # new system prompt (Twitter skill now advertised).
        from chat.gemini_api import invalidate_user_sessions
        invalidate_user_sessions(user["id"])

        if is_popup:
            response = HTMLResponse(generate_oauth_popup_success_page("Twitter"))
        else:
            response = RedirectResponse("/", status_code=303)
        response.delete_cookie(_STATE_COOKIE)
        return response

    except Exception as e:
        if is_popup:
            response = HTMLResponse(generate_oauth_popup_error_page("Twitter", str(e)))
        else:
            response = _error_page(
                "Twitter Authentication Error", str(e), retry_link=True,
            )
        response.delete_cookie(_STATE_COOKIE)
        return response


@router.post("/twitter/disconnect")
async def disconnect_twitter(request: Request):
    """Remove the Twitter/X OAuth connection.

    POST /auth/twitter/disconnect
    """
    signed_cookie = request.cookies.get(COOKIE_NAME)
    if not signed_cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_user_from_cookie(signed_cookie)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await delete_credential(user["id"], "twitter")

    logger.info("[Auth] Twitter OAuth disconnected (user=%s)", user["email"])

    from chat.gemini_api import invalidate_user_sessions
    invalidate_user_sessions(user["id"])

    return {"success": True}
