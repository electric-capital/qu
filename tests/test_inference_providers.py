"""Tests for inference-provider configuration.

Covers ``config/inference_providers.py`` (per-provider API-key store with
restrictive permissions, legacy server_credentials.json fallback, Vertex
environment detection), the ``get_available_models()`` Vertex config
presence (plus its model-health filtering on top of
``get_configured_models()``), and the admin inference-provider endpoints in
``chat/routes/admin.py`` (masked reads, keep-key-on-empty writes, admin
gating). No API-key provider is registered in production today (the Gemini
API provider was removed when all Gemini models moved to Vertex), so the
API-key store and endpoint tests register a fake provider to keep the
generic machinery covered. Everything runs against tmp_path -- no real data
directory or network involved.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest
from fastapi import HTTPException

import config.inference_providers as ip
import config.server_config as scfg


SA_KEY = {
    "type": "service_account",
    "project_id": "sa-project",
    "client_email": "vertex-runner@sa-project.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the store, legacy file, config file, and env at tmp_path."""
    store_dir = tmp_path / "inference_credentials"
    legacy_file = tmp_path / "server_credentials.json"
    monkeypatch.setattr(ip, "INFERENCE_CREDENTIALS_DIR", store_dir)
    monkeypatch.setattr(ip, "LEGACY_CREDENTIALS_FILE", legacy_file)
    monkeypatch.setattr(scfg, "SERVER_CONFIG_FILE", tmp_path / "server_config.json")
    for var in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "ANTHROPIC_VERTEX_REGION",
        "GEMINI_VERTEX_PROJECT_ID",
        "GEMINI_VERTEX_REGION",
    ):
        monkeypatch.delenv(var, raising=False)
    # Keep the dev machine's real gcloud ADC out of detection results.
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path / "gcloud"))
    return store_dir, legacy_file


@pytest.fixture
def fake_api(store, monkeypatch):
    """Register a fake API-key provider (the registry is empty in prod)."""
    monkeypatch.setattr(ip, "API_KEY_PROVIDERS", {
        "fake_api": {
            "label": "Fake API",
            "hint": "fk-...",
            "legacy_section": "fakeco",
            "model_backend": "vertex",
        },
    })
    return "fake_api"


def _write_vertex_config(gemini_project: str = "", anthropic_project: str = ""):
    """Write a server_config.json with the given Vertex project ids."""
    config = {}
    if gemini_project:
        config["gemini_vertex"] = {"vertex_project_id": gemini_project}
    if anthropic_project:
        config["anthropic"] = {"vertex_project_id": anthropic_project}
    scfg.SERVER_CONFIG_FILE.write_text(json.dumps(config))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_openrouter_is_the_only_registered_api_key_provider():
    """OpenRouter is the sole API-key provider (genapi Gemini was removed)."""
    import config.inference_providers as real_ip

    assert list(real_ip.API_KEY_PROVIDERS) == ["openrouter"]
    spec = real_ip.API_KEY_PROVIDERS["openrouter"]
    assert spec["label"] == "OpenRouter"
    assert spec["model_backend"] == "openrouter"
    # No legacy server_credentials.json section ever existed for OpenRouter.
    assert "legacy_section" not in spec


# ---------------------------------------------------------------------------
# Store read/write
# ---------------------------------------------------------------------------

def test_write_then_read_round_trip(fake_api):
    ip.write_inference_credentials("fake_api", {"api_key": "fk-new"})
    assert ip.read_inference_credentials("fake_api") == {"api_key": "fk-new"}


def test_write_sets_restrictive_permissions(store, fake_api):
    store_dir, _ = store
    ip.write_inference_credentials("fake_api", {"api_key": "k"})
    file_mode = stat.S_IMODE(os.stat(store_dir / "fake_api.json").st_mode)
    dir_mode = stat.S_IMODE(os.stat(store_dir).st_mode)
    assert file_mode == 0o600
    assert dir_mode == 0o700


def test_write_leaves_no_temp_files(store, fake_api):
    store_dir, _ = store
    ip.write_inference_credentials("fake_api", {"api_key": "k"})
    assert sorted(p.name for p in store_dir.iterdir()) == ["fake_api.json"]


