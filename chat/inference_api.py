"""One-shot inference API: POST /api/inference.

Lets internal applications run a single prompt as a Quest user and get
back one markdown response -- Quest's data reach (connected services,
workspace tools, skills) surfaced on other surfaces. Authentication is a
named bearer token minted in Settings > Inference API (see
chat/routes/inference_api_keys.py and db/inference_api_key_store.py);
the user's session-cookie/api_key auth is deliberately NOT accepted here.

Each call creates a fresh conversation with ``origin="inference_api"``
(read-only in the web UI, like user_subagent conversations) and drives
``run_conversation_turn`` headlessly, mirroring chat/user_subagent.py. The model
runs with the restricted INFERENCE_API_TOOLS tier and a system prompt that
forbids intermediate output; the final markdown is captured from the
``return_final_response`` tool call (its dispatch arm in
chat/gemini_api/conversation.py ends the run via the
FinishInferenceResponse sentinel). No streaming: the HTTP request blocks
until the run finishes, is nudged out, times out, or fails.
"""

import asyncio
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from chat._flush_helper import FLUSH_EVENT_TYPES, FlushFn, make_flush_callback
from chat.auth import check_user_allowed
from db import inference_api_key_store

logger = logging.getLogger(__name__)

RETURN_TOOL_NAME = "return_final_response"

MAX_PROMPT_LENGTH = 100_000

# Wall-clock cap on a single inference run. The caller's HTTP request
# blocks for the duration, so a hung run must not hold the connection
# forever.
INFERENCE_TIMEOUT_SECONDS = 15 * 60

# How many times to remind the model to call return_final_response when
# the loop ends without one (mirrors chat/user_subagent.py).
_MAX_RETURN_NUDGES = 1

_NUDGE_MESSAGE = (
    "You ended your turn without calling return_final_response. You MUST "
    "finish this run by calling return_final_response(response=...) with "
    "your complete final answer as markdown. If you cannot complete the "
    "task, call return_final_response with a response explaining why."
)


class InferenceRequest(BaseModel):
    prompt: str
    model: Optional[str] = None


async def get_current_user_inference_token(request: Request) -> dict:
    """Authenticate an inference API call by its dedicated bearer token.

    Only tokens from the inference_api_keys table are accepted -- neither
    session cookies nor the per-user ``users.api_key`` work here, so a
    leaked inference token cannot be replayed against the wider app
    surface (and vice versa). Bumps the key's ``last_used_at`` on success
    and annotates the returned user dict with ``_inference_key_id``.
    """
    from db.user_store import get_user_by_id

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_api_key",
                "message": (
                    "Invalid or missing inference API key. Include "
                    "'Authorization: Bearer <key>' with a key from "
                    "Settings > Inference API."
                ),
            },
        )

    token = auth_header[7:]
    key = await inference_api_key_store.get_key_by_token(token)
    if key is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_api_key",
                "message": (
                    "Invalid or missing inference API key. Include "
                    "'Authorization: Bearer <key>' with a key from "
                    "Settings > Inference API."
                ),
            },
        )

    user = await get_user_by_id(key["user_id"])
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_api_key",
                "message": "The key's owning user no longer exists.",
            },
        )
    if not check_user_allowed(user["email"]):
        from auth.config import allowed_login_domain
        raise HTTPException(
            status_code=403,
            detail={
                "error": "access_denied",
                "message": (
                    f"Access restricted to @{allowed_login_domain()} "
                    "accounts."
                ),
            },
        )

    try:
        await inference_api_key_store.touch_last_used(key["id"])
    except Exception:
        logger.debug(
            "[inference-api] touch_last_used failed (key=%s)",
            key["id"], exc_info=True,
        )

    user["_inference_key_id"] = key["id"]
    return user


def extract_final_response(messages: list[dict]) -> Optional[str]:
    """Pull the delivered markdown out of a run's structured messages.

    The response text lives on the persisted ``return_final_response``
    tool_use (appended before its dispatch arm raised the
    FinishInferenceResponse sentinel). Empty-response attempts were
    rejected back to the model, so only a non-empty string counts.
    """
    for msg in reversed(messages):
        if (
            msg.get("type") == "tool_use"
            and msg.get("tool_name") == RETURN_TOOL_NAME
        ):
            response = (msg.get("tool_input") or {}).get("response")
            if isinstance(response, str) and response.strip():
                return response
    return None


