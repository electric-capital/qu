"""Session/cookie management and FastAPI authentication dependencies."""

from typing import Optional

from fastapi import HTTPException, Request
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer, BadSignature

from db.user_store import get_user_by_api_key, get_user_by_id
from auth.config import COOKIE_NAME, COOKIE_VERSION, get_secret_key


def get_cookie_serializer() -> URLSafeSerializer:
    """Get the serializer for signing cookies."""
    return URLSafeSerializer(get_secret_key(), salt="quest-session")


def get_timed_serializer() -> URLSafeTimedSerializer:
    """Get the timed serializer for signing cookies with expiration."""
    return URLSafeTimedSerializer(get_secret_key(), salt="quest-timed")


async def get_user_from_cookie(signed_cookie: str) -> Optional[dict]:
    """Deserialize a session cookie and return the user dict.

    Only accepts cookies matching the current COOKIE_VERSION with a
    valid positive integer user ID.  Rejects legacy formats, version
    mismatches, boolean uid values (bool is a subclass of int in
    Python), and non-positive IDs.
    """
    try:
        cookie_value = get_cookie_serializer().loads(signed_cookie)

        # Reject anything that is not a dict
        if not isinstance(cookie_value, dict):
            return None

        # Strict version check: must be exactly COOKIE_VERSION (int),
        # not a float, bool, or other numeric type
        version = cookie_value.get("v")
        if not isinstance(version, int) or isinstance(version, bool):
            return None
        if version != COOKIE_VERSION:
            return None

        # Strict user ID check: must be a positive int, NOT a bool
        user_id = cookie_value.get("uid")
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            return None
        if user_id <= 0:
            return None

        user = await get_user_by_id(user_id)
        if user is None:
            return None

        # Check for impersonation: if "imp" (impersonator user ID) is present,
        # validate the impersonator is still a valid admin. If so, annotate the
        # returned user dict with impersonator info. If the impersonator is no
        # longer valid/admin, treat the cookie as invalid.
        imp_uid = cookie_value.get("imp")
        if imp_uid is not None:
            if not isinstance(imp_uid, int) or isinstance(imp_uid, bool) or imp_uid <= 0:
                return None
            impersonator = await get_user_by_id(imp_uid)
            if impersonator is None:
                return None
            from chat.auth import is_admin as check_is_admin
            if not check_is_admin(impersonator["email"]):
                # Admin access revoked -- invalidate impersonation session
                return None
            user["_impersonator_uid"] = imp_uid
            user["_impersonator_email"] = impersonator["email"]
            user["_impersonator_name"] = impersonator.get("name", "")

        return user

    except BadSignature:
        return None


def _require_allowed(user: dict) -> dict:
    """Apply the admission policy (allowed domain / email whitelist).

    Lazy import: ``chat.auth`` imports this module (same pattern as the
    ``is_admin`` check in ``get_user_from_cookie``).
    """
    from chat.auth import require_user_allowed
    return require_user_allowed(user)


async def get_current_user(request: Request) -> dict:
    """Extract and validate API key from Authorization header.

    Raises 401 on a missing/unknown key and 403 when the key's owner no
    longer passes the admission policy.
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_api_key",
                "message": "Invalid or missing API key. Include 'Authorization: Bearer <key>' header."
            }
        )

    api_key = auth_header[7:]  # Remove "Bearer " prefix
    user = await get_user_by_api_key(api_key)

    if not user:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_api_key",
                "message": "Invalid or missing API key. Include 'Authorization: Bearer <key>' header."
            }
        )

    return _require_allowed(user)


async def get_current_user_cookie_or_apikey(request: Request) -> dict:
    """Authenticate via session cookie or API key. Used by endpoints
    that serve both the browser frontend and script/LLM clients.

    Raises 401 when neither credential resolves a user and 403 when the
    resolved user no longer passes the admission policy.
    """
    # Try cookie
    signed_cookie = request.cookies.get(COOKIE_NAME)
    if signed_cookie:
        user = await get_user_from_cookie(signed_cookie)
        if user:
            return _require_allowed(user)

    # Try API key
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header[7:]
        user = await get_user_by_api_key(api_key)
        if user:
            return _require_allowed(user)

    raise HTTPException(
        status_code=401,
        detail={
            "error": "not_authenticated",
            "message": "Authentication required."
        }
    )
