"""Provider capability map — the code-side source of truth for how each provider
authenticates and how its credential form is shaped.

Sibling of ``model_catalog.py``: that file holds per-MODEL facts (context window,
price, thinking style); this one holds per-PROVIDER facts (display name, auth kind,
the credential fields a client must render). Both are versioned code. Which model
backs a tier, and whose key is used, remain DB data.

At four providers a fixed (api_key, base_url) pair was enough. It is not: Bedrock
authenticates with a region plus SigV4 credentials and Azure with an endpoint plus a
deployment name, so the credential form is declared per provider and rendered from
this declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AuthKind = Literal["api_key", "keyless_base_url", "aws_sigv4", "azure_deployment"]
FieldKind = Literal["secret", "text", "url"]


@dataclass(frozen=True)
class CredentialField:
    """One input in a provider's credential form.

    ``kind="secret"`` is load-bearing: a secret field's VALUE is never returned by the
    API, only its key name, so a client can say "configured — leave blank to keep"
    without the response envelope ever echoing it.
    """

    key: str
    label: str
    kind: FieldKind
    required: bool
    placeholder: str | None = None


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    display_name: str
    auth_kind: AuthKind
    credential_fields: tuple[CredentialField, ...]
    docs_url: str | None = None


_API_KEY = CredentialField("api_key", "API key", "secret", True, "sk-…")
_BASE_URL_OPTIONAL = CredentialField("base_url", "Base URL — optional", "url", False, None)


PROVIDER_CATALOG: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        provider="anthropic",
        display_name="Anthropic",
        auth_kind="api_key",
        credential_fields=(_API_KEY, _BASE_URL_OPTIONAL),
        docs_url="https://console.anthropic.com/settings/keys",
    ),
    "openai": ProviderSpec(
        provider="openai",
        display_name="OpenAI",
        auth_kind="api_key",
        credential_fields=(_API_KEY, _BASE_URL_OPTIONAL),
        docs_url="https://platform.openai.com/api-keys",
    ),
    "google_genai": ProviderSpec(
        provider="google_genai",
        display_name="Google Gemini",
        auth_kind="api_key",
        credential_fields=(_API_KEY, _BASE_URL_OPTIONAL),
        docs_url="https://aistudio.google.com/apikey",
    ),
    "ollama": ProviderSpec(
        provider="ollama",
        display_name="Ollama",
        auth_kind="keyless_base_url",
        # Keyless: the base URL IS the credential. Required, unlike everywhere else.
        credential_fields=(
            CredentialField("base_url", "Base URL", "url", True, "http://localhost:11434"),
        ),
        docs_url="https://ollama.com/download",
    ),
}


def get_provider_spec(provider: str) -> ProviderSpec | None:
    """Return the ProviderSpec for *provider*, or None if it is not catalogued."""
    return PROVIDER_CATALOG.get(provider)


def public_field_keys(provider: str) -> frozenset[str]:
    """Credential keys whose VALUES may be returned to a client.

    Fails closed by construction: a key is public only if it is a DECLARED field with
    a non-secret kind. An undeclared key stored in ``extra_config`` is therefore
    treated as a secret and never echoed.
    """
    spec = PROVIDER_CATALOG.get(provider)
    if spec is None:
        return frozenset()
    return frozenset(f.key for f in spec.credential_fields if f.kind != "secret")
