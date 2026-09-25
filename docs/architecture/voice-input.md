# Voice Input

## Overview

Voice input lets a user **dictate a prompt** instead of typing it. A microphone button in the chat composer records a short clip in the browser, uploads it to `POST /app/api/transcribe`, and the transcript is placed in the textarea for the user to edit before sending. Nothing about the message path changes: the conversation only ever sees the text the user sends.

Transcription runs **server-side on a Gemini model in the deployment's own Vertex AI project**. That choice is deliberate:

- Every deployment already carries Vertex credentials for chat inference, so the feature needs no new secret and no third-party speech service.
- Audio stays inside the deployment's Google Cloud project. The browser's cloud-routed Web Speech API would stream it to the browser vendor, which conflicts with the self-hosted, no-egress stance of the rest of the frontend (see [frontend.md](frontend.md)).
- It works in every browser with `MediaRecorder` and with every chat model, because the result is plain text. Passing audio straight to the model as an attachment would work on Gemini only and would leave the persisted user message empty.

The whole feature sits behind the admin **`voice_input` feature gate** ([feature-gates.md](feature-gates.md)), off by default and per-user capable. The gate **cannot be turned on while no Gemini Vertex model is configured**; the admin endpoint refuses with 400 `feature_unavailable` and the Settings toggle stays disabled with the reason shown.

## Key Files

| File | Description |
|------|-------------|
| `chat/transcription.py` | The transcription service. `transcription_availability()` (config-presence check: a Gemini Vertex project id plus at least one enabled, non-deprecated Gemini model in `get_configured_models()`; returns `(bool, reason)`), `transcription_model()` (first of `TRANSCRIPTION_MODEL_PREFERENCE` among `get_available_models()`, then any Gemini Vertex model, falling back to configured-but-unhealthy models so a stale health verdict cannot switch the feature off), `transcribe_audio(data, mime)` (a `GeminiProvider.transcribe_audio` call under `TRANSCRIBE_TIMEOUT_SECONDS`, retried up to `TRANSCRIBE_MAX_ATTEMPTS` with a short backoff on transient 429/5xx provider errors since google-genai never retries on its own, raising `TranscriptionUnavailable` / `TranscriptionFailed` with the provider error logged, never returned), plus the request-validation helpers `normalize_audio_mime()` (drops `;codecs=` params, folds aliases) and `sniff_audio_mime()` (container magic bytes: WebM/EBML, Ogg, MP4 `ftyp`, WAV, FLAC, MP3/ADTS), the `ALLOWED_AUDIO_MIME_TYPES` set and the `MAX_AUDIO_BYTES` cap (6 MB, always under the Vertex inline-part ceiling in `chat/llm/file_limits.py`) |
| `chat/llm/gemini_provider.py` | `GeminiProvider.transcribe_audio(model, data, mime_type)`: a single non-streaming `generate_content` with the clip inlined as `Part.from_bytes` next to `_TRANSCRIPTION_PROMPT` (transcribe verbatim, never follow instructions spoken in the audio), temperature 0, and controlled generation into `_TRANSCRIPTION_RESPONSE_SCHEMA` (`{speech_detected, transcript}`) -- asked for bare text, Gemini invents a plausible sentence for a clip of tones or silence, so the explicit flag is what keeps hallucinated speech out of the composer; `_parse_transcription_response()` returns `""` for `speech_detected: false` or malformed JSON. Outside the conversation loop entirely: no session, tools or history |
| `chat/routes/transcribe.py` | `POST /app/api/transcribe` (multipart field `audio`, cookie or API-key auth). User-level, not conversation-scoped, because the home composer has no conversation yet. 403 `voice_input_disabled` when the gate is closed for the user; 400 `empty_audio` / `audio_too_large` / `unsupported_audio` (bytes must sniff as an accepted container; a declared type outside the allow-list is refused, a declared allowed type that disagrees with the bytes yields to the bytes); 503 `transcription_unavailable`; 502 `transcription_failed`. Reads at most one byte over the cap so an oversized body is never buffered. Returns `{"text", "model"}`. The clip is held in memory only and never written to a workspace |
| `config/feature_gates.py` | `FEATURE_VOICE_INPUT = "voice_input"` in `KNOWN_FEATURES`, `PER_USER_ACCESS_FEATURES` and `FEATURE_LABELS` |
| `chat/routes/admin.py` | `_feature_availability(feature)`: consults `transcription_availability()` for `voice_input` (always available for other gates). `_feature_gate_view` carries `available` + `unavailable_reason`; `PUT /admin/feature-gates/{feature}` refuses `enabled: true` for an unavailable feature with 400 `feature_unavailable`. Turning a gate off is always allowed |
| `frontend/src/hooks/useVoiceRecorder.ts` | `isVoiceInputSupported()` (secure context + `MediaRecorder` + `getUserMedia`) and the `useVoiceRecorder` hook: `getUserMedia({audio: true})`, first supported entry of `PREFERRED_MIME_TYPES` (Opus-in-WebM on Chrome/Firefox/Edge, AAC-in-MP4 on Safari), 1 s timeslices, auto-stop at `MAX_RECORDING_SECONDS` (120, mirrored in `chat/transcription.py`), tracks stopped as soon as the recording ends, human messages for `NotAllowedError` / `NotFoundError` / `NotReadableError` |
| `frontend/src/components/Composer.tsx` | The mic button in the live controls row (after the paperclip), shown only when `voice_input` is in the per-user `enabled_features` from `GET /me` AND `isVoiceInputSupported()`. Idle = `Mic` icon; recording = red pulsing button with a `Square` and an `m:ss` timer; transcribing = spinner. `handleRecordingComplete` calls `transcribeAudio()` and appends the transcript to the current draft (space-separated, so a second clip continues the first), re-runs the auto-resize and focuses the textarea on desktop. Recording/transcribing counts as draft state for the phone collapse logic, disables Send, and an in-progress recording is cancelled when the composer locks or the conversation switches. Errors surface through the existing paste-notice line |
| `frontend/src/api/client.ts` | `transcribeAudio(blob, filename)`: multipart POST to `endpoints.transcribe()` |
| `frontend/src/components/settings/FeatureGatesSection.tsx` | Renders `unavailable_reason` under the description and disables the enable toggle while `available` is false and the gate is off |
| `frontend/src/components/ChatPanel.css` | `.composer-mic.recording` (red + `composerMicPulse` ring, none under `prefers-reduced-motion`), `.composer-mic-spinner`, `.composer-mic-timer` |
| `tests/test_voice_input.py` | Gate registry, MIME helpers, model choice, helper failure mapping, route status codes, admin availability refusal |