def test_read_missing_returns_none(fake_api):
    assert ip.read_inference_credentials("fake_api") is None


def test_read_malformed_returns_none(store, fake_api):
    store_dir, _ = store
    store_dir.mkdir(parents=True)
    (store_dir / "fake_api.json").write_text("{not json")
    assert ip.read_inference_credentials("fake_api") is None


def test_unknown_provider_rejected(store):
    with pytest.raises(ValueError):
        ip.read_inference_credentials("nope")
    with pytest.raises(ValueError):
        ip.write_inference_credentials("nope", {})
    with pytest.raises(ValueError):
        ip.effective_api_key("nope")


# ---------------------------------------------------------------------------
# Effective key precedence
# ---------------------------------------------------------------------------

def test_effective_key_prefers_store_over_legacy(store, fake_api):
    _, legacy_file = store
    legacy_file.write_text(json.dumps({"fakeco": {"api_key": "legacy-key"}}))
    assert ip.effective_api_key("fake_api") == ("legacy-key", "legacy")
    ip.write_inference_credentials("fake_api", {"api_key": "store-key"})
    assert ip.effective_api_key("fake_api") == ("store-key", "store")


def test_effective_key_unconfigured(fake_api):
    assert ip.effective_api_key("fake_api") == (None, None)


def test_legacy_null_placeholder_treated_missing(store, fake_api):
    _, legacy_file = store
    legacy_file.write_text(json.dumps({"fakeco": {"api_key": "null"}}))
    assert ip.effective_api_key("fake_api") == (None, None)


# ---------------------------------------------------------------------------
# Available / configured models
# ---------------------------------------------------------------------------

def test_gemini_models_require_vertex_project(store, health_store):
    from chat.llm.config import MODEL_REGISTRY, get_available_models

    gemini_models = [
        model_id
        for model_id, entry in MODEL_REGISTRY.items()
        if entry["provider"] == "gemini" and not entry.get("deprecated")
    ]
    assert gemini_models, "expected at least one non-deprecated Gemini model"

    before = get_available_models()
    assert not any(m in before for m in gemini_models)

    _write_vertex_config(gemini_project="gv-project")
    after = get_available_models()
    assert all(m in after for m in gemini_models)


def test_anthropic_project_covers_gemini_via_fallback(store, health_store):
    """gemini_vertex inherits the Anthropic project id, so one project id
    configures every Vertex model (OpenRouter models need their own key)."""
    from chat.llm.config import MODEL_REGISTRY, get_configured_models

    _write_vertex_config(anthropic_project="one-project")
    configured = get_configured_models()
    expected = [
        m for m, e in MODEL_REGISTRY.items()
        if not e.get("deprecated") and e["provider"] != "openrouter"
    ]
    assert configured == expected


def test_openrouter_models_require_api_key(store, health_store):
    """OpenRouter models appear in the configured list only once an
    OpenRouter API key is present in the inference-credential store."""
    from chat.llm.config import MODEL_REGISTRY, get_configured_models

    openrouter_models = [
        m for m, e in MODEL_REGISTRY.items()
        if e["provider"] == "openrouter" and not e.get("deprecated")
    ]
    assert openrouter_models, "expected at least one OpenRouter model"

    before = get_configured_models()
    assert not any(m in before for m in openrouter_models)

    ip.write_inference_credentials("openrouter", {"api_key": "sk-or-v1-test"})
    after = get_configured_models()
    assert all(m in after for m in openrouter_models)
    # Vertex configured-ness is untouched by the OpenRouter key.
    assert [m for m in after if m not in openrouter_models] == before


def test_available_models_health_filter(store, health_store):
    """A failing health verdict hides a model from the picker; a later
    passing check brings it back. Never-checked models stay visible."""
    from chat.llm.config import get_available_models, get_configured_models

    _write_vertex_config(gemini_project="gv-project")
    configured = get_configured_models()
    assert "gemini-3.5-flash-lite" in configured

    # No verdicts recorded yet: everything configured is offered.
    assert get_available_models() == configured

    _run(health_store.record("gemini-3.5-flash-lite", False, "quota exhausted"))
    available = get_available_models()
    assert "gemini-3.5-flash-lite" not in available
    assert available == [m for m in configured if m != "gemini-3.5-flash-lite"]

    _run(health_store.record("gemini-3.5-flash-lite", True, None))
    assert get_available_models() == configured


