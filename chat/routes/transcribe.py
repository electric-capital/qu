"""``POST /app/api/transcribe``: speech-to-text for the composer's voice input.

The composer's microphone button (``frontend/src/hooks/useVoiceRecorder.ts``
+ ``Composer.tsx``) records a clip with ``MediaRecorder`` and uploads it here
as multipart ``audio``; the transcript comes back as ``{"text": ...}`` and
lands in the textarea for the user to edit. The endpoint is user-level (not
conversation-scoped) because the home composer has no conversation yet, and
the clip is held in memory only -- it is never written to a workspace.

Gated by the admin ``voice_input`` feature gate per user (403
``voice_input_disabled``), validated for size and container type (400), and
served by ``chat/transcription.py`` (503 ``transcription_unavailable`` when no
Gemini Vertex model can run, 502 ``transcription_failed`` on provider errors).
"""

import logging

from fastapi import Depends, File, HTTPException, UploadFile

from chat.auth import get_current_user_cookie_or_apikey_checked
from chat.routes import router
from chat.transcription import (
    ALLOWED_AUDIO_MIME_TYPES,
    MAX_AUDIO_BYTES,
    TranscriptionFailed,
    TranscriptionUnavailable,
    normalize_audio_mime,
    sniff_audio_mime,
    transcribe_audio,
)
from config.feature_gates import FEATURE_VOICE_INPUT, is_feature_enabled_for_user

logger = logging.getLogger(__name__)


def _bad_request(error: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": error, "message": message})


@router.post("/transcribe")
async def transcribe_composer_audio(
    audio: UploadFile = File(...),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Transcribe one recorded clip and return ``{"text", "model"}``.

    The declared content type (with any ``;codecs=`` parameter dropped) must
    be an accepted audio container and must agree with the magic bytes; a
    missing declared type is fine when the bytes are recognizable. Empty
    and oversized clips are rejected before any provider call.
    """
    if not is_feature_enabled_for_user(FEATURE_VOICE_INPUT, user["email"]):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "voice_input_disabled",
                "message": "Voice input is not enabled for your account.",
            },
        )

    # Read at most one byte over the cap so an oversized upload is rejected
    # without buffering the whole body.
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise _bad_request("empty_audio", "The recording is empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise _bad_request(
            "audio_too_large",
            f"Recording exceeds the {MAX_AUDIO_BYTES // (1024 * 1024)} MB limit.",
        )

    declared = normalize_audio_mime(audio.content_type)
    sniffed = sniff_audio_mime(data)
    if sniffed is None:
        raise _bad_request(
            "unsupported_audio",
            "The recording is not in a supported audio format.",
        )
    if declared and declared != sniffed:
        # Some browsers label an Opus-in-WebM recording audio/ogg or vice
        # versa; both are accepted containers, so trust the bytes there.
        # Anything else that disagrees is refused.
        if declared not in ALLOWED_AUDIO_MIME_TYPES:
            raise _bad_request(
                "unsupported_audio",
                f"Unsupported audio type: {declared}",
            )
        logger.info(
            "Transcribe: declared %s but bytes look like %s; using sniffed type",
            declared, sniffed,
        )
    mime_type = sniffed

    try:
        result = await transcribe_audio(data, mime_type)
    except TranscriptionUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "transcription_unavailable", "message": str(exc)},
        )
    except TranscriptionFailed as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "transcription_failed", "message": str(exc)},
        )
    return {"text": result["text"], "model": result["model"]}
