"""Check that host runtimes needed to spawn MCP servers are present.

We no longer run MCP servers in Docker — stdio servers run via ``npx`` and the
Google Workspace server runs via ``uvx``. Missing runtimes are not fatal at
startup (an MCP call will surface a structured error), but we log a loud
warning so operators notice before a user hits it.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# Provider -> its LangChain integration package (import name). A configured
# provider whose package is not importable cannot make model calls.
#
# NOTE (L1): these packages ship in ``[project.dependencies]`` (always-installed),
# so in practice this warning essentially never fires today. It is correct
# defensive / future-proofing per spec §11 ("preflight warns if a configured
# provider's package is absent").
_PROVIDER_PACKAGES = {
    "anthropic": "langchain_anthropic",
    "openai": "langchain_openai",
    "google_genai": "langchain_google_genai",
    "ollama": "langchain_ollama",
}


def check_mcp_runtimes(required: list[str]) -> list[str]:
    """Return the subset of required runtimes not found on PATH."""
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        logger.warning(
            "[mcp:preflight] missing host runtime(s): %s — MCP servers needing "
            "them will fail until installed (npx=Node, uvx=uv)",
            ", ".join(missing),
        )
    # Same startup entry point: also warn about configured providers whose
    # LangChain package is absent.
    check_configured_providers()
    return missing


def check_configured_providers() -> list[str]:
    """Warn for each configured model provider whose LangChain package is absent.

    "Configured" is detected DB-free (this is a host preflight, not a DB check):
    a provider is configured when its env-fallback settings key (from the
    resolver's ``_ENV_KEY_ATTR`` map) names a non-empty settings attribute.

    Limitation: providers configured *only* via DB credential rows are not seen
    here; the turn-time resolver still surfaces a missing package as an import
    error. Returns the list of configured providers with a missing package.
    """
    # Imported here (read-only) to avoid a module-level cycle and keep the env-key
    # map single-sourced with the resolver.
    from src.services.model_resolver import _ENV_KEY_ATTR

    settings = get_settings()
    missing_providers: list[str] = []
    for provider, package in _PROVIDER_PACKAGES.items():
        env_attr = _ENV_KEY_ATTR.get(provider)
        if not env_attr:
            # No env-fallback key (e.g. local ``ollama``) — not env-configurable,
            # so nothing to warn about from a host preflight.
            continue
        if not getattr(settings, env_attr, None):
            continue  # provider not configured via env
        if importlib.util.find_spec(package) is None:
            logger.warning(
                "[preflight] provider '%s' is configured but its package '%s' is "
                "not installed — model calls for it will fail",
                provider,
                package,
            )
            missing_providers.append(provider)
    return missing_providers