def test_available_models_malformed_health_entry_kept(store, health_store):
    """Only an explicit failing verdict hides a model; a malformed stored
    entry (no ``ok`` key) must not."""
    from chat.llm.config import get_available_models

    _write_vertex_config(gemini_project="gv-project")
    health_store._statuses["gemini-3.5-flash-lite"] = {"weird": True}
    assert "gemini-3.5-flash-lite" in get_available_models()


# ---------------------------------------------------------------------------
# Vertex environment detection
# ---------------------------------------------------------------------------

def test_vertex_detection_nothing_configured(store):
    status = ip.vertex_environment_status()
    assert status["configured"] is False
    assert status["credentials"]["source"] is None
    assert status["anthropic"]["configured"] is False
    assert status["gemini_vertex"]["configured"] is False


def test_vertex_detection_env_service_account(store, tmp_path, monkeypatch):
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps(SA_KEY))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key_file))
    creds = ip.vertex_environment_status()["credentials"]
    assert creds["source"] == "env"
    assert creds["key_path"] == str(key_file)
    assert creds["service_account_email"] == SA_KEY["client_email"]
    assert creds["project_id"] == "sa-project"
    assert creds["problem"] is None


def test_vertex_detection_env_unreadable_key(store, tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json")
    )
    creds = ip.vertex_environment_status()["credentials"]
    assert creds["source"] == "env"
    assert creds["problem"]


def test_vertex_detection_gcloud_adc(store, tmp_path):
    gcloud_dir = tmp_path / "gcloud"
    gcloud_dir.mkdir()
    (gcloud_dir / "application_default_credentials.json").write_text(
        json.dumps({"type": "authorized_user", "quota_project_id": "adc-project"})
    )
    creds = ip.vertex_environment_status()["credentials"]
    assert creds["source"] == "gcloud_adc"
    assert creds["project_id"] == "adc-project"


def test_vertex_sections_report_config_sources(store, tmp_path):
    scfg.SERVER_CONFIG_FILE.write_text(
        json.dumps({"anthropic": {"vertex_project_id": "cfg-project"}})
    )
    status = ip.vertex_environment_status()
    assert status["configured"] is True
    assert status["anthropic"]["vertex_project_id"] == "cfg-project"
    assert status["anthropic"]["project_source"] == "server_config"
    # gemini_vertex inherits the anthropic project id
    assert status["gemini_vertex"]["vertex_project_id"] == "cfg-project"
    assert status["gemini_vertex"]["project_source"] == "anthropic_fallback"
    assert status["gemini_vertex"]["vertex_region"] == "global"


def test_vertex_sections_env_override_source(store, monkeypatch):
    monkeypatch.setenv("GEMINI_VERTEX_PROJECT_ID", "env-project")
    status = ip.vertex_environment_status()
    assert status["gemini_vertex"]["vertex_project_id"] == "env-project"
    assert status["gemini_vertex"]["project_source"] == "env"
    assert status["anthropic"]["configured"] is False


# ---------------------------------------------------------------------------
# Admin endpoints (chat/routes/admin.py)
# ---------------------------------------------------------------------------

ADMIN_USER = {"id": 1, "email": "admin@example.com"}


@pytest.fixture
def health_store(tmp_path, monkeypatch):
    """Swap the model-health store singleton for a tmp_path-backed one."""
    import chat.llm.health as health

    fresh = health.ModelHealthStore(tmp_path / "model_health.json")
    monkeypatch.setattr(health, "_store", fresh)
    return fresh


@pytest.fixture
def admin_routes(store, health_store, monkeypatch):
    import chat.routes.admin as admin
    monkeypatch.setattr(admin, "is_admin", lambda email: email == ADMIN_USER["email"])
    return admin


