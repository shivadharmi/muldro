"""Provider catalog facts, and the invariants that keep it in step with its siblings."""

import pytest

from src.config.model_catalog import MODEL_CATALOG
from src.config.provider_catalog import (
    PROVIDER_CATALOG,
    get_provider_spec,
    public_field_keys,
)
from src.services.model_resolver import KEYLESS_PROVIDERS


def test_every_catalogued_provider_has_a_spec():
    assert set(PROVIDER_CATALOG) == set(MODEL_CATALOG)


def test_display_names_are_human_readable():
    """Guards B5: the UI was rendering raw provider slugs (e.g. "google_genai")
    instead of human names. No provider's display_name may equal its own slug, so a
    newly-added provider that forgets a display name fails this test."""
    assert PROVIDER_CATALOG["google_genai"].display_name == "Google Gemini"
    assert PROVIDER_CATALOG["anthropic"].display_name == "Anthropic"
    for provider, spec in PROVIDER_CATALOG.items():
        assert spec.display_name != provider


def test_catalogs_reject_mutation():
    """Immutable by construction, not by convention: a frozen ProviderSpec inside a
    mutable dict is still reassignable, so the mapping is frozen too."""
    with pytest.raises(TypeError):
        PROVIDER_CATALOG["anthropic"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        MODEL_CATALOG["anthropic"] = ()  # type: ignore[index]


def test_keyless_providers_agree_with_the_resolver():
    """Two sources of truth for 'needs no API key' must not drift apart."""
    keyless = {p for p, s in PROVIDER_CATALOG.items() if s.auth_kind == "keyless_base_url"}
    assert keyless == set(KEYLESS_PROVIDERS)


def test_ollama_requires_a_base_url_and_no_key():
    spec = get_provider_spec("ollama")
    assert spec is not None
    keys = {f.key: f for f in spec.credential_fields}
    assert "api_key" not in keys
    assert keys["base_url"].required is True


def test_api_key_field_is_marked_secret():
    spec = get_provider_spec("anthropic")
    assert spec is not None
    api_key = next(f for f in spec.credential_fields if f.key == "api_key")
    assert api_key.kind == "secret"


def test_public_field_keys_excludes_secrets():
    """Fails closed: only DECLARED non-secret fields are public."""
    assert public_field_keys("anthropic") == frozenset({"base_url"})
    assert public_field_keys("unknown_provider") == frozenset()


def test_get_provider_spec_returns_none_for_unknown():
    assert get_provider_spec("nope") is None
