"""Tests for Gmail gateway settings defaults.

The gateway slice (ToolHive vMCP + OpenConnector) is opt-in and off by default —
these fields must default to their poll/legacy-safe values so an unconfigured
deployment behaves exactly as before.
"""

from src.config.settings import Settings


def test_gmail_via_gateway_defaults_false():
    assert Settings().gmail_via_gateway is False


def test_toolhive_vmcp_url_defaults_none():
    assert Settings().toolhive_vmcp_url is None


def test_openconnector_mcp_url_defaults_none():
    assert Settings().openconnector_mcp_url is None


def test_openconnector_runtime_token_defaults_none():
    assert Settings().openconnector_runtime_token is None


def test_platform_jwt_private_pem_defaults_none():
    assert Settings().platform_jwt_private_pem is None
