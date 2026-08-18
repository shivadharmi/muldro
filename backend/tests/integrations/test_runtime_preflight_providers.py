import logging
from types import SimpleNamespace
from unittest.mock import patch

from src.integrations.runtime_preflight import check_configured_providers


def _settings(**keys):
    """Minimal settings stub with explicit api-key attrs (avoid MagicMock truthiness)."""
    return SimpleNamespace(
        anthropic_api_key=keys.get("anthropic_api_key", ""),
        openai_api_key=keys.get("openai_api_key", ""),
        google_api_key=keys.get("google_api_key", ""),
    )


def test_warns_when_configured_provider_package_missing(caplog):
    with (
        patch(
            "src.integrations.runtime_preflight.get_settings",
            return_value=_settings(openai_api_key="sk-x"),
        ),
        patch("importlib.util.find_spec", return_value=None),
        caplog.at_level(logging.WARNING),
    ):
        missing = check_configured_providers()

    assert "openai" in missing
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "openai" in joined
    assert "langchain_openai" in joined


def test_no_warning_when_package_present(caplog):
    with (
        patch(
            "src.integrations.runtime_preflight.get_settings",
            return_value=_settings(openai_api_key="sk-x"),
        ),
        patch("importlib.util.find_spec", return_value=object()),
        caplog.at_level(logging.WARNING),
    ):
        missing = check_configured_providers()

    assert missing == []
    assert caplog.records == []


def test_no_warning_for_unconfigured_provider(caplog):
    # openai env key empty -> not configured -> never checked, even if pkg absent.
    with (
        patch(
            "src.integrations.runtime_preflight.get_settings",
            return_value=_settings(anthropic_api_key="sk-anthropic"),
        ),
        patch("importlib.util.find_spec", return_value=None) as find_spec,
        caplog.at_level(logging.WARNING),
    ):
        missing = check_configured_providers()

    # Only anthropic is configured; openai/google are not, so they are skipped.
    assert "openai" not in missing
    assert "google_genai" not in missing
    # find_spec is only consulted for the configured provider (anthropic).
    for call in find_spec.call_args_list:
        assert call.args[0] == "langchain_anthropic"
