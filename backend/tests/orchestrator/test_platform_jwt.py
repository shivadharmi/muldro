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
    assert claims["iss"] == "muldro-auth"
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
    assert key["kid"] == "muldro-platform-1"
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
    assert pj.get_jwks()["keys"][0]["kid"] == "muldro-platform-1"

    # And minting from a verify-only process is a loud error, not an ephemeral key.
    with pytest.raises(pj.MissingSigningKeyError):
        pj.mint_platform_jwt(
            principal_id="user_1", tenant_id="t", workspace_id="w", capabilities=[]
        )


def _rsa_pem_pair() -> tuple[str, str]:
    """Generate an RSA keypair and return its (private_pem, public_pem) strings."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

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
    return private_pem, public_pem


def _configure_keys(monkeypatch, module, private: str | None, public: str | None) -> None:
    """Reset the module's lazy key caches and re-resolve under new settings."""
    monkeypatch.setattr(module, "_private_key", None, raising=False)
    monkeypatch.setattr(module, "_public_key", None, raising=False)

    class _S:
        platform_jwt_private_pem = private
        platform_jwt_public_pem = public

    monkeypatch.setattr(module, "get_settings", lambda: _S())


def test_private_key_wins_over_mismatched_public_pem(monkeypatch):
    """A process that HOLDS the signing key must derive its public half from it.

    deploy.sh puts BOTH PEMs in the minting process's env, so a rotated private
    PEM with a stale public one would otherwise make the published JWKS (and this
    process's own verification) describe a key it does not sign with.
    """
    import json

    import jwt

    import src.orchestrator.platform_jwt as pj

    signing_private_pem, signing_public_pem = _rsa_pem_pair()
    stale_private_pem, stale_public_pem = _rsa_pem_pair()
    assert signing_public_pem != stale_public_pem

    # Deliberately mismatched pair: private = signing key, public = an unrelated key.
    _configure_keys(monkeypatch, pj, signing_private_pem, stale_public_pem)

    # JWKS follows the PRIVATE key, not the configured (stale) public PEM.
    from cryptography.hazmat.primitives import serialization

    expected = serialization.load_pem_private_key(
        signing_private_pem.encode(), password=None
    ).public_key()
    stale = serialization.load_pem_public_key(stale_public_pem.encode())

    jwks_key = pj.get_jwks()["keys"][0]
    expected_n = expected.public_numbers().n
    stale_n = stale.public_numbers().n
    assert jwks_key["n"] == pj._b64url_uint(
        expected_n.to_bytes((expected_n.bit_length() + 7) // 8, byteorder="big")
    )
    assert jwks_key["n"] != pj._b64url_uint(
        stale_n.to_bytes((stale_n.bit_length() + 7) // 8, byteorder="big")
    )

    # verify_platform_jwt follows the private key too: its own token verifies...
    token = pj.mint_platform_jwt(
        principal_id="user_1", tenant_id="t", workspace_id="w", capabilities=["email.read"]
    )
    assert pj.verify_platform_jwt(token, audience="toolhive-vmcp")["sub"] == "user_1"

    # ...while a token signed by the stale key does not.
    foreign = jwt.encode(
        {
            "iss": pj.ISSUER,
            "sub": "user_1",
            "aud": pj.DEFAULT_AUDIENCE,
            "iat": 0,
            "exp": 9999999999,
        },
        stale_private_pem,
        algorithm="RS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        pj.verify_platform_jwt(foreign, audience="toolhive-vmcp")

    # And a token minted here verifies against this process's PUBLISHED JWKS.
    jwks_public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwks_key))
    claims = jwt.decode(
        token,
        jwks_public_key,
        algorithms=["RS256"],
        audience=pj.DEFAULT_AUDIENCE,
        issuer=pj.ISSUER,
    )
    assert claims["sub"] == "user_1"


def test_public_pem_still_used_when_no_private_key(monkeypatch):
    """Verify-only behaviour is unchanged: public PEM alone verifies and cannot mint.

    Guards the BUG-1 fix against over-correcting — `_verifying_key()` must not let
    `MissingSigningKeyError` escape while resolving a verify-only process's key.
    """
    import src.orchestrator.platform_jwt as pj

    private_pem, public_pem = _rsa_pem_pair()

    _configure_keys(monkeypatch, pj, private_pem, None)
    token = pj.mint_platform_jwt(
        principal_id="user_2", tenant_id="t", workspace_id="w", capabilities=["email.read"]
    )

    _configure_keys(monkeypatch, pj, None, public_pem)
    assert pj.verify_platform_jwt(token, audience="toolhive-vmcp")["sub"] == "user_2"
    assert pj.get_jwks()["keys"][0]["kid"] == "muldro-platform-1"

    with pytest.raises(pj.MissingSigningKeyError):
        pj.mint_platform_jwt(
            principal_id="user_2", tenant_id="t", workspace_id="w", capabilities=[]
        )


def test_malformed_private_pem_fails_at_import(monkeypatch):
    """A bad PEM must crash at import, not as a 500 on the first gateway call."""
    import importlib

    import src.config.settings as settings_mod
    import src.orchestrator.platform_jwt as pj

    class _S:
        platform_jwt_private_pem = "-----BEGIN PRIVATE KEY-----\nnot-a-key\n"
        platform_jwt_public_pem = None

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _S())
    try:
        with pytest.raises(ValueError):
            importlib.reload(pj)
    finally:
        monkeypatch.undo()
        importlib.reload(pj)


def test_import_without_any_pem_generates_no_key(monkeypatch):
    """Neither PEM configured: import stays clean and lazy (no ephemeral key yet)."""
    import importlib

    import src.config.settings as settings_mod
    import src.orchestrator.platform_jwt as pj

    class _S:
        platform_jwt_private_pem = None
        platform_jwt_public_pem = None

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _S())
    try:
        importlib.reload(pj)
        assert pj._private_key is None
        assert pj._public_key is None
    finally:
        monkeypatch.undo()
        importlib.reload(pj)