async def run_inference(
    app, user: dict, prompt: str, model: Optional[str],
) -> dict:
    """Drive a one-shot inference conversation to completion.

    Creates the ``origin="inference_api"`` conversation, persists the
    prompt as its first user message, runs the loop (with one nudge if the
    model ends without a return call, mirroring chat/user_subagent.py),
    and returns ``{"conversation_id", "response"}``.

    Raises HTTPException(502) when no response was returned; other
    exceptions propagate to the endpoint's error handling. The transcript
    is always persisted via the flush callback, so failed runs remain
    inspectable in the (read-only) web UI.
    """
    from chat.gemini_api import run_conversation_turn
    from chat.realtime import bus, events as realtime_events
    from chat.realtime.socket import _publish_transient_event
    from chat.storage import ChatStorage, utc_timestamp

    conversation_id = await ChatStorage.create_inference_api_conversation(
        user["id"], model=model,
    )
    await ChatStorage.append_message(
        conversation_id, "user", prompt, timestamp=utc_timestamp(),
    )

    # Surface the new conversation in the owner's sidebar.
    try:
        bus.publish_to_user(
            user["id"],
            realtime_events.make_conversation_list_changed(
                conversation_id, "created",
            ),
        )
    except Exception:
        logger.debug(
            "[inference-api] publish conversation_list_changed failed",
            exc_info=True,
        )

    user_tz = (user.get("settings") or {}).get("timezone") or "UTC"

    messages_out: list[dict] = []
    _flush: Optional[FlushFn] = None
    try:
        _flush, _ = make_flush_callback(
            conversation_id, messages_out, log_prefix="[inference-api]",
        )
        flush = _flush

        async def _on_event(event: dict) -> None:
            if event.get("type") in FLUSH_EVENT_TYPES:
                await flush()
            try:
                _publish_transient_event(conversation_id, event)
            except Exception:
                logger.debug(
                    "[inference-api] publish_transient_event failed "
                    "(conversation=%s, type=%s)",
                    conversation_id, event.get("type"), exc_info=True,
                )

        # An inference token is deliberately narrower than the owner's
        # reusable ``users.api_key`` (that key authenticates the whole app
        # surface via get_current_user_cookie_or_apikey). Everything the
        # model can see -- the system-prompt proxy preamble and the
        # system-skill docs rendered by load_skills -- is delivered to the
        # token holder through the final response, so the broader key must
        # never enter this run. In-process tools (tool_call, curl_proxy_*
        # via route dispatch) authenticate from the user dict itself and
        # keep working, and run_script/run_python containers still get the
        # loopback tool-API bridge: their QUEST_API_KEY is a per-run
        # ephemeral sandbox token minted from ``user["id"]``
        # (chat/sandbox_tokens.py), not this field.
        run_user = {**user, "api_key": ""}

        message = prompt
        for attempt in range(_MAX_RETURN_NUDGES + 1):
            await run_conversation_turn(
                app=app,
                user=run_user,
                message=message,
                conversation_id=conversation_id,
                timezone=user_tz,
                model=model,
                on_event=_on_event,
                messages_out=messages_out,
                origin="inference_api",
            )

            response = extract_final_response(messages_out)
            if response is not None:
                return {
                    "conversation_id": conversation_id,
                    "response": response,
                }

            if attempt < _MAX_RETURN_NUDGES:
                logger.info(
                    "[inference-api] Conversation %s ended its loop "
                    "without a return call; nudging (attempt %d)",
                    conversation_id, attempt + 1,
                )
                message = _NUDGE_MESSAGE
                try:
                    await ChatStorage.append_message(
                        conversation_id, "user", _NUDGE_MESSAGE,
                        timestamp=utc_timestamp(),
                    )
                except Exception:
                    logger.debug(
                        "[inference-api] persisting nudge message failed "
                        "(conversation=%s)", conversation_id, exc_info=True,
                    )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_response",
                "message": (
                    "The model ended the run without calling "
                    "return_final_response, so there is no response to "
                    "deliver."
                ),
                "conversation_id": conversation_id,
            },
        )
    finally:
        if _flush is not None:
            try:
                await _flush()
            except Exception:
                logger.exception(
                    "[inference-api] Final flush failed (conversation=%s)",
                    conversation_id,
                )


async def inference_endpoint(
    body: InferenceRequest,
    request: Request,
    user: dict = Depends(get_current_user_inference_token),
):
    """POST /api/inference: run one prompt as the token's user, no streaming.

    Body: ``{"prompt": "...", "model": "<optional model id>"}``.
    Success: ``{"response": "<markdown>", "conversation_id": "...",
    "model": "<effective model id or null>"}``.
    """
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_prompt",
                "message": "prompt is required.",
            },
        )
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_prompt",
                "message": (
                    f"prompt is too long (max {MAX_PROMPT_LENGTH} "
                    "characters)."
                ),
            },
        )

    model = body.model
    if model:
        from chat.llm.config import MODEL_REGISTRY
        if model not in MODEL_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_model",
                    "message": f"Unknown model id: {model}",
                },
            )
    else:
        # Fall back to the user's web default; run_conversation_turn resolves
        # None to the server-config default model.
        model = (user.get("settings") or {}).get("default_model") or None

    logger.info(
        "[inference-api] Run requested (user=%s, key=%s, model=%s, "
        "prompt_chars=%d)",
        user["email"], user.get("_inference_key_id"), model, len(prompt),
    )

    try:
        result = await asyncio.wait_for(
            run_inference(request.app, user, prompt, model),
            timeout=INFERENCE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "inference_timeout",
                "message": (
                    "The run exceeded the "
                    f"{INFERENCE_TIMEOUT_SECONDS}s limit and was "
                    "cancelled."
                ),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[inference-api] Run failed (user=%s)", user["email"],
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "inference_failed",
                "message": f"{type(e).__name__}: {e}",
            },
        )

    return {**result, "model": model}