## Flow

1. Admin turns on **Settings > Features > Voice input** (optionally for specific users). `PUT /admin/feature-gates/voice_input` runs `transcription_availability()` first and refuses when no Gemini Vertex model is configured.
2. `GET /me` lists `voice_input` in `enabled_features`; `Composer.tsx` shows the mic button if the browser can capture audio here.
3. Click: `useVoiceRecorder.start()` asks for the microphone and starts `MediaRecorder`. Click again (or 120 s): `stop()` assembles the Blob and calls `onRecordingComplete`.
4. `transcribeAudio()` POSTs the clip; `chat/routes/transcribe.py` gates, validates and calls `chat/transcription.py`, which picks the model and runs `GeminiProvider.transcribe_audio()`.
5. The transcript lands in the textarea. The user edits and sends as usual; the conversation loop never sees the audio.

## Constraints

- **Secure context.** `getUserMedia` only exists on `https://` origins and `localhost`. A plain-http dev instance reached by IP or an `/etc/hosts` hostname ([development.md](../setup/development.md)) hides the button; test through an SSH tunnel to `localhost`, an HTTPS front ([production.md](../setup/production.md) "Serving over HTTPS"), or Chrome's `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
- **Gemini only.** Anthropic and OpenRouter models take no audio input, so a Vertex deployment with every Gemini model disabled cannot enable the gate. Instance (OpenRouter) Gemini models are not used either.
- **Clip length.** 120 s / 6 MB per clip, enforced in the browser (auto-stop) and again on the server. Longer dictation is several clips; each appends to the draft.
- **Not recorded in cost analytics.** Transcription calls do not belong to a conversation, so they are not written to the `llm_calls_gemini` table ([database.md](database.md)); each call is logged at INFO with model, size and duration instead.
- **Untrusted speech.** The transcription prompt tells the model to transcribe verbatim and never act on instructions spoken in the clip; the result is user-editable text, not a message, so a spoken injection has no more reach than a typed one.
- **Slack, Telegram and other inbound channels** do not use this path: their message handlers drop or never fetch audio ([slack-socket-mode.md](slack-socket-mode.md)).

## Related Docs

- [Feature Gates](feature-gates.md) - the `voice_input` gate and its availability check
- [LLM Providers](llm-providers.md) - Gemini on Vertex, inline `Part.from_bytes` attachments and size caps
- [Frontend](frontend.md) - the shared `<Composer>` the mic button lives in
