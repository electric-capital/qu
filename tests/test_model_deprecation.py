"""Tests for deprecated-model handling (Gemini 3.1 Pro).

gemini-3.1-pro-preview is deprecated: it is hidden from the new-conversation
picker (excluded from get_available_models()) but stays in MODEL_REGISTRY so
existing conversations, routines, and stored defaults keep working. As part of
the deprecation its transport moved from the genapi developer endpoint to the
Gemini Vertex backend.
"""

from unittest.mock import patch

from chat.llm.config import (
    MODEL_REGISTRY,
    get_available_models,
    get_backend_for_model,
    get_provider_for_model,
)


class TestGemini31ProDeprecation:
    def test_still_registered(self):
        """The model stays in the registry so existing rows keep resolving."""
        assert "gemini-3.1-pro-preview" in MODEL_REGISTRY
        assert get_provider_for_model("gemini-3.1-pro-preview") == "gemini"

    def test_flagged_deprecated(self):
        assert MODEL_REGISTRY["gemini-3.1-pro-preview"].get("deprecated") is True

    def test_served_via_vertex(self):
        """The deprecated model now runs on the Gemini Vertex backend."""
        assert get_backend_for_model("gemini-3.1-pro-preview") == "vertex"

    def test_all_gemini_models_on_vertex(self):
        """Every Gemini model runs on Vertex (the genapi transport was
        removed; thought signatures don't cross the Vertex/AI Studio border)."""
        for model_id, entry in MODEL_REGISTRY.items():
            if entry["provider"] == "gemini":
                assert get_backend_for_model(model_id) == "vertex", model_id

    def test_excluded_from_available_models_even_with_credentials(self):
        """With every backend credentialed, the deprecated model is still
        absent from available_models while all other registry models appear."""
        fake_config = {
            "gemini_vertex": {"vertex_project_id": "proj"},
            "anthropic": {"vertex_project_id": "proj"},
        }

        class _EmptyHealthStore:
            def get_all(self):
                return {}

        with patch("config.server_config.load_server_config", return_value=fake_config), \
             patch("chat.llm.health.get_model_health_store", return_value=_EmptyHealthStore()), \
             patch(
                 "config.inference_providers.effective_api_key",
                 return_value=("sk-or-v1-test", "store"),
             ):
            available = get_available_models()

        assert "gemini-3.1-pro-preview" not in available
        expected = [m for m, e in MODEL_REGISTRY.items() if not e.get("deprecated")]
        assert available == expected