def _run(coro):
    return asyncio.run(coro)


def test_admin_endpoints_require_admin(admin_routes, fake_api):
    admin = admin_routes
    nobody = {"id": 2, "email": "user@example.com"}
    with pytest.raises(HTTPException) as exc_info:
        _run(admin.admin_list_inference_providers(user=nobody))
    assert exc_info.value.status_code == 403
    body = admin.InferenceProviderKeyUpdate(api_key="k")
    with pytest.raises(HTTPException) as exc_info:
        _run(admin.admin_update_inference_provider_key("fake_api", body, user=nobody))
    assert exc_info.value.status_code == 403


def test_admin_list_default_registry_cards(admin_routes):
    """The default registry yields the Vertex card plus the OpenRouter card."""
    result = _run(admin_routes.admin_list_inference_providers(user=ADMIN_USER))
    providers = result["providers"]
    assert [p["provider"] for p in providers] == ["vertex", "openrouter"]
    assert providers[0]["kind"] == "detected"
    openrouter = providers[1]
    assert openrouter["kind"] == "api_key"
    assert openrouter["configured"] is False
    # The OpenRouter card lists exactly the openrouter-backend models.
    from chat.llm.config import MODEL_REGISTRY
    expected = [
        m for m, e in MODEL_REGISTRY.items()
        if e["provider"] == "openrouter" and not e.get("deprecated")
    ]
    assert [m["id"] for m in openrouter["models"]] == expected


def test_admin_list_shape_and_masking(admin_routes, fake_api):
    ip.write_inference_credentials("fake_api", {"api_key": "fk-secret"})
    result = _run(admin_routes.admin_list_inference_providers(user=ADMIN_USER))
    providers = result["providers"]
    assert [p["provider"] for p in providers] == ["vertex", "fake_api"]

    vertex = providers[0]
    assert vertex["kind"] == "detected"
    assert "credentials" in vertex["detail"]

    fake = providers[1]
    assert fake["kind"] == "api_key"
    assert fake["configured"] is True
    assert fake["source"] == "store"
    assert fake["credentials"] == {"api_key_set": True}
    assert "fk-secret" not in json.dumps(result)


def test_admin_list_reports_legacy_source(admin_routes, store, fake_api):
    _, legacy_file = store
    legacy_file.write_text(json.dumps({"fakeco": {"api_key": "legacy-key"}}))
    result = _run(admin_routes.admin_list_inference_providers(user=ADMIN_USER))
    fake = result["providers"][1]
    assert fake["configured"] is True
    assert fake["source"] == "legacy"
    assert "legacy-key" not in json.dumps(result)


def test_admin_put_writes_store(admin_routes, fake_api):
    body = admin_routes.InferenceProviderKeyUpdate(api_key="  fk-new  ")
    status = _run(
        admin_routes.admin_update_inference_provider_key(
            "fake_api", body, user=ADMIN_USER
        )
    )
    assert status["configured"] is True
    assert status["source"] == "store"
    assert ip.read_inference_credentials("fake_api") == {"api_key": "fk-new"}
    assert "fk-new" not in json.dumps(status)


def test_admin_put_empty_keeps_stored_key(admin_routes, fake_api):
    ip.write_inference_credentials("fake_api", {"api_key": "keep-me"})
    body = admin_routes.InferenceProviderKeyUpdate(api_key="")
    _run(
        admin_routes.admin_update_inference_provider_key(
            "fake_api", body, user=ADMIN_USER
        )
    )
    assert ip.read_inference_credentials("fake_api") == {"api_key": "keep-me"}


def test_admin_put_empty_migrates_legacy_key_to_store(admin_routes, store, fake_api):
    _, legacy_file = store
    legacy_file.write_text(json.dumps({"fakeco": {"api_key": "legacy-key"}}))
    body = admin_routes.InferenceProviderKeyUpdate(api_key="")
    status = _run(
        admin_routes.admin_update_inference_provider_key(
            "fake_api", body, user=ADMIN_USER
        )
    )
    assert status["source"] == "store"
    assert ip.read_inference_credentials("fake_api") == {"api_key": "legacy-key"}


