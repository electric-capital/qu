"""Inference-provider configuration for the admin settings panel.

Two kinds of inference providers are surfaced in Settings > Inference
Providers:

- **API-key providers** (none registered today; direct Anthropic/OpenAI
  APIs are future candidates): the key is stored in a per-provider JSON
  file under ``DATA_DIR / "inference_credentials"`` (one ``<provider>.json``,
  mode 0600, directory 0700, encrypted at rest in the same
  ``{"encrypted": "qenc1:..."}`` wrapper as the service-credential store --
  see config/service_credentials.py; plaintext files written by local-mode
  pre-baking are converted by ``encrypt_plaintext_inference_files()``),
  editable from the admin UI. A provider may
  declare a ``legacy_section`` of the consolidated ``server_credentials.json``
  at the project root that is read as a fallback when the store has nothing;
  the store always wins once a key is saved through the UI.

- **Vertex AI**: credentials come from the environment (a service-account
  key file pointed at by ``GOOGLE_APPLICATION_CREDENTIALS``, or gcloud
  Application Default Credentials) plus ``vertex_project_id``/``vertex_region``
  in ``server_config.json`` / env vars. The panel only *displays* what was
  detected -- it does not edit Vertex configuration.

Adding a new API-key provider is a one-line change: add an entry to
``API_KEY_PROVIDERS`` below. The admin endpoints and the settings UI render
every registered provider generically.

This module sticks to the Python standard library (mirroring
``config/service_credentials.py``) so it can be imported early; the only
non-stdlib touchpoint is a lazy import of ``load_server_config`` inside
``vertex_environment_status()``.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

from config.paths import INFERENCE_CREDENTIALS_DIR
from config.service_credentials import (
    LEGACY_CREDENTIALS_FILE,
    _is_encrypted_file,
    _read_json_object,
    _unwrap_encrypted_file,
    _wrap_encrypted_file,
)

logger = logging.getLogger(__name__)

# Editable API-key providers, in display order. ``legacy_section`` names the
# section of the legacy server_credentials.json whose ``api_key`` is read
# when the per-provider store file does not exist yet (omit for providers
# with no legacy location). ``hint`` is the input placeholder shown in the
# admin UI when no key is stored. ``model_backend`` maps the provider to the
# MODEL_REGISTRY backend label whose models it serves (see
# ``get_backend_for_model`` in chat/llm/config.py) so the settings panel can
# list the models each provider powers.
API_KEY_PROVIDERS: dict[str, dict] = {
    # OpenRouter: one OpenAI-compatible API serving many third-party models
    # behind a single key -- the non-Vertex model path for personal
    # deployments. The models it powers are the MODEL_REGISTRY entries with
    # backend "openrouter" (chat/llm/config.py).
    "openrouter": {
        "label": "OpenRouter",
        "hint": "sk-or-v1-...",
        "model_backend": "openrouter",
    },
    # Historical note: the Gemini API ("genapi") provider was removed when
    # all Gemini models moved to Vertex (thought signatures are not portable
    # across the Vertex / AI Studio border, breaking mid-conversation model
    # switches). Future direct-API providers slot in here, e.g.:
    # "anthropic_api": {"label": "Anthropic API", "hint": "sk-ant-..."},
    # "openai_api": {"label": "OpenAI API", "hint": "sk-..."},
}


def inference_credentials_path(provider: str) -> Path:
    """Return the per-provider credential file path for a known provider."""
    if provider not in API_KEY_PROVIDERS:
        raise ValueError(f"Unknown inference provider: {provider!r}")
    return INFERENCE_CREDENTIALS_DIR / f"{provider}.json"


def read_inference_credentials(provider: str) -> dict | None:
    """Read one provider's credentials from the per-provider store.

    Returns None when the file is missing, unreadable, or not a non-empty
    JSON object -- callers treat None as "not in the store" and fall back
    to the legacy location.
    """
    path = inference_credentials_path(provider)
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable inference credential file: %s", path)
        return None
    if not isinstance(data, dict) or not data:
        logger.warning("Ignoring malformed inference credential file: %s", path)
        return None
    data = _unwrap_encrypted_file(data, _file_aad(provider), path)
    if not isinstance(data, dict) or not data:
        logger.warning("Ignoring malformed inference credential file: %s", path)
        return None
    return data


def _file_aad(provider: str) -> str:
    return f"inference_credentials/{provider}"


def encrypt_plaintext_inference_files() -> list[str]:
    """Encrypt any provider file still holding plaintext JSON; returns the
    providers converted. Idempotent."""
    converted: list[str] = []
    for provider in API_KEY_PROVIDERS:
        path = inference_credentials_path(provider)
        raw = _read_json_object(path)
        if raw is None or _is_encrypted_file(raw):
            continue
        write_inference_credentials(provider, raw)
        converted.append(provider)
        logger.info("Encrypted plaintext inference credential file %s", path)
    return converted


def write_inference_credentials(provider: str, credentials: dict) -> None:
    """Atomically write one provider's credentials, encrypted, with
    restrictive permissions.

    Same temp-file + ``os.replace`` scheme as the service-credential store so
    a crash mid-write can never leave a truncated credential file behind.
    """
    path = inference_credentials_path(provider)
    INFERENCE_CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_path = tempfile.mkstemp(
        dir=INFERENCE_CREDENTIALS_DIR, prefix=f".{provider}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(_wrap_encrypted_file(credentials, _file_aad(provider)), f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def read_legacy_api_key(provider: str) -> str | None:
    """Read a provider's API key from its legacy server_credentials.json section.

    Returns None when the provider has no legacy location or the section holds
    no usable key. The literal string ``"null"`` is treated as missing (a
    historical placeholder value).
    """
    section_name = API_KEY_PROVIDERS[provider].get("legacy_section")
    if not section_name:
        return None
    data = _read_json_object(LEGACY_CREDENTIALS_FILE)
    section = (data or {}).get(section_name)
    if not isinstance(section, dict):
        return None
    api_key = section.get("api_key")
    if not api_key or api_key == "null":
        return None
    return api_key


def effective_api_key(provider: str) -> tuple[str | None, str | None]:
    """Return (api_key, source) with the per-provider store preferred.

    source is "store", "legacy", or None when the provider is unconfigured.
    """
    if provider not in API_KEY_PROVIDERS:
        raise ValueError(f"Unknown inference provider: {provider!r}")
    stored = read_inference_credentials(provider)
    if stored and stored.get("api_key"):
        return stored["api_key"], "store"
    legacy = read_legacy_api_key(provider)
    if legacy:
        return legacy, "legacy"
    return None, None


# ---------------------------------------------------------------------------
# Vertex AI environment detection (display-only)
# ---------------------------------------------------------------------------


def _gcloud_adc_path() -> Path:
    """Well-known gcloud Application Default Credentials file location."""
    config_dir = os.environ.get("CLOUDSDK_CONFIG")
    base = Path(config_dir) if config_dir else Path.home() / ".config" / "gcloud"
    return base / "application_default_credentials.json"


def _detect_google_credentials() -> dict:
    """Describe where Google credentials for Vertex would come from.

    Checks ``GOOGLE_APPLICATION_CREDENTIALS`` first (a service-account key
    file; this is how both run.py and the prod bootstrap wire Vertex auth),
    then the gcloud ADC well-known file. Metadata-server credentials (GCE)
    are intentionally not probed -- that would add network latency to a
    settings request -- so ``source`` may be None on a GCE deployment that
    relies on the instance service account.
    """
    info: dict = {
        "source": None,
        "key_path": None,
        "service_account_email": None,
        "project_id": None,
        "problem": None,
    }
    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    candidates = (
        [("env", Path(env_path))] if env_path else [("gcloud_adc", _gcloud_adc_path())]
    )
    for source, path in candidates:
        if source != "env" and not path.exists():
            continue
        info["source"] = source
        info["key_path"] = str(path)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            info["problem"] = "The credentials file could not be read."
            return info
        info["service_account_email"] = data.get("client_email") or None
        # Service-account keys carry project_id; gcloud user ADC carries at
        # most a quota_project_id.
        info["project_id"] = (
            data.get("project_id") or data.get("quota_project_id") or None
        )
        return info
    return info


def _vertex_section_status(config: dict, raw_config: dict, section: str) -> dict:
    """Project one Vertex config section (anthropic / gemini_vertex) with sources.

    ``config`` is the merged ``load_server_config()`` result (env overrides and
    the gemini_vertex->anthropic project fallback already applied); ``raw_config``
    is the unmerged server_config.json content, used to tell which layer a
    value actually came from.
    """
    env_var = {
        "anthropic": "ANTHROPIC_VERTEX_PROJECT_ID",
        "gemini_vertex": "GEMINI_VERTEX_PROJECT_ID",
    }[section]
    project_id = config[section]["vertex_project_id"]
    raw_project = (raw_config.get(section) or {}).get("vertex_project_id", "")
    if not project_id:
        source = None
    elif os.environ.get(env_var):
        source = "env"
    elif raw_project:
        source = "server_config"
    else:
        # Only reachable for gemini_vertex, via the anthropic project fallback
        # in load_server_config().
        source = "anthropic_fallback"
    return {
        "configured": bool(project_id),
        "vertex_project_id": project_id,
        "vertex_region": config[section]["vertex_region"],
        "project_source": source,
    }


def vertex_environment_status() -> dict:
    """Snapshot of the detected Vertex AI environment for the admin panel.

    Everything here is read-only display data: which credentials were found
    in the environment and which project/region each Vertex-backed model
    family (Anthropic, Gemini) would use, with the layer each value came from.
    """
    from config.server_config import SERVER_CONFIG_FILE, load_server_config

    config = load_server_config()
    raw_config = _read_json_object(SERVER_CONFIG_FILE) or {}
    anthropic = _vertex_section_status(config, raw_config, "anthropic")
    gemini_vertex = _vertex_section_status(config, raw_config, "gemini_vertex")
    return {
        "credentials": _detect_google_credentials(),
        "anthropic": anthropic,
        "gemini_vertex": gemini_vertex,
        "configured": anthropic["configured"] or gemini_vertex["configured"],
    }
