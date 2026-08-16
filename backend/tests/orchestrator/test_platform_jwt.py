"""Unit tests for short-lived platform JWT mint/verify + JWKS (ToolHive gateway).

Pure unit tests — no DB, no network. Exercises mint/verify roundtrip, audience
rejection, and JWKS key exposure.
"""

import pytest

from src.orchestrator.platform_jwt import get_jwks, mint_platform_jwt, verify_platform_jwt


def test_mint_verify_roundtrip():
    token = mint_platform_jwt(
        principal_id="user_123",
        tenant_id="tenant_abc",
        workspace_id="ws_xyz",
        capabilities=["email.read", "email.send"],
    )

    claims = verify_platform_jwt(token, audience="toolhive-vmcp")

    assert claims["sub"] == "user_123"
    assert claims["tenant_id"] == "tenant_abc"
    assert claims["workspace_id"] == "ws_xyz"
    assert claims["aud"] == "toolhive-vmcp"
    assert claims["capabilities"] == ["email.read", "email.send"]
    assert claims["authorization_source"] == "direct_user_request"
    assert claims["iss"] == "jarvis-auth"
    assert claims["exp"] == claims["iat"] + 300


def test_verify_rejects_wrong_audience():
    token = mint_platform_jwt(
        principal_id="user_123",
        tenant_id="tenant_abc",
        workspace_id="ws_xyz",
        capabilities=["email.read"],
    )

    with pytest.raises(Exception):
        verify_platform_jwt(token, audience="some-other-audience")


def test_get_jwks_returns_public_key_with_kid():
    jwks = get_jwks()

    assert "keys" in jwks
    assert len(jwks["keys"]) >= 1

    key = jwks["keys"][0]
    assert key["kid"] == "jarvis-platform-1"
    assert key["kty"] in ("RSA", "EC")
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
