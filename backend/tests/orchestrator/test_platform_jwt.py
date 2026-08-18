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


def test_verify_only_process_needs_no_private_key(monkeypatch):
    """The adapter is the tenant-isolation boundary and only ever VERIFIES.

    Giving it the signing key would mean anything that compromised it could mint a
    valid JWT for any tenant. A process configured with only the public half must
    therefore verify successfully — and refuse to mint rather than silently sign
    with an ephemeral key nothing else can verify.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    import src.orchestrator.platform_jwt as pj

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    def _run_with(private: str | None, public: str | None):
        """Reset the module's lazy key cache and re-resolve under new settings."""
        monkeypatch.setattr(pj, "_private_key", None, raising=False)
        monkeypatch.setattr(pj, "_public_key", None, raising=False)

        class _S:
            platform_jwt_private_pem = private
            platform_jwt_public_pem = public

        monkeypatch.setattr(pj, "get_settings", lambda: _S())

    # Minter (private only) produces a token...
    _run_with(private_pem, None)
    token = pj.mint_platform_jwt(
        principal_id="user_1", tenant_id="t", workspace_id="w", capabilities=["email.read"]
    )

    # ...and a verify-only process (public only) accepts it.
    _run_with(None, public_pem)
    claims = pj.verify_platform_jwt(token, audience="toolhive-vmcp")
    assert claims["sub"] == "user_1"

    # JWKS still publishes the right key without the private half.
    assert pj.get_jwks()["keys"][0]["kid"] == "jarvis-platform-1"

    # And minting from a verify-only process is a loud error, not an ephemeral key.
    with pytest.raises(pj.MissingSigningKeyError):
        pj.mint_platform_jwt(
            principal_id="user_1", tenant_id="t", workspace_id="w", capabilities=[]
        )