def test_admin_put_empty_without_stored_key_400(admin_routes, fake_api):
    body = admin_routes.InferenceProviderKeyUpdate(api_key="")
    with pytest.raises(HTTPException) as exc_info:
        _run(
            admin_routes.admin_update_inference_provider_key(
                "fake_api", body, user=ADMIN_USER
            )
        )
    assert exc_info.value.status_code == 400


def test_admin_put_vertex_not_editable(admin_routes):
    body = admin_routes.InferenceProviderKeyUpdate(api_key="k")
    with pytest.raises(HTTPException) as exc_info:
        _run(
            admin_routes.admin_update_inference_provider_key(
                "vertex", body, user=ADMIN_USER
            )
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "not_editable"


def test_admin_put_unknown_provider_404(admin_routes):
    """Unregistered providers 404 -- including the removed gemini_api."""
    body = admin_routes.InferenceProviderKeyUpdate(api_key="k")
    for provider in ("nope", "gemini_api"):
        with pytest.raises(HTTPException) as exc_info:
            _run(
                admin_routes.admin_update_inference_provider_key(
                    provider, body, user=ADMIN_USER
                )
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Per-provider model lists
# ---------------------------------------------------------------------------

def test_admin_list_includes_all_models_under_vertex(admin_routes):
    from chat.llm.config import MODEL_REGISTRY

    result = _run(admin_routes.admin_list_inference_providers(user=ADMIN_USER))
    by_provider = {p["provider"]: p for p in result["providers"]}

    vertex_models = by_provider["vertex"]["models"]
    expected_vertex = [
        m for m, e in MODEL_REGISTRY.items()
        if not e.get("deprecated") and e["provider"] != "openrouter"
    ]
    assert [m["id"] for m in vertex_models] == expected_vertex
    families = {m["id"]: m["family"] for m in vertex_models}
    assert families["claude-opus-4-8"] == "anthropic"
    assert families["gemini-3.5-flash-lite"] == "gemini_vertex"
    assert families["gemini-3.7-flash"] == "gemini_vertex"
    assert families["gemini-3.8-flash"] == "gemini_vertex"
    assert all(m["display_name"] for m in vertex_models)


def test_admin_list_models_exclude_deprecated(admin_routes):
    from chat.llm.config import MODEL_REGISTRY

    deprecated = {m for m, e in MODEL_REGISTRY.items() if e.get("deprecated")}
    assert deprecated, "expected at least one deprecated model in the registry"
    result = _run(admin_routes.admin_list_inference_providers(user=ADMIN_USER))
    listed = {
        m["id"] for p in result["providers"] for m in p["models"]
    }
    assert not listed & deprecated


# ---------------------------------------------------------------------------
# Model health check (chat/llm/health.py + admin endpoint)
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.checked: list[str] = []

    async def check_model_access(self, model: str) -> None:
        self.checked.append(model)
        if self.exc is not None:
            raise self.exc


@pytest.fixture
def fake_provider(monkeypatch):
    """Route health checks to a swappable fake provider instance."""
    import chat.llm.config as llm_config

    holder = {"provider": _FakeProvider()}
    monkeypatch.setattr(
        llm_config, "get_provider_instance", lambda name: holder["provider"]
    )
    return holder


def test_check_model_ok(fake_provider):
    from chat.llm.health import check_model

    result = _run(check_model("claude-opus-4-8"))
    assert result == {"model": "claude-opus-4-8", "ok": True, "error": None}
    assert fake_provider["provider"].checked == ["claude-opus-4-8"]


def test_check_model_unknown(fake_provider):
    from chat.llm.health import check_model

    result = _run(check_model("nope-1"))
    assert result["ok"] is False
    assert "Unknown model" in result["error"]
    assert fake_provider["provider"].checked == []


def test_check_model_failure_extracts_anthropic_body(fake_provider):
    from chat.llm.health import check_model

    exc = RuntimeError("Error code: 404")
    exc.status_code = 404
    exc.body = {
        "error": {
            "code": 404,
            "message": "Publisher Model claude-opus-4-8 was not found",
            "status": "NOT_FOUND",
        }
    }
    fake_provider["provider"] = _FakeProvider(exc=exc)
    result = _run(check_model("claude-opus-4-8"))
    assert result["ok"] is False
    assert result["error"] == (
        "HTTP 404: Publisher Model claude-opus-4-8 was not found"
    )


def test_check_model_failure_extracts_genai_message(fake_provider):
    from chat.llm.health import check_model

    exc = RuntimeError("boom")
    exc.code = 429
    exc.message = "Quota exceeded for quota metric 'GenerateContent requests'"
    fake_provider["provider"] = _FakeProvider(exc=exc)
    result = _run(check_model("gemini-3-flash-preview"))
    assert result["ok"] is False
    assert result["error"] == (
        "HTTP 429: Quota exceeded for quota metric 'GenerateContent requests'"
    )


def test_check_model_failure_plain_exception_and_truncation(fake_provider):
    from chat.llm.health import _MAX_ERROR_LENGTH, check_model

    fake_provider["provider"] = _FakeProvider(exc=ValueError("x" * 2000))
    result = _run(check_model("gemini-3-flash-preview"))
    assert result["ok"] is False
    assert len(result["error"]) == _MAX_ERROR_LENGTH
    assert result["error"].endswith("...")


def test_admin_test_model_endpoint(admin_routes, fake_provider, health_store):
    body = admin_routes.InferenceModelTestRequest(model="claude-sonnet-5")
    result = _run(admin_routes.admin_test_inference_model(body, user=ADMIN_USER))
    assert result["model"] == "claude-sonnet-5"
    assert result["ok"] is True
    assert result["error"] is None
    assert result["checked_at"]
    # The verdict landed in the server-global store
    stored = health_store.get("claude-sonnet-5")
    assert stored["ok"] is True
    assert stored["checked_at"] == result["checked_at"]


def test_admin_test_model_unknown_404(admin_routes, fake_provider):
    body = admin_routes.InferenceModelTestRequest(model="nope-1")
    with pytest.raises(HTTPException) as exc_info:
        _run(admin_routes.admin_test_inference_model(body, user=ADMIN_USER))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "unknown_model"


def test_admin_test_model_requires_admin(admin_routes, fake_provider):
    nobody = {"id": 2, "email": "user@example.com"}
    body = admin_routes.InferenceModelTestRequest(model="claude-sonnet-5")
    with pytest.raises(HTTPException) as exc_info:
        _run(admin_routes.admin_test_inference_model(body, user=nobody))
    assert exc_info.value.status_code == 403
    assert fake_provider["provider"].checked == []


# ---------------------------------------------------------------------------
# Model-health store
# ---------------------------------------------------------------------------

def test_store_record_persists_and_reloads(health_store, tmp_path):
    import chat.llm.health as health

    status = _run(health_store.record("claude-sonnet-5", False, "boom"))
    assert status["ok"] is False
    assert status["error"] == "boom"
    assert status["checked_at"]

    # A fresh store instance reads the same verdict back from disk
    reloaded = health.ModelHealthStore(tmp_path / "model_health.json")
    assert reloaded.get("claude-sonnet-5")["error"] == "boom"
    assert reloaded.get("missing") is None


def test_store_run_check_records_verdict(health_store, fake_provider):
    fake_provider["provider"] = _FakeProvider(exc=ValueError("no access"))
    result = _run(health_store.run_check("claude-opus-5"))
    assert result["ok"] is False
    assert result["error"] == "no access"
    assert health_store.get("claude-opus-5")["ok"] is False

    fake_provider["provider"] = _FakeProvider()
    result = _run(health_store.run_check("claude-opus-5"))
    assert result["ok"] is True
    assert health_store.get("claude-opus-5")["ok"] is True


def test_store_ignores_malformed_file(tmp_path):
    import chat.llm.health as health

    path = tmp_path / "model_health.json"
    path.write_text("{not json")
    assert health.ModelHealthStore(path).get_all() == {}


def test_startup_checks_cover_configured_models(health_store, fake_provider, monkeypatch):
    import chat.llm.config as llm_config

    monkeypatch.setattr(
        llm_config,
        "get_configured_models",
        lambda: ["claude-sonnet-5", "gemini-3-flash-preview"],
    )
    _run(health_store.run_startup_checks())
    assert sorted(fake_provider["provider"].checked) == [
        "claude-sonnet-5",
        "gemini-3-flash-preview",
    ]
    assert health_store.get("claude-sonnet-5")["ok"] is True
    assert health_store.get("gemini-3-flash-preview")["ok"] is True


def test_startup_checks_noop_when_unconfigured(health_store, fake_provider, monkeypatch):
    import chat.llm.config as llm_config

    monkeypatch.setattr(llm_config, "get_configured_models", lambda: [])
    _run(health_store.run_startup_checks())
    assert fake_provider["provider"].checked == []
    assert health_store.get_all() == {}


def test_startup_checks_sweep_health_hidden_models(health_store, fake_provider, monkeypatch):
    """A model hidden from the picker by a failing verdict is still swept
    at startup (the sweep iterates the config-presence list, not the
    health-filtered one), so it can recover."""
    import chat.llm.config as llm_config

    monkeypatch.setattr(
        llm_config, "get_configured_models", lambda: ["claude-sonnet-5"]
    )
    _run(health_store.record("claude-sonnet-5", False, "was broken"))
    _run(health_store.run_startup_checks())
    assert fake_provider["provider"].checked == ["claude-sonnet-5"]
    assert health_store.get("claude-sonnet-5")["ok"] is True


def test_run_startup_model_checks_never_raises(monkeypatch):
    import chat.llm.health as health

    class _ExplodingStore:
        async def run_startup_checks(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(health, "_store", _ExplodingStore())
    _run(health.run_startup_model_checks())  # must not raise


def test_schedule_model_rechecks_runs_and_records(health_store, fake_provider):
    import chat.llm.health as health

    async def _go():
        task = health.schedule_model_rechecks(
            ["claude-sonnet-5", "gemini-3-flash-preview"]
        )
        assert task is not None
        await task

    _run(_go())
    assert fake_provider["provider"].checked == [
        "claude-sonnet-5",
        "gemini-3-flash-preview",
    ]
    assert health_store.get("claude-sonnet-5")["ok"] is True
    assert health_store.get("gemini-3-flash-preview")["ok"] is True


def test_schedule_model_rechecks_empty_is_noop(health_store):
    import chat.llm.health as health

    async def _go():
        assert health.schedule_model_rechecks([]) is None

    _run(_go())


def test_admin_put_schedules_backend_rechecks(admin_routes, fake_api, monkeypatch):
    """Saving an API key kicks off background rechecks for that backend's
    models so ones hidden by a stale failing verdict can reappear."""
    import chat.llm.health as health
    from chat.llm.config import MODEL_REGISTRY

    scheduled: list[list[str]] = []
    monkeypatch.setattr(
        health, "schedule_model_rechecks", lambda ids: scheduled.append(ids)
    )
    body = admin_routes.InferenceProviderKeyUpdate(api_key="fk-new")
    _run(admin_routes.admin_update_inference_provider_key(
        "fake_api", body, user=ADMIN_USER
    ))
    # The fake provider declares model_backend "vertex", so every
    # non-deprecated Vertex model is rechecked.
    expected = [
        model_id
        for model_id, entry in MODEL_REGISTRY.items()
        if not entry.get("deprecated") and entry["provider"] != "openrouter"
    ]
    assert scheduled == [expected]


def test_admin_list_models_carry_status(admin_routes, health_store):
    _run(health_store.record("claude-opus-4-8", False, "not enabled"))
    result = _run(admin_routes.admin_list_inference_providers(user=ADMIN_USER))
    vertex_models = {
        m["id"]: m for m in result["providers"][0]["models"]
    }
    assert vertex_models["claude-opus-4-8"]["status"]["ok"] is False
    assert vertex_models["claude-opus-4-8"]["status"]["error"] == "not enabled"
    # Never-checked models carry a null status
    assert vertex_models["claude-sonnet-5"]["status"] is None
