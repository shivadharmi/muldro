"""Tests for GET /v1/model-catalog."""

from dataclasses import fields

from src.api.routes_model_config import CatalogModel, CatalogProvider, CredentialFieldModel
from src.config.model_catalog import ModelSpec
from src.config.provider_catalog import CredentialField, ProviderSpec
from tests.helpers.model_config import _client


def test_get_model_catalog_returns_flat_models():
    """Models are flat and carry their provider, so a client can search across
    providers with one filter instead of a nested walk."""
    with _client() as c:
        r = c.get("/v1/model-catalog")
        assert r.status_code == 200
        body = r.json()

        sonnet = next(m for m in body["models"] if m["model_id"] == "claude-sonnet-4-6")
        assert sonnet["provider"] == "anthropic"
        assert sonnet["context_window"] == 200_000
        assert sonnet["input_cost_per_1k"] == 0.003
        assert sonnet["output_cost_per_1k"] == 0.015
        assert sonnet["supports_prompt_cache"] is True
        assert sonnet["suggested_tier"] == "balanced"


def test_get_model_catalog_returns_providers_with_credential_schema():
    with _client() as c:
        body = c.get("/v1/model-catalog").json()

        by_name = {p["provider"]: p for p in body["providers"]}
        assert by_name["google_genai"]["display_name"] == "Google Gemini"
        assert by_name["anthropic"]["auth_kind"] == "api_key"
        assert by_name["anthropic"]["model_count"] == 3

        ollama = by_name["ollama"]
        assert ollama["auth_kind"] == "keyless_base_url"
        assert [f["key"] for f in ollama["credential_fields"]] == ["base_url"]
        assert ollama["credential_fields"][0]["required"] is True


def test_get_model_catalog_still_returns_agents():
    with _client() as c:
        body = c.get("/v1/model-catalog").json()
        names = {a["name"] for a in body["agents"]}
        assert {"planner", "perceiver", "persona"} <= names
        planner = next(a for a in body["agents"] if a["name"] == "planner")
        assert planner["tier"] == "reasoning"


def test_catalog_dtos_expose_every_source_field():
    """B5 was the API silently dropping fields ModelSpec already carried. These DTOs
    are hand-restated copies of their source dataclasses, so nothing but this test
    stops the same drift recurring the next time a source gains a field."""
    assert {f.name for f in fields(ModelSpec)} <= set(CatalogModel.model_fields)
    assert {f.name for f in fields(CredentialField)} <= set(CredentialFieldModel.model_fields)
    assert {f.name for f in fields(ProviderSpec)} <= set(CatalogProvider.model_fields)


def test_model_catalog_includes_agents():
    """The catalog must expose the agent roster + each agent's default tier so the
    Settings UI can offer per-agent override creation (F1)."""
    with _client() as c:
        r = c.get("/v1/model-catalog")
        assert r.status_code == 200
        body = r.json()
        assert "agents" in body
        agents = {a["name"]: a for a in body["agents"]}
        # The 6 canonical agents are present with a name/display_name/tier triple.
        assert {"planner", "perceiver", "librarian", "executor", "presenter", "persona"} <= set(
            agents
        )
        assert agents["planner"]["tier"] == "reasoning"
        assert agents["persona"]["tier"] == "fast"
        assert all({"name", "display_name", "tier"} <= set(a) for a in body["agents"])
