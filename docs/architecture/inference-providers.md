# Inference Providers (Admin Settings)

## Overview

Admin-only Settings > Inference Providers panel for LLM backend configuration. It surfaces two kinds of providers: **API-key providers** (one registered today: **OpenRouter** (`openrouter`), serving the `backend: "openrouter"` models in `MODEL_REGISTRY` -- see [LLM Providers -- OpenRouterProvider](llm-providers.md#openrouterprovider); the Gemini API provider was removed when all Gemini models moved to Vertex, because thought signatures are not portable across the Vertex / AI Studio border and mixing backends broke mid-conversation model switches; the registry and generic machinery also serve future direct Anthropic/OpenAI APIs), whose key is saved from the UI into a per-provider store at `DATA_DIR / "inference_credentials"` (one `<provider>.json`, 0600/0700, atomic writes, encrypted at rest in the same `{"encrypted": ...}` wrapper as the service-credential store with `encrypt_plaintext_inference_files()` sweeping pre-baked plaintext files -- see [Encryption at Rest](encryption-at-rest.md)), and **Vertex AI**, which is display-only -- the panel shows what was detected from the environment (a `GOOGLE_APPLICATION_CREDENTIALS` service-account key or the gcloud ADC file) plus the effective `vertex_project_id`/`vertex_region` per model family and which layer each value came from (env var, `server_config.json`, or the gemini_vertex → anthropic project fallback). Vertex configuration is not editable here.

Because `get_available_models()` re-reads config and the health store on every call, saving a key takes effect without a restart: the PUT handler drops cached SDK clients (`reset_provider_client_caches()`) and schedules background health rechecks for that backend's models (`schedule_model_rechecks()`), so models hidden by a failing verdict recorded under the old key reappear in the picker as soon as a check under the new key passes. (A provider may declare a `legacy_section` of `server_credentials.json` read as a fallback until a key is saved through the UI; OpenRouter declares none.) In local mode, the shared parent-directory `dev-config.json` can pre-bake keys into the store via its `inference_credentials` mapping (see [Run Modes](run-modes.md)).

## Model Health Checks (server-global)

Per-model health is **server state**, not a per-request UI probe. `ModelHealthStore` (`chat/llm/health.py`) keeps the latest verdict per model -- `{ok, error, checked_at}` -- in memory, mirrored atomically to `data/model_health.json` (`MODEL_HEALTH_FILE` in `config/paths.py`) so it survives restarts. At startup the quest lifespan launches `run_startup_model_checks()` as a background task: it checks every model returned by `get_configured_models()` (the config-presence filter, so an unconfigured deployment runs zero checks) with concurrency bounded to 4 to avoid tripping per-project rate limits, then logs a summary.

The store **drives the model picker**: `get_available_models()` (what `GET /app/api/config` returns as `available_models`) is `get_configured_models()` minus every model whose latest verdict is an explicit failure. A never-checked model stays visible -- only a recorded failing check hides one, so a fresh boot or a deleted `model_health.json` can never empty the picker -- and health only filters *selection*: existing conversations and routines on an unhealthy model keep running. Recovery paths are the startup sweep (which deliberately iterates the health-blind configured list so hidden models keep being rechecked), the admin per-model Recheck, and the automatic recheck after a credential save.

A check is a **minimal live inference call** through the real provider instances (`check_model_access()` on each provider: 1 output token for Claude, a ~25-token cap for Gemini) -- deliberately not a metadata lookup, so it surfaces exactly what a real send would hit: a Claude model never enabled in Vertex Model Garden, bad key, wrong project/region, quota exhaustion. `check_model()` wraps the call with a 45s timeout and returns failures as data with a compact extracted error (`extract_error_message()` understands both the anthropic `body.error.message` shape and google-genai's `code`/`message` attributes, truncated to 600 chars).

Every provider card has a right-hand **Models** panel listing the non-deprecated `MODEL_REGISTRY` entries that provider serves (`models` on each `GET /admin/inference-providers` entry, mapped via `get_backend_for_model()`; API-key providers declare their backend with `model_backend` in `API_KEY_PROVIDERS`), each carrying its stored `status` (null if never checked). Rows render the stored verdict (✓ / ✕ with the error inline, checked-at in the tooltip); Recheck buttons (per-row and Recheck-all; hidden while the family/provider is unconfigured, Vertex rows grouped into `anthropic` / `gemini_vertex` families) call `POST /admin/inference-providers/test-model` (`{model}` → `{model, ok, error, checked_at}`), which re-runs the live check **and updates the store**, so a recheck from Settings is authoritative for the whole server.

## Key Files

| File | Description |
|------|-------------|
| `config/inference_providers.py` | `API_KEY_PROVIDERS` registry, store read/write, `effective_api_key()` (store then legacy), and `vertex_environment_status()` (credential detection + per-section config sources) |
| `config/paths.py` | `INFERENCE_CREDENTIALS_DIR` constant |
| `chat/llm/config.py` | `reset_provider_client_caches()`; `get_configured_models()` (config-presence) and the health-filtered `get_available_models()` on top of it, both re-evaluated per call |
| `chat/llm/gemini_provider.py` | `GeminiProvider.reset_cached_clients()` (in-flight sessions keep the old client); `check_model_access()` minimal generate call |
| `chat/llm/anthropic_provider.py` | `check_model_access()` minimal `messages.create` call (`max_tokens=1`) |
| `chat/llm/openrouter_provider.py` | `OpenRouterProvider.reset_cached_clients()`; `check_model_access()` minimal `chat.completions.create` call (`max_tokens=1`) |
| `chat/llm/health.py` | `check_model()` (timeout, never raises for provider failures), `extract_error_message()`, `ModelHealthStore` + `get_model_health_store()` singleton, `run_startup_model_checks()` lifespan entry, `schedule_model_rechecks()` fire-and-forget recheck (used after credential saves) |
| `quest.py` | Lifespan starts (and cancels on shutdown) the background startup check task |
| `chat/routes/admin.py` | `GET /admin/inference-providers` (Vertex detected entry + masked API-key entries, each with `models` incl. stored `status`), `PUT /admin/inference-providers/{provider}` (empty key = keep; `vertex` is `not_editable`; schedules background health rechecks for the backend's models), and `POST /admin/inference-providers/test-model` (live per-model recheck, updates the store) |
| `frontend/src/components/settings/InferenceProvidersSection.tsx` | Renders cards by `kind` (`detected` / `api_key`), reusing the card chrome exported by `ServiceCredentialsSection.tsx`; full-width two-column card body with the shared Models panel showing stored verdicts + Recheck; API types in `frontend/src/api/types.ts`, client functions in `frontend/src/api/client.ts` |
| `tests/test_inference_providers.py` | Store, precedence, detection, admin-endpoint, model-list, and health-check tests |

## Adding a Provider

Adding a future API-key provider (direct Anthropic API, OpenAI) is a registry entry in `API_KEY_PROVIDERS` -- the admin endpoints and the settings UI render every registered provider generically. Only wiring the key into the corresponding SDK client construction is provider-specific work. OpenRouter is the worked example: its registry entry declares `model_backend: "openrouter"`, and `OpenRouterProvider._get_client()` reads the key via `effective_api_key("openrouter")`.

## Design Decisions

**Why is Vertex read-only in the panel?**
Vertex auth is ambient (ADC/env), typically provisioned by `run.py`, the prod bootstrap, or deployment tooling, and consumed by Google SDKs outside the app's control; the panel's job is to make the detected state visible, not to own it. Metadata-server credentials (GCE instance service accounts) are intentionally not probed to keep the settings request free of network calls, and the card says so.

**Why a store separate from `service_credentials/`?**
Inference providers are not upstream data integrations: they have their own registry, a different form shape (bare API key vs OAuth client), and their own settings section. Sharing the directory would leak them into the Service Credentials list endpoint, which iterates `KNOWN_SERVICES`. The file format and atomic-write scheme are deliberately identical.

**Why is the API key write-only with empty-means-keep?**
Same reasoning as service credentials: the browser never needs the key back, and an empty save keeps (or migrates a legacy) key -- see [Service Credentials](service-credentials.md).
