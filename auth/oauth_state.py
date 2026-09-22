"""Signed, session-bound OAuth ``state`` cookies shared by every OAuth flow.

Each flow that redirects the browser to a third-party authorize URL
(app login, Google Services, and every oauth-kind connector) needs a
CSRF nonce that the callback can compare against the ``state`` query
parameter.  Rather than keeping the nonce in a plaintext cookie, this
module issues one signed, time-limited cookie per flow whose payload
carries:

- ``csrf``: the random nonce that also travels as the ``state`` param;
- ``uid``: the logged-in user's id for connector flows (``None`` for the
  app login, where no session exists yet), so a state minted in one
  browser session cannot complete a callback in another;
- ``popup``: whether the flow was opened from the Settings popup;
- any flow-specific extras (the Twitter flow keeps its PKCE
  ``code_verifier`` here).

The cookie is HttpOnly, SameSite=Lax, ``Secure`` whenever the session
cookie is, and expires after :data:`STATE_TTL_SECONDS` both client-side
(``max_age``) and server-side (timed signature).  Tampering with or
forging the cookie fails signature verification, so a caller that can
overwrite cookies can only *break* a login transaction, not steer it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from auth.config import COOKIE_SECURE, get_secret_key

# How long an issued state stays valid: the user has this long to finish
# the third-party consent screen.
STATE_TTL_SECONDS = 600

_SALT = "quest-oauth-state"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_secret_key(), salt=_SALT)


@dataclass(frozen=True)
class IssuedState:
    """A minted nonce plus the signed cookie that vouches for it."""

    state: str
    cookie_name: str
    cookie_value: str

    def attach(self, response: Response) -> Response:
        """Set the signed state cookie on ``response`` and return it."""
        response.set_cookie(
            key=self.cookie_name,
            value=self.cookie_value,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            max_age=STATE_TTL_SECONDS,
        )
        return response


def mint_oauth_state(
    cookie_name: str,
    *,
    user_id: Optional[int] = None,
    popup: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> IssuedState:
    """Mint a CSRF nonce and its signed cookie.

    ``.state`` is what the caller passes as the ``state`` query parameter
    on the authorize URL; ``.attach(response)`` sets the cookie on the
    redirect that sends the browser there.
    """
    state = secrets.token_urlsafe(32)
    payload: dict[str, Any] = {"csrf": state, "uid": user_id, "popup": bool(popup)}
    if extra:
        payload.update(extra)
    return IssuedState(
        state=state,
        cookie_name=cookie_name,
        cookie_value=_serializer().dumps(payload),
    )


def read_oauth_state(request: Request, cookie_name: str) -> Optional[dict[str, Any]]:
    """Return the state cookie's payload if its signature and age check out.

    Does NOT compare the nonce -- use :func:`verify_oauth_state` for the
    full check.  This read-only variant exists so callbacks can recover
    the ``popup`` flag for error rendering even when the nonce fails.
    """
    raw = request.cookies.get(cookie_name)
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw, max_age=STATE_TTL_SECONDS)
    except BadSignature:  # also covers SignatureExpired
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def verify_oauth_state(
    request: Request,
    cookie_name: str,
    state: Optional[str],
    *,
    user_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Validate the callback's ``state`` against the signed cookie.

    Returns the cookie payload when the signature is valid, the cookie is
    within its TTL, the nonce matches ``state``, and the payload's
    ``uid`` equals ``user_id`` (both ``None`` for the app login flow).
    Returns ``None`` otherwise -- callers must treat that as a security
    error and not proceed with the token exchange.
    """
    payload = read_oauth_state(request, cookie_name)
    if payload is None or not state:
        return None
    csrf = payload.get("csrf")
    if not isinstance(csrf, str) or not secrets.compare_digest(csrf, state):
        return None
    if payload.get("uid") != user_id:
        return None
    return payload


def clear_oauth_state(response: Response, cookie_name: str) -> Response:
    """Drop the state cookie so it cannot be presented a second time."""
    response.delete_cookie(cookie_name)
    return response
